"""The seam the model servers are reached through, and the two ways it can fail."""

from __future__ import annotations

import pytest

from buy_agent.chat import (
    Prompt,
)
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


def test_a_prompt_fills_both_turns_in() -> None:
    system, human = PROMPT.format_messages(request="a tent", limit=7)

    assert system == {"role": "system", "content": "Extract at most 7 products."}
    assert human == {"role": "user", "content": "Wanted: a tent"}


def test_a_hole_the_payload_cannot_fill_is_a_failure_here() -> None:
    """Rather than a prompt reaching the model with the braces still in it."""
    with pytest.raises(KeyError):
        PROMPT.format_messages(request="a tent")


# -- letting go of what a model holds open -------------------------------------
