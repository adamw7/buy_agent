"""Which model server the agent talks to: Ollama, or vLLM's OpenAI-compatible API.

Everything that differs between the two lives here, one row per server, so
nothing above this module knows which is running. A :class:`Provider` answers
what the config could not: what the server **defaults to** (model, address and
key, from its own environment variables -- ADR-0029); what **chat model** a config
builds on it; which **transport failures** mean "not there" and **how one is
phrased**, ``ollama pull`` and ``vllm serve`` being different things to type; and
**what it is serving**, each model with whether it can answer a prompt at all.

The two are not symmetric, and pretending otherwise would lie to the shopper.
Ollama holds many pulled tags and switches per request, taking the context window
and the thinking switch with it -- some of those tags being embedding models no
run can use, which is why a listing there costs a second question per tag
(ADR-0032); a vLLM process serves one model chosen when it started, fixes the
window with ``--max-model-len`` and takes only the thinking switch.
:data:`Provider.takes_num_ctx` declares that difference once, so the CLI, the API
and the form say so rather than each offering a setting that does nothing.

Nothing is imported from :mod:`buy_agent.config`: a config is what this module is
handed. The dependency runs the other way -- ``AgentConfig.model_server`` is the
one place a name becomes behaviour.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
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

#: What an Ollama model's capabilities must include for it to answer a prompt at
#: all. ``ollama show`` reports them; ``ollama list`` does not, which is why the
#: listing below asks twice (ADR-0032).
_COMPLETION = "completion"

#: How many of those second questions to have in flight at once: together, the
#: whole listing being on one short budget, but capped -- fifty pulled tags should
#: not get fifty threads to save a few milliseconds on a local call.
_PROBES = 8


@dataclass(frozen=True, slots=True)
class InstalledModel:
    """One model a server is holding, and whether it can answer a chat prompt.

    The second half is not decoration: Ollama holds whatever has been pulled,
    routinely including embedding-only models, and a listing of bare names offers
    those as if a run could use one -- it cannot, and fails partway through on a
    message nothing the shopper saw predicted. vLLM serves the one model it was
    started with, so ``completion`` is true there by construction.

    Attributes:
        name: The tag, or the repository id -- what a config's ``model`` names.
        completion: Whether asking it for one is a run that can finish.
    """

    name: str
    completion: bool


@dataclass(frozen=True, slots=True)
class Provider:
    """One model server: what it defaults to, and how it is talked to.

    Attributes:
        name: What the CLI, the API and ``$BUY_AGENT_PROVIDER`` call it.
        label: What a person reads -- "Ollama", "vLLM".
        model: What a config naming none runs on this server.
        base_url: Where it listens when a config names no address. vLLM's includes
            the ``/v1`` its OpenAI API is served under.
        api_key: Sent when a config carries none of its own. Ollama has no notion
            of one; vLLM wants one only when started with ``--api-key``, read from
            the environment and nowhere else -- a secret belongs in neither a shell
            history nor what the API hands a browser.
        takes_num_ctx: Whether the context window is a per-request setting. False
            for vLLM, which fixes it at startup, so ``AgentConfig.num_ctx`` is
            ignored there rather than quietly failing to apply.
        chat_model: Builds the LangChain chat model this config asks for.
        installed: Lists what the server is serving and what each can do, raising
            whatever the transport raises -- both callers phrase that their own way.
        transport_errors: The exceptions meaning the server could not be reached,
            or refused rather than completed.
        hint: Turns one of those into something the user can act on.
    """

    name: str
    label: str
    model: str
    base_url: str
    api_key: str
    takes_num_ctx: bool
    chat_model: Callable[[AgentConfig], BaseChatModel]
    installed: Callable[[AgentConfig], list[InstalledModel]]
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


def _ollama_installed(config: AgentConfig) -> list[InstalledModel]:
    """Every model tag Ollama has pulled, and whether each one can be run.

    Two questions, Ollama answering them in two places: ``list`` gives the tags and
    nothing about what they do, ``show`` gives one tag's capabilities. The second
    goes out once per tag, together rather than in turn, :data:`_LIST_TIMEOUT`
    being the budget for the whole listing with a form waiting on it (ADR-0032).

    A tag that will not say what it can do counts as able to answer: keeping an
    unusable model out of the way is the point, but hiding a working one on a
    failed probe would be the worse mistake.
    """
    client = Client(config.base_url, timeout=_LIST_TIMEOUT)
    names = [model.model for model in client.list().models if model.model]
    if not names:
        return []
    with ThreadPoolExecutor(max_workers=min(len(names), _PROBES)) as pool:
        return list(pool.map(partial(_ollama_capability, client), names))


def _ollama_capability(client: Client, name: str) -> InstalledModel:
    """Ask one pulled tag whether it has a completion to give.

    ``capabilities`` is absent on an Ollama too old to report it -- the same answer
    as a failed probe, nothing here knowing better than the tag.
    """
    try:
        capabilities = client.show(name).capabilities
    except Exception:  # noqa: BLE001 -- any failure means "cannot say", not "cannot run"
        return InstalledModel(name, completion=True)
    return InstalledModel(
        name, completion=capabilities is None or _COMPLETION in capabilities
    )


def _ollama_hint(config: AgentConfig, exc: Exception) -> str:
    """Turn an Ollama failure into something the user can act on.

    Two cases are Ollama's own, both coming of it holding many tags rather than
    serving one. A name it does not know is one to pull. A name it knows that has
    no completion to give is an embedding model, which without this falls through
    to "start the server" -- wrong and unactionable, the server having answered
    (ADR-0032). The other two are what either server would say, written once below.
    """
    if isinstance(exc, httpx.TimeoutException):
        return _too_slow_hint(config, exc)
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


def _vllm_chat_model(config: AgentConfig) -> BaseChatModel:
    """vLLM through its OpenAI-compatible API.

    ``num_ctx`` is deliberately not passed: vLLM fixes the window at startup and
    would reject an unknown field, so it is declared a setting this provider does
    not take (:data:`Provider.takes_num_ctx`). ``reasoning`` keeps its tri-state --
    ``None`` sends nothing and leaves the chat template alone; True and False set
    the ``enable_thinking`` those templates read (ADR-0019).
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


