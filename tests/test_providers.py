"""The four model servers, and the four things each of them answers differently."""

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

#: Every server, every one reached through the OpenAI client, and every one rendering the
#: thinking switch into its chat template -- read off the table, so a new row is tried
#: wherever it belongs without editing each list. Names only: the rows themselves would
#: go stale on a reload.
_EVERY = list(providers_module.PROVIDERS)
_OPENAI_STYLE = [
    name
    for name, row in providers_module.PROVIDERS.items()
    if row.chat_model is not providers_module.OLLAMA.chat_model
]
_TEMPLATED = [
    name
    for name, row in providers_module.PROVIDERS.items()
    if row.chat_model is providers_module.VLLM.chat_model
]

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

    def install(
        answered: str = '{"query": "a refined query"}', *, choices: list | None = None
    ) -> dict:
        class FakeOpenAI:
            def __init__(self, **kwargs) -> None:
                sent["client"] = kwargs
                self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

            @staticmethod
            def close() -> None:
                sent["closed"] = True

        def create(**kwargs):
            sent.update(kwargs)
            if choices is not None:
                return SimpleNamespace(choices=choices)
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


# -- building the chat model ---------------------------------------------------


def asked(config: AgentConfig, sent: dict, schema: type = SearchQuery) -> dict:
    """Put one question to the config's provider, and hand back what it sent."""
    config.model_server.chat_model(config).answer(
        [{"role": "user", "content": "headphones"}], schema
    )
    return sent


#: A conversation of both turns, as ``chat.Chain`` hands one over.
_CONVERSATION = [
    {"role": "system", "content": "Rewrite the request as a shopping query."},
    {"role": "user", "content": "headphones under $200"},
]


@pytest.mark.parametrize("provider", _OPENAI_STYLE)
def test_the_prompt_is_what_an_openai_compatible_server_is_asked(
    completing, provider: str
) -> None:
    sent = completing()

    chat_model(AgentConfig(provider=provider)).answer(_CONVERSATION, SearchQuery)

    assert sent["messages"] == _CONVERSATION


def test_ollama_is_told_to_offload_nothing_for_a_cpu_only_run(chatting) -> None:
    """``num_gpu`` is how many layers go to the card, so none of them is zero."""
    sent = asked(AgentConfig(provider="ollama", cpu_only=True), chatting())

    assert sent["options"]["num_gpu"] == 0


@pytest.mark.parametrize("provider", _OPENAI_STYLE)
def test_an_openai_style_server_is_pointed_at_the_api_it_serves(
    completing, provider: str
) -> None:
    config = AgentConfig(
        provider=provider,
        model="Qwen/Qwen3-8B",
        base_url="http://gpu.internal:8000/v1",
        temperature=0.2,
    )

    sent = asked(config, completing())

    assert sent["client"]["base_url"] == "http://gpu.internal:8000/v1"
    assert sent["client"]["max_retries"] == 0, "the model is asked once (ADR-0051)"
    assert sent["model"] == "Qwen/Qwen3-8B"
    assert sent["temperature"] == 0.2


@pytest.mark.parametrize("provider", _OPENAI_STYLE)
def test_an_openai_style_server_is_asked_to_decode_against_the_schema(
    completing, provider: str
) -> None:
    """The same constraint as Ollama's ``format``, in the OpenAI API's spelling."""
    sent = asked(AgentConfig(provider=provider), completing(), SearchQuery)

    assert sent["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "SearchQuery",
            "schema": SearchQuery.model_json_schema(),
        },
    }


@pytest.mark.parametrize("provider", _TEMPLATED)
@pytest.mark.parametrize("reasoning", [True, False])
def test_vllm_carries_the_thinking_switch_its_templates_read(
    completing, reasoning: bool, provider: str
) -> None:
    """``enable_thinking`` is what the chat templates of the thinking models vLLM and
    ``trtllm-serve`` render look for, and ADR-0019's default of off has to reach them."""
    sent = asked(AgentConfig(provider=provider, reasoning=reasoning), completing())

    assert sent["extra_body"] == {"chat_template_kwargs": {"enable_thinking": reasoning}}


