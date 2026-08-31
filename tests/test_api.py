"""Reading options off a web request, and shaping the answer as JSON."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from buy_agent.agent import ModelUnavailableError
from buy_agent.api import (
    ApiError,
    defaults_payload,
    installed_models,
    limits_payload,
    parse_options,
    product_payload,
    run_search,
    sources_payload,
)
from buy_agent.config import LIMITS, AgentConfig
from buy_agent.models import Product, RankedProduct
from buy_agent.providers import VLLM
from buy_agent.search import SearchError
from buy_agent.sources import Source

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
            opinions=["the noise cancelling is uncanny"],
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


# -- the region, which is the one option a typo makes look like an empty web ---


def test_a_region_that_is_not_a_region_is_a_400_saying_what_is() -> None:
    """A form field with no closed set behind it, so the refusal carries the shape
    -- otherwise the run comes back "search returned nothing" (ADR-0031)."""
    with pytest.raises(ApiError) as failure:
        parse_options({"region": "en_us"})

    assert failure.value.status == 400
    assert "us-en" in str(failure.value)


def test_a_region_is_lower_cased_the_way_the_CLI_lower_cases_it() -> None:
    assert parse_options({"region": "PL-PL"})[0].region == "pl-pl"


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_region_field_is_the_default_region(blank: str) -> None:
    """A cleared field means "unset", not "search nowhere"."""
    assert parse_options({"region": blank})[0].region == AgentConfig().region


# -- the sources, which are the one option that is a list ----------------------


def test_no_sources_asked_for_is_the_whole_web() -> None:
    assert parse_options({})[0].sources == ()


@pytest.mark.parametrize("blank", ["", "   ", []])
def test_an_empty_sources_field_is_the_whole_web_too(blank) -> None:
    """A cleared form field means "unset", the same as every other option."""
    assert parse_options({"sources": blank})[0].sources == ()


def test_several_sources_arrive_as_one_separated_string() -> None:
    """Which is all a query string can carry, and what the form's field holds."""
    config, _ = parse_options({"sources": "rtings.com, @mkbhd"})

    assert config.sources == (
        Source(spec="rtings.com", domain="rtings.com"),
        Source(spec="@mkbhd", domain="youtube.com", term="@mkbhd"),
    )


def test_a_json_body_may_send_them_as_an_array_instead() -> None:
    """The one option ``_read`` cannot handle: it renders every value with ``str``
    first, which would turn a JSON array into its Python repr."""
    config, _ = parse_options({"sources": ["rtings.com", "@mkbhd"]})

    assert [source.domain for source in config.sources] == ["rtings.com", "youtube.com"]


def test_a_source_that_names_no_site_is_a_400_saying_what_would_work() -> None:
    with pytest.raises(ApiError) as failure:
        parse_options({"sources": "Marques Brownlee"})

    assert failure.value.status == 400
    assert "@mkbhd" in str(failure.value)


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


def test_a_native_json_boolean_is_taken_as_it_is() -> None:
    """A JSON body carries real booleans; only a query string turns them into text."""
    config, _ = parse_options({"fetch": False, "think": True})

    assert config.fetch_pages is False
    assert config.reasoning is True


@pytest.mark.parametrize("yes", ["true", "1", "yes", "on", "TRUE", " On "])
def test_a_flag_can_be_turned_on_in_any_of_the_spellings_a_form_sends(yes: str) -> None:
    """A checkbox reaches the query string as one of several words for the same thing."""
    config, _ = parse_options({"think": yes, "fetch": yes})

    assert config.reasoning is True
    assert config.fetch_pages is True


def test_searching_covers_the_wider_of_results_and_top() -> None:
    """Reporting more products than were searched for would cap the report."""
    config, _ = parse_options({"results": 3, "top": 8})
    assert config.search_results == 8


@pytest.mark.parametrize(
    ("data", "field", "expected"),
    [
        ({"results": 1}, "num_products", 1),
        ({"results": 50}, "num_products", 50),
        ({"top": 1}, "top_n", 1),
        ({"top": 50}, "top_n", 50),
        ({"num_ctx": 1}, "num_ctx", 1),
        ({"num_ctx": 1_000_000}, "num_ctx", 1_000_000),
        ({"temperature": 0.0}, "temperature", 0.0),
        ({"temperature": 2.0}, "temperature", 2.0),
    ],
)
def test_both_ends_of_a_range_are_inside_it(data: dict, field: str, expected) -> None:
    """The bounds are inclusive, which is only true while something checks it."""
    config, _ = parse_options(data)

    assert getattr(config, field) == expected


