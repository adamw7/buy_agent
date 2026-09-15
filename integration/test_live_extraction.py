"""The two chains against a real model: does Ollama's schema decoding still hold."""

from __future__ import annotations

from typing import TYPE_CHECKING

from buy_agent.agent import BuyAgent
from buy_agent.models import ExtractedProduct, ProductList, SearchQuery

from integration.conftest import REQUEST

if TYPE_CHECKING:
    from buy_agent.config import AgentConfig

    from integration.conftest import LiveRun

#: Longer than this is not a search query, it is the model explaining itself.
_MAX_QUERY_LENGTH = 200


def test_query_refinement_answers_with_a_query_and_not_with_prose(
    live_config: AgentConfig,
) -> None:
    """One line of search terms is what ``search_web`` is handed verbatim."""
    answer = BuyAgent(live_config).query_chain.invoke({"request": REQUEST})

    assert isinstance(answer, SearchQuery)
    query = answer.query.strip()
    assert query, "an empty query would silently fall back to the raw request"
    assert "\n" not in query
    assert len(query) <= _MAX_QUERY_LENGTH, query


def test_extraction_answers_with_the_schema_it_was_given(live_run: LiveRun) -> None:
    """Reads the answer the shared run already paid for."""
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
    """ADR-0004's other half: ``-1`` and ``""`` mean unknown, and the domain model is
    where that becomes ``None``."""
    for item in live_run.extracted.products:
        product = item.to_product()

        assert (product.price is None) == (item.price <= 0)
        assert (product.rating is None) == (not 0 <= item.rating <= 5)
        assert (product.review_count is None) == (item.review_count <= 0)
