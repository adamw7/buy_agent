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

import importlib
import threading
from types import SimpleNamespace

import httpx
import openai
import pytest
from langchain_core.exceptions import OutputParserException
from ollama import ResponseError

import buy_agent.providers as providers_module
from buy_agent.config import AgentConfig
from buy_agent.providers import InstalledModel, provider_for, provider_options

# The table and its rows are read off the module rather than imported by name,
# because ``reloaded_providers`` below re-imports it: a reload re-runs the module
# over its own globals, so ``provider_for`` and ``provider_options`` go on
# answering with whatever the table holds *now* while a name bound at import time
# would still hold the rows from before. Which of the two a test compared then
# decided nothing until pytest ran the reloading tests first -- which is what the
# Saturday mutation run's clean-test pass does, and every ordinary run does not.

OLLAMA_CONFIG = AgentConfig(provider="ollama", model="gemma4:12b")

#: What ``ollama show`` reports for a model that can be prompted at all. Written
#: out here rather than imported, so a rename of the private constant does not
#: quietly rename what these tests claim Ollama answers with.
_COMPLETION = "completion"
_EMBEDDING = "embedding"
VLLM_CONFIG = AgentConfig(provider="vllm", model="Qwen/Qwen3-8B")

#: The OpenAI client's errors all carry the request that failed, so building one
#: takes a request. Which request is irrelevant here -- nothing sends it.
_REQUEST = httpx.Request("POST", "http://localhost:8000/v1/chat/completions")


# Every one of these goes through ``AgentConfig.model_server``, which is the one
# way production code reaches a provider (ADR-0029); naming them here only saves
# writing the config twice on a line.


def chat_model(config: AgentConfig):
    return config.model_server.chat_model(config)


def listed(config: AgentConfig) -> list[InstalledModel]:
    return config.model_server.installed(config)


def names(config: AgentConfig) -> list[str]:
    """Just the tags, for the tests that are not about what each one can do."""
    return [model.name for model in listed(config)]


def hint(config: AgentConfig, exc: Exception) -> str:
    return config.model_server.hint(config, exc)


def errors(config: AgentConfig) -> tuple[type[BaseException], ...]:
    return config.model_server.transport_errors


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
    """Stand in for ollama's ``Client``, so a listing never opens a socket.

    Both of the calls a listing makes: ``list`` for the tags, and ``show`` per
    tag for what it can do. ``capabilities`` maps a tag to what ``ollama show``
    would report -- a tag missing from it is one the probe fails on, which is the
    third answer that call can give and the one nothing can be concluded from.
    """

    def install(
        models: list[str],
        *,
        capabilities: dict[str, list[str] | None] | None = None,
        error: Exception | None = None,
    ) -> dict:
        asked: dict = {"shown": [], "opened": {}}
        reported = {} if capabilities is None else capabilities

        class FakeClient:
            def __init__(self, base_url: str, **kwargs) -> None:
                if error is not None:
                    raise error
                asked["opened"] = {"base_url": base_url, **kwargs}

            def list(self):
                return SimpleNamespace(
                    models=[SimpleNamespace(model=name) for name in models]
                )

            def show(self, name: str):
                asked["shown"].append(name)
                if capabilities is not None and name not in reported:
                    raise ResponseError(f"model {name!r} not found", 404)
                return SimpleNamespace(capabilities=reported.get(name, [_COMPLETION]))

        monkeypatch.setattr("buy_agent.providers.Client", FakeClient)
        return asked

    return install


# -- the registry --------------------------------------------------------------


def test_a_provider_is_found_by_the_name_the_config_carries() -> None:
    assert provider_for("ollama") is providers_module.OLLAMA
    assert provider_for("vllm") is providers_module.VLLM


def test_an_unknown_provider_names_the_ones_that_exist() -> None:
    """This is reached from a flag, a form field and an environment variable, so
    the refusal has to be readable by someone who typed one of the three."""
    with pytest.raises(ValueError, match="Unknown provider 'llama.cpp'") as caught:
        provider_for("llama.cpp")

    for name in providers_module.PROVIDERS:
        assert name in str(caught.value)


def test_a_row_carries_both_what_a_server_defaults_to_and_how_it_is_reached() -> None:
    """One table rather than two: a name written twice is a name that can drift,
    and a provider is not configurable without both halves (ADR-0029)."""
    for server in providers_module.PROVIDERS.values():
        assert server.model and server.base_url, f"{server.name} cannot be reached"
        assert server.label, "a provider with no label is one nobody can read"
        assert callable(server.chat_model) and callable(server.installed)