@pytest.mark.parametrize("provider", _TEMPLATED)
def test_vllm_sends_nothing_when_thinking_is_left_alone(completing, provider: str) -> None:
    """The tri-state's third value: send nothing, and let the template decide."""
    sent = asked(AgentConfig(provider=provider, reasoning=None), completing())

    assert sent["extra_body"] == {}


@pytest.mark.parametrize("provider", _EVERY)
def test_a_server_that_answers_with_nothing_says_so(
    chatting, completing, provider: str
) -> None:
    """A dropped stream leaves an empty message, and "Invalid JSON answer:" with
    nothing after it names no symptom at all."""
    chatting("")
    completing("")

    with pytest.raises(UnreadableAnswerError, match="nothing at all"):
        asked(AgentConfig(provider=provider), {})


@pytest.mark.parametrize("provider", _OPENAI_STYLE)
def test_an_answer_with_no_choice_in_it_is_one_with_nothing_to_read(
    completing, provider: str
) -> None:
    """An OpenAI-style server can answer with an empty ``choices`` list. Indexed, that
    was an ``IndexError`` out of the run -- a failure that is none of its three, so a
    500 in the browser and a traceback on the CLI (ADR-0009)."""
    completing(choices=[])

    with pytest.raises(UnreadableAnswerError, match="nothing at all"):
        asked(AgentConfig(provider=provider), {})


# -- what the server is serving ------------------------------------------------


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


def test_a_tag_listed_twice_on_two_builds_is_offered_once_with_no_build(pulled) -> None:
    """Ollama 0.40 converts a model on its first load and then lists the tag once per
    runner, plus the converted weights under the runner's own alias. Which build
    answers is not said, so none is claimed (ADR-0075), and the alias, which nobody
    pulled, is not offered beside the tag it duplicates."""
    converted = "c97eb11d70b1" + "0" * 52
    pulled(
        [],
        entries=[
            {"model": "qwen3.5:9b", "digest": "2e16a80fe3d7" + "0" * 52},
            {"model": "qwen3.5:9b", "digest": converted},
            {"model": f"llamacpp:{converted}", "digest": converted},
            {"model": "gemma4:12b", "digest": "4eb23ef187e2" + "0" * 52},
            {"model": "gemma4:12b", "digest": "4eb23ef187e2" + "0" * 52},
        ],
    )

    assert [(model.name, model.digest[:12]) for model in listed(OLLAMA_CONFIG)] == [
        ("qwen3.5:9b", ""),
        ("gemma4:12b", "4eb23ef187e2"),
    ]


@pytest.mark.parametrize("base_url", ["localhost:11434", "http://localhost:11434/"])
def test_the_address_is_asked_however_it_was_written(pulled, base_url: str) -> None:
    """``$OLLAMA_HOST`` is written every way -- with the scheme and without it, with a
    trailing slash and without -- and ollama's client takes all of them for the chat."""
    asked = pulled(["gemma4:12b"])

    listed(AgentConfig(provider="ollama", base_url=base_url))

    assert asked["tags"]["url"] == "http://localhost:11434/api/tags"


@pytest.mark.parametrize(
    ("base_url", "url"),
    [
        ("0.0.0.0", "http://0.0.0.0:11434/api/tags"),
        ("localhost", "http://localhost:11434/api/tags"),
        ("gpu-box.lan/ollama/", "http://gpu-box.lan:11434/ollama/api/tags"),
        ("[::1]", "http://[::1]:11434/api/tags"),
        (":11435", "http://127.0.0.1:11435/api/tags"),
        # A scheme brings its own port, as it does to the client.
        ("http://gpu-box.lan", "http://gpu-box.lan/api/tags"),
    ],
)
def test_an_address_with_no_scheme_is_on_ollamas_own_port(
    pulled, base_url: str, url: str
) -> None:
    """``OLLAMA_HOST=0.0.0.0`` is how Ollama is told to listen everywhere, and its client
    reads the address as port 11434. The listing read it as HTTP's port 80, so a server
    every run reached was unreachable in the picker and "unknown" in every hint."""
    asked = pulled(["gemma4:12b"])

    listed(AgentConfig(provider="ollama", base_url=base_url))

    assert asked["tags"]["url"] == url


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


