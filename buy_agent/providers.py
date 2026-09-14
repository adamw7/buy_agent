"""Which model server the agent talks to: Ollama, or vLLM's OpenAI-compatible API
(ADR-0028, ADR-0029, ADR-0032, ADR-0051).
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeAlias

import httpx
import openai
from ollama import Client, RequestError, ResponseError

from buy_agent.chat import ChatModel, SchemaT, UnreadableAnswerError, read_answer

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from buy_agent.chat import Message
    from buy_agent.config import AgentConfig

#: What the OpenAI client is given when no key is configured. vLLM serves without one,
#: but the client refuses to send a request with no key at all, so a placeholder stands
#: in for the header vLLM is not checking.
_NO_KEY = "EMPTY"

#: How long to wait on a model listing -- all of it, not each request in it (ADR-0032,
#: ADR-0051).
_LIST_TIMEOUT = 5.0

#: What an Ollama model's capabilities must include to answer a prompt at all
#: (ADR-0032).
_COMPLETION = "completion"

#: What a row turns one of its failures into: a sentence naming what to do about it.
Hint: TypeAlias = "Callable[[AgentConfig, Exception], str]"

#: How each client says a server took the prompt and never came back: the OpenAI one
#: raises its own class, and everything else -- both listings included -- arrives as
#: httpx's. Named once rather than half of it per row.
_TIMEOUTS = (httpx.TimeoutException, openai.APITimeoutError)

#: How many of those second questions to have in flight at once: together, the listing
#: being on one short budget, but capped -- fifty pulled tags should not get fifty
#: threads to save milliseconds on a local call.
_PROBES = 8


@dataclass(frozen=True, slots=True)
class InstalledModel:
    """One model a server is holding, and whether it can answer a chat prompt (ADR-0032).
    """

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
    chat_model: Callable[[AgentConfig], ChatModel]
    installed: Callable[[AgentConfig], list[InstalledModel]]
    transport_errors: tuple[type[BaseException], ...]
    hint: Hint


@dataclass(frozen=True, slots=True)
class _OllamaChat:
    """Ollama's own client, asked for one schema-shaped answer (ADR-0004)."""

    client: Client
    model: str
    temperature: float
    num_ctx: int | None
    reasoning: bool | None

    def answer(self, messages: Sequence[Message], schema: type[SchemaT]) -> SchemaT:
        """One chat call, read back as ``schema``."""
        options: dict[str, Any] = {"temperature": self.temperature}
        # Sent only when there is one: ``None`` means "leave the model's own alone", and
        # a null in the options is not that (ADR-0019).
        if self.num_ctx is not None:
            options["num_ctx"] = self.num_ctx
        response = self.client.chat(
            model=self.model,
            messages=list(messages),
            format=schema.model_json_schema(),
            options=options,
            think=self.reasoning,
        )
        return read_answer(response.message.content or "", schema)

    def close(self) -> None:
        """Let go of the connection this client keeps to Ollama."""
        self.client.close()


