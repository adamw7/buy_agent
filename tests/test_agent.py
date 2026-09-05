"""End-to-end agent behaviour with a fake model and a fake search backend."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
import httpx
from ollama import RequestError, ResponseError

from buy_agent.agent import BuyAgent, ModelUnavailableError
from buy_agent.chat import UnreadableAnswerError
from buy_agent.config import AgentConfig
from buy_agent.models import ExtractedProduct, ProductList, SearchQuery
from buy_agent.ranking import RankingWeights
from buy_agent.search import SearchError, SearchResult
from buy_agent.sources import parse_sources

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


def test_every_named_source_is_searched_in_the_shoppers_own_region(
    agent_factory, search_results, extracted_products
) -> None:
    """The other search call, which the test above cannot reach.

    Naming sources takes a different branch -- one search per source rather than
    one for the web -- and a region dropped there sends a shopper in Poland the
    American edition of every site they asked for, with nothing in the report to
    say the setting was ignored.
    """
    llm = FakeLLM(query=SearchQuery(query="headphones"), products=extracted_products)
    agent, calls = agent_factory(
        llm, search_results, region="uk-en", sources=parse_sources("example.com rtings.com")
    )

    agent.run("headphones")

    searches = [call for call in calls if "region" in call]
    assert len(searches) == 2, "one per named source"
    assert {call["region"] for call in searches} == {"uk-en"}


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


def test_a_search_that_found_nothing_names_a_region_worth_suspecting(
    agent_factory, extracted_products, caplog
) -> None:
    """``en-us`` is the right shape the wrong way round, so it survives both front
    doors and comes back empty. The warning is the only place left to say so."""
    agent, _ = agent_factory(FakeLLM(products=extracted_products), [], region="en-us")

    with caplog.at_level(logging.WARNING):
        assert agent.run("obscure thing") == []

    assert "region en-us" in caplog.text
    assert "us-en" in caplog.text, "the shape is no use without a code that has it"


def test_the_default_region_is_not_blamed_for_an_empty_search(
    agent_factory, extracted_products, caplog
) -> None:
    """It is the one value known to work; naming it would send a shopper off to
    correct a setting that is correct."""
    agent, _ = agent_factory(FakeLLM(products=extracted_products), [])

    with caplog.at_level(logging.WARNING):
        agent.run("obscure thing")

    assert "region" not in caplog.text


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
    agent_factory, search_results, installed_models
) -> None:
    installed_models(["lfm2.5:latest"])
    llm = FakeLLM(raises=ResponseError("model 'llama3.2' not found", 404))
    agent, _ = agent_factory(llm, search_results, model="llama3.2")

    with pytest.raises(ModelUnavailableError, match="ollama pull llama3.2"):
        agent.run("headphones")


def test_unreachable_server_produces_an_actionable_error(
    agent_factory, search_results
) -> None:
    llm = FakeLLM(raises=ConnectionError("connection refused"))
    agent, _ = agent_factory(llm, search_results, base_url="http://localhost:9999")

    with pytest.raises(ModelUnavailableError, match="ollama serve"):
        agent.run("headphones")


def test_an_unreadable_extraction_is_the_model_failing_not_the_request(
    agent_factory, search_results, monkeypatch
) -> None:
    """A half-finished answer is a ``ValueError``, and it is not the shopper's.

    ``UnreadableAnswerError`` subclasses ``ValueError``, which ``run`` documents
    as "the request is empty" and ``api._STATUS`` answers 400 to -- so a model
    that ran out of room mid-JSON would tell the shopper their request was bad.
    It is the model that could not be used (ADR-0009), and the sentence says what
    to do about it.
    """
    agent, _ = agent_factory(FakeLLM(), search_results)
    monkeypatch.setattr(
        agent,
        "extraction_chain",
        _failing_chain(UnreadableAnswerError('Invalid JSON answer: {"products": [{"name"')),
    )

    with pytest.raises(ModelUnavailableError, match="not the JSON this asks for") as caught:
        agent.run("headphones")

    assert "--num-ctx" in str(caught.value), "the remedy Ollama has for too little room"
    assert isinstance(caught.value.__cause__, UnreadableAnswerError)


def test_an_unreadable_refinement_still_falls_back_to_the_raw_request(
    agent_factory, search_results, extracted_products, monkeypatch
) -> None:
    """Only extraction has nothing to fall back to, so only it fails the run."""
    llm = FakeLLM(products=extracted_products)
    agent, calls = agent_factory(llm, search_results)
    monkeypatch.setattr(
        agent, "query_chain", _failing_chain(UnreadableAnswerError("Invalid JSON answer: {"))
    )

    ranked = agent.run("wireless earbuds")

    assert calls[0]["query"] == "wireless earbuds"
    assert ranked


def test_search_failures_propagate(monkeypatch, extracted_products) -> None:
    def boom(*_args, **_kwargs):
        raise SearchError("rate limited")

    monkeypatch.setattr("buy_agent.agent.search_web", boom)
    agent = BuyAgent(AgentConfig(), llm=FakeLLM(products=extracted_products))

    with pytest.raises(SearchError, match="rate limited"):
        agent.run("headphones")


def _failing_chain(error: Exception):
    """A stand-in chain that raises instead of answering.

    ``invoke`` is the whole of one's surface, so this is a class with one method
    -- the same shape ``FakeLLM`` is for a model server.
    """

    class Failing:
        @staticmethod
        def invoke(_payload):
            raise error

    return Failing()


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

    assert calls[1] == {
        "enriched": 3,
        "max_chars": 1200,
        "opinion_chars": 400,
        "timeout": 8.0,
    }


def test_the_page_budget_and_timeout_are_the_config_s_own(
    agent_factory, search_results, extracted_products
) -> None:
    """Deliberately not the defaults: asserting those would pin nothing.

    ``page_chars``, ``opinion_chars`` and ``fetch_timeout`` exist to be changed, so
    the test has to show the config's value arriving rather than a literal that
    happens to match.
    """
    agent, calls = agent_factory(
        FakeLLM(products=extracted_products),
        search_results,
        page_chars=777,
        opinion_chars=99,
        fetch_timeout=2.5,
    )

    agent.run("headphones")

    assert calls[1] == {
        "enriched": 3,
        "max_chars": 777,
        "opinion_chars": 99,
        "timeout": 2.5,
    }


def test_fetching_can_be_turned_off(
    agent_factory, search_results, extracted_products
) -> None:
    agent, calls = agent_factory(
        FakeLLM(products=extracted_products), search_results, fetch_pages=False
    )

    agent.run("headphones")

    assert not any("enriched" in call for call in calls)


@pytest.fixture
def ollama_request(monkeypatch):
    """Capture the request the Ollama provider puts a run's settings into.

    There is no wrapper left whose constructor can be read back for them
    (ADR-0038): the window and the thinking switch are request options and the
    address belongs to the client, so what says a setting arrived is the call
    itself. Patched where the provider imported the class, which is the one place
    either client is named.
    """
    sent: dict = {}

    class FakeClient:
        def __init__(self, base_url: str, **kwargs) -> None:
            sent["base_url"] = base_url

        @staticmethod
        def chat(**kwargs):
            sent.update(kwargs)
            return SimpleNamespace(
                message=SimpleNamespace(content='{"query": "a refined query"}')
            )

    monkeypatch.setattr("buy_agent.providers.Client", FakeClient)
    return sent


def _refine(config: AgentConfig) -> None:
    """Put one question to the model the config's provider builds."""
    BuyAgent(config).query_chain.invoke({"request": "headphones"})


