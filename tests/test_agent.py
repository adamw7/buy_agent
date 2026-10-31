"""End-to-end agent behaviour with a fake model and a fake search backend."""

from __future__ import annotations

import json
import logging
from time import sleep
from types import SimpleNamespace

import pytest

from buy_agent.agent import BuyAgent, ModelUnavailableError, _asks_the_same_question
from buy_agent.cache import DEFAULT_TTL
from buy_agent.config import AgentConfig
from buy_agent.models import ExtractedProduct, ProductList, SearchQuery
import buy_agent.providers as providers_module
from buy_agent.providers import OllamaReply
from buy_agent.ranking import RankingWeights
from buy_agent.search import SearchError, SearchResult
from buy_agent.sources import parse_sources

from tests.conftest import FakeLLM
from tests.test_fetch import make_response, stub_client


@pytest.fixture
def agent_factory(monkeypatch):
    """Build an agent whose search backend is a recorded fake."""

    def build(llm: FakeLLM, results: list, **config_kwargs) -> tuple[BuyAgent, list]:
        calls: list[dict] = []

        def fake_search(
            query: str, *, max_results: int = 10, region: str = "us-en", wait: object = None,
            **_: object,
        ) -> list:
            calls.append(
                {"query": query, "max_results": max_results, "region": region, "wait": wait}
            )
            return results

        def fake_enrich(found: list, **kwargs) -> list:
            calls.append({"enriched": len(found), **kwargs})
            return found

        monkeypatch.setattr("buy_agent.agent.search_web", fake_search)
        monkeypatch.setattr("buy_agent.agent.enrich", fake_enrich)
        return BuyAgent(AgentConfig(**config_kwargs), llm=llm), calls

    return build


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
        # The clock a refused search waits by before asking again, as the fetching
        # is handed one (ADR-0053).
        "wait": sleep,
    }


@pytest.mark.parametrize(
    ("failure", "said"),
    [
        (ValueError("model returned garbage"), "model returned garbage"),
        # Pydantic's first line, not its dozen.
        (
            ValueError("1 validation error for SearchQuery\nquery\n  Field required"),
            "1 validation error for SearchQuery",
        ),
        # Its words, not the space around them.
        (ValueError("\n  model returned garbage\n"), "model returned garbage"),
        # A failure that says nothing is named by its type, as is one that says blanks.
        (RuntimeError(), "RuntimeError"),
        (ValueError("   "), "ValueError"),
    ],
)
def test_a_refinement_that_failed_says_why_in_one_line(
    agent_factory, search_results, extracted_products, monkeypatch, caplog, failure, said
) -> None:
    """The browser relays a line's message and nothing else, so a reason left to the
    traceback never reached it; and a traceback at WARNING put thirty lines of pydantic
    on the terminal over a run that went on fine. The traceback is still there, at the
    level ``-v`` shows."""
    llm = FakeLLM(products=extracted_products)
    agent, _ = agent_factory(llm, search_results)
    monkeypatch.setattr(agent, "query_chain", _failing_chain(failure))

    with caplog.at_level(logging.DEBUG, logger="buy_agent.agent"):
        agent.run("wireless earbuds")

    warned = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert [record.getMessage() for record in warned] == [
        f"Query refinement failed ({said}); using the raw request"
    ]
    assert warned[0].exc_info is None
    traced = [record for record in caplog.records if record.exc_info]
    assert [record.levelno for record in traced] == [logging.DEBUG]
    assert traced[0].getMessage() == "Why query refinement failed"
    assert traced[0].exc_info[1] is failure


def test_blank_refined_query_falls_back_to_the_raw_request(
    agent_factory, search_results, extracted_products
) -> None:
    llm = FakeLLM(query=SearchQuery(query="   "), products=extracted_products)
    agent, calls = agent_factory(llm, search_results)

    agent.run("wireless earbuds")

    assert calls[0]["query"] == "wireless earbuds"


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


def test_a_search_that_found_nothing_names_the_sources_it_was_confined_to(
    agent_factory, extracted_products, caplog
) -> None:
    """The likeliest reason of all, and the one with no recovery."""
    agent, _ = agent_factory(
        FakeLLM(products=extracted_products), [], sources=parse_sources(["rtings.com"])
    )

    with caplog.at_level(logging.WARNING):
        assert agent.run("espresso machine") == []

    said = caplog.text
    assert "Only rtings.com was searched" in said
    assert "no falling back" in said, "and that there is no wider web to fall back on"


def test_several_named_sources_are_listed_as_a_sentence(
    agent_factory, extracted_products, caplog
) -> None:
    """A shopper reads this line; "a, b and c" is how a list of three is written."""
    agent, _ = agent_factory(
        FakeLLM(products=extracted_products),
        [],
        sources=parse_sources(["rtings.com", "@mkbhd", "wired.com"]),
    )

    with caplog.at_level(logging.WARNING):
        agent.run("espresso machine")

    assert "rtings.com, @mkbhd and wired.com were searched" in caplog.text


def test_two_named_sources_are_joined_the_way_two_are_written(
    agent_factory, extracted_products, caplog
) -> None:
    """Two is the case the list's commas do not reach, and the one run together."""
    agent, _ = agent_factory(
        FakeLLM(products=extracted_products),
        [],
        sources=parse_sources(["rtings.com", "@mkbhd"]),
    )

    with caplog.at_level(logging.WARNING):
        agent.run("espresso machine")

    assert "Only rtings.com and @mkbhd were searched" in caplog.text