@pytest.mark.parametrize(
    "data",
    [
        {"results": 51},
        {"top": 51},
        {"num_ctx": 0},
        {"num_ctx": 1_000_001},
        {"temperature": 2.1},
        {"temperature": -0.1},
    ],
)
def test_one_step_outside_a_range_is_rejected(data: dict) -> None:
    with pytest.raises(ApiError):
        parse_options(data)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"results": 51}, "results must be between 1 and 50; got 51."),
        ({"top": 0}, "top must be between 1 and 50; got 0."),
        ({"num_ctx": 0}, "num_ctx must be between 1 and 1000000; got 0."),
        ({"results": "many"}, "results must be a whole number; got 'many'."),
        ({"temperature": 2.5}, "temperature must be between 0 and 2; got 2.5."),
        ({"temperature": "hot"}, "temperature must be a number; got 'hot'."),
        ({"think": "maybe"}, "think must be true or false; got 'maybe'."),
        ({"sort_by": "cheapness"}, "sort_by must be one of score, price, rating; got 'cheapness'."),
        ({"provider": "llama.cpp"}, "provider must be one of ollama, vllm; got 'llama.cpp'."),
    ],
)
def test_a_rejection_says_what_was_wrong_and_what_was_wanted(data: dict, message: str) -> None:
    """The message is the whole of a 400 response, so it is checked word for word."""
    with pytest.raises(ApiError) as excinfo:
        parse_options(data)

    assert str(excinfo.value) == message


@pytest.mark.parametrize(
    "data",
    [
        {"sort_by": "cheapness"},
        {"provider": "llama.cpp"},
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


@pytest.mark.parametrize(
    ("data", "field"),
    [
        ({"results": 51}, "results"),
        ({"top": 0}, "top"),
        ({"temperature": 9}, "temperature"),
        ({"num_ctx": "wide"}, "num_ctx"),
        ({"think": "maybe"}, "think"),
        ({"fetch": "sometimes"}, "fetch"),
        ({"sort_by": "cheapness"}, "sort_by"),
        ({"provider": "llama.cpp"}, "provider"),
        ({"region": "en_US"}, "region"),
        ({"sources": "Marques Brownlee"}, "sources"),
    ],
)
def test_a_refusal_carries_the_field_it_was_about(data: dict, field: str) -> None:
    """Which box the sentence belongs under is answered here rather than read
    back out of the message: the browser marks that input instead of leaving a
    banner to be read against ten settings (ADR-0033)."""
    with pytest.raises(ApiError) as excinfo:
        parse_options(data)

    assert excinfo.value.field == field
    assert excinfo.value.payload() == {"error": str(excinfo.value), "field": field}


# -- the ranges the form is shipped --------------------------------------------


def test_the_limits_are_the_ones_both_doors_hold_a_request_to() -> None:
    """Shipped off ``config.LIMITS``, so the form cannot come to offer a number
    the API refuses -- which is the whole reason it is sent rather than written
    into the template."""
    limits = limits_payload()

    assert limits["results"] == {"min": LIMITS["num_products"][0], "max": LIMITS["num_products"][1]}
    assert limits["top"] == {"min": LIMITS["top_n"][0], "max": LIMITS["top_n"][1]}
    assert limits["temperature"] == {"min": 0, "max": 2}
    assert set(limits) == {"results", "top", "temperature", "num_ctx"}


def test_every_shipped_range_is_one_a_request_is_actually_held_to() -> None:
    """A range on the form that nothing enforced would be a promise, not a rule."""
    for key, limit in limits_payload().items():
        for outside in (limit["min"] - 1, limit["max"] + 1):
            with pytest.raises(ApiError) as excinfo:
                parse_options({key: outside})
            assert excinfo.value.field == key


def test_the_form_defaults_carry_the_ranges() -> None:
    assert defaults_payload()["limits"] == limits_payload()


# -- the sources check, which is the one rule the form cannot apply itself ------


def test_a_field_naming_sources_has_nothing_wrong_with_it() -> None:
    assert sources_payload("rtings.com @mkbhd") == {"sources": "rtings.com @mkbhd", "error": ""}


def test_an_empty_field_is_the_whole_web_and_fine() -> None:
    assert sources_payload("") == {"sources": "", "error": ""}


def test_a_field_naming_a_person_is_answered_with_the_sentence_the_CLI_prints() -> None:
    """The same `parse_sources` a run would have used, so the page never grows a
    second idea of what a source is."""
    answer = sources_payload("Marques Brownlee")

    assert answer["sources"] == "Marques Brownlee"
    assert "does not name a source" in answer["error"]
    assert "@mkbhd" in answer["error"]


def test_the_answer_names_the_spec_it_was_about() -> None:
    """The field is typed into while the answer is in flight, and an answer about
    text since typed over must not be shown against what replaced it."""
    assert sources_payload("  rtings.com  ")["sources"] == "  rtings.com  "


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


def test_a_product_carries_what_the_sources_said_about_it() -> None:
    """Quoted, not summarised: the browser shows words Python already grounded."""
    assert product_payload(RANKED[0])["opinions"] == ["the noise cancelling is uncanny"]
    assert product_payload(RANKED[1])["opinions"] == []


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
        (ModelUnavailableError("start it with: ollama serve"), 503),
        (SearchError("rate limited"), 502),
    ],
)
def test_each_failure_gets_the_status_it_deserves(error: Exception, status: int) -> None:
    """The agent raises exactly these three; each maps to something actionable."""
    captured = agent_returning(error)
    with pytest.raises(ApiError) as excinfo:
        run_search("headphones", AgentConfig(), agent_factory=captured["factory"])
    assert excinfo.value.status == status
    # No field: a model server that did not answer is nothing a form could have
    # refused, so there is no box for the page to mark.
    assert excinfo.value.payload() == {"error": str(error), "field": None}


