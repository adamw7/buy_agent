"""The seam the model servers are reached through, and the two ways it can fail.

Nothing here builds a provider. What is being checked is the machinery that was
LangChain's until ADR-0038: a prompt with the run's values in it, a chain that
binds one to a schema, and an answer read back as that schema or refused.
"""

from __future__ import annotations

import pytest

from buy_agent.chat import Chain, Prompt, UnreadableAnswerError, read_answer
from buy_agent.models import ProductList, SearchQuery

PROMPT = Prompt(system="Extract at most {limit} products.", human="Wanted: {request}")


class Recording:
    """A model that answers one canned object and keeps what it was asked."""

    def __init__(self, answered: SearchQuery | None = None) -> None:
        self.answered = answered or SearchQuery(query="a refined query")
        self.messages: list = []
        self.schema: type | None = None

    def answer(self, messages, schema):
        self.messages = list(messages)
        self.schema = schema
        return self.answered


def test_a_prompt_fills_both_turns_in() -> None:
    system, human = PROMPT.format_messages(request="a tent", limit=7)

    assert system == {"role": "system", "content": "Extract at most 7 products."}
    assert human == {"role": "user", "content": "Wanted: a tent"}


def test_a_value_neither_turn_names_is_ignored() -> None:
    """One payload fills both templates, and neither names everything the other does."""
    system, _ = PROMPT.format_messages(request="a tent", limit=7, results="[1] ...")

    assert system["content"] == "Extract at most 7 products."


def test_a_hole_the_payload_cannot_fill_is_a_failure_here() -> None:
    """Rather than a prompt reaching the model with the braces still in it."""
    with pytest.raises(KeyError):
        PROMPT.format_messages(request="a tent")


def test_what_is_substituted_in_is_never_scanned_for_holes_of_its_own() -> None:
    """Ten fetched pages go into a hole, and a page is free to contain braces.

    A shop that prints ``{price}`` in a template it failed to render would
    otherwise take the run down with a ``KeyError`` between the search and the
    ranking, for a page nobody chose.
    """
    _, human = PROMPT.format_messages(request="{price} not {a real hole}", limit=1)

    assert human["content"] == "Wanted: {price} not {a real hole}"


def test_a_chain_asks_its_model_its_own_question() -> None:
    llm = Recording()

    answer = Chain(PROMPT, llm, SearchQuery).invoke({"request": "a tent", "limit": 3})

    assert answer.query == "a refined query"
    assert llm.schema is SearchQuery
    assert llm.messages[1]["content"] == "Wanted: a tent"


def test_an_answer_is_read_back_as_the_schema_it_was_asked_for() -> None:
    answer = read_answer('{"products": []}', ProductList)

    assert isinstance(answer, ProductList)
    assert answer.products == []


@pytest.mark.parametrize(
    ("said", "quoted"),
    [
        ("The best headphones are the Sony WH-1000XM5.", "The best headphones"),
        ('{"products": [{"name"', '{"products": [{"name"'),
        ('{"query": 4}', '{"query": 4}'),
        ("   ", "nothing at all"),
    ],
)
def test_an_answer_that_is_not_the_schema_carries_what_was_said(
    said: str, quoted: str
) -> None:
    """Prose, a half-finished object, the wrong shape, and a dropped stream. The
    answer itself is the symptom, which is what the provider's hint is written
    around -- an empty one saying nothing, so it is named instead."""
    with pytest.raises(UnreadableAnswerError) as caught:
        read_answer(said, SearchQuery)

    assert quoted in str(caught.value)


def test_a_long_answer_is_quoted_short_enough_to_write_a_sentence_around() -> None:
    with pytest.raises(UnreadableAnswerError) as caught:
        read_answer("no." * 500, SearchQuery)

    assert len(str(caught.value)) < 260


def test_an_unreadable_answer_is_a_value_error() -> None:
    """Uncaught it lands in the three failures ``run`` documents, not a fourth."""
    assert issubclass(UnreadableAnswerError, ValueError)
