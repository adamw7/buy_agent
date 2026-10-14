"""The model servers -- Ollama, vLLM, a LiteLLM proxy, TensorRT-LLM -- one row each
(ADR-0028, ADR-0029, ADR-0032, ADR-0051, ADR-0068, ADR-0081)."""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, TypeAlias, cast
from urllib.parse import urlsplit

import httpx
import openai
from ollama import Client, RequestError, ResponseError

from buy_agent.chat import ChatModel, SchemaT, UnreadableAnswerError, read_answer

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from openai.types.chat import ChatCompletionMessageParam

    from buy_agent.chat import Message
    from buy_agent.config import AgentConfig

#: The OpenAI client refuses to send no key, and keyless servers ignore this one.
_NO_KEY = "EMPTY"

#: The budget for a whole model listing (ADR-0032, ADR-0051).
_LIST_TIMEOUT = 5.0

#: The Ollama capability needed to answer a prompt (ADR-0032).
_COMPLETION = "completion"

#: Why an answer stopped, in Ollama's ``done_reason`` and the OpenAI API's
#: ``finish_reason`` alike, when the server's length limit stopped it.
_CUT_OFF = "length"

#: Turns a row's failure into a remedy sentence.
Hint: TypeAlias = "Callable[[AgentConfig, Exception], str]"

_TIMEOUTS = (httpx.TimeoutException, openai.APITimeoutError)

#: What "the server is not there" raises through the OpenAI client, for every row that
#: speaks its API. ``openai.OpenAIError`` leaves the socket's ``UnicodeError`` unwrapped.
_OPENAI_TRANSPORT = (openai.OpenAIError, OSError, httpx.HTTPError, UnicodeError)

#: Concurrent ``ollama show`` probes.
_PROBES = 8

#: Where Ollama's client puts an address that names no host or no port.
_OLLAMA_HOST = "127.0.0.1"
_OLLAMA_PORT = 11434


@dataclass(frozen=True, slots=True)
class InstalledModel:
    """A model a server holds, whether it can answer a prompt (ADR-0032), and the
    server's digest of its weights where it lists one (ADR-0075)."""

    name: str
    completion: bool
    digest: str = ""


@dataclass(frozen=True, slots=True)
class Provider:
    """One model server: what it defaults to, and how it is talked to."""

    name: str
    label: str
    model: str
    base_url: str
    api_key: str
    takes_num_ctx: bool
    takes_cpu_only: bool
    chat_model: Callable[[AgentConfig], ChatModel]
    installed: Callable[[AgentConfig], list[InstalledModel]]
    #: What "the server is not there" raises through this row's client.
    transport_errors: tuple[type[Exception], ...]
    #: This row's own failures; ``hint`` decides the shared ones first.
    own_hint: Hint
    #: How to give a model more room: the tail of the unreadable-answer hint (ADR-0019).
    more_room: str

    def hint(self, config: AgentConfig, exc: Exception) -> str:
        """The remedy for ``exc``: the failures every row shares, then the row's own."""
        if isinstance(exc, UnreadableAnswerError):
            return _unreadable_hint(self, config, exc)
        if isinstance(exc, _TIMEOUTS):
            return _too_slow_hint(self, config, exc)
        return self.own_hint(config, exc)


@dataclass(frozen=True, slots=True)
class _OllamaChat:
    """Ollama's own client, asked for one schema-shaped answer (ADR-0004)."""

    client: Client
    model: str
    temperature: float
    num_ctx: int | None
    reasoning: bool | None
    cpu_only: bool

    def answer(self, messages: Sequence[Message], schema: type[SchemaT]) -> SchemaT:
        options: dict[str, Any] = {"temperature": self.temperature}
        # ``None`` leaves the model's own (ADR-0019).
        if self.num_ctx is not None:
            options["num_ctx"] = self.num_ctx
        if self.cpu_only:
            options["num_gpu"] = 0
        response = self.client.chat(
            model=self.model,
            messages=list(messages),
            format=schema.model_json_schema(),
            options=options,
            think=self.reasoning,
        )
        return read_answer(
            response.message.content or "", schema, cut_off=response.done_reason == _CUT_OFF
        )

    def close(self) -> None:
        self.client.close()


def _ollama_chat_model(config: AgentConfig) -> ChatModel:
    return _OllamaChat(
        client=Client(config.base_url, timeout=config.model_timeout),
        model=config.model,
        temperature=config.temperature,
        num_ctx=config.num_ctx,
        reasoning=config.reasoning,
        cpu_only=config.cpu_only,
    )