def test_an_empty_search_with_nothing_narrowing_it_stops_after_the_query(
    agent_factory, extracted_products, caplog
) -> None:
    """Neither note applies, so the line is the query and a full stop -- not a
    sentence left hanging where a note would have gone."""
    agent, _ = agent_factory(FakeLLM(products=extracted_products), [])

    with caplog.at_level(logging.WARNING):
        agent.run("obscure thing")

    empty = [line for line in caplog.text.splitlines() if "Search returned nothing" in line]
    assert empty and empty[0].rstrip().endswith("'."), empty


def test_no_extractable_products_yields_no_products(agent_factory, search_results, caplog) -> None:
    agent, _ = agent_factory(FakeLLM(products=ProductList()), search_results)

    with caplog.at_level(logging.WARNING):
        assert agent.run("obscure thing") == []
    assert "No products could be extracted" in caplog.text


# -- why a run found nothing, in a sentence both doors show (ADR-0012) -----------


def test_a_run_that_searched_nothing_says_so(agent_factory) -> None:
    agent, _ = agent_factory(FakeLLM(), [])

    assert agent.run("obscure thing") == []
    assert agent.nothing == "Search returned nothing for 'fake refined query'."


def test_a_run_whose_model_named_nothing_says_so(agent_factory, search_results) -> None:
    agent, _ = agent_factory(FakeLLM(products=ProductList()), search_results)

    agent.run("obscure thing")

    assert agent.nothing == "No products could be extracted from the pages the search returned."


def test_a_run_that_read_only_snippets_says_reading_the_pages_finds_more(
    agent_factory, search_results
) -> None:
    """Named as the setting, never the flag: the sentence reaches the browser."""
    agent, _ = agent_factory(FakeLLM(products=ProductList()), search_results, fetch_pages=False)

    agent.run("obscure thing")

    assert agent.nothing == (
        "No products could be extracted from the search snippets; reading the result "
        "pages, which this run left off, finds more."
    )


def test_a_run_whose_candidates_all_failed_the_checks_counts_them(
    agent_factory, search_results
) -> None:
    named = ProductList(
        products=[
            ExtractedProduct(name="Made Up Phones X1", price=10.0),
            ExtractedProduct(name="Made Up Phones X2", price=12.0),
        ]
    )
    agent, _ = agent_factory(FakeLLM(products=named), search_results)

    assert agent.run("obscure thing") == []
    assert agent.nothing == (
        "The model named 2 candidates, and none passed the checks that a product is "
        "real and named on the pages searched."
    )


#: Both priced on the pages ``search_results`` stands in for.
PRICED = ProductList(
    products=[
        ExtractedProduct(name="Sony WH-1000XM5", price=328.0, currency="USD"),
        ExtractedProduct(name="Anker Soundcore Q30", price=79.0, currency="USD"),
    ]
)


def test_a_run_whose_limits_removed_everything_says_so(agent_factory, search_results) -> None:
    agent, _ = agent_factory(FakeLLM(products=PRICED), search_results, max_price=50)

    assert agent.run("headphones") == []
    assert agent.nothing == "All 2 products found are outside this run's limits; clear one to see them."


def test_one_product_or_candidate_is_counted_in_the_singular(agent_factory, search_results) -> None:
    one = ProductList(products=PRICED.products[:1])
    agent, _ = agent_factory(FakeLLM(products=one), search_results, max_price=50)
    agent.run("headphones")
    assert agent.nothing == "All 1 product found is outside this run's limits; clear one to see them."

    made_up = ProductList(products=[ExtractedProduct(name="Made Up Phones X1", price=10.0)])
    agent, _ = agent_factory(FakeLLM(products=made_up), search_results)
    agent.run("headphones")
    assert agent.nothing.startswith("The model named 1 candidate, and none passed")


def test_a_run_that_found_something_has_nothing_to_explain(agent_factory, search_results) -> None:
    """Cleared at the start of each run, so a found set never carries the last
    empty run's sentence."""
    agent, _ = agent_factory(FakeLLM(products=PRICED), search_results, max_price=50)
    agent.run("headphones")
    assert agent.nothing
    agent.config = AgentConfig()

    assert agent.run("headphones")
    assert agent.nothing == ""


def test_empty_request_is_rejected(agent_factory, search_results) -> None:
    agent, _ = agent_factory(FakeLLM(), search_results)

    # The whole sentence: the CLI prints it as the run's last line and the page as its
    # failure, so it is the shopper's to read.
    with pytest.raises(ValueError, match=r"^Nothing to shop for: the request is empty\.$"):
        agent.run("   ")


def test_search_failures_propagate(monkeypatch, extracted_products) -> None:
    def boom(*_args, **_kwargs):
        raise SearchError("rate limited")

    monkeypatch.setattr("buy_agent.agent.search_web", boom)
    agent = BuyAgent(AgentConfig(), llm=FakeLLM(products=extracted_products))

    with pytest.raises(SearchError, match="rate limited"):
        agent.run("headphones")