def test_every_provider_offers_its_defaults_to_the_form() -> None:
    """One row per provider, each carrying the pair to fill the fields in with."""
    options = {option["name"]: option for option in provider_options()}

    assert set(options) == set(providers_module.PROVIDERS)
    for name, server in providers_module.PROVIDERS.items():
        assert options[name]["model"] == server.model
        assert options[name]["base_url"] == server.base_url
        assert options[name]["takes_num_ctx"] == server.takes_num_ctx


def test_the_key_is_the_one_default_the_form_is_never_told() -> None:
    """It is a secret, and this payload is what the API hands a browser."""
    assert all("api_key" not in option for option in provider_options())


def test_only_one_of_them_takes_the_context_window_per_request() -> None:
    """vLLM fixes it with --max-model-len when it starts, so offering a per-run
    setting for it would be a field that quietly does nothing."""
    assert providers_module.OLLAMA.takes_num_ctx is True
    assert providers_module.VLLM.takes_num_ctx is False


# -- building the chat model ---------------------------------------------------


def test_ollama_is_given_the_window_and_the_thinking_switch() -> None:
    """Both are Ollama request options, and ADR-0019 is about them arriving."""
    config = AgentConfig(
        provider="ollama",
        model="qwen3.5:9b",
        base_url="http://ollama.internal:11434",
        temperature=0.2,
        num_ctx=8192,
        reasoning=False,
    )

    llm = config.model_server.chat_model(config)

    assert (llm.model, llm.base_url) == ("qwen3.5:9b", "http://ollama.internal:11434")
    assert (llm.temperature, llm.num_ctx, llm.reasoning) == (0.2, 8192, False)


def test_vllm_is_pointed_at_the_openai_api_it_serves() -> None:
    config = AgentConfig(
        provider="vllm",
        model="Qwen/Qwen3-8B",
        base_url="http://gpu.internal:8000/v1",
        temperature=0.2,
    )

    llm = config.model_server.chat_model(config)

    assert llm.model_name == "Qwen/Qwen3-8B"
    assert llm.openai_api_base == "http://gpu.internal:8000/v1"
    assert llm.temperature == 0.2


def test_vllm_is_never_sent_the_context_window() -> None:
    """It is not a request option there; sent anyway it would be rejected, and the
    run would fail for a setting the shopper could not have known was Ollama's."""
    llm = chat_model(AgentConfig(provider="vllm", num_ctx=8192))

    assert "num_ctx" not in llm.extra_body
    assert "num_ctx" not in llm.model_kwargs


@pytest.mark.parametrize("reasoning", [True, False])
def test_vllm_carries_the_thinking_switch_its_templates_read(reasoning: bool) -> None:
    """``enable_thinking`` is what the chat templates of the thinking models vLLM
    serves look for, and ADR-0019's default of off has to reach them."""
    llm = chat_model(AgentConfig(provider="vllm", reasoning=reasoning))

    assert llm.extra_body == {"chat_template_kwargs": {"enable_thinking": reasoning}}


def test_vllm_sends_nothing_when_thinking_is_left_alone() -> None:
    """The tri-state's third value: send nothing, and let the template decide."""
    llm = chat_model(AgentConfig(provider="vllm", reasoning=None))

    assert llm.extra_body == {}


def test_a_vllm_without_a_key_still_gets_one() -> None:
    """The OpenAI client refuses to send a request with no key at all, and a vLLM
    started without --api-key is not checking the header it arrives in."""
    llm = chat_model(AgentConfig(provider="vllm", api_key=""))

    assert llm.openai_api_key.get_secret_value() == "EMPTY"


def test_a_configured_key_reaches_the_client() -> None:
    llm = chat_model(AgentConfig(provider="vllm", api_key="s3cret"))

    assert llm.openai_api_key.get_secret_value() == "s3cret"


# -- what the server is serving ------------------------------------------------


def test_ollama_lists_every_tag_it_has_pulled(pulled) -> None:
    pulled(["gemma4:12b", "qwen3:8b", ""])

    assert names(OLLAMA_CONFIG) == ["gemma4:12b", "qwen3:8b"]