def test_every_tag_is_probed_at_the_address_that_was_listed(pulled) -> None:
    """``ollama show`` asked of the default host while ``/api/tags`` was read off
    another would mark one machine's models with another's capabilities."""
    asked = pulled(["gemma4:12b"])

    listed(AgentConfig(provider="ollama", base_url="http://gpu-box.lan:11434"))

    assert asked["opened"]["base_url"] == "http://gpu-box.lan:11434"


def test_a_tag_that_will_not_say_what_it_can_do_is_still_offered(pulled) -> None:
    """The probe failed; nothing was learnt."""
    pulled(["gemma4:12b", "qwen3:8b"], capabilities={"gemma4:12b": [_COMPLETION]})

    assert listed(OLLAMA_CONFIG) == [
        installed("gemma4:12b", completion=True),
        installed("qwen3:8b", completion=True),
    ]


@pytest.mark.parametrize("provider", _TEMPLATED)
def test_the_listing_is_asked_of_the_api_root_the_config_names(serving, provider: str) -> None:
    """``base_url`` already ends at the API root, so the path is appended to it --
    a second ``/v1`` or a stripped one is a 404 the picker would show as "down"."""
    asked = serving(["Qwen/Qwen3-8B"])
    config = AgentConfig(provider=provider, base_url="http://gpu.internal:8000/v1/")

    assert names(config) == ["Qwen/Qwen3-8B"]
    assert asked["url"] == "http://gpu.internal:8000/v1/models"


# -- what a failure says -------------------------------------------------------


def test_a_stopped_vllm_is_told_to_serve_the_model_it_was_asked_for() -> None:
    """Unlike Ollama's, this command needs the model in it: a vLLM is started for
    one model, so "start it" and "start it with this" are the same sentence."""
    message = hint(VLLM_CONFIG, openai.APIConnectionError(request=_REQUEST))

    assert "vllm serve Qwen/Qwen3-8B" in message
    assert VLLM_CONFIG.base_url in message
    assert "(Connection error.)" in message, "with the transport's own words"


def test_a_refused_vllm_key_quotes_the_refusal() -> None:
    response = httpx.Response(401, request=_REQUEST)
    refused = openai.AuthenticationError("Invalid API key: sk-...", response=response, body=None)

    message = hint(VLLM_CONFIG, refused)

    assert "refused the API key (Invalid API key: sk-...)" in message
    assert "$VLLM_API_KEY" in message


def test_a_slow_ollama_is_offered_the_window_it_can_be_given() -> None:
    """The remedy is the row's: a smaller window is something Ollama takes per run,
    where vLLM fixed its own at startup and can only be sent a shorter prompt."""
    assert "or a smaller context window." in hint(OLLAMA_CONFIG, httpx.ReadTimeout("slow"))
    assert "or a shorter prompt." in hint(VLLM_CONFIG, httpx.ReadTimeout("slow"))


def test_a_timeout_that_says_nothing_is_named_by_its_kind() -> None:
    """httpx raises its timeouts with no message at all, which would leave "()" in the
    middle of the one sentence the shopper gets."""
    assert "did not answer in time (ReadTimeout)" in hint(OLLAMA_CONFIG, httpx.ReadTimeout(""))
    assert "did not answer in time (timed out)" in hint(
        OLLAMA_CONFIG, httpx.ReadTimeout("timed out")
    )


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


def test_an_unreadable_answer_that_says_nothing_is_named_by_its_kind() -> None:
    """There is no first line of nothing to quote: indexed, the hint about a broken
    answer would itself have broken."""
    message = hint(OLLAMA_CONFIG, UnreadableAnswerError("  "))

    assert "not the JSON this asks for (UnreadableAnswerError)." in message