# -- the rest ------------------------------------------------------------------


def test_a_request_can_name_the_provider_to_run_against() -> None:
    config, _ = parse_options({"provider": "vllm"})

    assert config.provider == "vllm"


def test_choosing_a_provider_brings_its_model_and_its_server_with_it() -> None:
    """A form that switched provider and left the two fields blank must not run
    an Ollama tag against a vLLM: blank means "this provider's own", not "the
    one the server happened to start on"."""
    config, _ = parse_options({"provider": "vllm"})

    assert config.model == VLLM.model
    assert config.base_url == VLLM.base_url


def test_a_named_model_still_wins_over_the_provider_default() -> None:
    config, _ = parse_options({"provider": "vllm", "model": "meta-llama/Llama-3.1-8B"})

    assert config.model == "meta-llama/Llama-3.1-8B"


def test_defaults_payload_matches_the_config() -> None:
    payload = defaults_payload()
    defaults = AgentConfig()
    assert payload["provider"] == defaults.provider
    assert payload["model"] == defaults.model
    assert payload["results"] == defaults.num_products
    assert payload["top"] == defaults.top_n
    assert payload["sort_options"] == ["score", "price", "rating"]
    # One text field holding all of them, which is what the form sends back.
    assert payload["sources"] == ""


def test_the_defaults_carry_every_provider_with_its_own_pair() -> None:
    """The picker fills the model and the server fields from these, so a provider
    that arrived without them would leave the other one's tag in the box."""
    options = {option["name"]: option for option in defaults_payload()["provider_options"]}

    assert set(options) == {"ollama", "vllm"}
    assert options["vllm"]["model"] == VLLM.model
    assert options["vllm"]["base_url"] == VLLM.base_url
    assert options["ollama"]["label"] == "Ollama"


def test_the_defaults_never_carry_the_api_key() -> None:
    """It is a secret read from $VLLM_API_KEY, and this payload is what the server
    hands every page that asks for the form -- including one on another origin
    that got past the guard by being a browser nobody expected."""
    assert "api_key" not in defaults_payload()


def test_the_defaults_say_which_providers_take_a_context_window() -> None:
    """The form disables the field for the one that does not, rather than sending
    a setting vLLM fixed when it started."""
    options = {option["name"]: option for option in defaults_payload()["provider_options"]}

    assert options["ollama"]["takes_num_ctx"] is True
    assert options["vllm"]["takes_num_ctx"] is False