def test_model_settings_reach_the_chat_model(ollama_request) -> None:
    _refine(AgentConfig(model="qwen3.5:9b", temperature=0.2, num_ctx=8192, reasoning=False))

    assert ollama_request["model"] == "qwen3.5:9b"
    assert ollama_request["options"] == {"temperature": 0.2, "num_ctx": 8192}
    assert ollama_request["think"] is False


def test_the_ollama_address_reaches_the_chat_model(ollama_request) -> None:
    """$OLLAMA_HOST is only worth having if the model is built with what it set."""
    BuyAgent(AgentConfig(base_url="http://ollama.internal:11434"))

    assert ollama_request["base_url"] == "http://ollama.internal:11434"


def test_the_defaults_reach_the_chat_model(ollama_request) -> None:
    """DEFAULT_MODEL thinks, so the defaults that make it usable have to arrive."""
    _refine(AgentConfig())

    assert ollama_request["options"]["num_ctx"] == 8192
    assert ollama_request["think"] is False


def test_thinking_and_context_can_be_left_alone(ollama_request) -> None:
    """None means "send nothing": a model that cannot think must not be told to."""
    _refine(AgentConfig(num_ctx=None, reasoning=None))

    assert "num_ctx" not in ollama_request["options"]
    assert ollama_request["think"] is None