def test_a_refused_key_says_which_variable_sets_one() -> None:
    """The one failure that is neither "not running" nor "not serving that", and
    the only one whose fix is an environment variable rather than a command."""
    message = hint(VLLM_CONFIG, _status_error(openai.AuthenticationError, 401))

    assert "$VLLM_API_KEY" in message
    assert "--api-key" in message


def test_a_model_vllm_reports_as_not_found_is_one_it_is_not_serving(serving) -> None:
    """The other way a vLLM words a model it does not have; this one as plain as Ollama's
    for a tag it has not pulled."""
    serving(["Qwen/Qwen3-0.6B"])
    response = httpx.Response(404, request=_REQUEST)
    refused = openai.NotFoundError("Model Qwen/Qwen3-8B not found", response=response, body=None)

    message = hint(VLLM_CONFIG, refused)

    assert "is not serving 'Qwen/Qwen3-8B'" in message
    assert "serving: Qwen/Qwen3-0.6B" in message


@pytest.mark.parametrize(
    ("provider", "command"), [("vllm", "vllm serve"), ("trtllm", "trtllm-serve")]
)
def test_a_one_model_server_that_cannot_be_listed_still_says_how_to_restart_it(
    serving, provider: str, command: str
) -> None:
    """Whatever else is broken, the command is still the thing to try -- and the
    second failure must not replace the message being written about the first."""
    serving([], error=httpx.ConnectError("refused"))
    config = AgentConfig(provider=provider, model="Qwen/Qwen3-8B")

    message = hint(config, _status_error(openai.NotFoundError, 404))

    assert "serving: unknown" in message
    assert f"{command} Qwen/Qwen3-8B" in message


@pytest.mark.parametrize("provider", _EVERY)
def test_a_listing_with_no_list_in_it_is_a_server_serving_nothing(
    monkeypatch, provider: str
) -> None:
    """An object without its ``models`` or ``data`` -- a proxy in front, a build that
    leaves an empty list out -- is nothing served. Read as a failed listing, the picker
    would say a server that had just answered could not be reached."""

    class Answered:
        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict:
            return {}

    monkeypatch.setattr("buy_agent.providers.httpx.get", lambda url, **_: Answered())

    assert listed(AgentConfig(provider=provider)) == []


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


def test_an_ollama_serving_nothing_that_answers_reports_none(pulled) -> None:
    """The empty case of that narrowing: tags are pulled, none of them can chat."""
    pulled(["nomic-embed-text"], capabilities={"nomic-embed-text": [_EMBEDDING]})
    config = AgentConfig(provider="ollama", model="nomic-embed-text")
    message = hint(config, ResponseError('"nomic-embed-text" does not support chat', 400))

    assert "installed: none" in message


# -- what counts as "the server is not there" ----------------------------------


@pytest.mark.parametrize("provider", _OPENAI_STYLE)
@pytest.mark.parametrize(
    "failure",
    [
        openai.APIConnectionError(request=_REQUEST),
        httpx.ConnectError("refused"),
        ConnectionRefusedError("refused"),
        # The socket's own, for a host it cannot encode (``192.168.1..5``).
        UnicodeError("label empty or too long"),
    ],
)
def test_an_openai_style_server_counts_every_way_of_not_being_there(
    failure: Exception, provider: str
) -> None:
    assert isinstance(failure, AgentConfig(provider=provider).model_server.transport_errors)


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


@pytest.mark.parametrize("provider", _OPENAI_STYLE)
def test_the_key_is_read_from_the_environment(reloaded_providers, provider: str) -> None:
    """The one setting with no flag and no form field: it is a secret, so it does not land
    in a shell history and is not in what the API hands a browser."""
    reloaded_providers(**{f"{provider.upper()}_API_KEY": "s3cret"})

    assert AgentConfig(provider=provider).api_key == "s3cret"


