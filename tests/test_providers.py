"""The three model servers, and the four things each of them answers differently."""

from __future__ import annotations

import importlib
import threading
from types import SimpleNamespace

import httpx
import openai
import pytest
from ollama import ResponseError

import buy_agent.providers as providers_module
from buy_agent.chat import UnreadableAnswerError
from buy_agent.config import AgentConfig
from buy_agent.models import SearchQuery
from buy_agent.providers import provider_for, provider_options

# The table and its rows are read off the module rather than imported by name, because
# ``reloaded_providers`` below re-imports it: a reload re-runs the module over its own
# globals, so ``provider_for`` and ``provider_options`` go on answering with whatever the
# table holds *now* while a name bound at import time would still hold the rows from
# before.

OLLAMA_CONFIG = AgentConfig(provider="ollama", model="gemma4:12b")

#: What ``ollama show`` reports for a model that can be prompted at all.
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


def listed(config: AgentConfig) -> list[providers_module.InstalledModel]:
    return config.model_server.installed(config)


def installed(name: str, *, completion: bool) -> providers_module.InstalledModel:
    """One entry of an expected listing, built through the module for the same reason the
    rows are read off it."""
    return providers_module.InstalledModel(name, completion=completion)


def names(config: AgentConfig) -> list[str]:
    """Just the tags, for the tests that are not about what each one can do."""
    return [model.name for model in listed(config)]


def hint(config: AgentConfig, exc: Exception) -> str:
    return config.model_server.hint(config, exc)


def errors(config: AgentConfig) -> tuple[type[Exception], ...]:
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
def chatting(monkeypatch):
    """Stand in for ollama's ``Client`` being asked a question, capturing it."""
    sent: dict = {}

    def install(answered: str = '{"query": "a refined query"}') -> dict:
        class FakeClient:
            def __init__(self, base_url: str, **kwargs) -> None:
                sent["base_url"] = base_url
                # Everything else the client was built with -- the wait, which is
                # on the client here and on the request nowhere (ADR-0051).
                sent["client"] = kwargs

            @staticmethod
            def chat(**kwargs):
                sent.update(kwargs)
                return SimpleNamespace(message=SimpleNamespace(content=answered))

            @staticmethod
            def close() -> None:
                sent["closed"] = True

        monkeypatch.setattr("buy_agent.providers.Client", FakeClient)
        return sent

    return install


@pytest.fixture
def completing(monkeypatch):
    """Stand in for the OpenAI client vLLM is reached through, capturing the call."""
    sent: dict = {}

    def install(answered: str = '{"query": "a refined query"}') -> dict:
        class FakeOpenAI:
            def __init__(self, **kwargs) -> None:
                sent["client"] = kwargs
                self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

            @staticmethod
            def close() -> None:
                sent["closed"] = True

        def create(**kwargs):
            sent.update(kwargs)
            choice = SimpleNamespace(message=SimpleNamespace(content=answered))
            return SimpleNamespace(choices=[choice])

        monkeypatch.setattr("buy_agent.providers.openai.OpenAI", FakeOpenAI)
        return sent

    return install


@pytest.fixture
def pulled(monkeypatch):
    """Stand in for an Ollama being asked what it holds, so a listing opens no socket."""

    def install(
        models: list[str],
        *,
        entries: list[dict] | None = None,
        capabilities: dict[str, list[str] | None] | None = None,
        error: Exception | None = None,
    ) -> dict:
        asked: dict = {"shown": [], "opened": {}, "tags": {}}
        reported = {} if capabilities is None else capabilities
        listing = (
            entries
            if entries is not None
            else [{"model": name, "name": name} for name in models]
        )

        class Response:
            @staticmethod
            def raise_for_status() -> None:
                return None

            @staticmethod
            def json() -> dict:
                return {"models": listing}

        def get(url, **kwargs):
            if error is not None:
                raise error
            asked["tags"] = {"url": url, **kwargs}
            return Response()

        class FakeClient:
            def __init__(self, base_url: str, **kwargs) -> None:
                asked["opened"] = {"base_url": base_url, **kwargs}

            def show(self, name: str):
                asked["shown"].append(name)
                if capabilities is not None and name not in reported:
                    raise ResponseError(f"model {name!r} not found", 404)
                return SimpleNamespace(capabilities=reported.get(name, [_COMPLETION]))

            # The listing opens a client of its own, so it closes one of its own --
            # recorded rather than ignored, ``test_the_listing_lets_go_of_what_it
            # _opened`` being what says the pool does not outlive the question.
            def close(self) -> None:
                asked["closed"] = asked.get("closed", 0) + 1

        monkeypatch.setattr("buy_agent.providers.httpx.get", get)
        monkeypatch.setattr("buy_agent.providers.Client", FakeClient)
        return asked

    return install


# -- the registry --------------------------------------------------------------


def test_a_provider_is_found_by_the_name_the_config_carries() -> None:
    assert provider_for("ollama") is providers_module.OLLAMA
    assert provider_for("vllm") is providers_module.VLLM
    assert provider_for("litellm") is providers_module.LITELLM


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
        assert options[name]["takes_cpu_only"] == server.takes_cpu_only


def test_the_key_is_the_one_default_the_form_is_never_told() -> None:
    """It is a secret, and this payload is what the API hands a browser."""
    assert all("api_key" not in option for option in provider_options())


