"""Turning a web request into an :class:`~buy_agent.agent.BuyAgent` run.

The whole HTTP-facing half of the agent that is worth testing: reading options
off a request, running the pipeline, shaping the answer as JSON.
:mod:`buy_agent.server` is only the socket around it. Options arrive as a JSON
body (``POST /api/search``) or as query parameters (``GET
/api/search/stream``), so every value is coerced from either its native JSON
type or the string a query string yields.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING, Any, TypeVar, get_args

from buy_agent.agent import BuyAgent, ModelUnavailableError
from buy_agent.config import LIMITS, AgentConfig, parse_region
from buy_agent.providers import PROVIDERS, provider_options
from buy_agent.ranking import SortBy
from buy_agent.search import SearchError
from buy_agent.sources import Source, format_sources, parse_sources

if TYPE_CHECKING:
    from collections.abc import Mapping

    from buy_agent.models import RankedProduct
    from buy_agent.providers import InstalledModel

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

#: Constrained, so ``minimum <= number <= maximum`` in :func:`_as_number` is a
#: comparison the type checker can see is valid for whichever kind it was given.
_Number = TypeVar("_Number", int, float)

#: Builds the agent :func:`run_search` uses -- ``BuyAgent`` itself, unless a test
#: hands it a stub instead.
AgentFactory = Callable[[AgentConfig], BuyAgent]

SORT_OPTIONS: tuple[str, ...] = get_args(SortBy)

#: The model servers a request may name, read off the registry rather than
#: written down again -- a provider added there is offered here on the same day.
PROVIDER_OPTIONS: tuple[str, ...] = tuple(PROVIDERS)

_TRUE = frozenset({"true", "1", "yes", "on"})
_FALSE = frozenset({"false", "0", "no", "off"})

#: Which HTTP status each of the agent's three failure modes deserves. It raises
#: exactly these (see ``BuyAgent.run``), so a new one has to be added here and to
#: ``__main__.main`` or it reaches the client as a 500.
_STATUS: dict[type[Exception], int] = {
    ValueError: 400,
    ModelUnavailableError: 503,
    SearchError: 502,
}


class ApiError(Exception):
    """A failure with the HTTP status the client should be told about."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status

    def payload(self) -> dict[str, Any]:
        return {"error": str(self)}


def parse_options(data: Mapping[str, Any]) -> tuple[AgentConfig, str]:
    """Read an :class:`AgentConfig` and a sort criterion out of request data.

    Args:
        data: Decoded JSON body or query parameters. A missing key and an empty
            string alike fall back to the ``AgentConfig`` default -- a blank form
            field means "unset", not "zero".

    Returns:
        The config to run with, and the ``sort_by`` criterion.

    Raises:
        ApiError: if a value is present but not usable.
    """
    defaults = AgentConfig()
    num_products = _read(data, "results", defaults.num_products, _bounded(int, "num_products"))
    top_n = _read(data, "top", defaults.top_n, _bounded(int, "top_n"))
    sort_by = _read(data, "sort_by", "score", _as_text)
    if sort_by not in SORT_OPTIONS:
        raise ApiError(f"sort_by must be one of {', '.join(SORT_OPTIONS)}; got {sort_by!r}.")

    provider = _read(data, "provider", defaults.provider, _as_text)
    if provider not in PROVIDER_OPTIONS:
        raise ApiError(
            f"provider must be one of {', '.join(PROVIDER_OPTIONS)}; got {provider!r}."
        )

    config = AgentConfig(
        provider=provider,
        # Blank rather than ``defaults``, which was built for whichever provider
        # the server starts on: an empty string is what ``AgentConfig`` resolves
        # per provider, so a form that chose one and left these alone gets its pair.
        model=_read(data, "model", "", _as_text),
        base_url=_read(data, "base_url", "", _as_text),
        temperature=_read(
            data, "temperature", defaults.temperature, _bounded(float, "temperature")
        ),
        num_ctx=_read(data, "num_ctx", defaults.num_ctx, _bounded(int, "num_ctx")),
        reasoning=_read(data, "think", defaults.reasoning, _as_bool),
        # Searching fewer pages than we report would cap the report -- as in the CLI.
        search_results=max(num_products, top_n),
        num_products=num_products,
        top_n=top_n,
        region=_read(data, "region", defaults.region, _as_region),
        sources=_read_sources(data, defaults.sources),
        fetch_pages=_read(data, "fetch", defaults.fetch_pages, _as_bool),
    )
    return config, sort_by