def test_a_tag_with_no_completion_to_give_is_listed_as_one(pulled) -> None:
    """The whole point of asking twice: an embedding model is pulled the same way
    a chat model is, sits in the same listing, and cannot answer a prompt. Hidden,
    the picker would silently drop a pull someone made on purpose; unmarked, it is
    a run that fails a minute in (ADR-0032)."""
    pulled(
        ["gemma4:12b", "nomic-embed-text"],
        capabilities={
            "gemma4:12b": [_COMPLETION, "tools"],
            "nomic-embed-text": [_EMBEDDING],
        },
    )

    assert listed(OLLAMA_CONFIG) == [
        InstalledModel("gemma4:12b", completion=True),
        InstalledModel("nomic-embed-text", completion=False),
    ]


def test_every_tag_is_asked_what_it_can_do(pulled) -> None:
    """``ollama list`` says nothing about capabilities, so the second call is per
    tag -- and a tag left unasked is one the picker cannot mark."""
    asked = pulled(["gemma4:12b", "qwen3:8b", ""])

    listed(OLLAMA_CONFIG)

    assert sorted(asked["shown"]) == ["gemma4:12b", "qwen3:8b"]


def test_the_whole_listing_is_held_to_the_one_short_timeout(pulled) -> None:
    """It is asked for while a form renders, and it is now several calls rather
    than one -- a client with no timeout would hang the picker on a slow server."""
    asked = pulled(["gemma4:12b"])

    listed(OLLAMA_CONFIG)

    assert asked["opened"]["timeout"] == providers_module._LIST_TIMEOUT


def test_a_tag_that_will_not_say_what_it_can_do_is_still_offered(pulled) -> None:
    """The probe failed; nothing was learnt. Marking the model unusable on that
    would hide a working one, which is the worse of the two mistakes."""
    pulled(["gemma4:12b", "qwen3:8b"], capabilities={"gemma4:12b": [_COMPLETION]})

    assert listed(OLLAMA_CONFIG) == [
        InstalledModel("gemma4:12b", completion=True),
        InstalledModel("qwen3:8b", completion=True),
    ]


def test_an_ollama_too_old_to_report_capabilities_offers_everything(pulled) -> None:
    """``capabilities`` is absent rather than empty there, which says nothing
    about the tag -- and the same rule applies: it is taken at its word."""
    pulled(["gemma4:12b"], capabilities={"gemma4:12b": None})

    assert listed(OLLAMA_CONFIG) == [InstalledModel("gemma4:12b", completion=True)]


def test_an_ollama_with_nothing_pulled_is_asked_nothing_further(pulled) -> None:
    """No tags is an answer, and the branch that skips the second round of calls."""
    asked = pulled([])

    assert listed(OLLAMA_CONFIG) == []
    assert asked["shown"] == []


def test_vllm_lists_the_one_model_it_was_started_with(serving) -> None:
    serving(["Qwen/Qwen3-8B"])

    assert listed(VLLM_CONFIG) == [InstalledModel("Qwen/Qwen3-8B", completion=True)]


def test_everything_a_vllm_serves_can_answer_a_prompt(serving) -> None:
    """There is no second question to ask: a vLLM process serves the model it was
    started for, so a listing there is by construction a listing of usable models."""
    serving(["Qwen/Qwen3-8B"])

    assert all(model.completion for model in listed(VLLM_CONFIG))


def test_the_listing_is_asked_of_the_api_root_the_config_names(serving) -> None:
    """``base_url`` already ends at the API root, so the path is appended to it --
    a second ``/v1`` or a stripped one is a 404 the picker would show as "down"."""
    asked = serving(["Qwen/Qwen3-8B"])
    listed(AgentConfig(provider="vllm", base_url="http://gpu.internal:8000/v1/"))

    assert asked["url"] == "http://gpu.internal:8000/v1/models"


def test_a_key_is_sent_with_the_listing_when_there_is_one(serving) -> None:
    """The listing goes over httpx rather than the OpenAI client, so the header a
    vLLM started with --api-key demands has to be written here too."""
    asked = serving(["Qwen/Qwen3-8B"])
    listed(AgentConfig(provider="vllm", api_key="s3cret"))

    assert asked["headers"] == {"Authorization": "Bearer s3cret"}


def test_no_key_means_no_header(serving) -> None:
    asked = serving(["Qwen/Qwen3-8B"])
    listed(AgentConfig(provider="vllm", api_key=""))

    assert asked["headers"] == {}


def test_a_listing_that_fails_raises_rather_than_reporting_nothing(serving) -> None:
    """Both callers phrase it themselves -- a hint on the CLI, a status in the
    browser -- so an empty list here would be indistinguishable from a real one."""
    serving([], error=httpx.ConnectError("refused"))

    with pytest.raises(httpx.ConnectError):
        listed(VLLM_CONFIG)


