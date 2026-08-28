"""The config is where every default lives -- the CLI reads its flag defaults off it."""

from __future__ import annotations

import dataclasses
import importlib

import pytest

import buy_agent.config as config_module
from buy_agent.config import AgentConfig
from buy_agent.providers import OLLAMA, VLLM
from buy_agent.ranking import RankingWeights


def test_defaults_are_ten_results_ten_products_and_a_top_three() -> None:
    config = AgentConfig()

    assert config.search_results == 10
    assert config.num_products == 10
    assert config.top_n == 3
    assert config.region == "us-en"


def test_the_default_provider_is_the_one_the_readme_starts_with() -> None:
    """Ollama, because that is the run the README's first transcript is of."""
    assert AgentConfig().provider == "ollama"


def test_an_unset_model_and_server_come_from_the_provider() -> None:
    """The pair cannot be a plain field default: which value is right depends on
    a sibling field, so the empty string is the "unset" that gets resolved."""
    ollama, vllm = AgentConfig(), AgentConfig(provider="vllm")

    assert (ollama.model, ollama.base_url) == (OLLAMA.model, OLLAMA.base_url)
    assert (vllm.model, vllm.base_url) == (VLLM.model, VLLM.base_url)


def test_the_provider_name_resolves_to_the_behaviour_behind_it() -> None:
    """``model_server`` is the one way anything reaches a provider, which is what
    keeps the agent, the API and the CLI from branching on a name (ADR-0029)."""
    assert AgentConfig().model_server is OLLAMA
    assert AgentConfig(provider="vllm").model_server is VLLM


def test_a_named_model_and_server_are_left_alone() -> None:
    config = AgentConfig(provider="vllm", model="a-model", base_url="http://gpu:8000/v1")

    assert (config.model, config.base_url) == ("a-model", "http://gpu:8000/v1")


def test_a_provider_nothing_can_serve_is_refused_where_it_is_set() -> None:
    """Not at the first request: a typo in $BUY_AGENT_PROVIDER should fail when
    the config is built, not a minute into a run that has already searched."""
    with pytest.raises(ValueError, match="Unknown provider 'llama.cpp'"):
        AgentConfig(provider="llama.cpp")


def test_extraction_is_a_copying_task_not_a_creative_one() -> None:
    assert AgentConfig().temperature == 0.0


def test_pages_are_fetched_by_default() -> None:
    """Snippets alone rarely quote a price, and a model asked to fill that gap invents one."""
    config = AgentConfig()

    assert config.fetch_pages is True
    assert config.page_chars == 1200
    assert config.fetch_timeout == 8.0


def test_context_and_thinking_default_to_suiting_the_default_model() -> None:
    """DEFAULT_MODEL thinks, so out of the box it is told not to, and given room."""
    config = AgentConfig()

    assert config.num_ctx == 8192
    assert config.reasoning is False


def test_context_and_thinking_can_still_be_left_to_the_model() -> None:
    """None means "send nothing", which is what another model may want."""
    config = AgentConfig(num_ctx=None, reasoning=None)

    assert config.num_ctx is None
    assert config.reasoning is None


def test_each_config_gets_its_own_weights() -> None:
    """A shared default would let one run's tuning leak into the next."""
    first, second = AgentConfig(), AgentConfig()

    assert first.weights == second.weights
    assert first.weights is not second.weights


def test_weights_can_be_replaced_wholesale() -> None:
    weights = RankingWeights(rating=1.0, popularity=0.0, price=0.0)

    assert AgentConfig(weights=weights).weights is weights


def test_the_default_weights_sum_to_one() -> None:
    assert RankingWeights().total == pytest.approx(1.0)


def test_total_follows_whatever_weights_it_is_given() -> None:
    assert RankingWeights(rating=0.5, popularity=0.5, price=1.0).total == pytest.approx(2.0)


def test_weights_are_frozen() -> None:
    """Scoring reads them per product; a mid-run edit would rank on two scales."""
    with pytest.raises(dataclasses.FrozenInstanceError):
        RankingWeights().rating = 0.9


def test_a_misspelled_field_is_rejected_rather_than_silently_added() -> None:
    """slots=True: config.top = 5 must fail loudly, not shadow top_n."""
    config = AgentConfig()

    with pytest.raises(AttributeError):
        config.top = 5


def test_an_unknown_keyword_is_rejected() -> None:
    with pytest.raises(TypeError):
        AgentConfig(temperatur=0.5)


@pytest.fixture
def reloaded_config(monkeypatch):
    """Re-import the module so ``$BUY_AGENT_PROVIDER`` is read again.

    The only environment variable left here: what each *server* defaults to is
    its row in :data:`buy_agent.providers.PROVIDERS`, and is reloaded there.
    """

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
    assert reloaded.AgentConfig().base_url == VLLM.base_url
