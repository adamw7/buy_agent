"""The config is where every default lives -- the CLI reads its flag defaults off it."""

from __future__ import annotations

import importlib

import pytest

import buy_agent.config as config_module
import buy_agent.providers as providers_module
from buy_agent.config import AgentConfig

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
