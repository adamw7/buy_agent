"""End-to-end agent behaviour with a fake model and a fake search backend."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
import httpx
from ollama import RequestError, ResponseError

from buy_agent.agent import BuyAgent, OllamaUnavailableError
from buy_agent.config import AgentConfig
from buy_agent.models import ExtractedProduct, ProductList, SearchQuery
from buy_agent.search import SearchError, SearchResult

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


def test_the_page_budget_and_timeout_are_the_config_s_own(
    agent_factory, search_results, extracted_products
) -> None:
    """Deliberately not the defaults: asserting those would pin nothing.

    ``page_chars`` and ``fetch_timeout`` exist to be changed, so the test has to
    show the config's value arriving rather than a literal that happens to match.
    """
    agent, calls = agent_factory(
        FakeLLM(products=extracted_products),
        search_results,
        page_chars=777,
        fetch_timeout=2.5,
    )

    agent.run("headphones")

    assert calls[1] == {"enriched": 3, "max_chars": 777, "timeout": 2.5}


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


@pytest.fixture
def installed_models(monkeypatch):
    """Stand in for ollama.Client, so listing models never opens a socket."""

    def install(models: list[str] | None, *, error: Exception | None = None) -> None:
        class FakeClient:
            def __init__(self, base_url: str) -> None:
                if error is not None:
                    raise error

            def list(self):
                return SimpleNamespace(
                    models=[SimpleNamespace(model=name) for name in models or []]
                )

        monkeypatch.setattr("ollama.Client", FakeClient)

    return install


def test_the_missing_model_error_names_what_is_installed(
    agent_factory, search_results, installed_models
) -> None:
    """Half of these failures are a typo in the tag, so show the real ones."""
    installed_models(["lfm2.5:latest", "qwen3:8b"])
    llm = FakeLLM(raises=ResponseError("model 'llama3.2' not found", 404))
    agent, _ = agent_factory(llm, search_results, model="llama3.2")

    with pytest.raises(OllamaUnavailableError, match="lfm2.5:latest, qwen3:8b"):
        agent.run("headphones")


def test_an_ollama_with_nothing_pulled_reports_none(
    agent_factory, search_results, installed_models
) -> None:
    installed_models([])
    llm = FakeLLM(raises=ResponseError("model 'llama3.2' not found", 404))
    agent, _ = agent_factory(llm, search_results, model="llama3.2")

    with pytest.raises(OllamaUnavailableError, match="installed: none"):
        agent.run("headphones")


def test_an_unlistable_server_still_gives_the_pull_command(
    agent_factory, search_results, installed_models
) -> None:
    """Whatever else is broken, 'ollama pull' is still the thing to try."""
    installed_models(None, error=ConnectionError("refused"))
    llm = FakeLLM(raises=ResponseError("model 'llama3.2' not found", 404))
    agent, _ = agent_factory(llm, search_results, model="llama3.2")

    with pytest.raises(OllamaUnavailableError, match="installed: unknown"):
        agent.run("headphones")


@pytest.mark.parametrize(
    "error",
    [
        RequestError("malformed request"),
        ConnectionError("connection refused"),
        OSError("socket died"),
        # What the real client actually raises. ``ChatOllama`` chats over the
        # ollama client's *streaming* path, and that path -- unlike the plain one
        # -- does not convert httpx's errors into builtins, so these arrive raw.
        httpx.ConnectError("[Errno 111] Connection refused"),
        httpx.RemoteProtocolError("the server closed the stream"),
        httpx.ProxyError("no route to the proxy"),
    ],
)
def test_transport_failures_all_become_one_actionable_error(
    agent_factory, search_results, error
) -> None:
    agent, _ = agent_factory(FakeLLM(raises=error), search_results)

    with pytest.raises(OllamaUnavailableError, match="ollama serve"):
        agent.run("headphones")


@pytest.mark.parametrize(
    "error", [httpx.ReadTimeout("timed out"), httpx.ConnectTimeout("timed out")]
)
def test_a_model_too_slow_to_answer_says_so_rather_than_ollama_serve(
    agent_factory, search_results, error
) -> None:
    """A timeout is a running server, so telling the user to start one misleads."""
    agent, _ = agent_factory(FakeLLM(raises=error), search_results, model="qwen3.5:9b")

    with pytest.raises(OllamaUnavailableError, match="did not answer in time") as caught:
        agent.run("headphones")

    assert "ollama serve" not in str(caught.value)
    assert "qwen3.5:9b" in str(caught.value)


def test_a_missing_ollama_is_not_papered_over_by_the_query_fallback(
    agent_factory, search_results
) -> None:
    """Refinement is recoverable; searching with a model that is not there is not."""
    agent, calls = agent_factory(FakeLLM(raises=ConnectionError("refused")), search_results)

    with pytest.raises(OllamaUnavailableError):
        agent.run("headphones")

    assert calls == [], "the search must not run without a working model"


def test_the_request_is_stripped_before_it_reaches_the_model(
    agent_factory, search_results, extracted_products
) -> None:
    llm = FakeLLM(products=extracted_products)
    agent, _ = agent_factory(llm, search_results)

    agent.run("  wireless earbuds \n")

    assert llm.calls[0].to_messages()[-1].content.endswith("wireless earbuds")


def test_the_extraction_prompt_gets_the_results_and_the_limit(
    agent_factory, search_results, extracted_products
) -> None:
    llm = FakeLLM(products=extracted_products)
    agent, _ = agent_factory(llm, search_results, num_products=4)

    agent.run("headphones")

    system, human = llm.calls[1].to_messages()
    assert "at most 4 distinct products" in system.content
    assert "https://example.com/sony" in human.content


def test_sort_by_reaches_the_ranking(
    agent_factory, search_results, extracted_products
) -> None:
    agent, _ = agent_factory(FakeLLM(products=extracted_products), search_results)

    ranked = agent.run("headphones", sort_by="price")

    assert [entry.product.price for entry in ranked] == [79.0, 328.0, None]


def test_the_number_of_products_kept_is_capped(
    agent_factory, search_results, extracted_products
) -> None:
    agent, _ = agent_factory(FakeLLM(products=extracted_products), search_results, num_products=1)

    assert len(agent.run("headphones")) == 1


def _one_page(content: str) -> tuple[list, list]:
    """A single result, and the same result once its page has been read."""
    found = [
        SearchResult(
            title="JBL Live 780NC deal",
            url="https://shop.example/jbl",
            snippet="Great headphones.",
        )
    ]
    return found, [found[0].model_copy(update={"content": content})]


def test_a_figure_only_on_the_fetched_page_is_still_grounded(monkeypatch) -> None:
    """Extraction and verification must be handed the same text, or nothing passes."""
    found, fetched = _one_page("JBL Live 780NC\n$149.00\nRated 4.4 out of 5")
    monkeypatch.setattr("buy_agent.agent.search_web", lambda *_a, **_k: found)
    monkeypatch.setattr("buy_agent.agent.enrich", lambda _results, **_k: fetched)
    llm = FakeLLM(
        products=ProductList(
            products=[
                ExtractedProduct(name="JBL Live 780NC", price=149.0, currency="USD", rating=4.4)
            ]
        )
    )

    ranked = BuyAgent(AgentConfig(), llm=llm).run("headphones")

    assert ranked[0].product.price == 149.0
    assert ranked[0].product.rating == 4.4


def test_without_the_page_the_same_figures_are_unsupported(monkeypatch) -> None:
    """Snippets alone back nothing, so with --no-fetch the figures are blanked."""
    found, _ = _one_page("JBL Live 780NC\n$149.00\nRated 4.4 out of 5")
    monkeypatch.setattr("buy_agent.agent.search_web", lambda *_a, **_k: found)
    llm = FakeLLM(
        products=ProductList(
            products=[
                ExtractedProduct(name="JBL Live 780NC", price=149.0, currency="USD", rating=4.4)
            ]
        )
    )

    ranked = BuyAgent(AgentConfig(fetch_pages=False), llm=llm).run("headphones")

    assert ranked[0].product.name == "JBL Live 780NC"
    assert ranked[0].product.price is None
    assert ranked[0].product.rating is None


def test_the_report_is_logged_even_when_the_top_n_exceeds_what_was_found(
    agent_factory, search_results, extracted_products, caplog
) -> None:
    agent, _ = agent_factory(FakeLLM(products=extracted_products), search_results, top_n=10)

    with caplog.at_level(logging.INFO, logger="buy_agent"):
        agent.run("headphones")

    assert "TOP 3 OF 3 PRODUCTS" in caplog.text


def test_a_name_wearing_a_headline_is_cleaned_before_it_is_grounded(monkeypatch) -> None:
    """Grounding must not fail a real product for words the page never had to contain."""
    found = [
        SearchResult(
            title="Sennheiser HD 450BT",
            url="https://shop.example/sennheiser",
            snippet="$129 today.",
        )
    ]
    monkeypatch.setattr("buy_agent.agent.search_web", lambda *_a, **_k: found)
    llm = FakeLLM(
        products=ProductList(
            products=[
                ExtractedProduct(
                    name="Sennheiser HD 450BT Review | Gadget Site Weekly", price=129.0
                )
            ]
        )
    )

    ranked = BuyAgent(AgentConfig(fetch_pages=False), llm=llm).run("headphones")

    assert [entry.product.name for entry in ranked] == ["Sennheiser HD 450BT"]
    assert ranked[0].product.price == 129.0


def test_listings_are_grounded_before_they_are_merged(monkeypatch) -> None:
    """Merging picks the fuller listing, so invented figures must be gone by then."""
    found = [
        SearchResult(
            title="Sony WH-CH720N deal",
            url="https://real.example",
            snippet="Now $129 at the shop.",
        )
    ]
    monkeypatch.setattr("buy_agent.agent.search_web", lambda *_a, **_k: found)
    llm = FakeLLM(
        products=ProductList(
            products=[
                ExtractedProduct(name="Sony WH-CH720N", price=129.0, url="https://real.example"),
                ExtractedProduct(
                    name="Sony WH-CH720N Wireless",
                    price=99.0,
                    rating=4.9,
                    review_count=5,
                    url="https://invented.example",
                ),
            ]
        )
    )

    ranked = BuyAgent(AgentConfig(fetch_pages=False), llm=llm).run("headphones")

    assert len(ranked) == 1
    product = ranked[0].product
    assert product.price == 129.0, "the supported price must survive the merge"
    assert product.url == "https://real.example", "invented figures must not win completeness"
    assert product.rating is None
    assert product.review_count is None