def run_search(
    request: str,
    config: AgentConfig,
    *,
    sort_by: str = "score",
    agent_factory: AgentFactory = BuyAgent,
) -> dict[str, Any]:
    """Run the pipeline and shape the answer as JSON-ready data.

    Args:
        request: What the user wants to buy, in their own words.
        config: Model, search and ranking settings.
        sort_by: ``"score"``, ``"price"`` or ``"rating"``.
        agent_factory: Builds the agent from the config -- the seam tests inject a
            stub through, mirroring ``BuyAgent(config, llm=...)``.

    Returns:
        ``{"request", "count", "top_n", "sort_by", "products"}``, best first, each
        product carrying its rank and score.

    Raises:
        ApiError: for every failure the agent raises, with the status it deserves.
    """
    try:
        ranked = agent_factory(config).run(request, sort_by=sort_by)  # type: ignore[arg-type]
    except tuple(_STATUS) as exc:
        # The clause and the mapping are one table, so this cannot come up empty.
        status = next(status for kind, status in _STATUS.items() if isinstance(exc, kind))
        raise ApiError(str(exc), status) from exc

    return {
        "request": request.strip(),
        "count": len(ranked),
        "top_n": config.top_n,
        "sort_by": sort_by,
        "products": [product_payload(entry) for entry in ranked],
    }


def product_payload(entry: RankedProduct) -> dict[str, Any]:
    """One ranked product as JSON.

    The raw fields *and* the labels ``Product`` already knows how to write, so the
    browser never reinvents how a blank price reads.
    """
    return {
        "rank": entry.rank,
        "score": round(entry.score, 4),
        **entry.product.model_dump(),
        "price_label": entry.product.price_label(),
        "rating_label": entry.product.rating_label(),
    }


def model_payload(model: InstalledModel) -> dict[str, Any]:
    """One model a server is holding, as the picker needs it.

    A name would have been enough until Ollama's listing started answering what
    each tag can do. ``completion`` is that answer, and it is sent rather than
    acted on here: the form marks a model that cannot answer a prompt instead of
    hiding it, so a tag pulled by mistake stays visible (ADR-0032).
    """
    return {"name": model.name, "completion": model.completion}


def defaults_payload() -> dict[str, Any]:
    """The form's starting values: the same defaults the CLI shows in ``--help``."""
    defaults = AgentConfig()
    return {
        "provider": defaults.provider,
        # Each provider's model and server travel with it, so choosing one in the
        # form fills in its pair rather than leaving an Ollama tag for a vLLM.
        "provider_options": provider_options(),
        "model": defaults.model,
        "base_url": defaults.base_url,
        "temperature": defaults.temperature,
        "num_ctx": defaults.num_ctx,
        "think": defaults.reasoning,
        "results": defaults.num_products,
        "top": defaults.top_n,
        "region": defaults.region,
        # One text field's worth, written the way the form sends it back.
        # Empty -- the default -- is the whole web.
        "sources": format_sources(defaults.sources),
        "fetch": defaults.fetch_pages,
        "sort_by": "score",
        "sort_options": list(SORT_OPTIONS),
    }


def installed_models(provider: str, base_url: str) -> dict[str, Any]:
    """Ask a model server what it is serving, for the UI's model picker.

    An unreachable server is an answer and not an error: the UI shows it as a
    status rather than refusing to render a form, and a provider name nothing can
    serve is the same kind of answer -- ``AgentConfig`` refuses it below and the
    refusal is what the browser is told. ``label`` travels with it because the
    pill above the form names the server, and "Ollama unreachable" over a vLLM
    address would be a lie the browser could not catch.

    Each model carries what it can do beside its name, because Ollama holds
    embedding-only tags that a run cannot use and a listing of bare names offers
    them as if it could (ADR-0032).

    A failure carries two fields, not one: ``detail`` is the transport's own
    reason, and ``hint`` is the sentence the provider would have raised had a run
    hit the same failure -- the command to start the server, the key to set, the
    tag to pull. It is written here rather than in TypeScript for the reason
    everything else is: the browser decides nothing, and the sentence that names
    the remedy already exists and is tested.
    """
    label = PROVIDERS[provider].label if provider in PROVIDERS else provider
    status = {"provider": provider, "label": label, "base_url": base_url}
    config: AgentConfig | None = None
    try:
        config = AgentConfig(provider=provider, base_url=base_url)
        models = config.model_server.installed(config)
    except Exception as exc:  # noqa: BLE001 -- any transport failure means "not there"
        logger.debug("Could not list %s models at %s", label, base_url, exc_info=True)
        failed = {**status, "reachable": False, "models": [], "detail": str(exc)}
        # A config that never got built named a provider nothing can serve, so
        # there is no row to ask for a remedy -- and its own refusal is one.
        if config is not None:
            failed["hint"] = config.model_server.hint(config, exc)
        return failed
    return {
        **status,
        "reachable": True,
        "models": [model_payload(model) for model in models],
    }