def _status_error(kind: type[openai.APIStatusError], status: int) -> openai.APIStatusError:
    """One of the OpenAI client's status errors, built the way the client builds it."""
    response = httpx.Response(status, request=_REQUEST)
    return kind("The model `Qwen/Qwen3-8B` does not exist.", response=response, body=None)


def test_the_listing_budget_covers_the_listing_and_not_each_tag(monkeypatch) -> None:
    """``_LIST_TIMEOUT`` on the client bounds one question; a listing asks many."""
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    class Slow:
        def __init__(self, base_url: str, **kwargs) -> None:
            pass

        def show(self, name: str):
            started.set()
            release.wait(timeout=5.0)
            finished.set()
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
        # Read before the release: a listing that waited out its probes -- in ``wait``,
        # in ``result`` or in the pool's shutdown -- answers only once they gave up.
        waited_for_them = finished.is_set()
    finally:
        # Before the assertions: a probe still blocked here is a non-daemon pool
        # thread, and the interpreter joins those on the way out.
        release.set()

    assert not waited_for_them, "the listing answered on its budget, not the probes'"
    assert started.is_set(), "the probes did go out"
    assert [model.name for model in models] == ["a:1", "b:1", "c:1"]
    assert all(model.completion for model in models), "a tag that did not say is offered"


# -- how long one question may take --------------------------------------------


def test_ollamas_client_is_given_the_wait_the_config_sets(chatting) -> None:
    """On the client because that is where ollama's own takes one."""
    sent = asked(AgentConfig(provider="ollama", model_timeout=12.5), chatting())

    assert sent["client"]["timeout"] == 12.5


def test_the_listing_keeps_its_own_short_wait(pulled) -> None:
    """Deliberately not ``model_timeout``: that is how long a shopper will wait for
    an answer, and this is how long a page will wait to draw a dropdown."""
    asked_for = pulled(["gemma4:12b"])

    providers_module.OLLAMA.installed(AgentConfig(provider="ollama", model_timeout=600.0))

    assert asked_for["tags"]["timeout"] == providers_module._LIST_TIMEOUT


# -- a LiteLLM proxy (ADR-0068) ------------------------------------------------

LITELLM_CONFIG = AgentConfig(provider="litellm", model="local_model")


@pytest.mark.parametrize(
    ("reasoning", "extra_body"),
    [
        (None, {}),
        (True, {"reasoning_effort": "medium"}),
        (False, {"reasoning_effort": "none"}),
    ],
)
def test_a_proxy_is_asked_through_the_openai_client(
    completing, reasoning: bool | None, extra_body: dict
) -> None:
    """vLLM's client, asked once, with thinking in LiteLLM's own spelling and neither
    the window nor the device, both of which belong to what the proxy routes to."""
    config = AgentConfig(
        provider="litellm", reasoning=reasoning, num_ctx=8192, cpu_only=True, model_timeout=9
    )

    sent = asked(config, completing())

    assert sent["client"] == {
        "base_url": "http://localhost:4000/v1",
        "api_key": "EMPTY",
        "timeout": 9,
        "max_retries": 0,
    }
    assert sent["model"] == "local_model"
    assert sent["response_format"]["json_schema"]["schema"] == SearchQuery.model_json_schema()
    assert sent["extra_body"] == extra_body


def _proxy(monkeypatch, info: Exception | list[dict], models: tuple[str, ...] = ()) -> list:
    """Stand in for a proxy's ``/model/info`` and ``/v1/models``, recording each ask."""
    asked_for: list = []

    def get(url, **kwargs):
        asked_for.append((url, kwargs["headers"], kwargs["timeout"]))
        if url.endswith("/model/info") and isinstance(info, Exception):
            raise info
        data = info if url.endswith("/model/info") else [{"id": name} for name in models]
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"data": data})

    monkeypatch.setattr("buy_agent.providers.httpx.get", get)
    return asked_for


def test_an_alias_that_can_chat_anywhere_is_offered_whichever_deployment_is_last(
    monkeypatch,
) -> None:
    """The other order from the test below: a chat deployment listed first and an
    embedding one after it is still an alias that answers a prompt."""
    _proxy(
        monkeypatch,
        [
            {"model_name": "local_model", "model_info": {"mode": "chat"}},
            {"model_name": "local_model", "model_info": {"mode": "embedding"}},
        ],
    )

    assert listed(LITELLM_CONFIG) == [installed("local_model", completion=True)]


