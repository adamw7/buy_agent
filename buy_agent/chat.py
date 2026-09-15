"""The seam between a prompt and a model server's answer: what LangChain used to be
(ADR-0002, ADR-0038)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Any,
    Generic,
    Protocol,
    TypeAlias,
    TypeVar,
    runtime_checkable,
)

from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

#: One turn sent to a model server, in the shape both read it: ollama's
#: ``chat(messages=...)`` and the OpenAI API's ``messages`` take the same pair.
Message: TypeAlias = dict[str, str]

#: The Pydantic model one call is constrained to and read back as -- ``SearchQuery`` for
#: the refining step, ``ProductList`` for the extracting one (ADR-0004).
SchemaT = TypeVar("SchemaT", bound=BaseModel)

#: How much of an unreadable answer the failure carries: enough to recognise a
#: half-finished object by, short enough for a one-line hint.
_QUOTED = 200


class UnreadableAnswerError(ValueError):
    """The server answered, with something that is not the JSON it was asked for
    (ADR-0009, ADR-0019)."""


class ChatModel(Protocol):
    """What the pipeline needs of a model server, and nothing else."""

    def answer(self, messages: Sequence[Message], schema: type[SchemaT]) -> SchemaT:
        """Answer ``messages`` with an instance of ``schema``, and nothing else."""


@runtime_checkable
class Closable(Protocol):
    """Something holding a connection open that can be told to let go of it."""

    def close(self) -> None:
        """Let go of it. Asked by whoever opened it, once, and never mid-answer."""


def release(held: object) -> None:
    """Let go of whatever ``held`` has open, where it has anything at all."""
    if isinstance(held, Closable):
        held.close()


@dataclass(frozen=True, slots=True)
class Prompt:
    """A system instruction and a human turn, each with ``{name}`` holes in it."""

    system: str
    human: str

    def format_messages(self, **values: Any) -> list[Message]:
        """The two turns, filled in."""
        return [
            {"role": "system", "content": self.system.format(**values)},
            {"role": "user", "content": self.human.format(**values)},
        ]


@dataclass(frozen=True, slots=True)
class Chain(Generic[SchemaT]):
    """A prompt, the model that answers it, and the schema the answer is read as."""

    prompt: Prompt
    model: ChatModel
    schema: type[SchemaT]

    def invoke(self, payload: Mapping[str, Any]) -> SchemaT:
        """Ask this chain's model this chain's question."""
        return self.model.answer(self.prompt.format_messages(**payload), self.schema)


def read_answer(content: str, schema: type[SchemaT]) -> SchemaT:
    """Read a server's answer back as ``schema``, or say it could not be."""
    try:
        return schema.model_validate_json(content)
    except ValidationError as exc:
        said = content.strip() or "nothing at all"
        raise UnreadableAnswerError(f"Invalid JSON answer: {said[:_QUOTED]}") from exc
