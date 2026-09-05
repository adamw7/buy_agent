"""The two chains against a real model: does Ollama's schema decoding still hold.

The unit suite fakes the model server outright, so the whole premise
of ADR-0004 -- that a JSON schema compiled into a decoding grammar makes a small
model structurally unable to answer with prose, a half-closed object or ``"N/A"``
in a number -- is checked nowhere in it. It is checked here, and only here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from buy_agent.agent import BuyAgent
from buy_agent.models import ExtractedProduct, ProductList, SearchQuery

from integration.conftest import REQUEST

if TYPE_CHECKING:
    from buy_agent.config import AgentConfig

    from integration.conftest import LiveRun

#: Longer than this is not a search query, it is the model explaining itself.
#: Generous on purpose: what would fail is prose, not a wordy query.
_MAX_QUERY_LENGTH = 200


def test_query_refinement_answers_with_a_query_and_not_with_prose(
    live_config: AgentConfig,
) -> None:
    """One line of search terms is what ``search_web`` is handed verbatim.

    The schema cannot enforce that on its own -- ``SearchQuery.query`` is a
    string, and a paragraph is a string -- so this is the one place the prompt's
    "answer with the query only" is put to a model that could ignore it.

    The chain is taken off a real ``BuyAgent`` rather than built here out of a
    hand-rolled client. Constructing one by hand meant copying all five
    of ``BuyAgent.__init__``'s arguments, and a sixth added there would have
    left this quietly testing a configuration the agent no longer ships --
    the same rule ``scripts/start.ps1`` follows by reading its defaults out of
    ``buy_agent.config`` instead of writing them down twice.
    """
    answer = BuyAgent(live_config).query_chain.invoke({"request": REQUEST})

    assert isinstance(answer, SearchQuery)
    query = answer.query.strip()
    assert query, "an empty query would silently fall back to the raw request"
    assert "\n" not in query
    assert len(query) <= _MAX_QUERY_LENGTH, query


def test_extraction_answers_with_the_schema_it_was_given(live_run: LiveRun) -> None:
    """Reads the answer the shared run already paid for.

    What is being checked is the decoding contract, not the content: every field
    arrives typed, and the sentinels are sentinels rather than nulls -- which is
    what ``ExtractedProduct.to_product`` converts and would crash on if Ollama
    ever started emitting ``None`` for an unknown price.
    """
    extracted = live_run.extracted

    assert isinstance(extracted, ProductList)
    for item in extracted.products:
        assert isinstance(item, ExtractedProduct)
        assert isinstance(item.name, str)
        assert isinstance(item.price, float)
        assert isinstance(item.rating, float)
        assert isinstance(item.review_count, int)
        assert all(isinstance(opinion, str) for opinion in item.opinions)


def test_a_sentinel_survives_the_round_trip_into_a_none(live_run: LiveRun) -> None:
    """ADR-0004's other half: ``-1`` and ``""`` mean unknown, and the domain model
    is where that becomes ``None``. Asserted over whatever the model happened to
    leave blank, since a run in which it filled everything in proves nothing and
    must not fail."""
    for item in live_run.extracted.products:
        product = item.to_product()

        assert (product.price is None) == (item.price <= 0)
        assert (product.rating is None) == (not 0 <= item.rating <= 5)
        assert (product.review_count is None) == (item.review_count <= 0)