def _ollama_installed(config: AgentConfig) -> list[InstalledModel]:
    """Every pulled tag, whether it can run (ADR-0032), and its digest (ADR-0075)."""
    deadline = time.monotonic() + _LIST_TIMEOUT
    tags = _ollama_tags(config)
    if not tags:
        return []
    client = Client(config.base_url, timeout=_LIST_TIMEOUT)
    try:
        probed = _probe(client, list(tags), deadline)
    finally:
        client.close()
    return [replace(model, digest=tags[model.name]) for model in probed]


def _ollama_tags(config: AgentConfig) -> dict[str, str]:
    """The tags Ollama holds, each with its digest ("" where none, or where one tag is
    listed twice with two, since which of them answers is not said)."""
    response = httpx.get(_ollama_url(config.base_url, "/api/tags"), timeout=_LIST_TIMEOUT)
    response.raise_for_status()
    tags: dict[str, str] = {}
    for entry in response.json().get("models", []):
        name = entry.get("model") or entry.get("name")
        digest = str(entry.get("digest") or "")
        # A runner's own alias ("llamacpp:<digest>") is a tag nobody pulled.
        if not name or (digest and name.partition(":")[2] == digest):
            continue
        tags[name] = digest if tags.get(name, digest) == digest else ""
    return tags


def _ollama_url(base_url: str, path: str) -> str:
    """An Ollama URL, joined as its client joins one: an address with no scheme is
    plain HTTP on Ollama's own port (``$OLLAMA_HOST=0.0.0.0``)."""
    if "://" in base_url:
        return f"{base_url.rstrip('/')}{path}"
    split = urlsplit(f"http://{base_url}")
    host = split.hostname or _OLLAMA_HOST
    # ``hostname`` drops an IPv6 literal's brackets.
    if ":" in host:
        host = f"[{host}]"
    return f"http://{host}:{split.port or _OLLAMA_PORT}{split.path.rstrip('/')}{path}"


def _probe(client: Client, names: list[str], deadline: float) -> list[InstalledModel]:
    """Probe every tag's capabilities within one deadline."""
    pool = ThreadPoolExecutor(max_workers=min(len(names), _PROBES))
    try:
        probes = [(name, pool.submit(_ollama_capability, client, name)) for name in names]
        wait([probe for _, probe in probes], timeout=max(0.0, deadline - time.monotonic()))
        return [
            probe.result() if probe.done() else InstalledModel(name, completion=True)
            for name, probe in probes
        ]
    finally:
        # Not a context manager, whose exit would wait for late probes.
        pool.shutdown(wait=False, cancel_futures=True)


def _ollama_capability(client: Client, name: str) -> InstalledModel:
    try:
        capabilities = client.show(name).capabilities
    # Any failure here means "cannot say", not "cannot run".
    # pylint: disable-next=broad-exception-caught
    except Exception:
        return InstalledModel(name, completion=True)
    return InstalledModel(name, completion=capabilities is None or _COMPLETION in capabilities)


def _ollama_hint(config: AgentConfig, exc: Exception) -> str:
    lowered = _answered_by(exc, ResponseError)
    if "invalid model name" in lowered:
        return (
            f"Ollama cannot read {config.model!r} as a model name ({exc}). "
            f"Ask for one it has (installed: {_listed(config)})"
        )
    if "not found" in lowered:
        return (
            f"Ollama has no model named {config.model!r}. "
            f"Pull it with:  ollama pull {config.model}  "
            f"(installed: {_listed(config)})"
        )
    # Before the embedding model's "does not support chat": Ollama refuses thinking asked
    # of a model without it in the same words, and that model answers once it is off.
    if "does not support thinking" in lowered:
        return (
            f"Ollama's {config.model!r} cannot think ({exc}), and this run asked it to. "
            f"Turn thinking off, or ask for a model that thinks."
        )
    if "does not support" in lowered:
        return (
            f"Ollama has {config.model!r}, but it cannot answer a prompt ({exc}). "
            f"An embedding model is pulled the same way a chat model is and looks "
            f"the same in a listing. Ask for one that answers "
            f"(installed: {_listed(config, completing=True)})"
        )
    return _unreachable_hint(config, exc, "ollama serve")