def test_a_proxy_lists_every_alias_marked_by_its_mode(monkeypatch) -> None:
    """One alias may be several deployments, and answers if any of them does; a mode
    nobody reported is "cannot say", which is offered (ADR-0032)."""
    asked_for = _proxy(
        monkeypatch,
        [
            {"model_name": "local_model", "model_info": {"mode": "embedding"}},
            {"model_name": "local_model", "model_info": {"mode": "chat"}},
            {"model_name": "embedder", "model_info": {"mode": "embedding"}},
            {"model_name": "unsaid", "model_info": None},
            {"model_info": {"mode": "chat"}},
        ],
    )
    config = AgentConfig(provider="litellm", base_url="http://proxy:4000/v1/", api_key="k")

    assert listed(config) == [
        installed("local_model", completion=True),
        installed("embedder", completion=False),
        installed("unsaid", completion=True),
    ]
    assert asked_for == [
        ("http://proxy:4000/model/info", {"Authorization": "Bearer k"}, providers_module._LIST_TIMEOUT)
    ]


@pytest.mark.parametrize(
    "refusal",
    [httpx.HTTPStatusError("403", request=_REQUEST, response=httpx.Response(403)), ValueError()],
)
def test_a_proxy_that_will_not_say_falls_back_to_the_plain_listing(
    monkeypatch, refusal: Exception
) -> None:
    asked_for = _proxy(monkeypatch, refusal, models=("local_model",))

    assert listed(LITELLM_CONFIG) == [installed("local_model", completion=True)]
    assert asked_for[-1] == ("http://localhost:4000/v1/models", {}, providers_module._LIST_TIMEOUT)


def _answered(kind: type[openai.APIStatusError], status: int, said: str) -> Exception:
    return kind(said, response=httpx.Response(status, request=_REQUEST), body=None)


@pytest.mark.parametrize(
    ("exc", "says", "never"),
    [
        (openai.APIConnectionError(request=_REQUEST), "litellm --config config.yaml", "model_list"),
        (_answered(openai.AuthenticationError, 401, "no"), "$LITELLM_API_KEY", "litellm --config"),
        (
            _answered(openai.BadRequestError, 400, "Invalid model name passed in model=x"),
            "(routing: embedder), or add it to the model_list",
            "litellm --config",
        ),
        (
            # A key a proxy with no database cannot look up, answered with a 400.
            _answered(openai.BadRequestError, 400, "No connected db."),
            "$LITELLM_API_KEY",
            "answered, but",
        ),
        (
            # ...and the same refusal, met first by the listing.
            httpx.HTTPStatusError(
                "400",
                request=_REQUEST,
                response=httpx.Response(400, text='{"error":{"message":"No connected db."}}'),
            ),
            "$LITELLM_API_KEY",
            "Could not reach",
        ),
        (
            # The routed server's own miss, relayed: the alias is the proxy's.
            _answered(
                openai.NotFoundError,
                404,
                "litellm.NotFoundError: Ollama_chatException - model 'qwen3' not found",
            ),
            "answered, but the model behind 'local_model' failed (litellm.NotFoundError",
            "model_list",
        ),
        (
            _answered(openai.RateLimitError, 429, "rpm limit reached"),
            "answered, but the model behind 'local_model' failed (rpm limit reached)",
            "Could not reach",
        ),
        (
            # A 404 the OpenAI client did not raise is not the proxy's answer.
            httpx.HTTPStatusError("404", request=_REQUEST, response=httpx.Response(404)),
            "Could not reach LiteLLM",
            "model_list",
        ),
        (httpx.ReadTimeout("slow"), "or a shorter prompt", "litellm --config"),
        (
            UnreadableAnswerError("Invalid json output: {"),
            "the model the proxy routes to a larger context window",
            "--max-model-len",
        ),
    ],
)
def test_a_proxy_failure_names_what_to_do_about_it(
    monkeypatch, exc: Exception, says: str, never: str
) -> None:
    _proxy(monkeypatch, [{"model_name": "embedder", "model_info": {"mode": "embedding"}}])

    message = hint(LITELLM_CONFIG, exc)

    assert says in message
    assert never not in message
    assert LITELLM_CONFIG.base_url in message, "every one of them names the proxy asked"


