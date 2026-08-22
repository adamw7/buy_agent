"""The config is where every default lives -- the CLI reads its flag defaults off it."""

from __future__ import annotations

import dataclasses
import importlib

import pytest

import buy_agent.config as config_module
from buy_agent.config import AgentConfig
from buy_agent.ranking import RankingWeights


def test_defaults_are_ten_results_ten_products_and_a_top_three() -> None:
    config = AgentConfig()

    assert config.search_results == 10
    assert config.num_products == 10
    assert config.top_n == 3
    assert config.region == "us-en"


def test_extraction_is_a_copying_task_not_a_creative_one() -> None:
    assert AgentConfig().temperature == 0.0


def test_pages_are_fetched_by_default() -> None:
    """Snippets alone rarely quote a price, and a model asked to fill that gap invents one."""
    config = AgentConfig()

    assert config.fetch_pages is True
    assert config.page_chars == 1200
    assert config.fetch_timeout == 8.0


def test_context_and_thinking_default_to_leaving_the_model_alone() -> None:
    """None means "send nothing": a model that cannot think must not be told to."""
    config = AgentConfig()

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
    """Re-import the module so its environment-derived defaults are read again."""

    def reload(**environment: str):
        for name, value in environment.items():
            monkeypatch.setenv(name, value)
        return importlib.reload(config_module)

    yield reload
    monkeypatch.undo()
    importlib.reload(config_module)


def test_the_model_and_host_can_be_set_from_the_environment(reloaded_config) -> None:
    reloaded = reloaded_config(
        OLLAMA_MODEL="qwen2.5:7b", OLLAMA_HOST="http://ollama.internal:11434"
    )

    assert reloaded.AgentConfig().model == "qwen2.5:7b"
    assert reloaded.AgentConfig().base_url == "http://ollama.internal:11434"


def test_the_environment_defaults_fall_back_to_a_local_ollama(reloaded_config, monkeypatch) -> None:
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)

    reloaded = reloaded_config()

    assert reloaded.AgentConfig().model == "llama3.2"
    assert reloaded.AgentConfig().base_url == "http://localhost:11434"