@dataclass(frozen=True, slots=True)
class _OpenAIChat:
    """An OpenAI-compatible chat API (vLLM, LiteLLM, TensorRT-LLM) asked for one
    schema-shaped answer (ADR-0004, ADR-0028, ADR-0068, ADR-0081)."""

    client: openai.OpenAI
    model: str
    temperature: float
    extra_body: dict[str, Any]

    def answer(self, messages: Sequence[Message], schema: type[SchemaT]) -> SchemaT:
        response = self.client.chat.completions.create(
            model=self.model,
            # Our plain dicts are the client's ``TypedDict`` at run time (ADR-0038).
            messages=cast("list[ChatCompletionMessageParam]", list(messages)),
            temperature=self.temperature,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()},
            },
            extra_body=self.extra_body,
        )
        # No choice at all is an answer with nothing to read, not an ``IndexError``.
        if not response.choices:
            return read_answer("", schema)
        choice = response.choices[0]
        return read_answer(
            choice.message.content or "", schema, cut_off=choice.finish_reason == _CUT_OFF
        )

    def close(self) -> None:
        self.client.close()


def _openai_chat_model(
    thinking: Callable[[bool], dict[str, Any]],
) -> Callable[[AgentConfig], ChatModel]:
    """A row's ``chat_model``: the OpenAI client, asked once (ADR-0051), with
    ``reasoning`` sent in the row's own words, or nothing sent for ``None`` (ADR-0019)."""

    def chat_model(config: AgentConfig) -> ChatModel:
        extra_body = {} if config.reasoning is None else thinking(config.reasoning)
        return _OpenAIChat(
            client=openai.OpenAI(
                base_url=config.base_url,
                api_key=config.api_key or _NO_KEY,
                timeout=config.model_timeout,
                max_retries=0,
            ),
            model=config.model,
            temperature=config.temperature,
            extra_body=extra_body,
        )

    return chat_model


#: ``reasoning`` as ``enable_thinking``, read by the chat templates of the thinking
#: models vLLM and ``trtllm-serve`` render (ADR-0019, ADR-0081).
_templated_chat_model = _openai_chat_model(
    lambda on: {"chat_template_kwargs": {"enable_thinking": on}}
)

#: ``reasoning`` as ``reasoning_effort`` (ADR-0019, ADR-0068).
_litellm_chat_model = _openai_chat_model(
    lambda on: {"reasoning_effort": "medium" if on else "none"}
)


def _authorised(config: AgentConfig) -> dict[str, str]:
    return {"Authorization": f"Bearer {config.api_key}"} if config.api_key else {}


def _openai_models(config: AgentConfig) -> list[InstalledModel]:
    """The ``/models`` listing, each taken as able to answer."""
    response = httpx.get(
        f"{config.base_url.rstrip('/')}/models", headers=_authorised(config),
        timeout=_LIST_TIMEOUT,
    )
    response.raise_for_status()
    return [
        InstalledModel(entry["id"], completion=True)
        for entry in response.json().get("data", [])
        if entry.get("id")
    ]


def _one_model_hint(config: AgentConfig, exc: Exception, serve: str, key: str) -> str:
    """The hint of a server started for one model by ``serve``: a refused key, a model
    it is not serving, and nothing answering (ADR-0028, ADR-0081)."""
    label = config.model_server.label
    if isinstance(exc, openai.AuthenticationError):
        return f"{label} at {config.base_url} refused the API key ({exc}). {key}"
    lowered = _answered_by(exc, openai.APIStatusError)
    if "does not exist" in lowered or "not found" in lowered:
        return (
            f"{label} at {config.base_url} is not serving {config.model!r}. "
            f"A {label} process serves one model, chosen when it starts, so either "
            f"ask for what it has (serving: {_listed(config)}) or restart it "
            f"with:  {serve} {config.model}"
        )
    return _unreachable_hint(config, exc, f"{serve} {config.model}")


def _vllm_hint(config: AgentConfig, exc: Exception) -> str:
    return _one_model_hint(
        config,
        exc,
        "vllm serve",
        "Set $VLLM_API_KEY to the key it was started with:  vllm serve ... --api-key <key>",
    )


def _trtllm_hint(config: AgentConfig, exc: Exception) -> str:
    """``trtllm-serve`` constrains decoding only when started with a backend for it."""
    if "guided" in _answered_by(exc, openai.APIStatusError):
        return (
            f"TensorRT-LLM at {config.base_url} would not decode against the JSON schema "
            f"this sends ({exc}). Restart it with guided decoding on: put "
            f"'guided_decoding_backend: xgrammar' in a YAML file and start it with:  "
            f"trtllm-serve {config.model} --extra_llm_api_options <that file>"
        )
    return _one_model_hint(
        config,
        exc,
        "trtllm-serve",
        "Set $TRTLLM_API_KEY to the key whatever stands in front of it expects.",
    )