def _failing_chain(error: Exception):
    """A stand-in chain that raises instead of answering."""

    class Failing:
        @staticmethod
        def invoke(_payload):
            raise error

    return Failing()


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
    unsupported = next(entry for entry in ranked if entry.product.name == "Unknown Brand Buds")

    assert unsupported.product.price is None
    assert unsupported.product.rating is None
    assert ranked[0].product.name == "Sony WH-1000XM5"


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
        "cache_ttl": DEFAULT_TTL,
        # The clock the fetching waits by, which a step of the pipeline is given
        # rather than holding (ADR-0053).
        "wait": sleep,
    }


def test_fetching_can_be_turned_off(agent_factory, search_results, extracted_products) -> None:
    agent, calls = agent_factory(
        FakeLLM(products=extracted_products), search_results, fetch_pages=False
    )

    agent.run("headphones")

    assert not any("enriched" in call for call in calls)


def test_a_page_the_search_repeats_is_fetched_once(agent_factory, extracted_products) -> None:
    """Fetched twice at once, a repeat is too quick for the page cache; a hit with no
    address is nobody's repeat, so both of those stay."""
    repeated = [
        SearchResult(title="Sony", url="https://shop.example/a"),
        SearchResult(title="Sony again", url="https://shop.example/a"),
        SearchResult(title="no link", url=""), SearchResult(title="no link either", url=""),
    ]
    agent, calls = agent_factory(FakeLLM(products=extracted_products), repeated)

    agent.run("headphones")

    assert [call["enriched"] for call in calls if "enriched" in call] == [3]


def _prompt(llm: FakeLLM) -> str:
    """What the extraction step showed the model, as one string."""
    return json.dumps(llm.calls[-1])


def test_the_first_of_a_repeat_is_the_one_the_model_sees(monkeypatch) -> None:
    """Order is the backend's ranking, so the earlier hit is the one kept."""
    monkeypatch.setattr(
        "buy_agent.agent.search_web",
        lambda *_args, **_kwargs: [
            SearchResult(title="First", url="https://shop.example/a"),
            SearchResult(title="Second", url="https://shop.example/a"),
            SearchResult(title="Third", url="https://shop.example/b"),
        ],
    )
    llm = FakeLLM()

    BuyAgent(AgentConfig(fetch_pages=False), llm=llm).run("headphones")

    prompt = _prompt(llm)
    assert "TITLE: First" in prompt
    assert "TITLE: Second" not in prompt
    assert "TITLE: Third" in prompt


def test_a_hit_with_no_address_reaches_the_model_unfetched(monkeypatch) -> None:
    """Through the real fetch: only the page with an address is asked for, and the
    linkless hit's snippet is still in front of the model."""
    monkeypatch.setattr(
        "buy_agent.agent.search_web",
        lambda *_args, **_kwargs: [
            SearchResult(title="No link", snippet="Anker Q30 for $79"),
            SearchResult(title="Shop", url="https://shop.example/sony"),
        ],
    )
    asked: list[str] = []

    def handler(url: str):
        asked.append(url)
        return make_response(url, "<p>Sony WH-1000XM5</p><p>Now $328</p>")

    stub_client(monkeypatch, handler)
    llm = FakeLLM()

    BuyAgent(AgentConfig(cache_ttl=0), llm=llm).run("headphones")

    assert asked == ["https://shop.example/sony"]
    prompt = _prompt(llm)
    assert "Anker Q30 for $79" in prompt
    assert "Now $328" in prompt


@pytest.fixture
def ollama_request(monkeypatch):
    """Capture the request the Ollama provider puts a run's settings into."""
    sent: dict = {}

    class FakeClient:
        def __init__(self, base_url: str, **kwargs) -> None:
            sent["base_url"] = base_url

        @staticmethod
        def chat(**kwargs):
            sent.update(kwargs)
            return OllamaReply('{"query": "a refined query"}', "stop")

    monkeypatch.setattr("buy_agent.providers.OllamaClient", FakeClient)
    return sent


def _refine(config: AgentConfig) -> None:
    """Put one question to the model the config's provider builds."""
    BuyAgent(config).query_chain.invoke({"request": "headphones"})


def test_model_settings_reach_the_chat_model(ollama_request) -> None:
    _refine(AgentConfig(model="qwen3.5:9b", temperature=0.2, num_ctx=8192, reasoning=False))

    assert ollama_request["model"] == "qwen3.5:9b"
    assert ollama_request["options"] == {"temperature": 0.2, "num_ctx": 8192}
    assert ollama_request["think"] is False


def test_thinking_and_context_can_be_left_alone(ollama_request) -> None:
    """None means "send nothing": a model that cannot think must not be told to."""
    _refine(AgentConfig(num_ctx=None, reasoning=None))

    assert "num_ctx" not in ollama_request["options"]
    assert ollama_request["think"] is None


# -- and answers the same question off disk (ADR-0044) -------------------------


@pytest.fixture
def ollama_calls(monkeypatch):
    """Ollama's client again, counting the questions rather than reading one."""
    asked: list[dict] = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def chat(self, **kwargs):
            asked.append(kwargs)
            return OllamaReply('{"query": "a refined query"}', "stop")

    monkeypatch.setattr("buy_agent.providers.OllamaClient", FakeClient)
    return asked


