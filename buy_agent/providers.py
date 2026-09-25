"""The model servers -- Ollama, vLLM, a LiteLLM proxy -- one row each (ADR-0028,
ADR-0029, ADR-0032, ADR-0051, ADR-0068)."""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeAlias, cast

import httpx
import openai
from ollama import Client, RequestError, ResponseError

from buy_agent.chat import ChatModel, SchemaT, UnreadableAnswerError, read_answer

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from openai.types.chat import ChatCompletionMessageParam

    from buy_agent.chat import Message
    from buy_agent.config import AgentConfig

#: A placeholder key: the OpenAI client refuses to send none, and keyless servers ignore it.
_NO_KEY = "EMPTY"

#: The budget for a whole model listing (ADR-0032, ADR-0051).
_LIST_TIMEOUT = 5.0

#: The Ollama capability needed to answer a prompt (ADR-0032).
_COMPLETION = "completion"

#: Turns a row's failure into a remedy sentence.
Hint: TypeAlias = "Callable[[AgentConfig, Exception], str]"

#: Timeouts from either client.
_TIMEOUTS = (httpx.TimeoutException, openai.APITimeoutError)

#: Concurrent ``ollama show`` probes.
_PROBES = 8


@dataclass(frozen=True, slots=True)
class InstalledModel:
    """One model a server is holding, and whether it can answer a chat prompt (ADR-0032)."""

    name: str
    completion: bool


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
    #: What "the server is not there" raises through this row's client; typed
    #: ``Exception`` so ``hint`` can accept what the ``except`` binds.
    transport_errors: tuple[type[Exception], ...]
    hint: Hint
    #: How to give a model more room, as the tail of the unreadable-answer hint
    #: (ADR-0019).
    more_room: str


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
        """One chat call, read back as ``schema``."""
        options: dict[str, Any] = {"temperature": self.temperature}
        # ``None`` means leave the model's own (ADR-0019).
        if self.num_ctx is not None:
            options["num_ctx"] = self.num_ctx
        # Sent only when asked for: ``False`` means the default offload.
        if self.cpu_only:
            options["num_gpu"] = 0
        response = self.client.chat(
            model=self.model,
            messages=list(messages),
            format=schema.model_json_schema(),
            options=options,
            think=self.reasoning,
        )
        return read_answer(response.message.content or "", schema)

    def close(self) -> None:
        """Close the client's connection pool."""
        self.client.close()


def _ollama_chat_model(config: AgentConfig) -> ChatModel:
    """Ollama takes the window and the thinking switch as request options (ADR-0051)."""
    return _OllamaChat(
        client=Client(config.base_url, timeout=config.model_timeout),
        model=config.model,
        temperature=config.temperature,
        num_ctx=config.num_ctx,
        reasoning=config.reasoning,
        cpu_only=config.cpu_only,
    )


def _ollama_installed(config: AgentConfig) -> list[InstalledModel]:
    """Every model tag Ollama has pulled, and whether each one can be run (ADR-0032)."""
    deadline = time.monotonic() + _LIST_TIMEOUT
    names = _ollama_tags(config)
    if not names:
        return []
    client = Client(config.base_url, timeout=_LIST_TIMEOUT)
    try:
        return _probe(client, names, deadline)
    finally:
        # This function opened the pool, so it closes it.
        client.close()


def _ollama_tags(config: AgentConfig) -> list[str]:
    """The tags Ollama holds."""
    response = httpx.get(
        _ollama_url(config.base_url, "/api/tags"), timeout=_LIST_TIMEOUT
    )
    response.raise_for_status()
    return [
        name
        for entry in response.json().get("models", [])
        if (name := entry.get("model") or entry.get("name"))
    ]


def _ollama_url(base_url: str, path: str) -> str:
    """An Ollama URL, joined as its client would join it."""
    base = base_url if "://" in base_url else f"http://{base_url}"
    return f"{base.rstrip('/')}{path}"


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
    """Ask one pulled tag whether it has a completion to give."""
    try:
        capabilities = client.show(name).capabilities
    # Any failure here means "cannot say", not "cannot run".
    # pylint: disable-next=broad-exception-caught
    except Exception:
        return InstalledModel(name, completion=True)
    return InstalledModel(
        name, completion=capabilities is None or _COMPLETION in capabilities
    )