def test_only_one_of_them_takes_the_context_window_per_request() -> None:
    """vLLM fixes it with --max-model-len when it starts, so offering a per-run
    setting for it would be a field that quietly does nothing."""
    assert providers_module.OLLAMA.takes_num_ctx is True
    assert providers_module.VLLM.takes_num_ctx is False
    assert providers_module.LITELLM.takes_num_ctx is False


def test_only_one_of_them_takes_the_device_per_request() -> None:
    """vLLM picks its device with --device when it starts, for the reason it fixes
    its window there: a per-run switch would quietly do nothing."""
    assert providers_module.OLLAMA.takes_cpu_only is True
    assert providers_module.VLLM.takes_cpu_only is False
    assert providers_module.LITELLM.takes_cpu_only is False


# -- building the chat model ---------------------------------------------------


def asked(config: AgentConfig, sent: dict, schema: type = SearchQuery) -> dict:
    """Put one question to the config's provider, and hand back what it sent."""
    config.model_server.chat_model(config).answer(
        [{"role": "user", "content": "headphones"}], schema
    )
    return sent


def test_closing_a_chat_model_lets_go_of_the_client_underneath(
    chatting, completing
) -> None:
    """The one thing a chat model holds that outlives the answer."""
    ollama_sent, vllm_sent = chatting(), completing()

    chat_model(AgentConfig(provider="ollama")).close()
    chat_model(VLLM_CONFIG).close()

    assert ollama_sent["closed"] is True
    assert vllm_sent["closed"] is True


def test_ollama_is_given_the_window_and_the_thinking_switch(chatting) -> None:
    """Both are Ollama request options, and ADR-0019 is about them arriving."""
    config = AgentConfig(
        provider="ollama",
        model="qwen3.5:9b",
        base_url="http://ollama.internal:11434",
        temperature=0.2,
        num_ctx=8192,
        reasoning=False,
    )

    sent = asked(config, chatting())

    assert (sent["model"], sent["base_url"]) == ("qwen3.5:9b", "http://ollama.internal:11434")
    assert sent["options"] == {"temperature": 0.2, "num_ctx": 8192}
    assert sent["think"] is False


def test_ollama_is_sent_no_window_when_there_is_none_to_send(chatting) -> None:
    """``None`` is "leave the model's own alone", which a null option is not."""
    sent = asked(AgentConfig(provider="ollama", num_ctx=None), chatting())

    assert "num_ctx" not in sent["options"]


def test_ollama_is_told_to_offload_nothing_for_a_cpu_only_run(chatting) -> None:
    """``num_gpu`` is how many layers go to the card, so none of them is zero."""
    sent = asked(AgentConfig(provider="ollama", cpu_only=True), chatting())

    assert sent["options"]["num_gpu"] == 0


def test_ollama_is_sent_no_device_when_the_card_may_be_used(chatting) -> None:
    """The absence of the option is "offload whatever you would have", which is not
    a number this can send -- the same rule the window follows."""
    sent = asked(AgentConfig(provider="ollama", cpu_only=False), chatting())

    assert "num_gpu" not in sent["options"]


def test_ollama_is_asked_to_decode_against_the_schema(chatting) -> None:
    """The schema *is* the request option: Ollama compiles it into a grammar."""
    sent = asked(AgentConfig(provider="ollama"), chatting(), SearchQuery)

    assert sent["format"] == SearchQuery.model_json_schema()


def test_ollama_is_not_asked_to_stream_it(chatting) -> None:
    """Nothing reads a token before the whole answer is parsed against a schema,
    and the plain path is the one whose refused connection is an ``OSError``."""
    sent = asked(AgentConfig(provider="ollama"), chatting())

    assert not sent.get("stream")


def test_vllm_is_pointed_at_the_openai_api_it_serves(completing) -> None:
    config = AgentConfig(
        provider="vllm",
        model="Qwen/Qwen3-8B",
        base_url="http://gpu.internal:8000/v1",
        temperature=0.2,
    )

    sent = asked(config, completing())

    assert sent["client"]["base_url"] == "http://gpu.internal:8000/v1"
    assert sent["model"] == "Qwen/Qwen3-8B"
    assert sent["temperature"] == 0.2


def test_vllm_is_asked_to_decode_against_the_schema(completing) -> None:
    """The same constraint as Ollama's ``format``, in the OpenAI API's spelling."""
    sent = asked(AgentConfig(provider="vllm"), completing(), SearchQuery)

    assert sent["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "SearchQuery",
            "schema": SearchQuery.model_json_schema(),
        },
    }


def test_vllm_is_never_sent_the_context_window(completing) -> None:
    """It is not a request option there; sent anyway it would be rejected, and the
    run would fail for a setting the shopper could not have known was Ollama's."""
    sent = asked(AgentConfig(provider="vllm", num_ctx=8192), completing())

    assert "num_ctx" not in sent["extra_body"]
    assert "num_ctx" not in sent


def test_vllm_is_never_told_which_device_to_use(completing) -> None:
    """Its device is a startup flag, so a per-run switch has nowhere to go."""
    sent = asked(AgentConfig(provider="vllm", cpu_only=True), completing())

    assert "num_gpu" not in sent["extra_body"]
    assert "num_gpu" not in sent