@pytest.mark.parametrize(
    ("configs", "asked"),
    [
        # Two runs of the same search over the same pages ask the same thing,
        # and the second is most of a minute the shopper does not wait again.
        pytest.param([AgentConfig(), AgentConfig()], 1, id="the same question twice"),
        # One setting for a live run, pages and answers alike.
        pytest.param([AgentConfig(cache_ttl=0)] * 2, 2, id="--cache-ttl 0"),
        # A model asked for a different answer each time has none to remember,
        # and replaying one sample would be the cache deciding the result.
        pytest.param([AgentConfig(temperature=0.7)] * 2, 2, id="a sampled run"),
        # What a model answers is the whole of what is stored, so which model it
        # was is in the key -- with the server, and the request's own settings.
        pytest.param(
            [AgentConfig(model="gemma4:12b"), AgentConfig(model="lfm2.5")],
            2,
            id="another model",
        ),
    ],
)
def test_how_often_a_repeated_run_reaches_the_model(
    configs: list[AgentConfig], asked: int, ollama_calls
) -> None:
    for config in configs:
        _refine(config)

    assert len(ollama_calls) == asked


def test_a_different_window_on_ollama_is_a_different_question() -> None:
    """``num_ctx`` decides whether the extraction fits at all (ADR-0019, ADR-0050), so
    an answer cut off in a window too small to hold it is not the answer a wider one
    would have been handed back."""
    narrow = _asks_the_same_question(AgentConfig(provider="ollama", num_ctx=4096))
    wide = _asks_the_same_question(AgentConfig(provider="ollama", num_ctx=16384))

    assert narrow != wide


def test_a_setting_the_server_never_sees_is_not_part_of_the_question() -> None:
    """vLLM fixes its window with ``--max-model-len`` at startup, so ``num_ctx``
    changes nothing there -- and a key holding it would miss on a setting that
    never left the process."""
    ollama = _asks_the_same_question(AgentConfig(provider="ollama"))
    vllm = _asks_the_same_question(AgentConfig(provider="vllm"))

    assert "num_ctx" in ollama
    assert "num_ctx" not in vllm


@pytest.fixture
def installed_models(monkeypatch):
    """Stand in for an Ollama being asked what it holds, so nothing opens a socket."""

    def install(models: list[str] | None, *, error: Exception | None = None) -> None:
        def get(_url, **_kwargs):
            if error is not None:
                raise error
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"models": [{"model": name} for name in models or []]},
            )

        class FakeClient:
            def __init__(self, base_url: str, **_kwargs) -> None:
                self.base_url = base_url

            @staticmethod
            def show(_name: str):
                return ["completion"]

            def close(self) -> None:
                """The listing lets go of the client it opened."""

        monkeypatch.setattr("buy_agent.providers.httpx.get", get)
        monkeypatch.setattr("buy_agent.providers.OllamaClient", FakeClient)

    return install


def test_the_missing_model_error_names_what_is_installed(
    agent_factory, search_results, installed_models
) -> None:
    """Half of these failures are a typo in the tag, so show the real ones."""
    installed_models(["lfm2.5:latest", "qwen3:8b"])
    llm = FakeLLM(raises=providers_module.OllamaError("model 'llama3.2' not found", 404))
    agent, _ = agent_factory(llm, search_results, model="llama3.2")

    with pytest.raises(ModelUnavailableError, match="lfm2.5:latest, qwen3:8b"):
        agent.run("headphones")


def test_the_configured_weights_reach_the_ranking(
    agent_factory, search_results, extracted_products
) -> None:
    """The default blend puts the cheap Anker first; rating alone puts the Sony there."""
    agent, _ = agent_factory(
        FakeLLM(products=extracted_products), search_results,
        weights=RankingWeights(rating=1.0, popularity=0.0, price=0.0),
    )

    ranked = agent.run("headphones")

    assert ranked[0].product.name == "Sony WH-1000XM5"


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


# -- and lets go of the connection it opened -----------------------------------


@pytest.mark.parametrize(
    ("cache_ttl", "why"),
    [
        pytest.param(0, "the model itself", id="a live run"),
        pytest.param(DEFAULT_TTL, "through the remembering wrapper", id="a remembered run"),
    ],
)
def test_closing_an_agent_closes_the_client_it_opened(
    monkeypatch: pytest.MonkeyPatch, cache_ttl: float, why: str
) -> None:
    """Whichever of the two the agent is holding."""
    closed: list[str] = []

    class FakeClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def close(self) -> None:
            closed.append(why)

    monkeypatch.setattr("buy_agent.providers.OllamaClient", FakeClient)

    BuyAgent(AgentConfig(provider="ollama", cache_ttl=cache_ttl)).close()

    assert closed == [why]


def test_closing_an_agent_leaves_a_model_it_was_handed_alone() -> None:
    """A client passed in belongs to whoever passed it."""

    class Pooling(FakeLLM):
        closed = False

        def close(self) -> None:
            self.closed = True

    model = Pooling()

    BuyAgent(AgentConfig(), llm=model).close()

    assert model.closed is False


# -- searching only the sources the shopper named ------------------------------


@pytest.fixture
def source_search(monkeypatch):
    """An agent whose search backend answers per query."""

    def build(pages: dict[str, list[SearchResult]], llm: FakeLLM, **config_kwargs):
        asked: list[tuple[str, int]] = []
        reached: list[str] = []

        def fake_search(
            query: str, *, max_results: int = 10, region: str = "us-en", **_: object
        ) -> list:
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