def _read_sources(
    data: Mapping[str, Any], default: tuple[Source, ...]
) -> tuple[Source, ...]:
    """The sources the request named, if any -- the one option that is a list.

    Not through :func:`_read`, which renders every value with ``str`` first and
    would turn a JSON array into its Python repr. A query string can only spell
    several as one separated string; a JSON body may send either.
    """
    if not _present(data, "sources"):
        return default
    value = data["sources"]
    specs = value if isinstance(value, (list, tuple)) else str(value)
    try:
        return parse_sources(specs)
    except ValueError as exc:
        raise ApiError(str(exc)) from exc


def _present(data: Mapping[str, Any], key: str) -> bool:
    """Is the key set to something? An empty form field counts as unset."""
    value = data.get(key)
    return value is not None and not (isinstance(value, str) and not value.strip())


def _read(
    data: Mapping[str, Any],
    key: str,
    default: _T,
    parse: Callable[[str, str], _T],
) -> _T:
    """The value of ``key``, parsed -- or ``default`` where it is not set at all.

    Every option survives ``str`` intact -- a JSON ``true`` becomes "True", which
    :func:`_as_bool` reads straight back -- so parsing starts from text and the
    two carriers need one parser between them. ``parse`` is given the key too,
    because what it says when the text is unusable is which field it was.

    Raises:
        ApiError: if the value is present but ``parse`` cannot make sense of it.
    """
    if not _present(data, key):
        return default
    return parse(key, str(data[key]).strip())


def _as_text(_key: str, text: str) -> str:
    """Stripped text, which every string option already is."""
    return text


def _as_region(_key: str, text: str) -> str:
    """A region code, checked for shape the way a source is checked for a site.

    A 400 naming the shape, rather than a run that searches on it and reports
    that the web had nothing to say: this is the one setting a typo makes look
    like an empty web (ADR-0031). The message quotes the value and the shapes
    that work, so the field it came from needs no naming.
    """
    try:
        return parse_region(text)
    except ValueError as exc:
        raise ApiError(str(exc)) from exc


def _as_bool(key: str, text: str) -> bool:
    """A checkbox, a query parameter or a JSON boolean, all read the same way."""
    lowered = text.lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ApiError(f"{key} must be true or false; got {text!r}.")


def _bounded(kind: Callable[[str], _Number], field: str) -> Callable[[str, str], _Number]:
    """A parser for a number within the bounds ``field`` is held to.

    Read off :data:`buy_agent.config.LIMITS` rather than written down here, so the
    CLI and this cannot come to disagree about what a request may ask for. The
    bounds go into the rejection as they were declared, so whole ones are quoted
    whole: "between 0 and 2" is what a temperature is.
    """
    minimum, maximum = LIMITS[field]
    return partial(_as_number, kind, minimum, maximum)


def _as_number(
    kind: Callable[[str], _Number],
    minimum: _Number,
    maximum: _Number,
    key: str,
    text: str,
) -> _Number:
    """One number parser for both kinds: convert, then check the bounds."""
    try:
        number = kind(text)
    except ValueError as exc:
        # What to call the kind is the kind's own to say, not a second argument.
        described = "a whole number" if kind is int else "a number"
        raise ApiError(f"{key} must be {described}; got {text!r}.") from exc
    if not minimum <= number <= maximum:
        raise ApiError(f"{key} must be between {minimum} and {maximum}; got {number}.")
    return number