@pytest.mark.parametrize("reasoning", [True, False])
def test_vllm_carries_the_thinking_switch_its_templates_read(
    completing, reasoning: bool
) -> None:
    """``enable_thinking`` is what the chat templates of the thinking models vLLM
    serves look for, and ADR-0019's default of off has to reach them."""
    sent = asked(AgentConfig(provider="vllm", reasoning=reasoning), completing())

    assert sent["extra_body"] == {"chat_template_kwargs": {"enable_thinking": reasoning}}


def test_vllm_sends_nothing_when_thinking_is_left_alone(completing) -> None:
    """The tri-state's third value: send nothing, and let the template decide."""
    sent = asked(AgentConfig(provider="vllm", reasoning=None), completing())

    assert sent["extra_body"] == {}


def test_a_vllm_without_a_key_still_gets_one(completing) -> None:
    """The OpenAI client refuses to send a request with no key at all, and a vLLM
    started without --api-key is not checking the header it arrives in."""
    sent = asked(AgentConfig(provider="vllm", api_key=""), completing())

    assert sent["client"]["api_key"] == "EMPTY"


def test_a_configured_key_reaches_the_client(completing) -> None:
    sent = asked(AgentConfig(provider="vllm", api_key="s3cret"), completing())

    assert sent["client"]["api_key"] == "s3cret"


@pytest.mark.parametrize("provider", ["ollama", "vllm", "litellm"])
def test_an_answer_that_is_not_the_schema_is_one_failure_either_way(
    chatting, completing, provider: str
) -> None:
    """Every server can answer with prose, and it is the same thing when they do
    -- one class, one wording, whichever of them said it (ADR-0038)."""
    chatting("I think the best headphones are...")
    completing("I think the best headphones are...")
    config = AgentConfig(provider=provider)

    with pytest.raises(UnreadableAnswerError, match="I think the best"):
        asked(config, {})


@pytest.mark.parametrize("provider", ["ollama", "vllm", "litellm"])
def test_a_server_that_answers_with_nothing_says_so(
    chatting, completing, provider: str
) -> None:
    """A dropped stream leaves an empty message, and "Invalid JSON answer:" with
    nothing after it names no symptom at all."""
    chatting("")
    completing("")

    with pytest.raises(UnreadableAnswerError, match="nothing at all"):
        asked(AgentConfig(provider=provider), {})


# -- what the server is serving ------------------------------------------------


def test_ollama_lists_every_tag_it_has_pulled(pulled) -> None:
    pulled(["gemma4:12b", "qwen3:8b", ""])

    assert names(OLLAMA_CONFIG) == ["gemma4:12b", "qwen3:8b"]


def test_a_tag_spelled_only_the_way_ollama_list_prints_it_is_still_offered(
    pulled,
) -> None:
    """``/api/tags`` names a model twice, ``model`` and ``name``, and an entry carrying
    only the second used to vanish: the client's typed listing declares the first and
    pydantic discards what it does not declare."""
    pulled(
        [],
        entries=[
            {"name": "gemma4:12b", "size": 8149190253},
            {"model": "qwen3:8b", "name": "qwen3:8b"},
        ],
    )

    assert names(OLLAMA_CONFIG) == ["gemma4:12b", "qwen3:8b"]


def test_an_entry_that_names_nothing_is_left_out(pulled) -> None:
    """The other end of that: an entry with neither spelling is nothing to offer
    a shopper, which is what a ``/v1/models`` entry with no ``id`` is on the
    other row -- and it takes only itself out, not the listing around it."""
    pulled([], entries=[{"size": 1}, {"model": "qwen3:8b"}])

    assert names(OLLAMA_CONFIG) == ["qwen3:8b"]


def test_the_tags_are_read_off_ollamas_own_endpoint(pulled) -> None:
    """Where the answer comes from, since it is no longer the client's listing:
    the address the run itself would chat to, and Ollama's own path on it."""
    asked = pulled(["gemma4:12b"])

    listed(OLLAMA_CONFIG)

    assert asked["tags"]["url"] == f"{OLLAMA_CONFIG.base_url}/api/tags"


@pytest.mark.parametrize("base_url", ["localhost:11434", "http://localhost:11434/"])
def test_the_address_is_asked_however_it_was_written(pulled, base_url: str) -> None:
    """``$OLLAMA_HOST`` is written every way -- with the scheme and without it, with a
    trailing slash and without -- and ollama's client takes all of them for the chat."""
    asked = pulled(["gemma4:12b"])

    listed(AgentConfig(provider="ollama", base_url=base_url))

    assert asked["tags"]["url"] == "http://localhost:11434/api/tags"


def test_a_tag_with_no_completion_to_give_is_listed_as_one(pulled) -> None:
    """The whole point of asking twice: an embedding model is pulled the same way a chat
    model is, sits in the same listing, and cannot answer a prompt."""
    pulled(
        ["gemma4:12b", "nomic-embed-text"],
        capabilities={
            "gemma4:12b": [_COMPLETION, "tools"],
            "nomic-embed-text": [_EMBEDDING],
        },
    )

    assert listed(OLLAMA_CONFIG) == [
        installed("gemma4:12b", completion=True),
        installed("nomic-embed-text", completion=False),
    ]


