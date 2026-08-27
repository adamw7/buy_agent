"""Turning a web request into an :class:`~buy_agent.agent.BuyAgent` run.

This module is the whole HTTP-facing half of the agent that is worth testing:
reading options off a request, running the pipeline, and shaping the answer as
JSON. :mod:`buy_agent.server` is only the socket around it.

Options arrive either as a JSON body (``POST /api/search``) or as query
parameters (``GET /api/search/stream``), so every value is coerced from either
its native JSON type or the string a query string always yields.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING, Any, TypeVar, get_args

from buy_agent.agent import BuyAgent, OllamaUnavailableError, list_models
from buy_agent.config import AgentConfig
from buy_agent.ranking import SortBy
from buy_agent.search import SearchError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from buy_agent.models import RankedProduct

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

#: Constrained, so ``minimum <= number <= maximum`` in :func:`_as_number` is a
#: comparison the type checker can see is valid for whichever kind it was given.
_Number = TypeVar("_Number", int, float)

#: Builds the agent :func:`run_search` uses -- ``BuyAgent`` itself, unless a test
#: hands it a stub instead.
AgentFactory = Callable[[AgentConfig], BuyAgent]

SORT_OPTIONS: tuple[str, ...] = get_args(SortBy)

_TRUE = frozenset({"true", "1", "yes", "on"})
_FALSE = frozenset({"false", "0", "no", "off"})

#: Which HTTP status each of the agent's three failure modes deserves. The agent
#: raises exactly these (see ``BuyAgent.run``), so a new failure mode has to be
#: added here as well as to ``__main__.main`` or it reaches the client as a 500.
_STATUS: dict[type[Exception], int] = {
    ValueError: 400,
    OllamaUnavailableError: 503,
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
        data: Decoded JSON body or query parameters. Missing keys, and keys whose
            value is an empty string, fall back to the ``AgentConfig`` default --
            an empty form field means "unset", not "zero".

    Returns:
        The config to run with, and the ``sort_by`` criterion.

    Raises:
        ApiError: if a value is present but not usable.
    """
    defaults = AgentConfig()
    num_products = _read(data, "results", defaults.num_products, _bounded(int, 1, 50))
    top_n = _read(data, "top", defaults.top_n, _bounded(int, 1, 50))
    sort_by = _read(data, "sort_by", "score", _as_text)
    if sort_by not in SORT_OPTIONS:
        msg = f"sort_by must be one of {', '.join(SORT_OPTIONS)}; got {sort_by!r}."
        raise ApiError(msg)

    config = AgentConfig(
        model=_read(data, "model", defaults.model, _as_text),
        base_url=_read(data, "base_url", defaults.base_url, _as_text),
        temperature=_read(data, "temperature", defaults.temperature, _bounded(float, 0, 2)),
        num_ctx=_read(data, "num_ctx", defaults.num_ctx, _bounded(int, 1, 1_000_000)),
        reasoning=_read(data, "think", defaults.reasoning, _as_bool),
        # Searching for fewer pages than we intend to report would cap the report,
        # so the search width follows whichever of the two is larger -- as in the CLI.
        search_results=max(num_products, top_n),
        num_products=num_products,
        top_n=top_n,
        region=_read(data, "region", defaults.region, _as_text),
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
        agent_factory: Builds the agent from the config -- the seam tests inject
            a stub agent through, mirroring ``BuyAgent(config, llm=...)``.

    Returns:
        ``{"request", "count", "top_n", "sort_by", "products"}``, where products
        are best first and each carries its rank and score.

    Raises:
        ApiError: for every failure the agent raises, carrying the HTTP status
            that failure deserves.
    """
    try:
        ranked = agent_factory(config).run(request, sort_by=sort_by)  # type: ignore[arg-type]
    except tuple(_STATUS) as exc:
        # The mapping and the clause catching it are the same table, so this
        # search cannot come up empty and needs no fallback status.
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

    Carries the raw fields *and* the labels ``Product`` already knows how to
    write, so the browser never has to reinvent how an unknown price reads.
    """
    return {
        "rank": entry.rank,
        "score": round(entry.score, 4),
        **entry.product.model_dump(),
        "price_label": entry.product.price_label(),
        "rating_label": entry.product.rating_label(),
    }


def defaults_payload() -> dict[str, Any]:
    """The form's starting values: the same defaults the CLI shows in ``--help``."""
    defaults = AgentConfig()
    return {
        "model": defaults.model,
        "base_url": defaults.base_url,
        "temperature": defaults.temperature,
        "num_ctx": defaults.num_ctx,
        "think": defaults.reasoning,
        "results": defaults.num_products,
        "top": defaults.top_n,
        "region": defaults.region,
        "fetch": defaults.fetch_pages,
        "sort_by": "score",
        "sort_options": list(SORT_OPTIONS),
    }


def installed_models(base_url: str) -> dict[str, Any]:
    """Ask Ollama which models are pulled, for the UI's model picker.

    An unreachable server is an answer, not an error: the UI shows it as a
    status rather than refusing to render a form.
    """
    try:
        models = list_models(base_url)
    except Exception as exc:  # noqa: BLE001 -- any transport failure means "not there"
        logger.debug("Could not list Ollama models at %s", base_url, exc_info=True)
        return {"base_url": base_url, "reachable": False, "models": [], "detail": str(exc)}
    return {"base_url": base_url, "reachable": True, "models": models}


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

    Every option arrives either as its native JSON type or as the string a query
    string always yields, and every one of those survives ``str`` intact: a JSON
    ``true`` becomes "True", which :func:`_as_bool` reads straight back. So the
    parsing starts from text, and the two carriers need one parser between them.

    ``parse`` is given the key as well as the text, because what it has to say
    when the text is unusable is which field it was.

    Raises:
        ApiError: if the value is present but ``parse`` cannot make sense of it.
    """
    if not _present(data, key):
        return default
    return parse(key, str(data[key]).strip())


def _as_text(_key: str, text: str) -> str:
    """Stripped text, which every string option already is."""
    return text


def _as_bool(key: str, text: str) -> bool:
    """A checkbox, a query parameter or a JSON boolean, all read the same way."""
    lowered = text.lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    msg = f"{key} must be true or false; got {text!r}."
    raise ApiError(msg)


def _bounded(
    kind: Callable[[str], _Number], minimum: _Number, maximum: _Number
) -> Callable[[str, str], _Number]:
    """A parser for a number within bounds, whole or decimal as ``kind`` says.

    The bounds are written into the rejection as they are given, so whole ones
    are passed as whole numbers: "between 0 and 2" is what a temperature is.
    """
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
        msg = f"{key} must be {described}; got {text!r}."
        raise ApiError(msg) from exc
    if not minimum <= number <= maximum:
        msg = f"{key} must be between {minimum} and {maximum}; got {number}."
        raise ApiError(msg)
    return number