def _litellm_installed(config: AgentConfig) -> list[InstalledModel]:
    """Every routed alias, marked by its ``/model/info`` ``mode`` (ADR-0032), else
    ``/models``. ``/model/info`` sits beside ``/v1``."""
    try:
        response = httpx.get(
            f"{config.base_url.rstrip('/').removesuffix('/v1')}/model/info",
            headers=_authorised(config),
            timeout=_LIST_TIMEOUT,
        )
        response.raise_for_status()
        entries = response.json().get("data", [])
    except (httpx.HTTPError, ValueError):
        return _openai_models(config)
    models: dict[str, bool] = {}
    for entry in entries:
        if name := entry.get("model_name"):
            # An alias of several deployments answers if any of them does.
            mode = (entry.get("model_info") or {}).get("mode")
            models[name] = models.get(name, False) or mode in (None, "chat")
    return [InstalledModel(name, completion) for name, completion in models.items()]


def _litellm_hint(config: AgentConfig, exc: Exception) -> str:
    proxy = f"The LiteLLM proxy at {config.base_url}"
    if isinstance(exc, openai.AuthenticationError) or _no_key_store(exc):
        return (
            f"{proxy} refused the API key ({exc}). "
            "Set $LITELLM_API_KEY to its master key or to a virtual key it issued."
        )
    lowered = _answered_by(exc, openai.APIStatusError)
    # The proxy's own words for an unknown alias; a "not found" is relayed from behind.
    if "invalid model name" in lowered:
        return (
            f"{proxy} routes no model called {config.model!r}. Ask for one it has "
            f"(routing: {_listed(config)}), or add it to the model_list in the "
            f"proxy's config.yaml and restart it."
        )
    if lowered:
        return (
            f"{proxy} answered, but the model behind {config.model!r} failed ({exc}). "
            "The proxy's own log says which server it routed to."
        )
    return _unreachable_hint(config, exc, "litellm --config config.yaml")


def _no_key_store(exc: Exception) -> bool:
    """A database-less proxy refuses a non-master key with 400 "No connected db."."""
    if isinstance(exc, httpx.HTTPStatusError):
        said = exc.response.text
    elif isinstance(exc, openai.APIStatusError):
        said = str(exc)
    else:
        return False
    return "no connected db" in said.lower()


def _answered_by(exc: Exception, client_error: type[Exception]) -> str:
    """What the server itself answered, lower-cased, or "" if the row's client did not
    raise it: a bare 404 from something else must not earn a model remedy."""
    return str(exc).lower() if isinstance(exc, client_error) else ""


def _too_slow_hint(server: Provider, config: AgentConfig, exc: Exception) -> str:
    smaller = "a smaller context window" if server.takes_num_ctx else "a shorter prompt"
    # A timeout often stringifies to nothing.
    detail = str(exc) or type(exc).__name__
    return (
        f"{server.label} at {config.base_url} did not answer in time ({detail}). "
        f"The model {config.model!r} may be too slow for this prompt -- "
        f"try a smaller model, or {smaller}."
    )


def _unreadable_hint(server: Provider, config: AgentConfig, exc: UnreadableAnswerError) -> str:
    """Something other than the JSON asked for (ADR-0019): certainly out of room when
    the server said it stopped at its limit, and perhaps when it said nothing."""
    said = str(exc).strip()
    detail = said.splitlines()[0] if said else type(exc).__name__
    if exc.cut_off:
        return (
            f"{server.label} at {config.base_url} stopped at its length limit before "
            f"the model {config.model!r} finished the JSON this asks for ({detail}). "
            f"It ran out of room: {server.more_room}."
        )
    return (
        f"{server.label} at {config.base_url} answered with something that is not "
        f"the JSON this asks for ({detail}). The model {config.model!r} may have run "
        f"out of room before it finished: {server.more_room}."
    )


def _unreachable_hint(config: AgentConfig, exc: Exception, start: str) -> str:
    return (
        f"Could not reach {config.model_server.label} at {config.base_url} ({exc}). "
        f"Start it with:  {start}"
    )


