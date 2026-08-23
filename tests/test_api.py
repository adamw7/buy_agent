"""Reading options off a web request, and shaping the answer as JSON."""

from __future__ import annotations

import pytest

from buy_agent.agent import OllamaUnavailableError
from buy_agent.api import (
    ApiError,
    defaults_payload,
    installed_models,
    parse_options,
    product_payload,
    run_search,
)
from buy_agent.config import AgentConfig
from buy_agent.models import Product, RankedProduct
from buy_agent.search import SearchError

RANKED = [
    RankedProduct(
        product=Product(
            name="Sony WH-1000XM5",
            price=328.0,
            currency="USD",
            rating=4.7,
            review_count=12000,
            seller="Amazon",
            url="https://example.com/sony",
        ),
        score=0.912345,
        rank=1,
    ),
    RankedProduct(product=Product(name="Anker Q30"), score=0.5, rank=2),
]


def agent_returning(result):
    """An agent factory that records its config and answers with ``result``."""
    captured: dict = {}

    class Stub:
        def __init__(self, config):
            captured["config"] = config

        def run(self, request, *, sort_by="score"):
            captured["request"] = request
            captured["sort_by"] = sort_by
            if isinstance(result, BaseException):
                raise result
            return result

    captured["factory"] = Stub
    return captured


# -- parse_options -------------------------------------------------------------


def test_empty_request_data_gives_the_config_defaults() -> None:
    config, sort_by = parse_options({})
    assert config == AgentConfig()
    assert sort_by == "score"


def test_options_reach_the_config() -> None:
    config, sort_by = parse_options(
        {
            "model": "qwen2.5",
            "base_url": "http://elsewhere:11434",
            "results": 6,
            "top": 2,
            "region": "pl-pl",
            "temperature": 0.4,
            "num_ctx": 8192,
            "think": False,
            "fetch": False,
            "sort_by": "price",
        }
    )
    assert config.model == "qwen2.5"
    assert config.base_url == "http://elsewhere:11434"
    assert (config.num_products, config.top_n) == (6, 2)
    assert config.region == "pl-pl"
    assert config.temperature == 0.4
    assert config.num_ctx == 8192
    assert config.reasoning is False
    assert config.fetch_pages is False
    assert sort_by == "price"


def test_query_string_values_are_coerced_from_text() -> None:
    """A query string only ever yields strings; the JSON body yields real types."""
    config, sort_by = parse_options(
        {
            "results": "6",
            "top": "2",
            "temperature": "0.4",
            "num_ctx": "8192",
            "think": "false",
            "fetch": "no",
            "sort_by": "rating",
        }
    )
    assert (config.num_products, config.top_n, config.num_ctx) == (6, 2, 8192)
    assert config.temperature == 0.4
    assert config.reasoning is False
    assert config.fetch_pages is False
    assert sort_by == "rating"


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_field_means_unset_not_zero(blank: str) -> None:
    config, _ = parse_options({"model": blank, "num_ctx": blank, "think": blank})
    defaults = AgentConfig()
    assert config.model == defaults.model
    assert config.num_ctx is defaults.num_ctx
    assert config.reasoning is defaults.reasoning


def test_searching_covers_the_wider_of_results_and_top() -> None:
    """Reporting more products than were searched for would cap the report."""
    config, _ = parse_options({"results": 3, "top": 8})
    assert config.search_results == 8


@pytest.mark.parametrize(
    "data",
    [
        {"sort_by": "cheapness"},
        {"results": "many"},
        {"results": 0},
        {"results": 500},
        {"top": -1},
        {"temperature": "hot"},
        {"temperature": 9},
        {"num_ctx": "wide"},
        {"think": "maybe"},
        {"fetch": "sometimes"},
    ],
)
def test_unusable_values_are_rejected_with_a_message(data: dict) -> None:
    with pytest.raises(ApiError) as excinfo:
        parse_options(data)
    assert excinfo.value.status == 400
    assert str(excinfo.value)


def test_the_rejection_names_the_field() -> None:
    with pytest.raises(ApiError, match="num_ctx"):
        parse_options({"num_ctx": "wide"})


# -- run_search ----------------------------------------------------------------


def test_a_run_answers_with_ranked_products() -> None:
    captured = agent_returning(RANKED)
    payload = run_search(
        "  headphones  ",
        AgentConfig(top_n=2),
        sort_by="price",
        agent_factory=captured["factory"],
    )
    assert payload["request"] == "headphones"
    assert payload["count"] == 2
    assert payload["top_n"] == 2
    assert payload["sort_by"] == "price"
    assert [p["rank"] for p in payload["products"]] == [1, 2]
    assert captured["sort_by"] == "price"


def test_a_product_carries_both_the_figures_and_their_labels() -> None:
    """The browser should not have to reinvent how an unknown price reads."""
    payload = product_payload(RANKED[0])
    assert payload["price"] == 328.0
    assert payload["price_label"] == "328.00 USD"
    assert payload["rating_label"] == "4.7/5 (12,000 reviews)"
    assert payload["score"] == 0.9123  # rounded for display


def test_an_unknown_figure_is_labelled_not_hidden() -> None:
    payload = product_payload(RANKED[1])
    assert payload["price"] is None
    assert payload["price_label"] == "price unknown"
    assert payload["rating_label"] == "unrated"


def test_no_products_is_an_answer_not_a_failure() -> None:
    captured = agent_returning([])
    payload = run_search("nothing", AgentConfig(), agent_factory=captured["factory"])
    assert payload["count"] == 0
    assert payload["products"] == []


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (ValueError("the request is empty"), 400),
        (OllamaUnavailableError("start it with: ollama serve"), 503),
        (SearchError("rate limited"), 502),
    ],
)
def test_each_failure_gets_the_status_it_deserves(error: Exception, status: int) -> None:
    """The agent raises exactly these three; each maps to something actionable."""
    captured = agent_returning(error)
    with pytest.raises(ApiError) as excinfo:
        run_search("headphones", AgentConfig(), agent_factory=captured["factory"])
    assert excinfo.value.status == status
    assert excinfo.value.payload() == {"error": str(error)}


# -- the rest ------------------------------------------------------------------


def test_defaults_payload_matches_the_config() -> None:
    payload = defaults_payload()
    defaults = AgentConfig()
    assert payload["model"] == defaults.model
    assert payload["results"] == defaults.num_products
    assert payload["top"] == defaults.top_n
    assert payload["sort_options"] == ["score", "price", "rating"]


def test_installed_models_lists_what_ollama_has(monkeypatch) -> None:
    class FakeModel:
        def __init__(self, model):
            self.model = model

    class FakeList:
        models = [FakeModel("llama3.2"), FakeModel("lfm2.5"), FakeModel("")]

    class FakeClient:
        def __init__(self, base_url):
            self.base_url = base_url

        def list(self):
            return FakeList()

    monkeypatch.setattr("ollama.Client", FakeClient)
    assert installed_models("http://localhost:11434") == {
        "base_url": "http://localhost:11434",
        "reachable": True,
        "models": ["llama3.2", "lfm2.5"],
    }


def test_an_unreachable_ollama_is_a_status_not_an_error(monkeypatch) -> None:
    """The form still renders when Ollama is down; it just says so."""

    def explode(base_url):
        raise ConnectionError("connection refused")

    monkeypatch.setattr("ollama.Client", explode)
    payload = installed_models("http://localhost:11434")
    assert payload["reachable"] is False
    assert payload["models"] == []
    assert "refused" in payload["detail"]