def test_each_named_source_is_searched_for_on_its_own(source_search) -> None:
    """``site:`` takes one domain, so two sources are two searches."""
    agent, asked, _ = source_search(
        {}, FakeLLM(query=SearchQuery(query="headphones")), sources=parse_sources("a.com @mkbhd")
    )

    agent.run("headphones")

    assert [query for query, _ in asked] == [
        "headphones site:a.com", 'headphones site:youtube.com "@mkbhd"',
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


def test_a_result_from_outside_a_source_never_reaches_the_model(source_search, caplog) -> None:
    """The operator is the backend's promise; this is the check on it."""
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
    # The other half of the pair: how many on an ordinary run, which on ``-v``.
    assert "https://elsewhere.example/sony" not in caplog.text


def test_a_source_left_with_nothing_is_a_warning(source_search, caplog) -> None:
    """With no falling back to the wider web (ADR-0027), every result refused means
    the source reports nothing, which an ordinary run should show; some kept is INFO."""
    agent, _, _reached = source_search(
        {
            "headphones site:a.com": [_page("Sony XM5", "https://elsewhere.example/sony")],
            "headphones site:b.com": [
                _page("Anker Q30", "https://b.com/anker"),
                _page("Bose QC45", "https://elsewhere.example/bose"),
            ],
        },
        FakeLLM(query=SearchQuery(query="headphones")),
        sources=parse_sources("a.com, b.com"),
    )

    with caplog.at_level(logging.INFO, logger="buy_agent.agent"):
        agent.run("headphones")

    ignored = {
        record.getMessage(): record.levelno
        for record in caplog.records
        if record.getMessage().startswith("Ignored")
    }
    assert ignored == {
        "Ignored 1 result(s) from outside a.com, which leaves nothing from it": logging.WARNING,
        "Ignored 1 result(s) from outside b.com": logging.INFO,
    }


def test_a_source_that_kept_everything_says_nothing_about_ignoring(source_search, caplog) -> None:
    """No "Ignored 0": the count is only logged when something was refused."""
    agent, _, _reached = source_search(
        {"headphones site:a.com": [_page("Anker Q30", "https://a.com/anker")]},
        FakeLLM(query=SearchQuery(query="headphones")),
        sources=parse_sources("a.com"),
    )

    with caplog.at_level(logging.DEBUG, logger="buy_agent.agent"):
        agent.run("headphones")

    assert not any(
        record.getMessage().startswith(("Ignored", "From outside"))
        for record in caplog.records
    )


def test_a_source_that_found_nothing_is_not_said_to_have_ignored_anything(
    source_search, caplog
) -> None:
    """An empty answer is the empty-search warning's to explain, not this one's."""
    agent, _, _reached = source_search(
        {}, FakeLLM(query=SearchQuery(query="headphones")), sources=parse_sources("a.com")
    )

    with caplog.at_level(logging.INFO, logger="buy_agent.agent"):
        agent.run("headphones")

    assert "Ignored" not in caplog.text
    assert "Search returned nothing" in caplog.text


def test_a_result_ignored_for_its_domain_is_named_at_debug(source_search, caplog) -> None:
    """"Why is the one I had in mind not in there?" -- which ``covers`` refused is the
    answer, and with no falling back to the wider web (ADR-0027) the count alone leaves
    an empty report with nothing to argue with."""
    agent, _, _reached = source_search(
        {
            "headphones site:a.com": [
                _page("Anker Q30", "https://a.com/anker"),
                _page("Sony XM5", "https://elsewhere.example/sony"),
            ]
        },
        FakeLLM(query=SearchQuery(query="headphones")),
        sources=parse_sources("a.com"),
    )

    with caplog.at_level(logging.DEBUG, logger="buy_agent"):
        agent.run("headphones")

    named = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.DEBUG
        and "https://elsewhere.example/sony" in record.getMessage()
    ]
    assert named, "nothing names the result that was ignored"


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
    """The boundaries a caller can end a run at, in the order the pipeline reaches them."""
    agent, _ = agent_factory(FakeLLM(products=extracted_products), search_results)
    steps: list[str] = []

    agent.run("headphones", checkpoint=steps.append)

    assert steps == ["search", "fetch", "extract", "rank"]


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


# -- saying which model it asked and where the time went (ADR-0084) ------------


