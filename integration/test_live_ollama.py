"""The failure paths, against a real server rather than a raised exception."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from buy_agent.agent import BuyAgent, ModelUnavailableError
from buy_agent.api import ApiError, installed_models, run_search

if TYPE_CHECKING:
    from buy_agent.config import AgentConfig

#: A tag no registry has and nobody will pull by accident.
_MISSING_MODEL = "buy-agent-no-such-model:0b"


def test_ollama_lists_the_model_the_tests_are_running_on(
    live_config: AgentConfig,
) -> None:
    """The provider's own listing is what names the installed models in the
    CLI's hint and fills the UI's model picker; both go through this one call."""
    installed = live_config.model_server.installed(live_config)

    assert live_config.model in [model.name for model in installed]


def test_a_live_listing_says_which_models_can_answer_a_prompt(
    live_config: AgentConfig,
) -> None:
    """The half of the listing no faked client can vouch for: ``capabilities`` is a real
    field of a real ``ollama show``, and a rename or a removal there would leave every
    unit test passing and the picker marking nothing (ADR-0032)."""
    installed = live_config.model_server.installed(live_config)
    entry = next(model for model in installed if model.name == live_config.model)

    assert entry.completion is True


def test_the_model_picker_reports_a_reachable_server(
    tiny_model: str, base_url: str
) -> None:
    """``installed_models`` swallows every transport failure into ``reachable:
    False``, so on a broken client it answers exactly as it does on a stopped
    server -- and a unit test with a faked client cannot tell the two apart."""
    payload = installed_models("ollama", base_url)

    assert payload["reachable"] is True
    assert tiny_model in [model["name"] for model in payload["models"]]
    assert "detail" not in payload


def test_a_stopped_server_is_reported_as_something_to_start(
    live_config: AgentConfig, unreachable_base_url: str
) -> None:
    """The whole reason ``httpx.HTTPError`` is in the ``except`` tuple."""
    agent = BuyAgent(replace(live_config, base_url=unreachable_base_url))

    with pytest.raises(ModelUnavailableError) as excinfo:
        agent.run("noise cancelling headphones")

    assert "ollama serve" in str(excinfo.value)


def test_a_model_that_is_not_pulled_is_reported_with_a_pull_command(
    live_config: AgentConfig,
) -> None:
    """A live server answering 404 for an unknown model, which is a different failure from
    the server being absent and gets a different hint."""
    agent = BuyAgent(replace(live_config, model=_MISSING_MODEL))

    with pytest.raises(ModelUnavailableError) as excinfo:
        agent.run("noise cancelling headphones")

    message = str(excinfo.value)
    assert f"ollama pull {_MISSING_MODEL}" in message
    assert live_config.model in message, "the hint should name what is installed"


def test_the_api_turns_an_absent_ollama_into_a_503(
    live_config: AgentConfig, unreachable_base_url: str
) -> None:
    """ADR-0009 end to end: the browser is told the service is unavailable rather
    than being handed a 500 and a traceback."""
    with pytest.raises(ApiError) as excinfo:
        run_search(
            "noise cancelling headphones",
            replace(live_config, base_url=unreachable_base_url),
        )

    assert excinfo.value.status == 503
