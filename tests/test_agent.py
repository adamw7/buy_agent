"""End-to-end agent behaviour with a fake model and a fake search backend."""

from __future__ import annotations

import logging

import pytest
from ollama import ResponseError

from buy_agent.agent import BuyAgent, OllamaUnavailableError
from buy_agent.config import AgentConfig
from buy_agent.models import ProductList, SearchQuery
from buy_agent.search import SearchError

from tests.conftest import FakeLLM


@pytest.fixture
def agent_factory(monkeypatch):
    """Build an agent whose search backend is a recorded fake."""

    def build(llm: FakeLLM, results: list, **config_kwargs) -> tuple[BuyAgent, list]:
        calls: list[dict] = []

        def fake_search(query: str, *, max_results: int = 10, region: str = "us-en") -> list:
            calls.append({"query": query, "max_results": max_results, "region": region})
            return results

        def fake_enrich(found: list, **kwargs) -> list:
            calls.append({"enriched": len(found), **kwargs})
            return found

        monkeypatch.setattr("buy_agent.agent.search_web", fake_search)
        monkeypatch.setattr("buy_agent.agent.enrich", fake_enrich)
        return BuyAgent(AgentConfig(**config_kwargs), llm=llm), calls

    return build


def test_run_returns_products_ranked_best_first(
    agent_factory, search_results, extracted_products
) -> None:
    llm = FakeLLM(products=extracted_products)
    agent, _ = agent_factory(llm, search_results)

    ranked = agent.run("noise cancelling headphones")

    assert [entry.rank for entry in ranked] == [1, 2, 3]
    assert ranked[0].score >= ranked[-1].score
    assert ranked[0].product.name == "Anker Soundcore Q30"


def test_run_logs_exactly_the_top_n(
    agent_factory, search_results, extracted_products, caplog
) -> None:
    agent, _ = agent_factory(FakeLLM(products=extracted_products), search_results, top_n=2)

    with caplog.at_level(logging.INFO, logger="buy_agent"):
        agent.run("headphones")

    report = caplog.text
    assert "TOP 2 OF 3 PRODUCTS" in report
    assert "Anker Soundcore Q30" in report
    assert "Unknown Brand Buds" not in report


def test_report_includes_price_rating_and_url(
    agent_factory, search_results, extracted_products, caplog
) -> None:
    agent, _ = agent_factory(FakeLLM(products=extracted_products), search_results, top_n=1)

    with caplog.at_level(logging.INFO, logger="buy_agent"):
        agent.run("headphones")

    assert "79.00 USD" in caplog.text
    assert "4.3/5 (90,000 reviews)" in caplog.text
    assert "https://example.com/anker" in caplog.text


def test_the_refined_query_is_what_gets_searched(
    agent_factory, search_results, extracted_products
) -> None:
    llm = FakeLLM(query=SearchQuery(query="cheap ANC headphones price"), products=extracted_products)
    agent, calls = agent_factory(llm, search_results, search_results=7, region="uk-en")

    agent.run("something to listen to music with")

    assert calls[0] == {
        "query": "cheap ANC headphones price",
        "max_results": 7,
        "region": "uk-en",
    }


def test_search_falls_back_to_the_raw_request_when_refinement_fails(
    agent_factory, search_results, extracted_products, monkeypatch
) -> None:
    """A broken query step must not sink the run -- the raw request is searchable."""
    llm = FakeLLM(products=extracted_products)
    agent, calls = agent_factory(llm, search_results)
    monkeypatch.setattr(
        agent, "query_chain", _failing_chain(ValueError("model returned garbage"))
    )

    ranked = agent.run("wireless earbuds")

    assert calls[0]["query"] == "wireless earbuds"
    assert ranked


def test_blank_refined_query_falls_back_to_the_raw_request(
    agent_factory, search_results, extracted_products
) -> None:
    llm = FakeLLM(query=SearchQuery(query="   "), products=extracted_products)
    agent, calls = agent_factory(llm, search_results)

    agent.run("wireless earbuds")

    assert calls[0]["query"] == "wireless earbuds"


def test_duplicate_listings_are_merged_before_ranking(
    agent_factory, search_results, extracted_products
) -> None:
    doubled = ProductList(products=extracted_products.products + extracted_products.products)
    agent, _ = agent_factory(FakeLLM(products=doubled), search_results)

    assert len(agent.run("headphones")) == 3


def test_no_search_results_yields_no_products(
    agent_factory, extracted_products, caplog
) -> None:
    agent, _ = agent_factory(FakeLLM(products=extracted_products), [])

    with caplog.at_level(logging.WARNING):
        assert agent.run("obscure thing") == []
    assert "Search returned nothing" in caplog.text


def test_no_extractable_products_yields_no_products(
    agent_factory, search_results, caplog
) -> None:
    agent, _ = agent_factory(FakeLLM(products=ProductList()), search_results)

    with caplog.at_level(logging.WARNING):
        assert agent.run("obscure thing") == []
    assert "No products could be extracted" in caplog.text


def test_empty_request_is_rejected(agent_factory, search_results) -> None:
    agent, _ = agent_factory(FakeLLM(), search_results)

    with pytest.raises(ValueError, match="empty"):
        agent.run("   ")


def test_missing_model_produces_an_actionable_error(
    agent_factory, search_results, monkeypatch
) -> None:
    llm = FakeLLM(raises=ResponseError("model 'llama3.2' not found", 404))
    agent, _ = agent_factory(llm, search_results, model="llama3.2")
    monkeypatch.setattr(BuyAgent, "_installed_models", lambda self: "lfm2.5:latest")

    with pytest.raises(OllamaUnavailableError, match="ollama pull llama3.2"):
        agent.run("headphones")