def _listed(config: AgentConfig, *, completing: bool = False) -> str:
    """The server's models for a message, or "unknown"."""
    try:
        models = config.model_server.installed(config)
    # A second failure while writing a hint must not become a traceback.
    # pylint: disable-next=broad-exception-caught
    except Exception:
        return "unknown"
    if completing:
        models = [model for model in models if model.completion]
    return ", ".join(model.name for model in models) or "none"


OLLAMA = Provider(
    name="ollama",
    label="Ollama",
    model=os.getenv("OLLAMA_MODEL", "gemma4:12b"),
    base_url=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
    api_key="",
    takes_num_ctx=True,
    # GPU offload is per request on Ollama.
    takes_cpu_only=True,
    chat_model=_ollama_chat_model,
    installed=_ollama_installed,
    # A refused connection arrives as ``ConnectionError``, timeouts and dropped streams
    # as raw ``httpx`` errors, an unencodable host (``192.168.1..5``) as ``UnicodeError``.
    transport_errors=(ResponseError, RequestError, OSError, httpx.HTTPError, UnicodeError),
    own_hint=_ollama_hint,
    more_room="give it more room with a larger context window, or turn thinking off",
)

VLLM = Provider(
    name="vllm",
    label="vLLM",
    # A repository id, as ``vllm serve`` takes it.
    model=os.getenv("VLLM_MODEL", "Qwen/Qwen3-8B"),
    base_url=os.getenv("VLLM_HOST", "http://localhost:8000/v1"),
    api_key=os.getenv("VLLM_API_KEY", ""),
    # Both fixed at startup.
    takes_num_ctx=False,
    takes_cpu_only=False,
    chat_model=_templated_chat_model,
    installed=_openai_models,
    transport_errors=_OPENAI_TRANSPORT,
    own_hint=_vllm_hint,
    # vLLM's own flag, not ours, so fine to name at either door.
    more_room="ask for fewer products, or restart it with a larger --max-model-len",
)

LITELLM = Provider(
    name="litellm",
    label="LiteLLM",
    # An alias out of somebody's ``model_list``, which nothing here can know.
    model=os.getenv("LITELLM_MODEL", "local_model"),
    base_url=os.getenv("LITELLM_HOST", "http://localhost:4000/v1"),
    api_key=os.getenv("LITELLM_API_KEY", ""),
    # Both belong to whatever the proxy routes to.
    takes_num_ctx=False,
    takes_cpu_only=False,
    chat_model=_litellm_chat_model,
    installed=_litellm_installed,
    transport_errors=_OPENAI_TRANSPORT,
    own_hint=_litellm_hint,
    more_room=(
        "ask for fewer products, or give the model the proxy routes to a larger "
        "context window where that server is started"
    ),
)

TRTLLM = Provider(
    name="trtllm",
    label="TensorRT-LLM",
    # A repository id, as ``trtllm-serve`` takes it.
    model=os.getenv("TRTLLM_MODEL", "Qwen/Qwen3-8B"),
    base_url=os.getenv("TRTLLM_HOST", "http://localhost:8000/v1"),
    # ``trtllm-serve`` checks none; a gateway in front of it may.
    api_key=os.getenv("TRTLLM_API_KEY", ""),
    # Both fixed at startup, as vLLM's are.
    takes_num_ctx=False,
    takes_cpu_only=False,
    chat_model=_templated_chat_model,
    installed=_openai_models,
    transport_errors=_OPENAI_TRANSPORT,
    own_hint=_trtllm_hint,
    # Without a guided-decoding backend it answers in free text, which lands here too.
    more_room=(
        "ask for fewer products, or restart it with a larger --max_seq_len, and with "
        "guided decoding on (guided_decoding_backend: xgrammar)"
    ),
)

#: By the name the CLI, the API and ``$BUY_AGENT_PROVIDER`` use (ADR-0029).
PROVIDERS: dict[str, Provider] = {
    provider.name: provider for provider in (OLLAMA, VLLM, LITELLM, TRTLLM)
}


def provider_for(name: str) -> Provider:
    try:
        return PROVIDERS[name]
    except KeyError:
        raise ValueError(
            f"Unknown provider {name!r}; expected one of {', '.join(PROVIDERS)}."
        ) from None


#: What the form's picker is told about each provider: never its key.
_OFFERED = ("name", "label", "model", "base_url", "takes_num_ctx", "takes_cpu_only")


def provider_options() -> list[dict[str, object]]:
    return [{key: getattr(server, key) for key in _OFFERED} for server in PROVIDERS.values()]