class Clock:
    """``perf_counter`` for a run whose steps take as long as each stand-in says."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def passes(self, seconds: float) -> None:
        self.now += seconds


class SlowLLM(FakeLLM):
    """A model that takes a while over each question: the extraction is the long one."""

    def __init__(self, clock: Clock, **kwargs) -> None:
        super().__init__(**kwargs)
        self.clock = clock

    def answer(self, messages, schema):
        self.clock.passes(2.5 if schema is SearchQuery else 64.0)
        return super().answer(messages, schema)


@pytest.fixture
def clock(monkeypatch) -> Clock:
    """The run's clock, with a search taking a second and the fetching a second and a
    half: each step its own figure, so one reported as another reads wrong."""
    ticking = Clock()
    monkeypatch.setattr("buy_agent.agent.perf_counter", ticking)
    return ticking


@pytest.fixture
def timed_agent(monkeypatch, clock, search_results, extracted_products):
    def build(*, found: list | None = None, **config_kwargs) -> BuyAgent:
        def search(*_args, **_kwargs) -> list:
            clock.passes(1.0)
            return search_results if found is None else found

        def enrich(results: list, **_kwargs) -> list:
            clock.passes(1.5)
            return results

        monkeypatch.setattr("buy_agent.agent.search_web", search)
        monkeypatch.setattr("buy_agent.agent.enrich", enrich)
        llm = SlowLLM(clock, products=extracted_products)
        return BuyAgent(AgentConfig(**config_kwargs), llm=llm)

    return build


def _said(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.name == "buy_agent.agent"]


def test_each_model_call_says_how_long_it_took(timed_agent, caplog) -> None:
    """The extraction is most of a run on a local model, and the log was silent through
    it: a minute between two lines with nothing to say which step it went on."""
    with caplog.at_level(logging.INFO, logger="buy_agent.agent"):
        timed_agent().run("headphones")

    said = _said(caplog)
    assert "Refined search query in 2.5 s: fake refined query" in said
    assert "Extracted 3 candidate(s) in 1 min 04 s" in said


def test_a_run_ends_by_saying_where_its_time_went(timed_agent, caplog) -> None:
    """The whole first, then each step it waited on, in the order they ran."""
    with caplog.at_level(logging.INFO, logger="buy_agent.agent"):
        timed_agent().run("headphones")

    assert _said(caplog)[-1] == (
        "Finished in 1 min 09 s: query 2.5 s, search 1.0 s, fetch 1.5 s, extract 1 min 04 s"
    )


def test_a_step_that_did_not_run_is_not_timed(timed_agent, caplog) -> None:
    with caplog.at_level(logging.INFO, logger="buy_agent.agent"):
        timed_agent(fetch_pages=False).run("headphones")

    assert _said(caplog)[-1] == (
        "Finished in 1 min 08 s: query 2.5 s, search 1.0 s, extract 1 min 04 s"
    )


def test_a_run_that_found_nothing_still_says_how_long_it_took(timed_agent, caplog) -> None:
    """Most of a minute to find nothing is the case somebody asks about."""
    with caplog.at_level(logging.INFO, logger="buy_agent.agent"):
        assert timed_agent(found=[]).run("headphones") == []

    assert _said(caplog)[-1] == "Finished in 3.5 s: query 2.5 s, search 1.0 s"


def test_a_refinement_that_failed_is_timed_all_the_same(
    timed_agent, clock, monkeypatch, caplog
) -> None:
    """The run goes on with the raw request, and the wait it cost is still a wait."""
    agent = timed_agent(found=[])

    class Unreadable:
        @staticmethod
        def invoke(_payload):
            clock.passes(30.0)
            raise ValueError("model returned garbage")

    monkeypatch.setattr(agent, "query_chain", Unreadable())

    with caplog.at_level(logging.INFO, logger="buy_agent.agent"):
        agent.run("headphones")

    assert _said(caplog)[-1] == "Finished in 31.0 s: query 30.0 s, search 1.0 s"


def test_a_failed_run_does_not_say_it_finished(timed_agent, monkeypatch, caplog) -> None:
    """It raises, and the door that called it says what went wrong."""
    agent = timed_agent()
    monkeypatch.setattr(agent, "extraction_chain", _failing_chain(ConnectionError("refused")))

    with caplog.at_level(logging.INFO, logger="buy_agent.agent"), pytest.raises(
        ModelUnavailableError
    ):
        agent.run("headphones")

    assert not any(line.startswith("Finished") for line in _said(caplog))


def test_a_run_names_the_model_it_opened_and_where(ollama_request, monkeypatch, caplog) -> None:
    """A log downloaded from a failed run, or pasted into a bug report, says which
    model it was, which the settings left to their defaults never did."""
    monkeypatch.setattr("buy_agent.agent.search_web", lambda *_args, **_kwargs: [])
    config = AgentConfig(provider="ollama", model="qwen3:0.6b", base_url="http://gpu-box:11434")

    with caplog.at_level(logging.INFO, logger="buy_agent.agent"):
        BuyAgent(config).run("headphones")

    assert _said(caplog)[:2] == [
        "Shopping for: headphones", "Asking qwen3:0.6b on Ollama at http://gpu-box:11434",
    ]


def test_a_model_handed_in_is_its_caller_s_to_name(timed_agent, caplog) -> None:
    """The config still names a model, but not the one answering."""
    with caplog.at_level(logging.INFO, logger="buy_agent.agent"):
        timed_agent().run("headphones")

    assert not any(line.startswith("Asking") for line in _said(caplog))


# -- the shopper's own bounds (ADR-0039) ---------------------------------------


def test_bounds_that_admit_nothing_end_the_run_without_a_report(
    agent_factory, search_results, caplog
) -> None:
    """Not a failure -- the run worked -- so it is the empty answer the CLI turns into its
    own exit code, with the reason on the way past."""
    priced = ProductList(
        products=[
            ExtractedProduct(name="Sony WH-1000XM5", price=328.0, currency="USD"),
            ExtractedProduct(name="Anker Soundcore Q30", price=79.0, currency="USD"),
        ]
    )
    agent, _ = agent_factory(FakeLLM(products=priced), search_results, max_price=1.0)

    with caplog.at_level(logging.INFO):
        assert agent.run("headphones") == []

    assert "0 of 2 product(s) are within the limits (at most 1.00 USD)" in caplog.text
    assert "No products to report" not in caplog.text


def test_what_a_run_took_out_is_handed_to_whoever_is_recording(
    agent_factory, search_results
) -> None:
    """``run`` passes its recorder down to every step that removes a product, the page
    taken for one and the product outside the limits included -- which is what the
    browser's "left out" panel is built from (ADR-0055).

    One of each step a run can reach, in the order the pipeline runs them. The nameless
    drop in ``deduplicate`` is not one: a name with nothing to identify it by is
    mentioned by no page either, so ``ground`` takes it first; the merge is what
    ``deduplicate`` is handed the recorder for."""
    found = ProductList(
        products=[
            ExtractedProduct(name="The 5 best headphones of 2026"),
            ExtractedProduct(name="Bonavita Gooseneck Kettle", price=80.0, currency="USD"),
            ExtractedProduct(name="Sony WH-1000XM5", price=328.0, currency="USD"),
            ExtractedProduct(name="Anker Soundcore Q30", price=79.0, currency="USD"),
            ExtractedProduct(
                name="Anker Soundcore Q30 Wireless Headphones", price=79.0, currency="USD"
            ),
        ]
    )
    agent, _ = agent_factory(FakeLLM(products=found), search_results, max_price=200.0)
    removed: list = []

    agent.run("headphones", record=removed.append)

    assert [(removal.name, removal.step) for removal in removed] == [
        ("The 5 best headphones of 2026", "clean"),
        ("Bonavita Gooseneck Kettle", "ground"),
        ("Anker Soundcore Q30 Wireless Headphones", "merge"),
        ("Sony WH-1000XM5", "limits"),
    ]


def test_the_report_is_headed_by_the_order_the_run_ranked_in(
    agent_factory, search_results, extracted_products, caplog
) -> None:
    """``run`` hands the report the ``sort_by`` it ranked with, so the heading cannot
    say one order over a list in another."""
    agent, _ = agent_factory(FakeLLM(products=extracted_products), search_results)

    with caplog.at_level(logging.INFO, logger="buy_agent"):
        agent.run("headphones", sort_by="price")

    assert "PRODUCTS, CHEAPEST FIRST" in caplog.text


def test_the_report_names_the_weights_the_run_ranked_by(
    agent_factory, search_results, extracted_products, caplog
) -> None:
    """The score line puts each share beside its weight (ADR-0045), and a run blended
    on rating alone is read against that blend and not the default one."""
    agent, _ = agent_factory(
        FakeLLM(products=extracted_products), search_results,
        weights=RankingWeights(rating=1.0, popularity=0.0, price=0.0),
    )

    with caplog.at_level(logging.INFO, logger="buy_agent"):
        agent.run("headphones")

    assert "rating 0.94 x1.00" in caplog.text


# -- the currency a run counts itself in (ADR-0056) ---------------------------


def test_the_named_currency_reaches_the_ranking(
    agent_factory, search_results, extracted_products
) -> None:
    """The scale is the config's, and the pipeline is handed it rather than reading it."""
    agent, _ = agent_factory(FakeLLM(products=extracted_products), search_results, currency="JPY")

    ranked = agent.run("headphones")

    assert ranked, "the run still reports what it found"
    assert all("price" in entry.breakdown.neutral for entry in ranked)