def test_every_tag_is_asked_what_it_can_do(pulled) -> None:
    """``ollama list`` says nothing about capabilities, so the second call is per
    tag -- and a tag left unasked is one the picker cannot mark."""
    asked = pulled(["gemma4:12b", "qwen3:8b", ""])

    listed(OLLAMA_CONFIG)

    assert sorted(asked["shown"]) == ["gemma4:12b", "qwen3:8b"]


def test_the_whole_listing_is_held_to_the_one_short_timeout(pulled) -> None:
    """It is asked for while a form renders, and it is now several calls rather
    than one -- a call with no timeout would hang the picker on a slow server."""
    asked = pulled(["gemma4:12b"])

    listed(OLLAMA_CONFIG)

    assert asked["tags"]["timeout"] == providers_module._LIST_TIMEOUT
    assert asked["opened"]["timeout"] == providers_module._LIST_TIMEOUT


def test_the_listing_lets_go_of_what_it_opened(pulled) -> None:
    """The listing opens a client of its own, and closing it is its own too."""
    asked = pulled(["gemma4:12b"])

    listed(OLLAMA_CONFIG)

    assert asked["closed"] == 1


def test_a_tag_that_will_not_say_what_it_can_do_is_still_offered(pulled) -> None:
    """The probe failed; nothing was learnt."""
    pulled(["gemma4:12b", "qwen3:8b"], capabilities={"gemma4:12b": [_COMPLETION]})

    assert listed(OLLAMA_CONFIG) == [
        installed("gemma4:12b", completion=True),
        installed("qwen3:8b", completion=True),
    ]


def test_an_ollama_too_old_to_report_capabilities_offers_everything(pulled) -> None:
    """``capabilities`` is absent rather than empty there, which says nothing
    about the tag -- and the same rule applies: it is taken at its word."""
    pulled(["gemma4:12b"], capabilities={"gemma4:12b": None})

    assert listed(OLLAMA_CONFIG) == [installed("gemma4:12b", completion=True)]


def test_an_ollama_with_nothing_pulled_is_asked_nothing_further(pulled) -> None:
    """No tags is an answer, and the branch that skips the second round of calls."""
    asked = pulled([])

    assert listed(OLLAMA_CONFIG) == []
    assert asked["shown"] == []


def test_vllm_lists_the_one_model_it_was_started_with(serving) -> None:
    serving(["Qwen/Qwen3-8B"])

    assert listed(VLLM_CONFIG) == [installed("Qwen/Qwen3-8B", completion=True)]


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
    """A server that answered, badly."""
    message = hint(OLLAMA_CONFIG, UnreadableAnswerError("Invalid json output: {\"produ"))

    assert "not the JSON this asks for" in message
    assert "context window" in message and "turn thinking off" in message
    assert "ollama serve" not in message and "ollama pull" not in message


def test_an_unreadable_answer_names_what_vllm_can_be_given_instead(serving) -> None:
    """The same failure, and the same declared difference: a vLLM's window is
    fixed when it starts, so the only room to give it here is a shorter prompt."""
    message = hint(VLLM_CONFIG, UnreadableAnswerError("Invalid json output: {"))

    assert "not the JSON this asks for" in message
    assert "--max-model-len" in message
    assert "context window" not in message and "vllm serve" not in message


def test_a_half_finished_answer_is_not_read_as_a_missing_model() -> None:
    """The quoted text is the model's own words, and any of them could say "not
    found" -- which is the message Ollama uses for a tag it has not pulled."""
    message = hint(
        OLLAMA_CONFIG, UnreadableAnswerError('Invalid json output: {"name": "Page not found')
    )

    assert "ollama pull" not in message
    assert "not the JSON this asks for" in message