def _ollama_chat_model(config: AgentConfig) -> ChatModel:
    """Ollama takes the window and the thinking switch as request options (ADR-0051)."""
    return _OllamaChat(
        client=Client(config.base_url, timeout=config.model_timeout),
        model=config.model,
        temperature=config.temperature,
        num_ctx=config.num_ctx,
        reasoning=config.reasoning,
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
        # Closed here for the reason a chat model is: the pool this opened is this
        # function's to let go of, and a listing is asked again on every provider
        # change. A probe still running past the deadline loses its connection and
        # answers "cannot say", which is the answer its result was discarded for.
        client.close()


def _ollama_tags(config: AgentConfig) -> list[str]:
    """What Ollama says it is holding, each tag named the way Ollama named it."""
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
    """An address of Ollama's, joined the way its own client would have joined it."""
    base = base_url if "://" in base_url else f"http://{base_url}"
    return f"{base.rstrip('/')}{path}"


def _probe(client: Client, names: list[str], deadline: float) -> list[InstalledModel]:
    """Ask every tag what it can do, with one budget for the lot of them."""
    pool = ThreadPoolExecutor(max_workers=min(len(names), _PROBES))
    try:
        probes = [(name, pool.submit(_ollama_capability, client, name)) for name in names]
        wait([probe for _, probe in probes], timeout=max(0.0, deadline - time.monotonic()))
        return [
            probe.result() if probe.done() else InstalledModel(name, completion=True)
            for name, probe in probes
        ]
    finally:
        # Not the context manager: its exit waits for the probes that ran past the
        # deadline, which is the wait this budget exists to end.
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
    lowered = str(exc).lower()
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
class _VLLMChat:
    """vLLM through the OpenAI client, asked for one schema-shaped answer (ADR-0004,
    ADR-0028).
    """

    client: openai.OpenAI
    model: str
    temperature: float
    extra_body: dict[str, Any]

    def answer(self, messages: Sequence[Message], schema: type[SchemaT]) -> SchemaT:
        """One chat completion, read back as ``schema``."""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=list(messages),
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
        """Let go of the connection this client keeps to vLLM, as Ollama's does."""
        self.client.close()


def _vllm_chat_model(config: AgentConfig) -> ChatModel:
    """vLLM through its OpenAI-compatible API (ADR-0019)."""
    extra_body: dict[str, Any] = {}
    if config.reasoning is not None:
        extra_body["chat_template_kwargs"] = {"enable_thinking": config.reasoning}
    return _VLLMChat(
        client=openai.OpenAI(
            base_url=config.base_url,
            api_key=config.api_key or _NO_KEY,
            timeout=config.model_timeout,
            # Asked once. This client retries twice by default, so the wait a shopper
            # set would be a third of the wait they got -- and a prompt this size is not
            # one to send three times (ADR-0051).
            max_retries=0,
        ),
        model=config.model,
        temperature=config.temperature,
        extra_body=extra_body,
    )


def _vllm_installed(config: AgentConfig) -> list[InstalledModel]:
    """What vLLM is serving -- one model, in the list shape the picker wants."""
    headers = {"Authorization": f"Bearer {config.api_key}"} if config.api_key else {}
    response = httpx.get(
        f"{config.base_url.rstrip('/')}/models", headers=headers, timeout=_LIST_TIMEOUT
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
    lowered = detail.lower()
    if "does not exist" in lowered or "not found" in lowered:
        return (
            f"vLLM at {config.base_url} is not serving {config.model!r}. "
            f"A vLLM process serves one model, chosen when it starts, so either "
            f"ask for what it has (serving: {_listed(config)}) or restart it "
            f"with:  vllm serve {config.model}"
        )
    return _unreachable_hint(config, exc, f"vllm serve {config.model}")


def _hint(specific: Hint) -> Hint:
    """A row's ``hint``: the two failures both servers meet, then this one's own.

    Their sentences were already shared -- :func:`_unreadable_hint` and
    :func:`_too_slow_hint` -- and the deciding was the half still written out on
    both rows. Asked before either row reads the message, which is the order that
    matters: a half-finished answer is the model's own words, and any of them could
    say "not found".
    """

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
    # A timeout often stringifies to nothing, and "()" says less than the class.
    detail = str(exc) or type(exc).__name__
    return (
        f"{server.label} at {config.base_url} did not answer in time ({detail}). "
        f"The model {config.model!r} may be too slow for this prompt -- "
        f"try a smaller model, or {smaller}."
    )


def _unreadable_hint(config: AgentConfig, exc: Exception) -> str:
    """A server that answered, with something that is not the JSON asked for (ADR-0019).
    """
    server = config.model_server
    room = (
        "give it more room with a larger context window, or turn thinking off"
        if server.takes_num_ctx
        # ``--max-model-len`` stays: it is vLLM's own startup flag, which is the same
        # thing to type wherever this sentence is read.
        else "ask for fewer products, or restart it with a larger --max-model-len"
    )
    said = str(exc).strip()
    detail = said.splitlines()[0] if said else type(exc).__name__
    return (
        f"{server.label} at {config.base_url} answered with something that is not "
        f"the JSON this asks for ({detail}). The model {config.model!r} may have run "
        f"out of room before it finished: {room}."
    )


def _unreachable_hint(config: AgentConfig, exc: Exception, start: str) -> str:
    """Nothing answered at all, so the server itself is what is missing."""
    return (
        f"Could not reach {config.model_server.label} at {config.base_url} ({exc}). "
        f"Start it with:  {start}"
    )


def _listed(config: AgentConfig, *, completing: bool = False) -> str:
    """What the server has, for a message -- or "unknown" if it cannot be asked."""
    try:
        models = config.model_server.installed(config)
    # Any transport failure means "cannot say", and a hint is already being written: the
    # second failure must not replace it with a traceback.
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
    chat_model=_ollama_chat_model,
    installed=_ollama_installed,
    # Both halves are load-bearing, the ollama client converting exactly one of its
    # transport failures: a refused connection becomes a builtin ``ConnectionError``,
    # while a slow model and a dropped stream arrive as raw ``httpx`` errors, neither an
    # ``OSError``.
    transport_errors=(ResponseError, RequestError, OSError, httpx.HTTPError),
    hint=_hint(_ollama_hint),
)

VLLM = Provider(
    name="vllm",
    label="vLLM",
    # A repository id rather than a tag: what ``vllm serve`` is given and what
    # ``/v1/models`` reports back.
    model=os.getenv("VLLM_MODEL", "Qwen/Qwen3-8B"),
    base_url=os.getenv("VLLM_HOST", "http://localhost:8000/v1"),
    api_key=os.getenv("VLLM_API_KEY", ""),
    takes_num_ctx=False,
    chat_model=_vllm_chat_model,
    installed=_vllm_installed,
    # ``openai.OpenAIError`` is the root of that client's hierarchy.
    transport_errors=(openai.OpenAIError, OSError, httpx.HTTPError),
    hint=_hint(_vllm_hint),
)

#: Every provider, by the name the CLI, the API and ``$BUY_AGENT_PROVIDER`` use
#: (ADR-0029).
PROVIDERS: dict[str, Provider] = {provider.name: provider for provider in (OLLAMA, VLLM)}


def provider_for(name: str) -> Provider:
    """The provider called ``name``."""
    try:
        return PROVIDERS[name]
    except KeyError:
        raise ValueError(
            f"Unknown provider {name!r}; expected one of {', '.join(PROVIDERS)}."
        ) from None


def provider_options() -> list[dict[str, object]]:
    """Every provider a run can be pointed at, as the form's picker needs it."""
    return [
        {
            "name": server.name,
            "label": server.label,
            "model": server.model,
            "base_url": server.base_url,
            "takes_num_ctx": server.takes_num_ctx,
        }
        for server in PROVIDERS.values()
    ]
