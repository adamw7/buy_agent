"""The failure paths, against a real server rather than a raised exception.

``BuyAgent._invoke`` catches four things, and the docstring explaining why is
the longest in the module: the ollama client turns a refused connection into a
builtin ``ConnectionError`` only on its non-streaming path, ``ChatOllama``
always chats over the streaming one, and so a stopped server arrives as a raw
``httpx`` error that is not an ``OSError``. The unit suite checks that claim by
raising each of those four itself -- which proves the ``except`` tuple contains
them, and nothing about whether they are what Ollama actually raises.

That is what these tests are for. They need no inference and cost nothing: a
refused connection and a model that is not pulled both answer immediately.

Two of the four are covered here and two are not. A stopped server (raw
``httpx``) and a live server answering 404 for an unknown model
(``ResponseError``) are both reachable from outside the agent. The
``httpx.TimeoutException`` branch of ``_ollama_hint`` -- the one that says to
try a smaller model or a smaller ``--num-ctx`` -- is not: nothing in
``AgentConfig`` sets a client timeout, so provoking it live would mean
constructing the ``ChatOllama`` here by hand, which is the duplication
``test_live_extraction`` just stopped doing. It stays a unit test raising the
exception itself until there is a config field to turn it down.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from buy_agent.agent import BuyAgent, OllamaUnavailableError, list_models
from buy_agent.api import ApiError, installed_models, run_search

if TYPE_CHECKING:
    from buy_agent.config import AgentConfig

#: A tag no registry has and nobody will pull by accident.
_MISSING_MODEL = "buy-agent-no-such-model:0b"


def test_ollama_lists_the_model_the_tests_are_running_on(
    tiny_model: str, base_url: str
) -> None:
    """``list_models`` is what names the installed models in the CLI's hint and
    fills the UI's model picker; both go through this one call."""
    assert tiny_model in list_models(base_url)


def test_the_model_picker_reports_a_reachable_server(
    tiny_model: str, base_url: str
) -> None:
    """``installed_models`` swallows every transport failure into ``reachable:
    False``, so on a broken client it answers exactly as it does on a stopped
    server -- and a unit test with a faked client cannot tell the two apart."""
    payload = installed_models(base_url)

    assert payload["reachable"] is True
    assert tiny_model in payload["models"]
    assert "detail" not in payload


def test_a_stopped_server_is_reported_as_something_to_start(
    live_config: AgentConfig, unreachable_base_url: str
) -> None:
    """The whole reason ``httpx.HTTPError`` is in the ``except`` tuple. Reached
    through ``run`` rather than through the chain, because query refinement is
    the one recoverable step and this is the failure it deliberately re-raises
    instead of falling back to the raw request."""
    agent = BuyAgent(replace(live_config, base_url=unreachable_base_url))

    with pytest.raises(OllamaUnavailableError) as excinfo:
        agent.run("noise cancelling headphones")

    assert "ollama serve" in str(excinfo.value)


def test_a_model_that_is_not_pulled_is_reported_with_a_pull_command(
    live_config: AgentConfig,
) -> None:
    """A live server answering 404 for an unknown model, which is a different
    failure from the server being absent and gets a different hint. The hint
    lists what *is* installed, which needs a second live call to be worth
    printing."""
    agent = BuyAgent(replace(live_config, model=_MISSING_MODEL))

    with pytest.raises(OllamaUnavailableError) as excinfo:
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