def test_an_unreadable_answer_quotes_one_line_of_it() -> None:
    """The failure carries the answer the model did give -- a prompt's worth of
    half-finished JSON with a docs link under it -- and the sentence around it is
    the actionable half."""
    message = hint(
        OLLAMA_CONFIG,
        UnreadableAnswerError("Invalid json output: {\nFor troubleshooting, visit: https://x"),
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
    """The asymmetry with Ollama that matters most: there is nothing to pull."""
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


def test_a_name_ollama_cannot_parse_is_not_a_server_to_start(pulled) -> None:
    """Ollama answered, refusing the string before looking for any tag -- a browser that
    remembered ``[object Object]`` from a build older than the listing it was reading.
    "Start it with: ollama serve" sent that shopper to restart a server that was running,
    and a pull is no better, since ``ollama pull`` refuses the same string."""
    pulled(["gemma4:12b"])
    config = AgentConfig(provider="ollama", model="[object Object]")
    message = hint(config, ResponseError("invalid model name", 400))

    assert "cannot read '[object Object]' as a model name" in message
    assert "installed: gemma4:12b" in message
    assert "ollama serve" not in message, "the server answered; starting one is no help"
    assert "ollama pull" not in message, "a name Ollama cannot read cannot be pulled either"


def test_a_model_that_cannot_answer_a_prompt_is_named_as_one(pulled) -> None:
    """Ollama answered, and the run still failed: the tag is there and has no completion
    to give."""
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


def test_a_404_from_something_that_is_not_ollama_is_not_a_missing_tag(pulled) -> None:
    """The remedy names a *model*, so it needs Ollama's own answer and not a 404 from
    whatever else is listening there -- a vLLM, this project's own server on :8000. Read
    off the text alone, "404 Not Found" took the pull branch, and `ollama pull` against
    the real Ollama succeeds and changes nothing, the address being what is wrong."""
    pulled(["gemma4:12b"])
    absent = httpx.HTTPStatusError(
        "Client error '404 Not Found' for url 'http://localhost:11434/api/tags'",
        request=httpx.Request("GET", "http://localhost:11434/api/tags"),
        response=httpx.Response(404),
    )
    message = hint(OLLAMA_CONFIG, absent)

    assert "Could not reach Ollama" in message
    assert "ollama serve" in message
    assert "ollama pull" not in message


def test_a_404_from_something_that_is_not_vllm_is_not_a_model_it_lacks(serving) -> None:
    """The same rule on the other row: only what the OpenAI client raises for an answer
    it got says anything about which model is being served."""
    serving(["Qwen/Qwen3-8B"])
    absent = httpx.HTTPStatusError(
        "Client error '404 Not Found' for url 'http://localhost:8000/v1/models'",
        request=httpx.Request("GET", "http://localhost:8000/v1/models"),
        response=httpx.Response(404),
    )
    message = hint(VLLM_CONFIG, absent)

    assert "Could not reach vLLM" in message
    assert "is not serving" not in message


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
    """Re-import the table so its environment-derived defaults are read again."""

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
    """The one setting with no flag and no form field: it is a secret, so it does not land
    in a shell history and is not in what the API hands a browser."""
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
    """``_LIST_TIMEOUT`` on the client bounds one question; a listing asks many."""
    started = threading.Event()
    release = threading.Event()

    class Slow:
        def __init__(self, base_url: str, **kwargs) -> None:
            pass

        def show(self, name: str):
            started.set()
            release.wait(timeout=5.0)
            return SimpleNamespace(capabilities=[_COMPLETION])

        def close(self) -> None:
            pass

    def tags(url, **kwargs):
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"models": [{"model": name} for name in ("a:1", "b:1", "c:1")]},
        )

    monkeypatch.setattr("buy_agent.providers.httpx.get", tags)
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


# -- how long one question may take --------------------------------------------


def test_ollamas_client_is_given_the_wait_the_config_sets(chatting) -> None:
    """On the client because that is where ollama's own takes one."""
    sent = asked(AgentConfig(provider="ollama", model_timeout=12.5), chatting())

    assert sent["client"]["timeout"] == 12.5


def test_vllms_client_is_given_the_same_wait(completing) -> None:
    """Both servers are equally able to go quiet holding a prompt, so this is the
    one setting of its kind that needs no row on either provider."""
    sent = asked(AgentConfig(provider="vllm", model_timeout=12.5), completing())

    assert sent["client"]["timeout"] == 12.5


def test_vllm_is_asked_once(completing) -> None:
    """The OpenAI client retries twice by default, which would make the wait a
    shopper set a third of the wait they got -- and a 4.3k-token prompt is not one
    to send three times to a server already too slow for it (ADR-0051)."""
    sent = asked(VLLM_CONFIG, completing())

    assert sent["client"]["max_retries"] == 0


def test_the_listing_keeps_its_own_short_wait(pulled) -> None:
    """Deliberately not ``model_timeout``: that is how long a shopper will wait for
    an answer, and this is how long a page will wait to draw a dropdown."""
    asked_for = pulled(["gemma4:12b"])

    providers_module.OLLAMA.installed(AgentConfig(provider="ollama", model_timeout=600.0))

    assert asked_for["tags"]["timeout"] == providers_module._LIST_TIMEOUT


# -- a LiteLLM proxy (ADR-0067) ------------------------------------------------

LITELLM_CONFIG = AgentConfig(provider="litellm", model="local_model")


@pytest.fixture
def proxied(monkeypatch):
    """Stand in for a LiteLLM proxy answering ``/model/info`` and ``/v1/models``."""

    def install(
        info: list[dict] | None = None,
        *,
        models: list[str] = (),
        info_error: Exception | None = None,
        error: Exception | None = None,
    ) -> dict:
        asked: dict = {"urls": []}

        def get(url, **kwargs):
            asked["urls"].append(url)
            asked["headers"] = kwargs.get("headers")
            asked["timeout"] = kwargs.get("timeout")
            if error is not None:
                raise error
            if url.endswith("/model/info"):
                if info_error is not None:
                    raise info_error
                body = {"data": info or []}
            else:
                body = {"data": [{"id": name} for name in models]}

            class Response:
                @staticmethod
                def raise_for_status() -> None:
                    return None

                @staticmethod
                def json() -> dict:
                    return body

            return Response()

        monkeypatch.setattr("buy_agent.providers.httpx.get", get)
        return asked

    return install


def _alias(name: str, mode: str | None = "chat") -> dict:
    """One ``/model/info`` entry, the way the proxy reports a deployment."""
    return {"model_name": name, "model_info": {} if mode is None else {"mode": mode}}