def test_unreachable_server_produces_an_actionable_error(
    agent_factory, search_results
) -> None:
    llm = FakeLLM(raises=ConnectionError("connection refused"))
    agent, _ = agent_factory(llm, search_results, base_url="http://localhost:9999")

    with pytest.raises(OllamaUnavailableError, match="ollama serve"):
        agent.run("headphones")


def test_search_failures_propagate(monkeypatch, extracted_products) -> None:
    def boom(*_args, **_kwargs):
        raise SearchError("rate limited")

    monkeypatch.setattr("buy_agent.agent.search_web", boom)
    agent = BuyAgent(AgentConfig(), llm=FakeLLM(products=extracted_products))

    with pytest.raises(SearchError, match="rate limited"):
        agent.run("headphones")


def _failing_chain(error: Exception):
    from langchain_core.runnables import RunnableLambda

    def fail(_value):
        raise error

    return RunnableLambda(fail)


def test_article_headlines_never_reach_the_report(
    agent_factory, search_results, extracted_products, caplog
) -> None:
    """The model reports listicles as products; the agent must not pass them on."""
    from buy_agent.models import ExtractedProduct

    noisy = ProductList(
        products=[
            ExtractedProduct(name="12 Best Headphones Under $200 (2026)"),
            ExtractedProduct(name="Best Headphones under $200 - SoundGuys"),
            *extracted_products.products,
        ]
    )
    agent, _ = agent_factory(FakeLLM(products=noisy), search_results, top_n=5)

    with caplog.at_level(logging.INFO, logger="buy_agent"):
        ranked = agent.run("headphones")

    assert len(ranked) == 3
    assert all("Best Headphones" not in entry.product.name for entry in ranked)


def test_figures_absent_from_the_search_results_are_not_ranked_on(
    agent_factory, search_results
) -> None:
    """A price the sources never mention must not win the top spot."""
    from buy_agent.models import ExtractedProduct

    invented = ProductList(
        products=[
            ExtractedProduct(name="Sony WH-1000XM5", price=328.0, rating=4.7),
            ExtractedProduct(name="Unknown Brand Buds", price=1.0, rating=5.0),
        ]
    )
    agent, _ = agent_factory(FakeLLM(products=invented), search_results)

    ranked = agent.run("headphones")
    unsupported = next(
        entry for entry in ranked if entry.product.name == "Unknown Brand Buds"
    )

    assert unsupported.product.price is None
    assert unsupported.product.rating is None
    assert ranked[0].product.name == "Sony WH-1000XM5"


def test_a_product_the_sources_never_mention_is_dropped(
    agent_factory, search_results
) -> None:
    """Small models lift products straight out of the prompt's own example."""
    from buy_agent.models import ExtractedProduct

    leaked = ProductList(
        products=[
            ExtractedProduct(name="Sony WH-1000XM5", price=328.0),
            ExtractedProduct(name="Bonavita Gooseneck Kettle", price=80.0),
        ]
    )
    agent, _ = agent_factory(FakeLLM(products=leaked), search_results)

    ranked = agent.run("headphones")

    assert [entry.product.name for entry in ranked] == ["Sony WH-1000XM5"]


def test_result_pages_are_fetched_by_default(
    agent_factory, search_results, extracted_products
) -> None:
    agent, calls = agent_factory(FakeLLM(products=extracted_products), search_results)

    agent.run("headphones")

    assert calls[1] == {"enriched": 3, "max_chars": 1200, "timeout": 8.0}


def test_fetching_can_be_turned_off(
    agent_factory, search_results, extracted_products
) -> None:
    agent, calls = agent_factory(
        FakeLLM(products=extracted_products), search_results, fetch_pages=False
    )

    agent.run("headphones")

    assert not any("enriched" in call for call in calls)


@pytest.fixture
def recorded_chat_ollama(monkeypatch):
    """Capture the kwargs BuyAgent builds its real ChatOllama with."""
    captured: dict = {}

    class Recorder(FakeLLM):
        def __init__(self, **kwargs) -> None:
            super().__init__()
            captured.update(kwargs)

    monkeypatch.setattr("buy_agent.agent.ChatOllama", Recorder)
    return captured


def test_model_settings_reach_the_chat_model(recorded_chat_ollama) -> None:
    BuyAgent(AgentConfig(model="qwen3.5:9b", temperature=0.2, num_ctx=8192, reasoning=False))

    assert recorded_chat_ollama["model"] == "qwen3.5:9b"
    assert recorded_chat_ollama["temperature"] == 0.2
    assert recorded_chat_ollama["num_ctx"] == 8192
    assert recorded_chat_ollama["reasoning"] is False


def test_thinking_and_context_are_left_alone_by_default(recorded_chat_ollama) -> None:
    """None means "send nothing": a model that cannot think must not be told to."""
    BuyAgent(AgentConfig())

    assert recorded_chat_ollama["num_ctx"] is None
    assert recorded_chat_ollama["reasoning"] is None


def test_an_injected_model_bypasses_chat_ollama(recorded_chat_ollama) -> None:
    llm = FakeLLM()
    agent = BuyAgent(AgentConfig(num_ctx=8192), llm=llm)

    assert agent.llm is llm
    assert recorded_chat_ollama == {}