def _ollama_hint(config: AgentConfig, exc: Exception) -> str:
    """Turn an Ollama failure into something the user can act on (ADR-0032)."""
    lowered = _answered_by(exc, ResponseError)
    # A malformed name: pulling it would fail too.
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
    """An OpenAI-compatible chat API (vLLM, LiteLLM) asked for one schema-shaped answer
    (ADR-0004, ADR-0028, ADR-0068)."""

    client: openai.OpenAI
    model: str
    temperature: float
    extra_body: dict[str, Any]

    def answer(self, messages: Sequence[Message], schema: type[SchemaT]) -> SchemaT:
        """One chat completion, read back as ``schema``."""
        response = self.client.chat.completions.create(
            model=self.model,
            # Our plain dicts are the client's ``TypedDict`` at run time; the cast stays
            # on this side of the seam (ADR-0038).
            messages=cast("list[ChatCompletionMessageParam]", list(messages)),
            temperature=self.temperature,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "schema": schema.model_json_schema(),
                },
            },
            extra_body=self.extra_body,
        )
        return read_answer(response.choices[0].message.content or "", schema)

    def close(self) -> None:
        """Close the client's connection pool."""
        self.client.close()


def _openai_chat_model(config: AgentConfig, extra_body: dict[str, Any]) -> ChatModel:
    """The OpenAI client for this config, without retries (ADR-0051)."""
    return _OpenAIChat(
        client=openai.OpenAI(
            base_url=config.base_url,
            api_key=config.api_key or _NO_KEY,
            timeout=config.model_timeout,
            # Asked once.
            max_retries=0,
        ),
        model=config.model,
        temperature=config.temperature,
        extra_body=extra_body,
    )


def _vllm_chat_model(config: AgentConfig) -> ChatModel:
    """vLLM through its OpenAI-compatible API (ADR-0019)."""
    extra_body: dict[str, Any] = {}
    if config.reasoning is not None:
        extra_body["chat_template_kwargs"] = {"enable_thinking": config.reasoning}
    return _openai_chat_model(config, extra_body)


def _authorised(config: AgentConfig) -> dict[str, str]:
    """The auth header, if a key is configured."""
    return {"Authorization": f"Bearer {config.api_key}"} if config.api_key else {}


def _openai_models(config: AgentConfig) -> list[InstalledModel]:
    """The ``/models`` listing, each taken as able to answer."""
    response = httpx.get(
        f"{config.base_url.rstrip('/')}/models",
        headers=_authorised(config),
        timeout=_LIST_TIMEOUT,
    )
    response.raise_for_status()
    return [
        InstalledModel(entry["id"], completion=True)
        for entry in response.json().get("data", [])
        if entry.get("id")
    ]


def _vllm_hint(config: AgentConfig, exc: Exception) -> str:
    """Turn a vLLM failure into something the user can act on."""
    detail = str(exc)
    if isinstance(exc, openai.AuthenticationError):
        return (
            f"vLLM at {config.base_url} refused the API key ({detail}). "
            "Set $VLLM_API_KEY to the key it was started with:  "
            "vllm serve ... --api-key <key>"
        )
    lowered = _answered_by(exc, openai.APIStatusError)
    if "does not exist" in lowered or "not found" in lowered:
        return (
            f"vLLM at {config.base_url} is not serving {config.model!r}. "
            f"A vLLM process serves one model, chosen when it starts, so either "
            f"ask for what it has (serving: {_listed(config)}) or restart it "
            f"with:  vllm serve {config.model}"
        )
    return _unreachable_hint(config, exc, f"vllm serve {config.model}")


def _litellm_chat_model(config: AgentConfig) -> ChatModel:
    """A LiteLLM proxy; ``reasoning`` maps to ``reasoning_effort`` (ADR-0019, ADR-0068)."""
    if config.reasoning is None:
        return _openai_chat_model(config, {})
    effort = "medium" if config.reasoning else "none"
    return _openai_chat_model(config, {"reasoning_effort": effort})


def _litellm_installed(config: AgentConfig) -> list[InstalledModel]:
    """Every routed alias, marked by its ``/model/info`` ``mode`` (ADR-0032); falls back
    to ``/models``. ``/model/info`` sits beside ``/v1``."""
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
    """Turn a LiteLLM proxy's failure into something the user can act on (ADR-0068)."""
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
        # Any other answer is relayed from the routed server.
        return (
            f"{proxy} answered, but the model behind {config.model!r} failed ({exc}). "
            "The proxy's own log says which server it routed to."
        )
    return _unreachable_hint(config, exc, "litellm --config config.yaml")


