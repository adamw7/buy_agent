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
from typing import TYPE_CHECKING, Any, get_args

from buy_agent.agent import BuyAgent, OllamaUnavailableError, list_models
from buy_agent.config import AgentConfig
from buy_agent.ranking import SortBy
from buy_agent.search import SearchError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from buy_agent.models import RankedProduct

logger = logging.getLogger(__name__)

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
    num_products = _int(data, "results", defaults.num_products, minimum=1, maximum=50)
    top_n = _int(data, "top", defaults.top_n, minimum=1, maximum=50)
    sort_by = _text(data, "sort_by", "score")
    if sort_by not in SORT_OPTIONS:
        msg = f"sort_by must be one of {', '.join(SORT_OPTIONS)}; got {sort_by!r}."
        raise ApiError(msg)

    config = AgentConfig(
        model=_text(data, "model", defaults.model),
        base_url=_text(data, "base_url", defaults.base_url),
        temperature=_float(data, "temperature", defaults.temperature),
        num_ctx=_optional_int(data, "num_ctx", defaults.num_ctx),
        reasoning=_optional_bool(data, "think", defaults.reasoning),
        # Searching for fewer pages than we intend to report would cap the report,
        # so the search width follows whichever of the two is larger -- as in the CLI.
        search_results=max(num_products, top_n),
        num_products=num_products,
        top_n=top_n,
        region=_text(data, "region", defaults.region),
        fetch_pages=_bool(data, "fetch", defaults.fetch_pages),
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
        raise ApiError(str(exc), _status_for(exc)) from exc

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


def _status_for(exc: Exception) -> int:
    for kind, status in _STATUS.items():
        if isinstance(exc, kind):
            return status
    return 500


def _present(data: Mapping[str, Any], key: str) -> bool:
    """Is the key set to something? An empty form field counts as unset."""
    value = data.get(key)
    return value is not None and not (isinstance(value, str) and not value.strip())


def _text(data: Mapping[str, Any], key: str, default: str) -> str:
    if not _present(data, key):
        return default
    return str(data[key]).strip()


def _int(
    data: Mapping[str, Any], key: str, default: int, *, minimum: int, maximum: int
) -> int:
    if not _present(data, key):
        return default
    try:
        number = int(str(data[key]).strip())
    except (TypeError, ValueError) as exc:
        msg = f"{key} must be a whole number; got {data[key]!r}."
        raise ApiError(msg) from exc
    if not minimum <= number <= maximum:
        msg = f"{key} must be between {minimum} and {maximum}; got {number}."
        raise ApiError(msg)
    return number


def _optional_int(data: Mapping[str, Any], key: str, default: int | None) -> int | None:
    if not _present(data, key):
        return default
    return _int(data, key, 0, minimum=1, maximum=1_000_000)


def _float(data: Mapping[str, Any], key: str, default: float) -> float:
    if not _present(data, key):
        return default
    try:
        number = float(str(data[key]).strip())
    except (TypeError, ValueError) as exc:
        msg = f"{key} must be a number; got {data[key]!r}."
        raise ApiError(msg) from exc
    if not 0.0 <= number <= 2.0:
        msg = f"{key} must be between 0 and 2; got {number}."
        raise ApiError(msg)
    return number


def _bool(data: Mapping[str, Any], key: str, default: bool) -> bool:
    value = _optional_bool(data, key, None)
    return default if value is None else value


def _optional_bool(
    data: Mapping[str, Any], key: str, default: bool | None
) -> bool | None:
    if not _present(data, key):
        return default
    value = data[key]
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    msg = f"{key} must be true or false; got {value!r}."
    raise ApiError(msg)
