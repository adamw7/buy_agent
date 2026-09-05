"""Turning a web request into an :class:`~buy_agent.agent.BuyAgent` run.

The whole HTTP-facing half worth testing -- reading options off a request,
running the pipeline, shaping the answer as JSON; :mod:`buy_agent.server` is only
the socket around it. Options arrive as a JSON body (``POST /api/search``) or as
query parameters (``GET /api/search/stream``), so every value is coerced from
text, which either carrier can spell.

:func:`rank_again` is the one entry point that runs no pipeline: a finished run's
products, posted back and sorted another way. POST only -- a query string cannot
carry a list of products.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING, Any, TypeVar, cast, get_args

from pydantic import ValidationError

from buy_agent.agent import (
    BuyAgent,
    Checkpoint,
    ModelUnavailableError,
    every_step_passes,
)
from buy_agent.config import LIMITS, AgentConfig, parse_region
from buy_agent.models import Product
from buy_agent.providers import PROVIDERS, provider_options
from buy_agent.ranking import SortBy, rank_products
from buy_agent.search import SearchError
from buy_agent.sources import Source, format_sources, parse_sources

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

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

#: The config field whose range holds each number a request may carry, by the key
#: it arrives under -- ``results`` is what a request calls ``num_products``. One
#: table read twice: :func:`_bounded` holds an incoming value to the range, and
#: :func:`limits_payload` ships the same ranges to the form, so a field can refuse
#: 100 products before a run starts without a second copy of the bounds in
#: TypeScript (ADR-0033).
_BOUNDED: dict[str, str] = {
    "results": "num_products",
    "top": "top_n",
    "temperature": "temperature",
    "num_ctx": "num_ctx",
}

#: Which HTTP status each of the agent's three failure modes deserves. It raises
#: exactly these (see ``BuyAgent.run``), so a new one has to be added here and to
#: ``__main__.main`` or it reaches the client as a 500.
_STATUS: dict[type[Exception], int] = {
    ValueError: 400,
    ModelUnavailableError: 503,
    SearchError: 502,
}


class ApiError(Exception):
    """A failure with the HTTP status the client should be told about.

    ``field`` is the request key the unusable value arrived under, so the browser
    can mark that input rather than only writing the sentence into a banner --
    which field a message is about being Python's to say (ADR-0033). ``None``
    where the failure is about the run rather than a value: a model server that
    did not answer is nothing the form could have refused.
    """

    def __init__(self, message: str, status: int = 400, field: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.field = field

    def payload(self) -> dict[str, Any]:
        return {"error": str(self), "field": self.field}


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
    num_products = _read(data, "results", defaults.num_products, _bounded(int, "results"))
    top_n = _read(data, "top", defaults.top_n, _bounded(int, "top"))
    sort_by = _read(data, "sort_by", "score", _as_sort_by)

    provider = _read(data, "provider", defaults.provider, _as_text)
    if provider not in PROVIDER_OPTIONS:
        raise ApiError(
            f"provider must be one of {', '.join(PROVIDER_OPTIONS)}; got {provider!r}.",
            field="provider",
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
    checkpoint: Checkpoint = every_step_passes,
) -> dict[str, Any]:
    """Run the pipeline and shape the answer as JSON-ready data.

    Args:
        request: What the user wants to buy, in their own words.
        config: Model, search and ranking settings.
        sort_by: ``"score"``, ``"price"`` or ``"rating"``.
        agent_factory: Builds the agent from the config -- the seam tests inject a
            stub through, mirroring ``BuyAgent(config, llm=...)``.
        checkpoint: Handed to ``BuyAgent.run``, which calls it at each step
            boundary. A caller whose client has gone raises from it to end the run
            there (ADR-0034); what it raises is not turned into an ``ApiError``,
            since there is nobody left to answer with one.

    Returns:
        ``{"request", "count", "top_n", "sort_by", "products"}``, best first, each
        product carrying its rank and score.

    Raises:
        ApiError: for every failure the agent raises, with the status it deserves.
    """
    try:
        ranked = agent_factory(config).run(  # type: ignore[arg-type]
            request, sort_by=sort_by, checkpoint=checkpoint
        )
    except tuple(_STATUS) as exc:
        # The clause and the mapping are one table, so this cannot come up empty.
        status = next(status for kind, status in _STATUS.items() if isinstance(exc, kind))
        raise ApiError(str(exc), status) from exc

    return _run_payload(request, ranked, config.top_n, sort_by)


def rank_again(data: Mapping[str, Any]) -> dict[str, Any]:
    """Put a finished run's products in another order, without running it again.

    Nothing in :func:`~buy_agent.ranking.rank_products` needs a model or a
    network, so re-ordering is that ordering asked for on its own rather than a
    second minute-long run (ADR-0035). The judgement stays here: the products go
    back to Python and come back scored by the function a run ends with, and the
    scores the browser holds are ignored and recomputed, a score being a fact
    about the whole candidate set.

    Args:
        data: ``{"request", "products", "sort_by", "top"}`` -- the products as
            :func:`product_payload` wrote them (its extra keys are ignored), and
            the two settings shaping the answer around them. How many may arrive
            is what the body size allows, which is the server's to cap.

    Returns:
        The shape a finished run answers with, so the page shows it the same way.

    Raises:
        ApiError: if the criterion, the count or the products are unusable.
    """
    defaults = AgentConfig()
    request = _read(data, "request", "", _as_text)
    sort_by = _read(data, "sort_by", "score", _as_sort_by)
    top_n = _read(data, "top", defaults.top_n, _bounded(int, "top"))
    ranked = rank_products(_read_products(data), sort_by=cast(SortBy, sort_by))
    return _run_payload(request, ranked, top_n, sort_by)


def _run_payload(
    request: str, ranked: Sequence[RankedProduct], top_n: int, sort_by: str
) -> dict[str, Any]:
    """The shape a finished run answers with, however it was finished."""
    return {
        "request": request.strip(),
        "count": len(ranked),
        "top_n": top_n,
        "sort_by": sort_by,
        "products": results_payload(ranked),
    }


def results_payload(ranked: Sequence[RankedProduct]) -> list[dict[str, Any]]:
    """A whole run's products as JSON, best first.

    One shape for every way a run leaves the process: the API's answer, the file
    ``--json`` writes, and the file Download results hands over -- that answer
    saved, so the browser composes no document of its own.
    """
    return [product_payload(entry) for entry in ranked]


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

    ``completion`` is sent rather than acted on: the form *marks* a model that
    cannot answer a prompt instead of hiding it, so a tag pulled by mistake stays
    visible (ADR-0032).
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
        # What each number field may hold, so the form can refuse 51 products
        # itself rather than opening a stream to be told (ADR-0033).
        "limits": limits_payload(),
    }


def limits_payload() -> dict[str, dict[str, int]]:
    """The range each number a request carries is held to, by the key it uses.

    Shipped rather than written into the form, for the reason both front doors
    read :data:`buy_agent.config.LIMITS`: a range written down twice is a form
    that accepts what the API refuses. The browser applies it and does not choose
    it, which is the line ADR-0033 draws.
    """
    return {
        key: dict(zip(("min", "max"), LIMITS[field]))
        for key, field in _BOUNDED.items()
    }


def sources_payload(spec: str) -> dict[str, Any]:
    """Whether a Trusted-sources field names sources, and what is wrong if not.

    The one option the form cannot judge for itself: a range is two numbers the
    server ships, but a source is whatever
    :func:`~buy_agent.sources.parse_sources` reads, and writing that again in
    TypeScript is the drift ADR-0031 refused for the region. So the browser asks,
    and gets the sentence the CLI prints (ADR-0033).

    Args:
        spec: What the field holds -- one string, which may name several sources.
            Empty is the whole web, and fine.

    Returns:
        ``{"sources", "error"}``. ``error`` is empty for a field with nothing
        wrong with it; ``sources`` is the spec as given, so a form typed into
        since can drop an answer about what it held a keystroke ago.
    """
    try:
        parse_sources(spec)
    except ValueError as exc:
        return {"sources": spec, "error": str(exc)}
    return {"sources": spec, "error": ""}


def installed_models(provider: str, base_url: str) -> dict[str, Any]:
    """Ask a model server what it is serving, for the UI's model picker.

    An unreachable server is an answer and not an error: the UI shows it as a
    status rather than refusing to draw a form, and a provider name nothing can
    serve is the same kind of answer. ``label`` travels with it, since the pill
    above the form names the server and "Ollama unreachable" over a vLLM address
    would be a lie the browser could not catch. Each model carries what it can do
    beside its name, Ollama holding embedding-only tags a run cannot use
    (ADR-0032).

    A failure carries two fields: ``detail`` is the transport's own reason, and
    ``hint`` is the sentence the provider would have raised had a run hit the same
    failure -- the command to start the server, the key to set, the tag to pull.
    Written here rather than in TypeScript, because the browser decides nothing
    and that sentence already exists and is tested.
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

    Not through :func:`_read`, which renders every value with ``str`` and would
    turn a JSON array into its Python repr. A query string spells several as one
    separated string; a JSON body may send either.
    """
    if not _present(data, "sources"):
        return default
    value = data["sources"]
    specs = value if isinstance(value, (list, tuple)) else str(value)
    try:
        return parse_sources(specs)
    except ValueError as exc:
        raise ApiError(str(exc), field="sources") from exc


def _present(data: Mapping[str, Any], key: str) -> bool:
    """Is the key set to something? An empty form field counts as unset.

    A list holding nothing but blanks is the same answer in the shape only
    ``sources`` arrives in: ``[]`` and ``["", " "]`` say what ``""`` says, which
    is "use the default" (ADR-0012). Read as set, they reached ``parse_sources``,
    came back empty and left the run searching the whole web -- the widening
    ``parse_named_sources`` refuses on the command line, arrived at from the other
    side. Said here rather than there, because over the wire a blank really is how
    "unset" is spelled.
    """
    value = data.get(key)
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple)):
        return any(str(entry).strip() for entry in value)
    return True


def _read(
    data: Mapping[str, Any],
    key: str,
    default: _T,
    parse: Callable[[str, str], _T],
) -> _T:
    """The value of ``key``, parsed -- or ``default`` where it is not set at all.

    Every option survives ``str`` intact (a JSON ``true`` becomes "True", which
    :func:`_as_bool` reads back), so one parser serves both carriers. ``parse``
    is given the key because its refusal names the field.

    Raises:
        ApiError: if the value is present but ``parse`` cannot make sense of it.
    """
    if not _present(data, key):
        return default
    return parse(key, str(data[key]).strip())


def _read_products(data: Mapping[str, Any]) -> list[Product]:
    """The products of a finished run, read back off the request that carried them.

    Not through :func:`_read`, for the reason :func:`_read_sources` is not -- and
    a query string cannot carry a list at all, which is why re-sorting is a POST.
    ``Product`` validates them, so what comes back is the domain model rather than
    whatever JSON was posted: :func:`product_payload`'s extra keys are ignored,
    and a missing name is refused.
    """
    value = data.get("products")
    if not isinstance(value, list):
        raise ApiError(
            "products must be the list of products a run answered with.",
            field="products",
        )
    products = []
    for index, entry in enumerate(value):
        try:
            products.append(Product.model_validate(entry))
        except ValidationError as exc:
            raise ApiError(
                f"products[{index}] is not a product a run answered with: "
                f"{exc.errors()[0]['msg']}.",
                field="products",
            ) from exc
    return products


def _as_text(_key: str, text: str) -> str:
    """Stripped text, which every string option already is."""
    return text


def _as_sort_by(key: str, text: str) -> str:
    """A ranking criterion, checked against the ones ``rank_products`` sorts by.

    Read by both doors into the ranking -- the search that ends in one and the
    re-sort that is only one -- so a fourth is offered by both the day it is added.
    """
    if text not in SORT_OPTIONS:
        raise ApiError(
            f"sort_by must be one of {', '.join(SORT_OPTIONS)}; got {text!r}.", field=key
        )
    return text


def _as_region(key: str, text: str) -> str:
    """A region code, checked for shape the way a source is checked for a site.

    A 400 naming the shape, rather than a run that searches on it and reports the
    web as having nothing to say: this is the one setting a typo makes look like
    an empty web (ADR-0031). The key travels beside the message so the form can
    mark the box it came out of (ADR-0033).
    """
    try:
        return parse_region(text)
    except ValueError as exc:
        raise ApiError(str(exc), field=key) from exc


def _as_bool(key: str, text: str) -> bool:
    """A checkbox, a query parameter or a JSON boolean, all read the same way."""
    lowered = text.lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ApiError(f"{key} must be true or false; got {text!r}.", field=key)


def _bounded(kind: Callable[[str], _Number], key: str) -> Callable[[str, str], _Number]:
    """A parser for a number within the bounds the value arriving as ``key`` has.

    Read off :data:`buy_agent.config.LIMITS` through :data:`_BOUNDED` rather than
    written down here, so the CLI, this and the form -- shipped the same table --
    cannot disagree about what a request may ask for. The bounds are quoted back
    as declared: "between 0 and 2" is what a temperature is.
    """
    minimum, maximum = LIMITS[_BOUNDED[key]]
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
        raise ApiError(f"{key} must be {described}; got {text!r}.", field=key) from exc
    if not minimum <= number <= maximum:
        raise ApiError(
            f"{key} must be between {minimum} and {maximum}; got {number}.", field=key
        )
    return number