# -- what a failure says -------------------------------------------------------


def test_a_stopped_ollama_is_told_to_serve() -> None:
    message = hint(OLLAMA_CONFIG, ConnectionError("connection refused"))

    assert "ollama serve" in message
    assert OLLAMA_CONFIG.base_url in message


def test_a_stopped_vllm_is_told_to_serve_the_model_it_was_asked_for() -> None:
    """Unlike Ollama's, this command needs the model in it: a vLLM is started for
    one model, so "start it" and "start it with this" are the same sentence."""
    message = hint(VLLM_CONFIG, openai.APIConnectionError(request=_REQUEST))

    assert "vllm serve Qwen/Qwen3-8B" in message
    assert VLLM_CONFIG.base_url in message


def test_a_slow_vllm_is_not_told_to_start_one(serving) -> None:
    """A timeout is a running server; telling the user to start one misleads."""
    message = hint(VLLM_CONFIG, openai.APITimeoutError(request=_REQUEST))

    assert "did not answer in time" in message
    assert "vllm serve" not in message
    assert "Qwen/Qwen3-8B" in message


def test_a_slow_vllm_reported_by_httpx_says_the_same_thing() -> None:
    """The listing's transport times out as httpx, the chat's as openai; a shopper
    who waited two minutes should not get two different explanations of it."""
    message = hint(VLLM_CONFIG, httpx.ReadTimeout("timed out"))

    assert "did not answer in time" in message


def test_an_unreadable_answer_names_the_room_ollama_can_be_given() -> None:
    """A server that answered, badly. Neither "start it" nor "pull it" applies --
    it is running and it has the tag -- and the usual cause is a window too small
    for the prompt and its answer both (ADR-0019)."""
    message = hint(OLLAMA_CONFIG, OutputParserException("Invalid json output: {\"produ"))

    assert "not the JSON this asks for" in message
    assert "--num-ctx" in message and "--no-think" in message
    assert "ollama serve" not in message and "ollama pull" not in message


def test_an_unreadable_answer_names_what_vllm_can_be_given_instead(serving) -> None:
    """The same failure, and the same declared difference: a vLLM's window is
    fixed when it starts, so the only room to give it here is a shorter prompt."""
    message = hint(VLLM_CONFIG, OutputParserException("Invalid json output: {"))

    assert "not the JSON this asks for" in message
    assert "--max-model-len" in message
    assert "--num-ctx" not in message and "vllm serve" not in message


def test_a_half_finished_answer_is_not_read_as_a_missing_model() -> None:
    """The quoted text is the model's own words, and any of them could say "not
    found" -- which is the message Ollama uses for a tag it has not pulled."""
    message = hint(
        OLLAMA_CONFIG, OutputParserException('Invalid json output: {"name": "Page not found')
    )

    assert "ollama pull" not in message
    assert "not the JSON this asks for" in message


def test_an_unreadable_answer_quotes_one_line_of_it() -> None:
    """The failure carries the answer the model did give -- a prompt's worth of
    half-finished JSON with a docs link under it -- and the sentence around it is
    the actionable half."""
    message = hint(
        OLLAMA_CONFIG,
        OutputParserException("Invalid json output: {\nFor troubleshooting, visit: https://x"),
    )

    assert "For troubleshooting" not in message
    assert "Invalid json output: {" in message


def test_a_refused_key_says_which_variable_sets_one() -> None:
    """The one failure that is neither "not running" nor "not serving that", and
    the only one whose fix is an environment variable rather than a command."""
    message = hint(VLLM_CONFIG, _status_error(openai.AuthenticationError, 401))

    assert "$VLLM_API_KEY" in message
    assert "--api-key" in message


def test_a_model_vllm_is_not_serving_names_what_it_is(serving) -> None:
    """The asymmetry with Ollama that matters most: there is nothing to pull. The
    server has one model and it is not this one, so the message offers both ways
    out -- ask for what it has, or restart it for what you wanted."""
    serving(["Qwen/Qwen3-0.6B"])
    message = hint(VLLM_CONFIG, _status_error(openai.NotFoundError, 404))

    assert "serving: Qwen/Qwen3-0.6B" in message
    assert "vllm serve Qwen/Qwen3-8B" in message