def test_a_proxy_is_pointed_at_the_openai_api_it_serves(completing) -> None:
    """The same client as vLLM's, asked once and given the whole wait (ADR-0051)."""
    config = AgentConfig(
        provider="litellm",
        model="local_model",
        base_url="http://proxy.internal:4000/v1",
        temperature=0.2,
        model_timeout=12.5,
    )

    sent = asked(config, completing())

    assert sent["client"]["base_url"] == "http://proxy.internal:4000/v1"
    assert sent["client"]["timeout"] == 12.5
    assert sent["client"]["max_retries"] == 0
    assert sent["model"] == "local_model"
    assert sent["temperature"] == 0.2


def test_a_proxy_is_asked_to_decode_against_the_schema(completing) -> None:
    """The proxy translates ``response_format`` for whatever it routes to."""
    sent = asked(LITELLM_CONFIG, completing(), SearchQuery)

    assert sent["response_format"]["json_schema"]["schema"] == SearchQuery.model_json_schema()


def test_a_proxy_without_a_key_still_gets_one_and_a_key_reaches_it(completing) -> None:
    """A proxy started with no master key checks nothing, and the client insists."""
    sent = completing()
    asked(AgentConfig(provider="litellm", api_key=""), sent)
    assert sent["client"]["api_key"] == "EMPTY"

    asked(AgentConfig(provider="litellm", api_key="sk-1234"), sent)
    assert sent["client"]["api_key"] == "sk-1234"


@pytest.mark.parametrize(("reasoning", "effort"), [(True, "medium"), (False, "none")])
def test_a_proxy_carries_the_thinking_switch_as_its_own_effort(
    completing, reasoning: bool, effort: str
) -> None:
    """``reasoning_effort`` is LiteLLM's provider-neutral spelling, and vLLM's
    template switch would mean nothing to most of what a proxy routes to."""
    sent = asked(AgentConfig(provider="litellm", reasoning=reasoning), completing())

    assert sent["extra_body"] == {"reasoning_effort": effort}


def test_a_proxy_is_sent_nothing_the_server_behind_it_decides(completing) -> None:
    """No window, no device and, left alone, no thinking switch."""
    config = AgentConfig(provider="litellm", reasoning=None, num_ctx=8192, cpu_only=True)

    sent = asked(config, completing())

    assert sent["extra_body"] == {}
    assert "num_ctx" not in sent and "num_gpu" not in sent


def test_a_proxy_lists_every_alias_and_marks_what_cannot_answer(proxied) -> None:
    """``/model/info`` says what mode each alias is in -- ADR-0032's question, which a
    proxy can answer where vLLM's listing cannot."""
    proxied(
        [
            _alias("local_model"),
            _alias("embedder", "embedding"),
            _alias("unsaid", None),
            {"model_info": {"mode": "chat"}},
        ]
    )

    assert listed(LITELLM_CONFIG) == [
        installed("local_model", completion=True),
        installed("embedder", completion=False),
        installed("unsaid", completion=True),
    ]


def test_an_alias_answers_if_any_deployment_behind_it_does(proxied) -> None:
    """One alias can be several deployments, and the proxy lists each."""
    proxied(
        [
            _alias("local_model", "embedding"),
            _alias("local_model"),
            _alias("local_model", "embedding"),
        ]
    )

    assert listed(LITELLM_CONFIG) == [installed("local_model", completion=True)]


def test_the_proxy_is_asked_beside_its_api_root(proxied) -> None:
    """``/model/info`` is one of the proxy's own routes, not under ``/v1``."""
    asked_for = proxied([_alias("local_model")])

    listed(
        AgentConfig(
            provider="litellm", base_url="http://proxy.internal:4000/v1/", api_key="sk-1"
        )
    )

    assert asked_for["urls"] == ["http://proxy.internal:4000/model/info"]
    assert asked_for["headers"] == {"Authorization": "Bearer sk-1"}
    assert asked_for["timeout"] == providers_module._LIST_TIMEOUT


@pytest.mark.parametrize(
    "refusal",
    [
        httpx.HTTPStatusError(
            "Client error '403 Forbidden'",
            request=httpx.Request("GET", "http://localhost:4000/model/info"),
            response=httpx.Response(403),
        ),
        ValueError("Expecting value: line 1 column 1"),
    ],
)
def test_a_proxy_that_will_not_say_falls_back_to_the_openai_listing(proxied, refusal) -> None:
    """A key allowed only to call models, or an older proxy: "cannot say" is not
    "cannot run", so every model the plain listing names is offered."""
    asked_for = proxied(models=["local_model"], info_error=refusal)

    assert listed(LITELLM_CONFIG) == [installed("local_model", completion=True)]
    assert asked_for["urls"][-1] == "http://localhost:4000/v1/models"


def test_a_proxy_that_cannot_be_reached_raises_rather_than_reporting_nothing(proxied) -> None:
    proxied(error=httpx.ConnectError("refused"))

    with pytest.raises(httpx.ConnectError):
        listed(LITELLM_CONFIG)


def test_a_stopped_proxy_is_told_to_start_with_its_config() -> None:
    message = hint(LITELLM_CONFIG, openai.APIConnectionError(request=_REQUEST))

    assert "Could not reach LiteLLM" in message
    assert "litellm --config config.yaml" in message


def test_a_proxy_refusing_the_key_says_which_variable_sets_one() -> None:
    message = hint(LITELLM_CONFIG, _status_error(openai.AuthenticationError, 401))

    assert "$LITELLM_API_KEY" in message


