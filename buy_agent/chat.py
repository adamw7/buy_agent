"""The seam between a prompt and a model server's answer: what LangChain used to be.

Two calls are made of a model, and both want the same three things: a prompt with
the run's values in it, a schema decoding is constrained to, and the answer parsed
back into that schema. That is the whole of it -- no tools, no memory, no agent
loop, the pipeline being fixed by ADR-0002 -- so it is written here rather than
taken off a framework that brings a tracing client and fifteen other packages to
carry it (ADR-0038).

A :class:`Message` is a ``role``/``content`` mapping, which is what both servers
take over the wire; :class:`Prompt` fills one pair in, and :class:`Chain` is a
prompt bound to the model that answers it and the schema it is read back as. What
differs between the servers is :mod:`buy_agent.providers`', one row each.
"""

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

#: The Pydantic model one call is constrained to and read back as -- ``SearchQuery``
#: for the refining step, ``ProductList`` for the extracting one (ADR-0004).
SchemaT = TypeVar("SchemaT", bound=BaseModel)

#: How much of an unreadable answer the failure carries: enough to recognise a
#: half-finished object by, short enough for a one-line hint. The whole of it is
#: logged at DEBUG where it was caught.
_QUOTED = 200


class UnreadableAnswerError(ValueError):
    """The server answered, with something that is not the JSON it was asked for.

    A ``ValueError`` because that is what an answer nobody can use has always been
    here: uncaught it lands in the three failures ``BuyAgent.run`` documents
    (ADR-0009) rather than a fourth. It is not meant to reach a shopper that way --
    ``BuyAgent._extract_products`` turns it into a ``ModelUnavailableError`` carrying
    the provider's hint, the usual cause being room (ADR-0019).
    """


class ChatModel(Protocol):
    """What the pipeline needs of a model server, and nothing else.

    One method, so a stand-in is a class with one method: ``tests/conftest.py``,
    ``benchmark/scripted.py`` and ``demo/server.py`` each have one, and
    ``BuyAgent(config, llm=...)`` is where they go in.
    """

    def answer(self, messages: Sequence[Message], schema: type[SchemaT]) -> SchemaT:
        """Answer ``messages`` with an instance of ``schema``, and nothing else.

        Raises:
            UnreadableAnswerError: if what came back cannot be read as one.
        """


@runtime_checkable
class Closable(Protocol):
    """Something holding a connection open that can be told to let go of it.

    Deliberately *not* a second method on :class:`ChatModel`: a stand-in there is a
    class with one method and stays one, the three in the tests, the benchmark and the
    demo holding nothing open. What does hold something open is a provider's client.
    """

    def close(self) -> None:
        """Let go of it. Asked by whoever opened it, once, and never mid-answer."""


def release(held: object) -> None:
    """Let go of whatever ``held`` has open, where it has anything at all.

    Asked rather than required, which is what lets a caller point it at an injected
    model or an agent-shaped stand-in: neither has a ``close``, so neither is touched.
    ``None`` is a thing with nothing open.
    """
    if isinstance(held, Closable):
        held.close()


@dataclass(frozen=True, slots=True)
class Prompt:
    """A system instruction and a human turn, each with ``{name}`` holes in it.

    ``str.format`` and not a template engine: the holes are the payload's own keys, a
    substituted value is never scanned for holes of its own -- which matters, ten
    fetched pages going into ``{results}`` -- and a template naming a key the payload
    does not carry fails here rather than reaching the model with braces in it.
    """

    system: str
    human: str

    def format_messages(self, **values: Any) -> list[Message]:
        """The two turns, filled in. Extra values are ignored: one payload fills
        both templates, and neither has to name everything the other does."""
        return [
            {"role": "system", "content": self.system.format(**values)},
            {"role": "user", "content": self.human.format(**values)},
        ]


@dataclass(frozen=True, slots=True)
class Chain(Generic[SchemaT]):
    """A prompt, the model that answers it, and the schema the answer is read as.

    Built once per agent and invoked with the run's values, so the schema a call is
    constrained to is fixed where the prompt is: a prompt asking for products against
    a schema describing a query is not a mistake worth leaving reachable.
    """

    prompt: Prompt
    model: ChatModel
    schema: type[SchemaT]

    def invoke(self, payload: Mapping[str, Any]) -> SchemaT:
        """Ask this chain's model this chain's question."""
        return self.model.answer(self.prompt.format_messages(**payload), self.schema)


def read_answer(content: str, schema: type[SchemaT]) -> SchemaT:
    """Read a server's answer back as ``schema``, or say it could not be.

    The one place either provider turns text into a schema, so "the model answered
    with prose" is one failure with one wording. What the failure carries is the
    answer itself, truncated: a run out of context ends part-way through an object and
    the useful part is the start.

    Raises:
        UnreadableAnswerError: if it is not JSON, or not this schema's.
    """
    try:
        return schema.model_validate_json(content)
    except ValidationError as exc:
        said = content.strip() or "nothing at all"
        raise UnreadableAnswerError(f"Invalid JSON answer: {said[:_QUOTED]}") from exc