def test_an_injected_model_bypasses_chat_ollama(ollama_request) -> None:
    llm = FakeLLM()
    agent = BuyAgent(AgentConfig(num_ctx=8192), llm=llm)

    assert agent.llm is llm
    assert ollama_request == {}


@pytest.fixture
def installed_models(monkeypatch):
    """Stand in for ollama's Client, so listing models never opens a socket.

    Both calls it makes: the tags, and what each of them can do. Everything here
    can answer a prompt -- which of them cannot is ``tests/test_providers.py``'s
    question, not this module's.
    """

    def install(models: list[str] | None, *, error: Exception | None = None) -> None:
        class FakeClient:
            def __init__(self, base_url: str, **_kwargs) -> None:
                if error is not None:
                    raise error

            def list(self):
                return SimpleNamespace(
                    models=[SimpleNamespace(model=name) for name in models or []]
                )

            @staticmethod
            def show(_name: str):
                return SimpleNamespace(capabilities=["completion"])

        monkeypatch.setattr("buy_agent.providers.Client", FakeClient)

    return install


def test_the_missing_model_error_names_what_is_installed(
    agent_factory, search_results, installed_models
) -> None:
    """Half of these failures are a typo in the tag, so show the real ones."""
    installed_models(["lfm2.5:latest", "qwen3:8b"])
    llm = FakeLLM(raises=ResponseError("model 'llama3.2' not found", 404))
    agent, _ = agent_factory(llm, search_results, model="llama3.2")

    with pytest.raises(ModelUnavailableError, match="lfm2.5:latest, qwen3:8b"):
        agent.run("headphones")


def test_an_ollama_with_nothing_pulled_reports_none(
    agent_factory, search_results, installed_models
) -> None:
    installed_models([])
    llm = FakeLLM(raises=ResponseError("model 'llama3.2' not found", 404))
    agent, _ = agent_factory(llm, search_results, model="llama3.2")

    with pytest.raises(ModelUnavailableError, match="installed: none"):
        agent.run("headphones")


def test_an_unlistable_server_still_gives_the_pull_command(
    agent_factory, search_results, installed_models
) -> None:
    """Whatever else is broken, 'ollama pull' is still the thing to try."""
    installed_models(None, error=ConnectionError("refused"))
    llm = FakeLLM(raises=ResponseError("model 'llama3.2' not found", 404))
    agent, _ = agent_factory(llm, search_results, model="llama3.2")

    with pytest.raises(ModelUnavailableError, match="installed: unknown"):
        agent.run("headphones")


@pytest.mark.parametrize(
    "error",
    [
        RequestError("malformed request"),
        ConnectionError("connection refused"),
        OSError("socket died"),
        # What the real client actually raises. The ollama client converts a
        # refused connection and nothing else, so a timeout and a dropped stream
        # arrive as httpx's own -- which is why both kinds are in the tuple.
        httpx.ConnectError("[Errno 111] Connection refused"),
        httpx.RemoteProtocolError("the server closed the stream"),
        httpx.ProxyError("no route to the proxy"),
    ],
)
def test_transport_failures_all_become_one_actionable_error(
    agent_factory, search_results, error
) -> None:
    agent, _ = agent_factory(FakeLLM(raises=error), search_results)

    with pytest.raises(ModelUnavailableError, match="ollama serve"):
        agent.run("headphones")


@pytest.mark.parametrize(
    "error", [httpx.ReadTimeout("timed out"), httpx.ConnectTimeout("timed out")]
)
def test_a_model_too_slow_to_answer_says_so_rather_than_ollama_serve(
    agent_factory, search_results, error
) -> None:
    """A timeout is a running server, so telling the user to start one misleads."""
    agent, _ = agent_factory(FakeLLM(raises=error), search_results, model="qwen3.5:9b")

    with pytest.raises(ModelUnavailableError, match="did not answer in time") as caught:
        agent.run("headphones")

    assert "ollama serve" not in str(caught.value)
    assert "qwen3.5:9b" in str(caught.value)


def test_a_missing_ollama_is_not_papered_over_by_the_query_fallback(
    agent_factory, search_results
) -> None:
    """Refinement is recoverable; searching with a model that is not there is not."""
    agent, calls = agent_factory(FakeLLM(raises=ConnectionError("refused")), search_results)

    with pytest.raises(ModelUnavailableError):
        agent.run("headphones")

    assert calls == [], "the search must not run without a working model"


