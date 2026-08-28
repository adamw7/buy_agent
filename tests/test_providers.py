"""The two model servers, and the four things each of them answers differently.

Nothing here opens a socket. The chat models are built and then read back rather
than called -- what is being checked is that a setting reaches the client at all,
which is the half no live test can see once it has gone wrong. The listings and
the failures are driven through fakes standing in for the two transports:
ollama's own ``Client`` for one, ``httpx.get`` for the other.

The failure messages are asserted on their *wording* rather than their type,
because the wording is the whole value of ``ModelUnavailableError``: a shopper
whose vLLM is down is told to run ``vllm serve``, and one whose Ollama has not
pulled a tag is told to run ``ollama pull``. Swap the two and the exception is
still raised, still a 503, and useless.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import openai
import pytest
from ollama import ResponseError

from buy_agent.config import AgentConfig
from buy_agent.providers import (
    OLLAMA,
    PROVIDERS,
    VLLM,
    build_chat_model,
    list_models,
    provider_for,
    transport_errors,
    unavailable_hint,
)

OLLAMA_CONFIG = AgentConfig(provider="ollama", model="gemma4:12b")
VLLM_CONFIG = AgentConfig(provider="vllm", model="Qwen/Qwen3-8B")

#: The OpenAI client's errors all carry the request that failed, so building one
#: takes a request. Which request is irrelevant here -- nothing sends it.
_REQUEST = httpx.Request("POST", "http://localhost:8000/v1/chat/completions")


@pytest.fixture
def serving(monkeypatch):
    """Stand in for vLLM answering ``GET /v1/models``, capturing what was asked."""
    asked: dict = {}

    def install(models: list[str], *, error: Exception | None = None) -> dict:
        class Response:
            @staticmethod
            def raise_for_status() -> None:
                return None

            @staticmethod
            def json() -> dict:
                # An entry with no id is one the picker cannot offer, the same
                # way a nameless Ollama tag is dropped on the other side.
                return {"data": [{"id": name} for name in models] + [{}]}

        def get(url, **kwargs):
            if error is not None:
                raise error
            asked.update(url=url, **kwargs)
            return Response()

        monkeypatch.setattr("buy_agent.providers.httpx.get", get)
        return asked

    return install


@pytest.fixture
def pulled(monkeypatch):
    """Stand in for ollama's ``Client``, so a listing never opens a socket."""

    def install(models: list[str], *, error: Exception | None = None) -> None:
        class FakeClient:
            def __init__(self, base_url: str) -> None:
                if error is not None:
                    raise error

            def list(self):
                return SimpleNamespace(
                    models=[SimpleNamespace(model=name) for name in models]
                )

        monkeypatch.setattr("buy_agent.providers.Client", FakeClient)

    return install


# -- the registry --------------------------------------------------------------


def test_a_provider_is_found_by_the_name_the_config_carries() -> None:
    assert provider_for("ollama") is OLLAMA
    assert provider_for("vllm") is VLLM


def test_an_unknown_provider_names_the_ones_that_exist() -> None:
    """This is reached from a flag, a form field and an environment variable, so
    the refusal has to be readable by someone who typed one of the three."""
    with pytest.raises(ValueError, match="Unknown provider 'llama.cpp'") as caught:
        provider_for("llama.cpp")

    for name in PROVIDERS:
        assert name in str(caught.value)


def test_only_one_of_them_takes_the_context_window_per_request() -> None:
    """vLLM fixes it with --max-model-len when it starts, so offering a per-run
    setting for it would be a field that quietly does nothing."""
    assert OLLAMA.takes_num_ctx is True
    assert VLLM.takes_num_ctx is False


# -- building the chat model ---------------------------------------------------


def test_ollama_is_given_the_window_and_the_thinking_switch() -> None:
    """Both are Ollama request options, and ADR-0019 is about them arriving."""
    llm = build_chat_model(
        AgentConfig(
            provider="ollama",
            model="qwen3.5:9b",
            base_url="http://ollama.internal:11434",
            temperature=0.2,
            num_ctx=8192,
            reasoning=False,
        )
    )

    assert (llm.model, llm.base_url) == ("qwen3.5:9b", "http://ollama.internal:11434")
    assert (llm.temperature, llm.num_ctx, llm.reasoning) == (0.2, 8192, False)