def test_a_vllm_that_cannot_be_listed_still_says_how_to_restart_it(serving) -> None:
    """Whatever else is broken, the command is still the thing to try -- and the
    second failure must not replace the message being written about the first."""
    serving([], error=httpx.ConnectError("refused"))
    message = hint(VLLM_CONFIG, _status_error(openai.NotFoundError, 404))

    assert "serving: unknown" in message
    assert "vllm serve Qwen/Qwen3-8B" in message


def test_a_vllm_serving_nothing_reports_none(serving) -> None:
    serving([])
    message = hint(VLLM_CONFIG, _status_error(openai.NotFoundError, 404))

    assert "serving: none" in message


def test_a_missing_ollama_tag_is_told_to_pull_it(pulled) -> None:
    """The same shape of failure, the other command: Ollama holds many tags and
    the answer is to fetch one, not to restart the server."""
    pulled(["qwen3:8b"])
    message = hint(OLLAMA_CONFIG, ResponseError("model not found", 404))

    assert "ollama pull gemma4:12b" in message
    assert "installed: qwen3:8b" in message


def test_a_model_that_cannot_answer_a_prompt_is_named_as_one(pulled) -> None:
    """Ollama answered, and the run still failed: the tag is there and has no
    completion to give. Without this the message falls through to "start the
    server", which is wrong and unactionable -- it is running (ADR-0032)."""
    pulled(
        ["gemma4:12b", "nomic-embed-text"],
        capabilities={
            "gemma4:12b": [_COMPLETION],
            "nomic-embed-text": [_EMBEDDING],
        },
    )
    config = AgentConfig(provider="ollama", model="nomic-embed-text")
    message = hint(config, ResponseError('"nomic-embed-text" does not support chat', 400))

    assert "cannot answer a prompt" in message
    assert "ollama serve" not in message, "the server answered; starting one is no help"
    assert "installed: gemma4:12b" in message


def test_the_models_offered_instead_are_only_the_ones_that_can_answer(pulled) -> None:
    """Listing the embedding model back to someone whose run just failed on one
    would be the same mistake in the sentence written to explain it."""
    pulled(
        ["nomic-embed-text", "mxbai-embed-large", "qwen3:8b"],
        capabilities={
            "nomic-embed-text": [_EMBEDDING],
            "mxbai-embed-large": [_EMBEDDING],
            "qwen3:8b": [_COMPLETION],
        },
    )
    config = AgentConfig(provider="ollama", model="nomic-embed-text")
    message = hint(config, ResponseError('"nomic-embed-text" does not support chat', 400))

    assert "installed: qwen3:8b" in message
    assert "mxbai-embed-large" not in message


def test_an_ollama_serving_nothing_that_answers_reports_none(pulled) -> None:
    """The empty case of that narrowing: tags are pulled, none of them can chat."""
    pulled(["nomic-embed-text"], capabilities={"nomic-embed-text": [_EMBEDDING]})
    config = AgentConfig(provider="ollama", model="nomic-embed-text")
    message = hint(config, ResponseError('"nomic-embed-text" does not support chat', 400))

    assert "installed: none" in message


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
    assert isinstance(error, errors(VLLM_CONFIG))


def test_the_two_providers_do_not_share_a_failure_vocabulary() -> None:
    """Which is the reason the tuple is the provider's and not the agent's: an
    ``openai.OpenAIError`` from an Ollama run would be a bug, not a stopped server."""
    assert openai.OpenAIError in errors(VLLM_CONFIG)
    assert openai.OpenAIError not in errors(OLLAMA_CONFIG)


# -- what each server defaults to ----------------------------------------------


@pytest.fixture
def reloaded_providers(monkeypatch):
    """Re-import the table so its environment-derived defaults are read again.

    Only this module is reloaded: ``AgentConfig`` resolves through
    ``provider_for``, which reads the table out of these module globals, so a
    config built afterwards sees the new rows without ``config`` being reloaded
    too. The rows the teardown puts back hold the same values but are new
    objects, and a name another test module imported before the reload still
    holds the old ones -- so anything comparing rows by identity has to reach
    them through the module, the way ``tests/test_config.py`` does.
    """

    def reload(**environment: str):
        for name, value in environment.items():
            monkeypatch.setenv(name, value)
        return importlib.reload(providers_module)

    yield reload
    monkeypatch.undo()
    importlib.reload(providers_module)


def test_ollamas_model_and_host_can_be_set_from_the_environment(reloaded_providers) -> None:
    reloaded_providers(OLLAMA_MODEL="qwen2.5:7b", OLLAMA_HOST="http://ollama.internal:11434")
    config = AgentConfig()

    assert config.model == "qwen2.5:7b"
    assert config.base_url == "http://ollama.internal:11434"


