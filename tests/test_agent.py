"""End-to-end agent behaviour with a fake model and a fake search backend."""

from __future__ import annotations

import logging
from time import sleep
from types import SimpleNamespace

import pytest
from ollama import ResponseError

from buy_agent.agent import BuyAgent, ModelUnavailableError, _asks_the_same_question
from buy_agent.cache import DEFAULT_TTL
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

        def fake_search(
            query: str,
            *,
            max_results: int = 10,
            region: str = "us-en",
            wait: object = None,
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


def test_no_extractable_products_yields_no_products(
    agent_factory, search_results, caplog
) -> None:
    agent, _ = agent_factory(FakeLLM(products=ProductList()), search_results)

    with caplog.at_level(logging.WARNING):
        assert agent.run("obscure thing") == []
    assert "No products could be extracted" in caplog.text


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
    unsupported = next(
        entry for entry in ranked if entry.product.name == "Unknown Brand Buds"
    )

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
    """Capture the request the Ollama provider puts a run's settings into."""
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
            return SimpleNamespace(
                message=SimpleNamespace(content='{"query": "a refined query"}')
            )

    monkeypatch.setattr("buy_agent.providers.Client", FakeClient)
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
                return SimpleNamespace(capabilities=["completion"])

            def close(self) -> None:
                """The listing lets go of the client it opened."""

        monkeypatch.setattr("buy_agent.providers.httpx.get", get)
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


def test_the_configured_weights_reach_the_ranking(
    agent_factory, search_results, extracted_products
) -> None:
    """The default blend puts the cheap Anker first; rating alone puts the Sony there."""
    agent, _ = agent_factory(
        FakeLLM(products=extracted_products),
        search_results,
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

    monkeypatch.setattr("buy_agent.providers.Client", FakeClient)

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
        FakeLLM(products=extracted_products),
        search_results,
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
    agent, _ = agent_factory(
        FakeLLM(products=extracted_products), search_results, currency="JPY"
    )

    ranked = agent.run("headphones")

    assert ranked, "the run still reports what it found"
    assert all("price" in entry.breakdown.neutral for entry in ranked)


def test_a_currency_nothing_is_priced_in_is_said_out_loud(
    agent_factory, search_results, extracted_products, caplog
) -> None:
    """The one way to ask for a report whose price criterion is entirely assumed, so
    the run says so rather than quietly ranking on two criteria (ADR-0056)."""
    agent, _ = agent_factory(
        FakeLLM(products=extracted_products), search_results, currency="JPY"
    )

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