def test_the_request_is_stripped_before_it_reaches_the_model(
    agent_factory, search_results, extracted_products
) -> None:
    llm = FakeLLM(products=extracted_products)
    agent, _ = agent_factory(llm, search_results)

    agent.run("  wireless earbuds \n")

    assert llm.calls[0][-1]["content"].endswith("wireless earbuds")


def test_the_extraction_prompt_gets_the_results_and_the_limit(
    agent_factory, search_results, extracted_products
) -> None:
    llm = FakeLLM(products=extracted_products)
    agent, _ = agent_factory(llm, search_results, num_products=4)

    agent.run("headphones")

    system, human = llm.calls[1]
    assert "at most 4 distinct products" in system["content"]
    assert "https://example.com/sony" in human["content"]


def test_the_configured_weights_reach_the_ranking(
    agent_factory, search_results, extracted_products
) -> None:
    """The default blend puts the cheap Anker first; rating alone puts the Sony there.

    Ranking on the wrong weights is invisible in the output -- it is still a
    plausible order -- so the only way to see the config arrive is to pick weights
    that reorder the same products.
    """
    agent, _ = agent_factory(
        FakeLLM(products=extracted_products),
        search_results,
        weights=RankingWeights(rating=1.0, popularity=0.0, price=0.0),
    )

    ranked = agent.run("headphones")

    assert ranked[0].product.name == "Sony WH-1000XM5"


def test_the_request_reaches_the_extraction_prompt(
    agent_factory, search_results, extracted_products
) -> None:
    """Without it the model is asked to pick products out of results for nothing."""
    llm = FakeLLM(products=extracted_products)
    agent, _ = agent_factory(llm, search_results)

    agent.run("noise cancelling headphones under $200")

    _, human = llm.calls[1]
    assert "noise cancelling headphones under $200" in human["content"]


@pytest.mark.parametrize(
    ("sort_by", "order"),
    [
        ("score", ["Anker Soundcore Q30", "Sony WH-1000XM5", "Unknown Brand Buds"]),
        ("price", ["Anker Soundcore Q30", "Sony WH-1000XM5", "Unknown Brand Buds"]),
        # The one criterion that disagrees with the blended score on this set,
        # and so the one that says the argument was read rather than defaulted:
        # the Sony is the better-rated pair and the more expensive one.
        ("rating", ["Sony WH-1000XM5", "Anker Soundcore Q30", "Unknown Brand Buds"]),
    ],
)
def test_sort_by_reaches_the_ranking(
    agent_factory, search_results, extracted_products, sort_by: str, order: list[str]
) -> None:
    """Every criterion the two front doors offer, each asserted on an order only
    it produces -- ``--sort-by rating`` silently ignored is a report in the wrong
    order that still looks like a report."""
    agent, _ = agent_factory(FakeLLM(products=extracted_products), search_results)

    ranked = agent.run("headphones", sort_by=sort_by)

    assert [entry.product.name for entry in ranked] == order
    assert [entry.rank for entry in ranked] == [1, 2, 3]


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


def test_a_product_the_model_gave_no_link_for_is_linked_to_its_page(
    agent_factory, search_results
) -> None:
    """The real-world case: models leave the link empty, so the run supplies it."""
    llm = FakeLLM(
        products=ProductList(
            products=[ExtractedProduct(name="Anker Soundcore Q30", price=79.0)]
        )
    )
    agent, _ = agent_factory(llm, search_results)

    ranked = agent.run("headphones")

    assert ranked[0].product.url == "https://example.com/anker"


def test_an_invented_link_never_reaches_the_report(
    agent_factory, search_results, caplog
) -> None:
    """A link is what the shopper clicks, so a page nobody searched must not survive."""
    llm = FakeLLM(
        products=ProductList(
            products=[
                ExtractedProduct(
                    name="Anker Soundcore Q30", price=79.0, url="https://phishing.example/deal"
                )
            ]
        )
    )
    agent, _ = agent_factory(llm, search_results)

    with caplog.at_level(logging.INFO, logger="buy_agent"):
        ranked = agent.run("headphones")

    assert ranked[0].product.url == "https://example.com/anker"
    assert "phishing.example" not in caplog.text


