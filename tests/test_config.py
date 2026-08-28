"""The config is where every default lives -- the CLI reads its flag defaults off it."""

from __future__ import annotations

import dataclasses
import importlib

import pytest

import buy_agent.config as config_module
from buy_agent.config import (
    PROVIDER_DEFAULTS,
    VLLM_BASE_URL,
    VLLM_MODEL,
    AgentConfig,
    provider_options,
)
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

    assert (ollama.model, ollama.base_url) == PROVIDER_DEFAULTS["ollama"]
    assert (vllm.model, vllm.base_url) == (VLLM_MODEL, VLLM_BASE_URL)


def test_a_named_model_and_server_are_left_alone() -> None:
    config = AgentConfig(provider="vllm", model="a-model", base_url="http://gpu:8000/v1")

    assert (config.model, config.base_url) == ("a-model", "http://gpu:8000/v1")


def test_a_provider_nothing_can_serve_is_refused_where_it_is_set() -> None:
    """Not at the first request: a typo in $BUY_AGENT_PROVIDER should fail when
    the config is built, not a minute into a run that has already searched."""
    with pytest.raises(ValueError, match="Unknown provider 'llama.cpp'"):
        AgentConfig(provider="llama.cpp")


def test_every_provider_offers_its_defaults_to_the_form() -> None:
    """One row per provider, each carrying the pair to fill the fields in with."""
    options = {option["name"]: option for option in provider_options()}

    assert set(options) == set(PROVIDER_DEFAULTS)
    for name, (model, base_url) in PROVIDER_DEFAULTS.items():
        assert (options[name]["model"], options[name]["base_url"]) == (model, base_url)
        assert options[name]["label"], "a provider with no label is one nobody can read"


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

    assert reloaded.AgentConfig().model == "gemma4:12b"
    assert reloaded.AgentConfig().base_url == "http://localhost:11434"


def test_vllm_has_its_own_pair_of_variables(reloaded_config) -> None:
    """One machine can have both servers, so one pair of variables could not name
    both -- $OLLAMA_HOST moving the vLLM address would be nonsense."""
    reloaded = reloaded_config(
        VLLM_MODEL="meta-llama/Llama-3.1-8B", VLLM_HOST="http://gpu.internal:8000/v1"
    )
    config = reloaded.AgentConfig(provider="vllm")

    assert config.model == "meta-llama/Llama-3.1-8B"
    assert config.base_url == "http://gpu.internal:8000/v1"


def test_the_vllm_defaults_are_a_local_server_too(reloaded_config, monkeypatch) -> None:
    """Port 8000 and the ``/v1`` the OpenAI API is served under, which is what
    ``vllm serve`` gives you with no arguments."""
    for name in ("VLLM_MODEL", "VLLM_HOST"):
        monkeypatch.delenv(name, raising=False)

    reloaded = reloaded_config()

    assert reloaded.AgentConfig(provider="vllm").base_url == "http://localhost:8000/v1"


def test_the_provider_itself_can_be_set_from_the_environment(reloaded_config) -> None:
    """So a machine that only runs vLLM never types --provider, the same way one
    with a favourite tag never types --model."""
    reloaded = reloaded_config(BUY_AGENT_PROVIDER="vllm")

    assert reloaded.AgentConfig().provider == "vllm"
    assert reloaded.AgentConfig().base_url == reloaded.VLLM_BASE_URL


def test_the_api_key_is_read_from_the_environment(reloaded_config) -> None:
    """The one setting with no flag and no form field: it is a secret, so it does
    not land in a shell history and is not in what the API hands a browser. The
    other two halves of that are asserted where those two are built."""
    reloaded = reloaded_config(VLLM_API_KEY="s3cret")

    assert reloaded.AgentConfig().api_key == "s3cret"


def test_no_key_is_the_default(reloaded_config, monkeypatch) -> None:
    """Most vLLMs are started without one, and a placeholder is what the provider
    sends in that case rather than a value anybody has to set."""
    monkeypatch.delenv("VLLM_API_KEY", raising=False)

    assert reloaded_config().AgentConfig().api_key == ""