def _no_key_store(exc: Exception) -> bool:
    """Whether a database-less proxy refused a non-master key: it answers 400 "No
    connected db." rather than 401."""
    if isinstance(exc, httpx.HTTPStatusError):
        said = exc.response.text
    elif isinstance(exc, openai.APIStatusError):
        said = str(exc)
    else:
        return False
    return "no connected db" in said.lower()


def _answered_by(exc: Exception, client_error: type[Exception]) -> str:
    """What the model server itself answered, lower-cased, or ``""`` if the failure is
    not the row client's answer.

    A bare 404 from something else at that address must not earn a model remedy.
    """
    return str(exc).lower() if isinstance(exc, client_error) else ""


def _hint(specific: Hint) -> Hint:
    """A row's ``hint``: the shared failures first, then the row's own."""

    def hint(config: AgentConfig, exc: Exception) -> str:
        if isinstance(exc, UnreadableAnswerError):
            return _unreadable_hint(config, exc)
        if isinstance(exc, _TIMEOUTS):
            return _too_slow_hint(config, exc)
        return specific(config, exc)

    return hint


def _too_slow_hint(config: AgentConfig, exc: Exception) -> str:
    """A server that took the prompt and never came back."""
    server = config.model_server
    smaller = "a smaller context window" if server.takes_num_ctx else "a shorter prompt"
    # A timeout often stringifies to nothing.
    detail = str(exc) or type(exc).__name__
    return (
        f"{server.label} at {config.base_url} did not answer in time ({detail}). "
        f"The model {config.model!r} may be too slow for this prompt -- "
        f"try a smaller model, or {smaller}."
    )


def _unreadable_hint(config: AgentConfig, exc: Exception) -> str:
    """A server that answered with something other than the JSON asked for (ADR-0019)."""
    server = config.model_server
    said = str(exc).strip()
    detail = said.splitlines()[0] if said else type(exc).__name__
    return (
        f"{server.label} at {config.base_url} answered with something that is not "
        f"the JSON this asks for ({detail}). The model {config.model!r} may have run "
        f"out of room before it finished: {server.more_room}."
    )


def _unreachable_hint(config: AgentConfig, exc: Exception, start: str) -> str:
    """Nothing answered: the server is what is missing."""
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
    # A refused connection arrives as ``ConnectionError``; timeouts and dropped streams
    # as raw ``httpx`` errors.
    transport_errors=(ResponseError, RequestError, OSError, httpx.HTTPError),
    hint=_hint(_ollama_hint),
    more_room="give it more room with a larger context window, or turn thinking off",
)

VLLM = Provider(
    name="vllm",
    label="vLLM",
    # A repository id, as ``vllm serve`` takes it.
    model=os.getenv("VLLM_MODEL", "Qwen/Qwen3-8B"),
    base_url=os.getenv("VLLM_HOST", "http://localhost:8000/v1"),
    api_key=os.getenv("VLLM_API_KEY", ""),
    takes_num_ctx=False,
    # Fixed at startup, like the window.
    takes_cpu_only=False,
    chat_model=_vllm_chat_model,
    installed=_openai_models,
    # ``openai.OpenAIError`` is the client's root.
    transport_errors=(openai.OpenAIError, OSError, httpx.HTTPError),
    hint=_hint(_vllm_hint),
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
    transport_errors=(openai.OpenAIError, OSError, httpx.HTTPError),
    hint=_hint(_litellm_hint),
    more_room=(
        "ask for fewer products, or give the model the proxy routes to a larger "
        "context window where that server is started"
    ),
)

#: Every provider, by the name the CLI, the API and ``$BUY_AGENT_PROVIDER`` use
#: (ADR-0029).
PROVIDERS: dict[str, Provider] = {
    provider.name: provider for provider in (OLLAMA, VLLM, LITELLM)
}


def provider_for(name: str) -> Provider:
    """The provider called ``name``."""
    try:
        return PROVIDERS[name]
    except KeyError:
        raise ValueError(
            f"Unknown provider {name!r}; expected one of {', '.join(PROVIDERS)}."
        ) from None


def provider_options() -> list[dict[str, object]]:
    """Every provider, as the form's picker needs it."""

    return [
        {
            "name": server.name,
            "label": server.label,
            "model": server.model,
            "base_url": server.base_url,
            "takes_num_ctx": server.takes_num_ctx,
            "takes_cpu_only": server.takes_cpu_only,
        }
        for server in PROVIDERS.values()
    ]