def test_what_the_pages_said_reaches_the_report(agent_factory, caplog) -> None:
    """The whole point, end to end: a quote the sources printed is reported, and
    one the model wrote itself is gone before anybody reads it."""
    results = [
        SearchResult(
            title="Sony WH-1000XM5 review",
            url="https://example.com/sony",
            snippet="$328, rated 4.7 out of 5. Testers found the noise cancelling uncanny.",
        )
    ]
    products = ProductList(
        products=[
            ExtractedProduct(
                name="Sony WH-1000XM5",
                price=328.0,
                opinions=[
                    "Testers found the noise cancelling uncanny",
                    "battery life is disappointing",
                ],
            )
        ]
    )
    agent, _ = agent_factory(FakeLLM(products=products), results)

    with caplog.at_level(logging.INFO, logger="buy_agent"):
        ranked = agent.run("headphones")

    assert ranked[0].product.opinions == ["Testers found the noise cancelling uncanny"]
    assert "says   : Testers found the noise cancelling uncanny" in caplog.text
    assert "battery life" not in caplog.text


# -- searching only the sources the shopper named ------------------------------


@pytest.fixture
def source_search(monkeypatch):
    """An agent whose search backend answers per query.

    ``asked`` records the query and width every search was made with, and
    ``reached`` the URLs that survived the pooling -- read off ``enrich``, which
    is the next thing in the pipeline, so what is asserted is what the run went
    on to read rather than the return of a private method.
    """

    def build(pages: dict[str, list[SearchResult]], llm: FakeLLM, **config_kwargs):
        asked: list[tuple[str, int]] = []
        reached: list[str] = []

        def fake_search(query: str, *, max_results: int = 10, region: str = "us-en") -> list:
            asked.append((query, max_results))
            return pages.get(query, [])

        def fake_enrich(found: list, **_: object) -> list:
            reached.extend(result.url for result in found)
            return found

        monkeypatch.setattr("buy_agent.agent.search_web", fake_search)
        monkeypatch.setattr("buy_agent.agent.enrich", fake_enrich)
        return BuyAgent(AgentConfig(**config_kwargs), llm=llm), asked, reached

    return build


def _page(name: str, url: str) -> SearchResult:
    return SearchResult(title=f"{name} review", url=url, snippet=f"The {name} is $99.")


def test_naming_no_source_searches_the_web_once(source_search) -> None:
    agent, asked, _ = source_search({}, FakeLLM(query=SearchQuery(query="headphones")))

    agent.run("headphones")

    assert asked == [("headphones", 10)]


def test_each_named_source_is_searched_for_on_its_own(source_search) -> None:
    """``site:`` takes one domain, so two sources are two searches."""
    agent, asked, _ = source_search(
        {}, FakeLLM(query=SearchQuery(query="headphones")), sources=parse_sources("a.com @mkbhd")
    )

    agent.run("headphones")

    assert [query for query, _ in asked] == [
        "headphones site:a.com",
        'headphones site:youtube.com "@mkbhd"',
    ]


def test_the_search_width_is_shared_out_rather_than_multiplied(source_search) -> None:
    """Four sources at the full width would fetch forty pages for a report of three."""
    agent, asked, _ = source_search(
        {},
        FakeLLM(query=SearchQuery(query="headphones")),
        search_results=10,
        sources=parse_sources("a.com b.com c.com d.com"),
    )

    agent.run("headphones")

    # Ten between four, rounded up: nobody is left asking for none.
    assert [width for _, width in asked] == [3, 3, 3, 3]


def test_a_result_from_outside_a_source_never_reaches_the_model(
    source_search, caplog
) -> None:
    """The operator is the backend's promise; this is the check on it. Otherwise a
    backend that ignored ``site:`` would quietly source the facts from anywhere."""
    agent, _, _reached = source_search(
        {
            "headphones site:a.com": [
                _page("Anker Q30", "https://a.com/anker"),
                _page("Sony XM5", "https://elsewhere.example/sony"),
            ]
        },
        FakeLLM(
            query=SearchQuery(query="headphones"),
            products=ProductList(
                products=[
                    ExtractedProduct(name="Anker Q30", price=99.0),
                    ExtractedProduct(name="Sony XM5", price=99.0),
                ]
            ),
        ),
        sources=parse_sources("a.com"),
    )

    with caplog.at_level(logging.INFO, logger="buy_agent"):
        ranked = agent.run("headphones")

    # Sony was on the discarded page, so grounding has nothing to back it.
    assert [entry.product.name for entry in ranked] == ["Anker Q30"]
    assert "Ignored 1 result(s) from outside a.com" in caplog.text