def test_vllm_is_pointed_at_the_openai_api_it_serves() -> None:
    llm = build_chat_model(
        AgentConfig(
            provider="vllm",
            model="Qwen/Qwen3-8B",
            base_url="http://gpu.internal:8000/v1",
            temperature=0.2,
        )
    )

    assert llm.model_name == "Qwen/Qwen3-8B"
    assert llm.openai_api_base == "http://gpu.internal:8000/v1"
    assert llm.temperature == 0.2


def test_vllm_is_never_sent_the_context_window() -> None:
    """It is not a request option there; sent anyway it would be rejected, and the
    run would fail for a setting the shopper could not have known was Ollama's."""
    llm = build_chat_model(AgentConfig(provider="vllm", num_ctx=8192))

    assert "num_ctx" not in llm.extra_body
    assert "num_ctx" not in llm.model_kwargs


@pytest.mark.parametrize("reasoning", [True, False])
def test_vllm_carries_the_thinking_switch_its_templates_read(reasoning: bool) -> None:
    """``enable_thinking`` is what the chat templates of the thinking models vLLM
    serves look for, and ADR-0019's default of off has to reach them."""
    llm = build_chat_model(AgentConfig(provider="vllm", reasoning=reasoning))

    assert llm.extra_body == {"chat_template_kwargs": {"enable_thinking": reasoning}}


def test_vllm_sends_nothing_when_thinking_is_left_alone() -> None:
    """The tri-state's third value: send nothing, and let the template decide."""
    llm = build_chat_model(AgentConfig(provider="vllm", reasoning=None))

    assert llm.extra_body == {}


def test_a_vllm_without_a_key_still_gets_one() -> None:
    """The OpenAI client refuses to send a request with no key at all, and a vLLM
    started without --api-key is not checking the header it arrives in."""
    llm = build_chat_model(AgentConfig(provider="vllm", api_key=""))

    assert llm.openai_api_key.get_secret_value() == "EMPTY"


def test_a_configured_key_reaches_the_client() -> None:
    llm = build_chat_model(AgentConfig(provider="vllm", api_key="s3cret"))

    assert llm.openai_api_key.get_secret_value() == "s3cret"


# -- what the server is serving ------------------------------------------------


def test_ollama_lists_every_tag_it_has_pulled(pulled) -> None:
    pulled(["gemma4:12b", "qwen3:8b", ""])

    assert list_models(OLLAMA_CONFIG) == ["gemma4:12b", "qwen3:8b"]


def test_vllm_lists_the_one_model_it_was_started_with(serving) -> None:
    serving(["Qwen/Qwen3-8B"])

    assert list_models(VLLM_CONFIG) == ["Qwen/Qwen3-8B"]


def test_the_listing_is_asked_of_the_api_root_the_config_names(serving) -> None:
    """``base_url`` already ends at the API root, so the path is appended to it --
    a second ``/v1`` or a stripped one is a 404 the picker would show as "down"."""
    asked = serving(["Qwen/Qwen3-8B"])
    list_models(AgentConfig(provider="vllm", base_url="http://gpu.internal:8000/v1/"))

    assert asked["url"] == "http://gpu.internal:8000/v1/models"


def test_a_key_is_sent_with_the_listing_when_there_is_one(serving) -> None:
    """The listing goes over httpx rather than the OpenAI client, so the header a
    vLLM started with --api-key demands has to be written here too."""
    asked = serving(["Qwen/Qwen3-8B"])
    list_models(AgentConfig(provider="vllm", api_key="s3cret"))

    assert asked["headers"] == {"Authorization": "Bearer s3cret"}


def test_no_key_means_no_header(serving) -> None:
    asked = serving(["Qwen/Qwen3-8B"])
    list_models(AgentConfig(provider="vllm", api_key=""))

    assert asked["headers"] == {}


def test_a_listing_that_fails_raises_rather_than_reporting_nothing(serving) -> None:
    """Both callers phrase it themselves -- a hint on the CLI, a status in the
    browser -- so an empty list here would be indistinguishable from a real one."""
    serving([], error=httpx.ConnectError("refused"))

    with pytest.raises(httpx.ConnectError):
        list_models(VLLM_CONFIG)


# -- what a failure says -------------------------------------------------------


def test_a_stopped_ollama_is_told_to_serve() -> None:
    message = unavailable_hint(OLLAMA_CONFIG, ConnectionError("connection refused"))

    assert "ollama serve" in message
    assert OLLAMA_CONFIG.base_url in message


