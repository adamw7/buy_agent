"""The seam the model servers are reached through, and the two ways it can fail."""

from __future__ import annotations

import json

import pytest

from buy_agent.chat import Chain, Prompt, first_line
from buy_agent.models import SearchQuery

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


def test_a_failure_is_told_by_its_first_line_or_else_by_its_type() -> None:
    """One line for a hint or a warning, and never an empty pair of brackets."""
    assert first_line(ValueError("the first\nthe second")) == "the first"
    assert first_line(TimeoutError()) == "TimeoutError"


def test_a_prompt_fills_both_turns_in() -> None:
    system, human = PROMPT.format_messages(request="a tent", limit=7)

    assert system == {"role": "system", "content": "Extract at most 7 products."}
    assert human == {"role": "user", "content": "Wanted: a tent"}


def test_a_hole_the_payload_cannot_fill_is_a_failure_here() -> None:
    """Rather than a prompt reaching the model with the braces still in it."""
    with pytest.raises(KeyError):
        PROMPT.format_messages(request="a tent")


def test_half_a_surrogate_pair_is_sent_as_the_replacement_character() -> None:
    """``json`` reads a page's ``"\\ud83c"`` -- a name its JSON-LD cut mid-emoji -- as
    half a pair, which no client can encode as UTF-8, and every row counts that
    ``UnicodeError`` as a server it could not reach. It goes as the character an
    undecodable byte on a page is already read as."""
    model = Recording()

    Chain(PROMPT, model, SearchQuery).invoke({"request": "cool cans \ud83c", "limit": 7})

    assert model.messages[1] == {"role": "user", "content": "Wanted: cool cans \ufffd"}
    # As the clients send a prompt: a JSON body, in UTF-8.
    assert json.dumps(model.messages, ensure_ascii=False).encode("utf-8")


# -- letting go of what a model holds open -------------------------------------