def _vllm_installed(config: AgentConfig) -> list[InstalledModel]:
    """What vLLM is serving -- one model, in the list shape the picker wants.

    Over ``httpx`` rather than the OpenAI client, the whole request being a ``GET``
    of ``/v1/models``; ``base_url`` already ends in the API root. There is no
    second question to ask: a vLLM serves the model it was started for, so
    everything it lists is something a run can use.
    """
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
    """Turn a vLLM failure into something the user can act on.

    Two cases are vLLM's own. A refused key is a server started with ``--api-key``,
    which Ollama has no notion of. A name it does not know is not something to
    pull, it serving one model chosen at startup -- a server to restart or a name
    to correct, so the message names both.
    """
    detail = str(exc)
    if isinstance(exc, (httpx.TimeoutException, openai.APITimeoutError)):
        return _too_slow_hint(config, exc)
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


def _too_slow_hint(config: AgentConfig, exc: Exception) -> str:
    """A server that took the prompt and never came back.

    Only the remedy differs, for a reason already declared: where the window is a
    per-request setting there is a smaller one to ask for, and where the server
    fixed it at startup there is only a shorter prompt to send.
    """
    server = config.model_server
    smaller = "a smaller --num-ctx" if server.takes_num_ctx else "a shorter prompt"
    # A timeout often stringifies to nothing, and "()" says less than the class.
    detail = str(exc) or type(exc).__name__
    return (
        f"{server.label} at {config.base_url} did not answer in time ({detail}). "
        f"The model {config.model!r} may be too slow for this prompt -- "
        f"try a smaller model, or {smaller}."
    )


def _unreachable_hint(config: AgentConfig, exc: Exception, start: str) -> str:
    """Nothing answered at all, so the server itself is what is missing."""
    return (
        f"Could not reach {config.model_server.label} at {config.base_url} ({exc}). "
        f"Start it with:  {start}"
    )


def _listed(config: AgentConfig, *, completing: bool = False) -> str:
    """What the server has, for a message -- or "unknown" if it cannot be asked.

    A hint is already being written because something failed, so a second failure
    must not replace it with a traceback about the first. ``completing`` narrows
    the answer to the models that can answer a prompt, which is what to offer
    someone whose chosen model cannot; elsewhere the whole listing is the useful
    answer, a missing tag and an unusable one being different mistakes.
    """
    try:
        models = config.model_server.installed(config)
    except Exception:  # noqa: BLE001 -- any transport failure means "cannot say"
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
    # ``httpx.HTTPError`` is what actually reaches us when Ollama is not running:
    # the ollama client turns a refused connection into a builtin
    # ``ConnectionError`` only on its *non*-streaming path, and ``ChatOllama``
    # always chats over the streaming one -- so a stopped server, a model too slow
    # to answer and a killed stream all arrive as raw httpx errors, none an
    # ``OSError``, which covers the non-streaming path instead. ``RequestError``
    # is ollama's own, a different class from httpx's identically named one.
    transport_errors=(ResponseError, RequestError, OSError, httpx.HTTPError),
    hint=_ollama_hint,
)

VLLM = Provider(
    name="vllm",
    label="vLLM",
    # A repository id rather than a tag: what ``vllm serve`` is given and what
    # ``/v1/models`` reports back. The address is the API root and not the host,
    # the OpenAI client appending its paths to whatever it is given.
    model=os.getenv("VLLM_MODEL", "Qwen/Qwen3-8B"),
    base_url=os.getenv("VLLM_HOST", "http://localhost:8000/v1"),
    api_key=os.getenv("VLLM_API_KEY", ""),
    takes_num_ctx=False,
    chat_model=_vllm_chat_model,
    installed=_vllm_installed,
    # ``openai.OpenAIError`` is the root of that client's hierarchy: a refused
    # connection, a timeout and every status vLLM answers with. The other two are
    # for the listing above, which goes over httpx directly.
    transport_errors=(openai.OpenAIError, OSError, httpx.HTTPError),
    hint=_vllm_hint,
)

#: Every provider, by the name the CLI, the API and ``$BUY_AGENT_PROVIDER`` use.
#: The one table -- each row carries both what a server defaults to and how it is
#: talked to, so a third is a row here and nothing anywhere else (ADR-0029).
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
        raise ValueError(
            f"Unknown provider {name!r}; expected one of {', '.join(PROVIDERS)}."
        ) from None


def provider_options() -> list[dict[str, object]]:
    """Every provider a run can be pointed at, as the form's picker needs it.

    Carries each one's defaults, so choosing a provider in the browser fills in the
    model and address that go with it rather than leaving an Ollama tag in a field
    a vLLM will refuse. ``api_key`` is deliberately absent: this payload goes to a
    browser, and that one is a secret.
    """
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