def test_a_currency_nothing_is_priced_in_is_said_out_loud(
    agent_factory, search_results, extracted_products, caplog
) -> None:
    """The one way to ask for a report whose price criterion is entirely assumed, so
    the run says so rather than quietly ranking on two criteria (ADR-0056)."""
    agent, _ = agent_factory(FakeLLM(products=extracted_products), search_results, currency="JPY")

    with caplog.at_level(logging.WARNING, logger="buy_agent.agent"):
        agent.run("headphones")

    assert "Nothing found is priced in JPY" in caplog.text


# -- the search backend a run asks (ADR-0057) ---------------------------------


def test_the_run_s_backend_is_the_one_the_search_is_asked_through(monkeypatch) -> None:
    """``AgentConfig.search_backend`` is the only place a name becomes behaviour."""
    asked: list[object] = []

    def fake_search(query: str, **kwargs) -> list:
        asked.append(kwargs["backend"])
        return []

    monkeypatch.setattr("buy_agent.agent.search_web", fake_search)
    BuyAgent(AgentConfig(backend="searxng"), llm=FakeLLM()).run("headphones")

    assert [backend.name for backend in asked] == ["searxng"]


# -- a declared page, through the real fetch, to the report (ADR-0078, ADR-0079) -


#: A shop page that prints nothing visible but its name: every figure is declared.
DECLARING_PAGE = """<html><head><script type="application/ld+json">
{"@context": "https://schema.org", "@type": "Product", "name": "Sony WH-1000XM5",
 "offers": {"@type": "Offer", "price": "299.00", "priceCurrency": "USD",
            "availability": "https://schema.org/InStock",
            "itemCondition": "https://schema.org/RefurbishedCondition"},
 "aggregateRating": {"@type": "AggregateRating", "ratingValue": "4.6", "reviewCount": 3200}}
</script></head><body><h1>Sony WH-1000XM5</h1><p>Free returns.</p></body></html>"""