def test_ollama_falls_back_to_a_local_server(reloaded_providers, monkeypatch) -> None:
    for name in ("OLLAMA_MODEL", "OLLAMA_HOST"):
        monkeypatch.delenv(name, raising=False)

    reloaded_providers()
    config = AgentConfig()

    assert config.model == "gemma4:12b"
    assert config.base_url == "http://localhost:11434"


def test_vllm_has_its_own_pair_of_variables(reloaded_providers) -> None:
    """One machine can have both servers, so one pair of variables could not name
    both -- $OLLAMA_HOST moving the vLLM address would be nonsense."""
    reloaded_providers(
        VLLM_MODEL="meta-llama/Llama-3.1-8B", VLLM_HOST="http://gpu.internal:8000/v1"
    )
    config = AgentConfig(provider="vllm")

    assert config.model == "meta-llama/Llama-3.1-8B"
    assert config.base_url == "http://gpu.internal:8000/v1"


def test_the_vllm_defaults_are_a_local_server_too(reloaded_providers, monkeypatch) -> None:
    """Port 8000 and the ``/v1`` the OpenAI API is served under, which is what
    ``vllm serve`` gives you with no arguments."""
    for name in ("VLLM_MODEL", "VLLM_HOST"):
        monkeypatch.delenv(name, raising=False)

    reloaded_providers()

    assert AgentConfig(provider="vllm").base_url == "http://localhost:8000/v1"


def test_the_key_is_read_from_the_environment(reloaded_providers) -> None:
    """The one setting with no flag and no form field: it is a secret, so it does
    not land in a shell history and is not in what the API hands a browser. The
    other two halves of that are asserted where those two are built."""
    reloaded_providers(VLLM_API_KEY="s3cret")

    assert AgentConfig(provider="vllm").api_key == "s3cret"


def test_no_key_is_the_default(reloaded_providers, monkeypatch) -> None:
    """Most vLLMs are started without one, and a placeholder is what the provider
    sends in that case rather than a value anybody has to set."""
    monkeypatch.delenv("VLLM_API_KEY", raising=False)
    reloaded_providers()

    assert AgentConfig(provider="vllm").api_key == ""


def test_an_ollama_run_never_carries_a_vllm_key(reloaded_providers) -> None:
    """The key belongs to the server it authenticates, not to the run: Ollama has
    no notion of one, so a machine that sets $VLLM_API_KEY does not hand it out."""
    reloaded_providers(VLLM_API_KEY="s3cret")

    assert AgentConfig(provider="ollama").api_key == ""


def _status_error(kind: type[openai.APIStatusError], status: int) -> openai.APIStatusError:
    """One of the OpenAI client's status errors, built the way the client builds it."""
    response = httpx.Response(status, request=_REQUEST)
    return kind("The model `Qwen/Qwen3-8B` does not exist.", response=response, body=None)


def test_the_listing_budget_covers_the_listing_and_not_each_tag(monkeypatch) -> None:
    """``_LIST_TIMEOUT`` on the client bounds one question; a listing asks many.

    Ollama reports capabilities per tag (ADR-0032), so fifty pulled tags on a
    slow server was fifty timeouts eight at a time -- and the form waited on all
    of it while being promised a five-second answer. The probes share a deadline
    now, and one still running when it passes is the case the probe already has a
    word for: it did not say, so it counts as able to answer.
    """
    started = threading.Event()
    release = threading.Event()

    class Slow:
        def __init__(self, base_url: str, **kwargs) -> None:
            pass

        def list(self):
            return SimpleNamespace(
                models=[SimpleNamespace(model=name) for name in ("a:1", "b:1", "c:1")]
            )

        def show(self, name: str):
            started.set()
            release.wait(timeout=5.0)
            return SimpleNamespace(capabilities=[_COMPLETION])

    monkeypatch.setattr("buy_agent.providers.Client", Slow)
    monkeypatch.setattr(providers_module, "_LIST_TIMEOUT", 0.05)
    try:
        models = providers_module._ollama_installed(AgentConfig())
    finally:
        # Before the assertions: a probe still blocked here is a non-daemon pool
        # thread, and the interpreter joins those on the way out.
        release.set()

    assert started.is_set(), "the probes did go out"
    assert [model.name for model in models] == ["a:1", "b:1", "c:1"]
    assert all(model.completion for model in models), "a tag that did not say is offered"