def test_one_page_found_under_two_sources_is_read_once(source_search) -> None:
    """It is one page. Fetching it twice would cost a slot the second source
    could have filled with something the shopper has not already seen."""
    shared = _page("Anker Q30", "https://shop.example/anker")
    agent, _, reached = source_search(
        {
            "headphones site:shop.example": [shared],
            "headphones site:m.shop.example": [shared],
        },
        FakeLLM(query=SearchQuery(query="headphones")),
        sources=parse_sources("shop.example m.shop.example"),
    )

    agent.run("headphones")

    assert reached == ["https://shop.example/anker"]


def test_sources_that_between_them_found_nothing_end_the_run(source_search, caplog) -> None:
    """No silent fall back to the whole web: the shopper said where the facts come
    from, and a run that quietly went elsewhere would report facts they refused."""
    agent, asked, _ = source_search(
        {}, FakeLLM(query=SearchQuery(query="headphones")), sources=parse_sources("a.com")
    )

    with caplog.at_level(logging.WARNING, logger="buy_agent"):
        assert agent.run("headphones") == []

    assert [query for query, _ in asked] == ["headphones site:a.com"]
    assert "Search returned nothing" in caplog.text


def test_the_pool_is_cut_back_to_the_width_the_run_asked_for(source_search) -> None:
    """Rounding the share up hands out one more page than was asked for; the cut
    is what keeps a four-page run four pages."""
    agent, _, reached = source_search(
        {
            "headphones site:a.com": [_page(f"A{n}", f"https://a.com/{n}") for n in range(3)],
            "headphones site:b.com": [_page(f"B{n}", f"https://b.com/{n}") for n in range(3)],
        },
        FakeLLM(query=SearchQuery(query="headphones")),
        search_results=4,
        sources=parse_sources("a.com b.com"),
    )

    agent.run("headphones")

    assert len(reached) == 4


# -- ending a run nobody is reading any more -----------------------------------


def test_every_step_is_announced_to_the_checkpoint_before_it_starts(
    agent_factory, search_results, extracted_products
) -> None:
    """The boundaries a caller can end a run at, in the order the pipeline reaches
    them. Only these four: a step not announced is one a stopped run still pays for."""
    agent, _ = agent_factory(FakeLLM(products=extracted_products), search_results)
    steps: list[str] = []

    agent.run("headphones", checkpoint=steps.append)

    assert steps == ["search", "fetch", "extract", "rank"]


def test_a_run_that_fetches_nothing_never_announces_the_fetch(
    agent_factory, search_results, extracted_products
) -> None:
    """The boundary belongs to the step: announcing a fetch that is not going to
    happen would offer a stop that saves nothing."""
    agent, _ = agent_factory(
        FakeLLM(products=extracted_products), search_results, fetch_pages=False
    )
    steps: list[str] = []

    agent.run("headphones", checkpoint=steps.append)

    assert steps == ["search", "extract", "rank"]


def test_a_checkpoint_that_raises_ends_the_run_before_that_step(
    agent_factory, search_results, extracted_products
) -> None:
    """How a stream whose reader has gone stops a run (ADR-0034): the exception is
    the caller's own, nothing here catches it, and the step never runs."""
    llm = FakeLLM(products=extracted_products)
    agent, calls = agent_factory(llm, search_results)

    def stop_before_extraction(step: str) -> None:
        if step == "extract":
            raise KeyboardInterrupt(step)

    with pytest.raises(KeyboardInterrupt):
        agent.run("headphones", checkpoint=stop_before_extraction)

    # The query was refined and the pages were fetched; the extraction -- the
    # second and slower of the two model calls -- never happened.
    assert len(llm.calls) == 1
    assert any("enriched" in call for call in calls)


def test_a_run_stopped_before_ranking_reports_nothing(
    agent_factory, search_results, extracted_products, caplog
) -> None:
    """The last boundary earns its place: ranking is cheap, but it ends in the
    report, and a report for a run nobody is reading is a report nobody asked for."""
    agent, _ = agent_factory(FakeLLM(products=extracted_products), search_results)

    def stop_before_ranking(step: str) -> None:
        if step == "rank":
            raise KeyboardInterrupt(step)

    with caplog.at_level(logging.INFO, logger="buy_agent"), pytest.raises(KeyboardInterrupt):
        agent.run("headphones", checkpoint=stop_before_ranking)

    assert "TOP" not in caplog.text