@pytest.fixture
def declaring_shop(monkeypatch):
    """One search result whose page is ``DECLARING_PAGE``, fetched for real."""
    url = "https://shop.example/xm5"
    monkeypatch.setattr(
        "buy_agent.agent.search_web",
        lambda *_args, **_kwargs: [SearchResult(title="Sony WH-1000XM5", url=url)],
    )
    stub_client(monkeypatch, lambda asked: make_response(asked, DECLARING_PAGE))
    return url


def _sony(**fields) -> ProductList:
    return ProductList(products=[ExtractedProduct(name="Sony WH-1000XM5", **fields)])


def test_a_declared_offer_is_shown_to_the_model_and_survives_grounding(declaring_shop) -> None:
    """The figures exist only in the page's JSON-LD, so without ADR-0078 the model
    never saw them and grounding blanked whatever it said."""
    llm = FakeLLM(
        products=_sony(
            price=299.0, currency="USD", rating=4.6, review_count=3200,
            availability="in stock", condition="refurbished",
        )
    )
    agent = BuyAgent(AgentConfig(cache_ttl=0), llm=llm)

    [entry] = agent.run("headphones")

    shown = str(llm.calls[-1])
    assert "Sony WH-1000XM5: 299.00 USD, in stock, condition: refurbished" in shown
    assert "rated 4.6/5 from 3200 reviews" in shown
    product = entry.product
    assert (product.price, product.currency, product.rating, product.review_count) == (
        299.0, "USD", 4.6, 3200,
    )
    assert product.url == declaring_shop
    assert product.listing_label() == "In stock, refurbished"
    assert product.offers[0].condition == "refurbished"


def test_a_standing_the_page_never_declared_is_blanked_in_a_run(declaring_shop) -> None:
    """The model says "used, sold out"; the page says refurbished and in stock."""
    llm = FakeLLM(
        products=_sony(price=299.0, currency="USD", availability="sold out", condition="used")
    )

    [entry] = BuyAgent(AgentConfig(cache_ttl=0), llm=llm).run("headphones")

    assert entry.product.price == 299.0
    assert (entry.product.availability, entry.product.condition) == (None, None)


def test_a_price_the_page_never_declared_takes_its_standing_with_it(declaring_shop) -> None:
    """ADR-0022 through a whole run: the stock described the price that went."""
    llm = FakeLLM(
        products=_sony(
            price=249.0, currency="USD", availability="in stock", condition="refurbished"
        )
    )

    [entry] = BuyAgent(AgentConfig(cache_ttl=0), llm=llm).run("headphones")

    assert entry.product.price is None
    assert entry.product.listing_label() is None


def _a_shop_printing(monkeypatch, declaration: str) -> None:
    """One search result, fetched for real, whose page declares ``declaration`` and
    prints the Sony's price beside it."""
    url = "https://shop.example/xm5"
    page = (
        f'<html><head><script type="application/ld+json">{declaration}</script></head>'
        f"<body><p>Sony WH-1000XM5 for $299</p></body></html>"
    )
    monkeypatch.setattr(
        "buy_agent.agent.search_web",
        lambda *_args, **_kwargs: [SearchResult(title="Sony WH-1000XM5", url=url)],
    )
    stub_client(monkeypatch, lambda asked: make_response(asked, page))


@pytest.mark.parametrize(
    "declaration",
    [
        '{"@type": "Product", "name": "Sony WH-1000XM5", "offers": {"price": 1'
        + "0" * 400
        + "}}",
        "[" * 100_000,
    ],
    ids=["a price past a float's range", "nested past the decoder"],
)
def test_a_declaration_nobody_can_read_leaves_the_page_it_is_on(
    monkeypatch, declaration: str
) -> None:
    """Each raised past ``fetch``'s tally -- an ``OverflowError``, a ``RecursionError`` --
    and ended the whole run over one page; skipped, the page's own text still prices it."""
    _a_shop_printing(monkeypatch, declaration)
    llm = FakeLLM(products=_sony(price=299.0, currency="USD"))

    [entry] = BuyAgent(AgentConfig(cache_ttl=0), llm=llm).run("headphones")

    assert entry.product.price == 299.0


def test_a_name_declared_cut_mid_emoji_reaches_the_model_as_text_it_can_send(monkeypatch) -> None:
    """``json`` reads the ``"\\ud83c"`` as half a pair, which the clients cannot encode
    as UTF-8, and the run ended as "Could not reach Ollama"."""
    _a_shop_printing(
        monkeypatch,
        '{"@type": "Product", "name": "Sony WH-1000XM5 \\ud83c", '
        '"offers": {"price": 299, "priceCurrency": "USD"}}',
    )
    llm = FakeLLM(products=_sony(price=299.0, currency="USD"))

    BuyAgent(AgentConfig(cache_ttl=0), llm=llm).run("headphones")

    shown = llm.calls[-1][1]["content"]
    assert "Sony WH-1000XM5 \ufffd: 299.00 USD" in shown
    for messages in llm.calls:
        # As the clients send a prompt: a JSON body, in UTF-8.
        assert json.dumps(list(messages), ensure_ascii=False).encode("utf-8")
