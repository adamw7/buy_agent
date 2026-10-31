"""The config is where every default lives -- the CLI reads its flag defaults off it."""

from __future__ import annotations

import importlib
from dataclasses import asdict

import pytest

import buy_agent.config as config_module
import buy_agent.providers as providers_module
from buy_agent.config import AgentConfig, ServerEnvironment, server_environment

# The rows are reached through the module rather than imported by name, because
# tests/test_providers.py reloads it: a reload re-runs the module over its own globals, so
# ``provider_for`` goes on answering with whatever is in the table *now* while a name
# imported here would still hold the row from before.


# -- the region, the one search setting a typo makes look like an empty web ----


def test_a_config_holds_the_region_to_the_same_shape() -> None:
    """One dataclass every door builds, so a Python caller gets the same answer."""
    assert AgentConfig(region="PL-PL").region == "pl-pl"

    with pytest.raises(ValueError, match="is not a search region"):
        AgentConfig(region="en_us")


@pytest.fixture
def reloaded_config(monkeypatch):
    """Re-import the module so ``$BUY_AGENT_PROVIDER`` is read again."""

    def reload(**environment: str):
        for name, value in environment.items():
            monkeypatch.setenv(name, value)
        return importlib.reload(config_module)

    yield reload
    monkeypatch.undo()
    importlib.reload(config_module)


def test_the_provider_itself_can_be_set_from_the_environment(reloaded_config) -> None:
    """So a machine that only runs vLLM never types --provider, the same way one
    with a favourite tag never types --model."""
    reloaded = reloaded_config(BUY_AGENT_PROVIDER="vllm")

    assert reloaded.AgentConfig().provider == "vllm"
    assert reloaded.AgentConfig().base_url == providers_module.VLLM.base_url


def test_tensorrt_llm_can_be_the_provider_the_environment_names(reloaded_config) -> None:
    reloaded = reloaded_config(BUY_AGENT_PROVIDER="trtllm")

    assert reloaded.AgentConfig().provider == "trtllm"
    assert reloaded.AgentConfig().base_url == providers_module.TRTLLM.base_url


# -- paying --------------------------------------------------------------------


def test_a_rail_nothing_can_pay_through_is_refused_where_a_provider_would_be() -> None:
    with pytest.raises(ValueError, match="Unknown payment rail"):
        AgentConfig(rail="paypal")


def test_a_trailing_slash_is_dropped_so_a_rail_never_builds_a_double_one() -> None:
    assert (
        AgentConfig(pay=True, rail="http", merchant_url="https://pay.example/").merchant_url
        == "https://pay.example"
    )


# -- the currency a run counts itself in (ADR-0056) ----------------------------


def test_the_refusal_names_the_currencies_that_would_have_worked() -> None:
    with pytest.raises(ValueError, match="PLN"):
        AgentConfig(currency="XXX")


# -- the search backend a run asks (ADR-0057) ---------------------------------


def test_a_backend_nothing_can_search_is_refused_where_the_config_is_built() -> None:
    """A minute into a run is the wrong place to find out (ADR-0057)."""
    with pytest.raises(ValueError, match="Unknown search backend 'bing'"):
        AgentConfig(backend="bing")


# -- how many pages a run searches ----------------------------------------------


def test_a_run_searches_at_least_as_many_pages_as_it_reports() -> None:
    """Searching fewer pages than the run finds or shows would cap the report, so an unset
    width is the larger of the two -- for both doors and a Python caller alike."""
    assert AgentConfig(num_products=3, top_n=8).search_results == 8
    assert AgentConfig(num_products=12, top_n=3).search_results == 12
    assert AgentConfig(num_products=12, search_results=4).search_results == 4


def test_a_server_nobody_deployed_reads_nothing_off_its_environment() -> None:
    # Field by field: a reload elsewhere in the suite makes the class a new one.
    assert asdict(server_environment()) == asdict(ServerEnvironment())


def test_a_deployed_server_reads_its_settings_when_it_starts(monkeypatch) -> None:
    """At the call, not at import: the image sets them before the process starts, and a
    test after it."""
    for name, value in {
        "BUY_AGENT_HOST": " 0.0.0.0 ",
        "BUY_AGENT_PORT": "8080",
        "BUY_AGENT_ALLOWED_HOSTS": "shop.example,  buy.example\tother.example",
        "BUY_AGENT_MAX_RUNS": "2",
        "BUY_AGENT_BEHIND_PROXY": "TRUE",
        "BUY_AGENT_ACCESS_LOG": "on",
        "BUY_AGENT_LOG_FORMAT": "json",
        "BUY_AGENT_API_TOKEN": " a token long enough to keep ",
    }.items():
        monkeypatch.setenv(name, value)

    assert asdict(server_environment()) == asdict(ServerEnvironment(
        host="0.0.0.0", port="8080", allowed_hosts=("shop.example", "buy.example", "other.example"),
        max_runs="2", behind_proxy=True, access_log=True, log_format="json",
        token="a token long enough to keep",
    ))


@pytest.mark.parametrize("value", ["", "0", "no", "off", "false", "enabled"])
def test_a_switch_is_on_only_when_it_says_so(monkeypatch, value: str) -> None:
    monkeypatch.setenv("BUY_AGENT_BEHIND_PROXY", value)

    assert server_environment().behind_proxy is False


def test_the_token_is_kept_out_of_a_logged_environment(monkeypatch) -> None:
    monkeypatch.setenv("BUY_AGENT_API_TOKEN", "a token long enough to keep")

    assert "long enough" not in repr(server_environment())