def test_an_unreachable_proxy_quotes_the_transport() -> None:
    message = hint(LITELLM_CONFIG, httpx.ConnectError("connection refused"))

    assert "(connection refused)" in message
    assert "litellm --config config.yaml" in message


# -- TensorRT-LLM --------------------------------------------------------------

TRTLLM_CONFIG = AgentConfig(provider="trtllm", model="Qwen/Qwen3-8B")


def test_a_trtllm_started_without_guided_decoding_is_told_how_to_turn_it_on() -> None:
    """Without a backend for it, ``trtllm-serve`` cannot honour ``response_format`` --
    the one failure of this row that vLLM's never has."""
    refused = _answered(
        openai.BadRequestError, 400, "Guided decoding is not enabled for this server"
    )

    message = hint(TRTLLM_CONFIG, refused)

    assert "guided_decoding_backend: xgrammar" in message
    assert "trtllm-serve Qwen/Qwen3-8B --extra_llm_api_options" in message
    assert "(Guided decoding is not enabled for this server)" in message


@pytest.mark.parametrize(
    ("exc", "says"),
    [
        (openai.APIConnectionError(request=_REQUEST), "Start it with:  trtllm-serve Qwen/Qwen3-8B"),
        (_status_error(openai.AuthenticationError, 401), "Set $TRTLLM_API_KEY"),
        (
            _status_error(openai.NotFoundError, 404),
            "A TensorRT-LLM process serves one model",
        ),
        (httpx.ReadTimeout("slow"), "or a shorter prompt."),
        (UnreadableAnswerError("Invalid json output: {"), "guided decoding on"),
    ],
)
def test_a_trtllm_failure_names_its_own_command_and_variable(
    serving, exc: Exception, says: str
) -> None:
    serving(["Qwen/Qwen3-8B"])

    message = hint(TRTLLM_CONFIG, exc)

    assert says in message
    assert f"TensorRT-LLM at {TRTLLM_CONFIG.base_url}" in message
    assert "vllm serve" not in message and "$VLLM_API_KEY" not in message


def test_a_guided_decoding_mention_the_server_did_not_make_is_not_its_remedy() -> None:
    """Only the row's own client carries the server's words (``_answered_by``); a
    transport error that happens to say "guided" is still nothing answering."""
    message = hint(TRTLLM_CONFIG, httpx.ConnectError("guided tour of a refused socket"))

    assert "guided_decoding_backend" not in message
    assert "Could not reach TensorRT-LLM" in message


def test_trtllm_is_its_own_row_and_takes_neither_per_run_setting() -> None:
    """Fixed at startup, as vLLM's are: the form disables both boxes for it."""
    row = providers_module.provider_for("trtllm")

    assert row is providers_module.TRTLLM
    assert (row.takes_num_ctx, row.takes_cpu_only) == (False, False)
    assert row.chat_model is providers_module.VLLM.chat_model, "one client, shared"
    assert row.installed is providers_module.VLLM.installed, "one listing, shared"


def test_the_trtllm_defaults_are_read_from_its_own_variables(reloaded_providers) -> None:
    reloaded_providers(TRTLLM_MODEL="meta-llama/Llama-3.1-8B", TRTLLM_HOST="http://gpu:9000/v1")

    config = AgentConfig(provider="trtllm")

    assert (config.model, config.base_url) == ("meta-llama/Llama-3.1-8B", "http://gpu:9000/v1")
    assert AgentConfig(provider="vllm").base_url != "http://gpu:9000/v1", "vLLM's are its own"