def test_a_stopped_vllm_is_told_to_serve_the_model_it_was_asked_for() -> None:
    """Unlike Ollama's, this command needs the model in it: a vLLM is started for
    one model, so "start it" and "start it with this" are the same sentence."""
    message = unavailable_hint(VLLM_CONFIG, openai.APIConnectionError(request=_REQUEST))

    assert "vllm serve Qwen/Qwen3-8B" in message
    assert VLLM_CONFIG.base_url in message


def test_a_slow_vllm_is_not_told_to_start_one(serving) -> None:
    """A timeout is a running server; telling the user to start one misleads."""
    message = unavailable_hint(VLLM_CONFIG, openai.APITimeoutError(request=_REQUEST))

    assert "did not answer in time" in message
    assert "vllm serve" not in message
    assert "Qwen/Qwen3-8B" in message


def test_a_slow_vllm_reported_by_httpx_says_the_same_thing() -> None:
    """The listing's transport times out as httpx, the chat's as openai; a shopper
    who waited two minutes should not get two different explanations of it."""
    message = unavailable_hint(VLLM_CONFIG, httpx.ReadTimeout("timed out"))

    assert "did not answer in time" in message


def test_a_refused_key_says_which_variable_sets_one() -> None:
    """The one failure that is neither "not running" nor "not serving that", and
    the only one whose fix is an environment variable rather than a command."""
    message = unavailable_hint(VLLM_CONFIG, _status_error(openai.AuthenticationError, 401))

    assert "$VLLM_API_KEY" in message
    assert "--api-key" in message


def test_a_model_vllm_is_not_serving_names_what_it_is(serving) -> None:
    """The asymmetry with Ollama that matters most: there is nothing to pull. The
    server has one model and it is not this one, so the message offers both ways
    out -- ask for what it has, or restart it for what you wanted."""
    serving(["Qwen/Qwen3-0.6B"])
    message = unavailable_hint(VLLM_CONFIG, _status_error(openai.NotFoundError, 404))

    assert "serving: Qwen/Qwen3-0.6B" in message
    assert "vllm serve Qwen/Qwen3-8B" in message


def test_a_vllm_that_cannot_be_listed_still_says_how_to_restart_it(serving) -> None:
    """Whatever else is broken, the command is still the thing to try -- and the
    second failure must not replace the message being written about the first."""
    serving([], error=httpx.ConnectError("refused"))
    message = unavailable_hint(VLLM_CONFIG, _status_error(openai.NotFoundError, 404))

    assert "serving: unknown" in message
    assert "vllm serve Qwen/Qwen3-8B" in message


def test_a_vllm_serving_nothing_reports_none(serving) -> None:
    serving([])
    message = unavailable_hint(VLLM_CONFIG, _status_error(openai.NotFoundError, 404))

    assert "serving: none" in message


def test_a_missing_ollama_tag_is_told_to_pull_it(pulled) -> None:
    """The same shape of failure, the other command: Ollama holds many tags and
    the answer is to fetch one, not to restart the server."""
    pulled(["qwen3:8b"])
    message = unavailable_hint(OLLAMA_CONFIG, ResponseError("model not found", 404))

    assert "ollama pull gemma4:12b" in message
    assert "installed: qwen3:8b" in message


# -- what counts as "the server is not there" ----------------------------------


@pytest.mark.parametrize(
    "error",
    [
        openai.APIConnectionError(request=_REQUEST),
        openai.APITimeoutError(request=_REQUEST),
        httpx.ConnectError("refused"),
        OSError("socket died"),
    ],
)
def test_every_way_a_vllm_can_be_absent_is_one_the_agent_catches(error) -> None:
    """``BuyAgent._invoke`` catches exactly this tuple, so anything missing from
    it reaches the shopper as a traceback and the browser as a 500 (ADR-0009)."""
    assert isinstance(error, transport_errors(VLLM_CONFIG))


def test_the_two_providers_do_not_share_a_failure_vocabulary() -> None:
    """Which is the reason the tuple is the provider's and not the agent's: an
    ``openai.OpenAIError`` from an Ollama run would be a bug, not a stopped server."""
    assert openai.OpenAIError in transport_errors(VLLM_CONFIG)
    assert openai.OpenAIError not in transport_errors(OLLAMA_CONFIG)


def _status_error(kind: type[openai.APIStatusError], status: int) -> openai.APIStatusError:
    """One of the OpenAI client's status errors, built the way the client builds it."""
    response = httpx.Response(status, request=_REQUEST)
    return kind("The model `Qwen/Qwen3-8B` does not exist.", response=response, body=None)
