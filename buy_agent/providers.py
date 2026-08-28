"""Which model server the agent talks to: Ollama, or vLLM's OpenAI-compatible API.

Everything that differs between the two lives here, so nothing above this module
has to know which one is running. A provider answers four questions:

* **What chat model does this config build?** -- a ``ChatOllama`` or a
  ``ChatOpenAI`` pointed at vLLM's ``/v1``. Both are ``BaseChatModel``, so
  :mod:`buy_agent.extraction` builds the same two chains over either (ADR-0028).
* **Which transport failures mean "the server is not there"?** -- the tuple
  :meth:`~buy_agent.agent.BuyAgent._invoke` catches to raise
  ``ModelUnavailableError`` instead of leaking a traceback.
* **How is that failure phrased?** -- ``ollama pull`` and ``vllm serve`` are
  different things to type, and the message is the whole value of the exception.
* **What is the server serving?** -- ``/api/tags`` for Ollama, ``/v1/models``
  for vLLM, which is what fills the UI's model picker.

The two are not symmetric, and pretending otherwise would lie to the shopper.
Ollama holds many pulled tags and switches between them per request; a vLLM
process serves exactly one model, chosen when it started. Ollama takes the
context window and the thinking switch per request (``num_ctx``, ``think``);
vLLM fixes the window when it starts (``--max-model-len``) and takes only the
thinking switch, through ``chat_template_kwargs``. :data:`Provider.takes_num_ctx`
is that difference declared once, so the CLI, the API and the form can all say so
rather than each offering a setting that quietly does nothing.

This module deliberately imports nothing from :mod:`buy_agent.config`: the
defaults are that module's to read out of the environment, and this one's job is
only to act on a config it is handed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

import httpx
import openai
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from ollama import Client, RequestError, ResponseError

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from buy_agent.config import AgentConfig

#: What ``ChatOpenAI`` is given when no key is configured. vLLM serves without
#: one by default, but the OpenAI client refuses to send a request with no key at
#: all, so a placeholder stands in for the header vLLM is not checking.
_NO_KEY = "EMPTY"

#: How long to wait on a model listing. Short on purpose: it is asked for while a
#: form is rendering, and "unreachable" is an answer worth giving quickly.
_LIST_TIMEOUT = 5.0


@dataclass(frozen=True, slots=True)
class Provider:
    """One model server, and the four things only it can answer.

    Attributes:
        name: What the CLI, the API and ``$BUY_AGENT_PROVIDER`` call it.
        label: What a person reads -- "Ollama", "vLLM".
        takes_num_ctx: Whether the context window is a per-request setting. False
            for vLLM, where it is fixed by ``--max-model-len`` when the server
            starts, and where ``AgentConfig.num_ctx`` is therefore ignored rather
            than quietly failing to apply.
        chat_model: Builds the LangChain chat model this config asks for.
        installed: Lists what the server is serving. Raises whatever the
            transport raises -- both callers phrase that failure their own way.
        transport_errors: The exceptions that mean the server could not be
            reached, or answered with a refusal rather than a completion.
        hint: Turns one of those into something the user can act on.
    """

    name: str
    label: str
    takes_num_ctx: bool
    chat_model: Callable[[AgentConfig], BaseChatModel]
    installed: Callable[[AgentConfig], list[str]]
    transport_errors: tuple[type[BaseException], ...]
    hint: Callable[[AgentConfig, Exception], str]


def _ollama_chat_model(config: AgentConfig) -> BaseChatModel:
    """Ollama takes the window and the thinking switch as request options."""
    return ChatOllama(
        model=config.model,
        base_url=config.base_url,
        temperature=config.temperature,
        num_ctx=config.num_ctx,
        reasoning=config.reasoning,
    )


def _ollama_installed(config: AgentConfig) -> list[str]:
    """Every model tag Ollama has pulled."""
    return [model.model for model in Client(config.base_url).list().models if model.model]


def _ollama_hint(config: AgentConfig, exc: Exception) -> str:
    """Turn an Ollama failure into something the user can act on."""
    detail = str(exc)
    if isinstance(exc, httpx.TimeoutException):
        return (
            f"Ollama at {config.base_url} did not answer in time "
            f"({detail or type(exc).__name__}). "
            f"The model {config.model!r} may be too slow for this prompt -- "
            "try a smaller model, or a smaller --num-ctx."
        )
    if "not found" in detail.lower():
        return (
            f"Ollama has no model named {config.model!r}. "
            f"Pull it with:  ollama pull {config.model}  "
            f"(installed: {_listed(config)})"
        )
    return (
        f"Could not reach Ollama at {config.base_url} ({detail}). "
        "Start it with:  ollama serve"
    )


def _vllm_chat_model(config: AgentConfig) -> BaseChatModel:
    """vLLM through its OpenAI-compatible API.

    ``num_ctx`` is deliberately not passed: vLLM fixes the window when it starts
    and an unknown field would be rejected, so the setting is declared as one
    this provider does not take (:data:`Provider.takes_num_ctx`) rather than sent
    and ignored.

    ``reasoning`` keeps the tri-state it has everywhere else. ``None`` sends
    nothing and leaves the served model's own chat template alone; ``True`` and
    ``False`` set ``enable_thinking``, which is the switch the templates of the
    thinking models vLLM serves read (ADR-0019 explains why the default is off).
    """
    extra_body: dict[str, Any] = {}
    if config.reasoning is not None:
        extra_body["chat_template_kwargs"] = {"enable_thinking": config.reasoning}
    return ChatOpenAI(
        model=config.model,
        base_url=config.base_url,
        api_key=config.api_key or _NO_KEY,
        temperature=config.temperature,
        extra_body=extra_body,
    )


def _vllm_installed(config: AgentConfig) -> list[str]:
    """What vLLM is serving -- one model, in the list shape the picker wants.

    Asked over ``httpx`` rather than through the OpenAI client because that is
    the whole request: a ``GET`` of ``/v1/models`` on a server the config already
    names. ``base_url`` already ends in the API root, so the path is appended to
    it rather than assembled from a host.
    """
    headers = {"Authorization": f"Bearer {config.api_key}"} if config.api_key else {}
    response = httpx.get(
        f"{config.base_url.rstrip('/')}/models", headers=headers, timeout=_LIST_TIMEOUT
    )
    response.raise_for_status()
    return [entry["id"] for entry in response.json().get("data", []) if entry.get("id")]


def _vllm_hint(config: AgentConfig, exc: Exception) -> str:
    """Turn a vLLM failure into something the user can act on.

    The model case is the one that differs most from Ollama's. A vLLM process
    serves the single model it was started with, so a name it does not know is
    not something to pull -- it is a server to restart, or a name to correct to
    the one already being served, which is why the message names both.
    """
    detail = str(exc)
    if isinstance(exc, (httpx.TimeoutException, openai.APITimeoutError)):
        return (
            f"vLLM at {config.base_url} did not answer in time "
            f"({detail or type(exc).__name__}). "
            f"The model {config.model!r} may be too slow for this prompt -- "
            "try a smaller model, or a shorter prompt."
        )
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
    return (
        f"Could not reach vLLM at {config.base_url} ({detail}). "
        f"Start it with:  vllm serve {config.model}"
    )


def _listed(config: AgentConfig) -> str:
    """What the server has, for a message -- or "unknown" if it cannot be asked.

    A hint is already being written because something failed, so a second failure
    here must not replace it with a traceback about the first.
    """
    try:
        models = list_models(config)
    except Exception:  # noqa: BLE001 -- any transport failure means "cannot say"
        return "unknown"
    return ", ".join(models) or "none"


OLLAMA = Provider(
    name="ollama",
    label="Ollama",
    takes_num_ctx=True,
    chat_model=_ollama_chat_model,
    installed=_ollama_installed,
    # ``httpx.HTTPError`` is in the list because it is what actually reaches us
    # when Ollama is not running. The ollama client only converts a refused
    # connection into a builtin ``ConnectionError`` on its *non*-streaming path,
    # and ``ChatOllama`` always chats over the streaming one -- so a stopped
    # server, a model too slow to answer and a killed stream all arrive as raw
    # ``httpx`` errors, none of which is an ``OSError``. ``OSError`` covers the
    # builtin ``ConnectionError`` the non-streaming path raises, since that is a
    # subclass of it. Note that ``RequestError`` here is ollama's own, a
    # different class from httpx's identically named one.
    transport_errors=(ResponseError, RequestError, OSError, httpx.HTTPError),
    hint=_ollama_hint,
)

VLLM = Provider(
    name="vllm",
    label="vLLM",
    takes_num_ctx=False,
    chat_model=_vllm_chat_model,
    installed=_vllm_installed,
    # ``openai.OpenAIError`` is the root of that client's hierarchy, so it covers
    # a refused connection, a timeout and every status vLLM answers with. The
    # other two are for the listing above, which goes over httpx directly.
    transport_errors=(openai.OpenAIError, OSError, httpx.HTTPError),
    hint=_vllm_hint,
)

#: Every provider, by the name the CLI, the API and ``$BUY_AGENT_PROVIDER`` use.
#: :data:`buy_agent.config.PROVIDER_DEFAULTS` names the same ones, and
#: ``tests/test_conventions.py`` checks that the two agree -- a provider with
#: behaviour and no defaults is one that cannot be configured, and a provider
#: with defaults and no behaviour is a ``KeyError`` on the first run.
PROVIDERS: dict[str, Provider] = {provider.name: provider for provider in (OLLAMA, VLLM)}


def provider_for(name: str) -> Provider:
    """The provider called ``name``.

    Raises:
        ValueError: naming the ones that do exist, since this is reached from a
            CLI flag, a form field and an environment variable alike.
    """
    try:
        return PROVIDERS[name]
    except KeyError:
        msg = f"Unknown provider {name!r}; expected one of {', '.join(PROVIDERS)}."
        raise ValueError(msg) from None


def build_chat_model(config: AgentConfig) -> BaseChatModel:
    """The chat model this config asks for -- the one seam ``llm=`` bypasses."""
    return provider_for(config.provider).chat_model(config)


def list_models(config: AgentConfig) -> list[str]:
    """What the configured server is serving. Raises whatever the transport raises.

    Both callers need it -- the CLI names them in a failure message, the browser
    shows them in a picker -- and each phrases a failure its own way, so the
    shared part is only the call.
    """
    return provider_for(config.provider).installed(config)


def unavailable_hint(config: AgentConfig, exc: Exception) -> str:
    """Why the model server could not be used, and what to do about it."""
    return provider_for(config.provider).hint(config, exc)


def transport_errors(config: AgentConfig) -> tuple[type[BaseException], ...]:
    """The exceptions that mean this provider's server is not answering."""
    return provider_for(config.provider).transport_errors