@pytest.mark.parametrize(
    ("kind", "status", "said"),
    [
        (openai.BadRequestError, 400, "Invalid model name passed in model=local_model."),
        (openai.NotFoundError, 404, "The model `local_model` does not exist."),
    ],
)
def test_an_alias_the_proxy_does_not_route_names_what_it_does(
    proxied, kind, status: int, said: str
) -> None:
    """The remedy is the proxy's config, not a pull or a restart with a model name."""
    proxied([_alias("other_model")])
    response = httpx.Response(status, request=_REQUEST)
    message = hint(LITELLM_CONFIG, kind(said, response=response, body=None))

    assert "routing: other_model" in message
    assert "model_list" in message and "config.yaml" in message


def test_a_failure_the_proxy_relays_is_blamed_on_what_is_behind_it() -> None:
    """The proxy is running -- telling the shopper to start it would mislead."""
    relayed = openai.InternalServerError(
        "litellm.APIConnectionError: OllamaException - connection refused",
        response=httpx.Response(500, request=_REQUEST),
        body=None,
    )
    message = hint(LITELLM_CONFIG, relayed)

    assert "answered, but the model behind 'local_model' failed" in message
    assert "litellm --config" not in message


def test_a_404_from_something_that_is_not_a_proxy_is_not_a_missing_alias(proxied) -> None:
    """The rule the other two rows keep: a remedy naming a model needs the row's own
    client to have raised it."""
    proxied([_alias("local_model")])
    absent = httpx.HTTPStatusError(
        "Client error '404 Not Found'",
        request=httpx.Request("GET", "http://localhost:4000/model/info"),
        response=httpx.Response(404),
    )
    message = hint(LITELLM_CONFIG, absent)

    assert "Could not reach LiteLLM" in message
    assert "model_list" not in message


def test_an_unreadable_answer_names_the_room_behind_the_proxy() -> None:
    """The window belongs to whatever the proxy routes to, so the sentence names neither
    Ollama's per-request setting nor vLLM's startup flag."""
    message = hint(LITELLM_CONFIG, UnreadableAnswerError("Invalid json output: {"))

    assert "not the JSON this asks for" in message
    assert "the model the proxy routes to" in message
    assert "--max-model-len" not in message


def test_a_slow_proxy_is_not_told_to_start_one() -> None:
    message = hint(LITELLM_CONFIG, openai.APITimeoutError(request=_REQUEST))

    assert "did not answer in time" in message
    assert "litellm --config" not in message


@pytest.mark.parametrize(
    "error",
    [
        openai.APIConnectionError(request=_REQUEST),
        httpx.ConnectError("refused"),
        OSError("socket died"),
    ],
)
def test_every_way_a_proxy_can_be_absent_is_one_the_agent_catches(error) -> None:
    assert isinstance(error, errors(LITELLM_CONFIG))


def test_the_proxy_has_its_own_variables(reloaded_providers) -> None:
    """A machine can run a proxy beside the servers it routes to, so its address is
    its own and not $OLLAMA_HOST's."""
    reloaded_providers(
        LITELLM_MODEL="team-llama",
        LITELLM_HOST="http://proxy.internal:4000/v1",
        LITELLM_API_KEY="sk-1234",
    )
    config = AgentConfig(provider="litellm")

    assert config.model == "team-llama"
    assert config.base_url == "http://proxy.internal:4000/v1"
    assert config.api_key == "sk-1234"
    assert AgentConfig(provider="ollama").api_key == ""