def test_installed_models_lists_what_ollama_has(monkeypatch) -> None:
    """Each model with what it can do beside it: Ollama holds embedding-only tags
    and a listing of bare names offers them as if a run could use one (ADR-0032)."""

    class FakeModel:
        def __init__(self, model):
            self.model = model

    class FakeList:
        models = [FakeModel("llama3.2"), FakeModel("nomic-embed-text"), FakeModel("")]

    class FakeClient:
        def __init__(self, base_url, **_kwargs):
            self.base_url = base_url

        def list(self):
            return FakeList()

        @staticmethod
        def show(name):
            capability = "embedding" if "embed" in name else "completion"
            return SimpleNamespace(capabilities=[capability])

    monkeypatch.setattr("buy_agent.providers.Client", FakeClient)
    assert installed_models("ollama", "http://localhost:11434") == {
        "provider": "ollama",
        "label": "Ollama",
        "base_url": "http://localhost:11434",
        "reachable": True,
        "models": [
            {"name": "llama3.2", "completion": True},
            {"name": "nomic-embed-text", "completion": False},
        ],
    }


def test_installed_models_asks_vllm_what_it_is_serving(monkeypatch) -> None:
    """The other provider, over the same endpoint: one server, one model, one list."""
    monkeypatch.setattr(
        "buy_agent.providers.httpx.get", _serving(["Qwen/Qwen3-8B"])
    )

    assert installed_models("vllm", "http://localhost:8000/v1") == {
        "provider": "vllm",
        "label": "vLLM",
        "base_url": "http://localhost:8000/v1",
        "reachable": True,
        "models": [{"name": "Qwen/Qwen3-8B", "completion": True}],
    }


def test_a_provider_nothing_can_serve_is_a_status_too(monkeypatch) -> None:
    """A name the picker could not have produced still reaches the form as a
    status rather than a 500 -- the pill is already the place that says why."""
    payload = installed_models("llama.cpp", "http://localhost:8080")

    assert payload["reachable"] is False
    assert payload["label"] == "llama.cpp", "an unknown name is its own label"
    assert "llama.cpp" in payload["detail"]
    assert "hint" not in payload, "there is no provider to ask what to start"


def _serving(models: list[str]):
    """Stand in for ``httpx.get`` answering vLLM's ``/v1/models``."""

    class Response:
        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict:
            return {"data": [{"id": name} for name in models]}

    def get(url, **_kwargs):
        assert url.endswith("/models"), url
        return Response()

    return get


def test_an_unreachable_ollama_is_a_status_not_an_error(monkeypatch) -> None:
    """The form still renders when Ollama is down; it just says so."""

    def explode(base_url, **_kwargs):
        raise ConnectionError("connection refused")

    monkeypatch.setattr("buy_agent.providers.Client", explode)
    payload = installed_models("ollama", "http://localhost:11434")
    assert payload["reachable"] is False
    assert payload["models"] == []
    assert "refused" in payload["detail"]


def test_an_unreachable_server_says_how_to_start_it(monkeypatch) -> None:
    """The reason alone leaves the shopper where they were: the pill is the one
    moment where the fix is a single command, and the provider already writes it.
    The sentence is Python's so the browser keeps deciding nothing."""

    def explode(base_url, **_kwargs):
        raise ConnectionError("connection refused")

    monkeypatch.setattr("buy_agent.providers.Client", explode)
    hint = installed_models("ollama", "http://localhost:11434")["hint"]

    assert "http://localhost:11434" in hint, "the address it could not reach"
    assert "connection refused" in hint, "and the transport's own reason with it"
    assert "ollama serve" in hint


def test_an_unreachable_vllm_is_told_to_start_a_vllm(monkeypatch) -> None:
    """The other provider's remedy is its own -- ``vllm serve``, not ``ollama
    serve`` -- which is the whole reason the sentence is asked of the row."""

    def explode(url, **_kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("buy_agent.providers.httpx.get", explode)
    payload = installed_models("vllm", "http://localhost:8000/v1")

    assert "vllm serve" in payload["hint"]
    assert "ollama" not in payload["hint"].lower()