def test_the_proxy_defaults_are_a_local_one_with_a_placeholder_alias(
    reloaded_providers, monkeypatch
) -> None:
    """Port 4000 is what ``litellm --config`` binds; the alias is somebody's own
    ``model_list`` and nothing here can know it, so the default names nobody's model."""
    for name in ("LITELLM_MODEL", "LITELLM_HOST", "LITELLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    reloaded_providers()
    config = AgentConfig(provider="litellm")

    assert config.base_url == "http://localhost:4000/v1"
    assert config.model == "local_model"
    assert config.api_key == ""


def test_closing_a_proxy_chat_model_lets_go_of_its_client(completing) -> None:
    sent = completing()

    chat_model(LITELLM_CONFIG).close()

    assert sent["closed"] is True


@pytest.mark.parametrize(
    ("base_url", "root"),
    [
        ("http://localhost:4000/v1", "http://localhost:4000"),
        ("http://localhost:4000/v1/", "http://localhost:4000"),
        # A proxy mounted without the ``/v1`` prefix answers its routes where it is.
        ("http://localhost:4000", "http://localhost:4000"),
        ("http://gateway.internal/litellm/v1", "http://gateway.internal/litellm"),
    ],
)
def test_the_proxy_root_is_the_api_root_less_its_version(
    proxied, base_url: str, root: str
) -> None:
    asked_for = proxied([_alias("local_model")])

    listed(AgentConfig(provider="litellm", base_url=base_url))

    assert asked_for["urls"] == [f"{root}/model/info"]


def test_a_proxy_without_a_key_is_listed_with_no_header(proxied) -> None:
    asked_for = proxied([_alias("local_model")])

    listed(AgentConfig(provider="litellm", api_key=""))

    assert asked_for["headers"] == {}


def test_the_fallback_listing_carries_the_key_too(proxied) -> None:
    """A proxy that refused ``/model/info`` over the key's scope still wants that key
    on ``/v1/models``, or the fallback fails for a second reason."""
    refused = httpx.HTTPStatusError(
        "Client error '403 Forbidden'",
        request=httpx.Request("GET", "http://localhost:4000/model/info"),
        response=httpx.Response(403),
    )
    asked_for = proxied(models=["local_model"], info_error=refused)

    listed(AgentConfig(provider="litellm", api_key="sk-1"))

    assert asked_for["headers"] == {"Authorization": "Bearer sk-1"}


def test_an_entry_with_no_info_at_all_is_still_offered(proxied) -> None:
    """``model_info`` is null for a deployment the proxy knows nothing about, which is
    "cannot say" and so offered rather than hidden."""
    proxied([{"model_name": "bare", "model_info": None}, {"model_name": "missing"}])

    assert listed(LITELLM_CONFIG) == [
        installed("bare", completion=True),
        installed("missing", completion=True),
    ]


def test_a_proxy_routing_nothing_lists_nothing(proxied) -> None:
    """An empty ``/model/info`` is an answer, so there is no fallback to ask."""
    asked_for = proxied([])

    assert listed(LITELLM_CONFIG) == []
    assert len(asked_for["urls"]) == 1


def test_an_alias_the_proxy_does_not_route_says_so_even_when_it_routes_nothing(
    proxied,
) -> None:
    proxied([])
    response = httpx.Response(400, request=_REQUEST)
    message = hint(
        LITELLM_CONFIG,
        openai.BadRequestError(
            "Invalid model name passed in model=x", response=response, body=None
        ),
    )

    assert "routing: none" in message


def test_an_alias_the_proxy_does_not_route_still_says_so_when_it_cannot_list(
    proxied,
) -> None:
    """The second failure must not replace the sentence being written about the first."""
    proxied(error=httpx.ConnectError("refused"))
    message = hint(LITELLM_CONFIG, _status_error(openai.NotFoundError, 404))

    assert "routing: unknown" in message
    assert "model_list" in message


def test_the_models_offered_for_a_missing_alias_are_every_one_it_routes(proxied) -> None:
    proxied([_alias("local_model"), _alias("embedder", "embedding")])
    config = AgentConfig(provider="litellm", model="gone")
    message = hint(config, _status_error(openai.NotFoundError, 404))

    assert "routing: local_model, embedder" in message
    assert "'gone'" in message


def test_a_relayed_failure_quotes_what_the_proxy_said() -> None:
    """The proxy's message names the server it routed to, which is the useful half."""
    relayed = openai.BadRequestError(
        "litellm.BadRequestError: OllamaException - response_format not supported",
        response=httpx.Response(400, request=_REQUEST),
        body=None,
    )
    message = hint(LITELLM_CONFIG, relayed)

    assert "response_format not supported" in message
    assert "proxy's own log" in message


def test_a_proxy_rate_limit_is_blamed_on_what_is_behind_it() -> None:
    """A 429 is an answer from a running proxy, not a server to start."""
    limited = openai.RateLimitError(
        "litellm.RateLimitError: rpm limit reached for local_model",
        response=httpx.Response(429, request=_REQUEST),
        body=None,
    )
    message = hint(LITELLM_CONFIG, limited)

    assert "answered, but the model behind 'local_model' failed" in message
    assert "Could not reach" not in message


def test_the_openai_client_and_the_proxy_listing_share_one_failure_vocabulary() -> None:
    """Same client as vLLM's, so the same tuple -- and none of Ollama's own classes."""
    assert errors(LITELLM_CONFIG) == errors(VLLM_CONFIG)
    assert ResponseError not in errors(LITELLM_CONFIG)


def test_every_row_words_its_own_room_and_names_no_flag_of_this_cli() -> None:
    """``more_room`` ends a sentence both doors show, so it is the setting's name and
    never one of this project's flags -- ``--max-model-len`` is vLLM's own."""
    rooms = [server.more_room for server in providers_module.PROVIDERS.values()]

    assert all(rooms), "a row with no wording leaves the sentence ending in a colon"
    assert len(set(rooms)) == len(rooms), "each server has its own way to get more room"
    for room in rooms:
        assert "--num-ctx" not in room and "--think" not in room


@pytest.mark.parametrize("provider", ["ollama", "vllm", "litellm"])
def test_an_unreadable_answer_ends_with_that_row_s_own_room(provider: str) -> None:
    config = AgentConfig(provider=provider)
    message = hint(config, UnreadableAnswerError("Invalid json output: {"))

    assert message.endswith(f"{config.model_server.more_room}.")


def test_a_slow_proxy_is_told_to_shorten_the_prompt_not_the_window() -> None:
    """The proxy takes no per-run window, so the too-slow hint names the other lever."""
    message = hint(LITELLM_CONFIG, httpx.ReadTimeout("timed out"))

    assert "a shorter prompt" in message
    assert "a smaller context window" not in message


def test_the_proxy_is_offered_to_the_form_without_its_key() -> None:
    options = {option["name"]: option for option in provider_options()}

    assert options["litellm"]["label"] == "LiteLLM"
    assert options["litellm"]["base_url"] == providers_module.LITELLM.base_url
    assert "api_key" not in options["litellm"]
