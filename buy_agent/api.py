"""Turning a web request into an :class:`~buy_agent.agent.BuyAgent` run."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar, cast, get_args
from urllib.parse import urlparse

from pydantic import ValidationError

from buy_agent.agent import (
    BuyAgent,
    Checkpoint,
    ModelUnavailableError,
    every_step_passes,
    journal_for,
)
from buy_agent.bounds import Noticed, notice
from buy_agent.chat import release
from buy_agent.config import LIMITS, AgentConfig, parse_currency, parse_region
from buy_agent.models import Offer, Product, Removal, dominant_currency
from buy_agent.money import CODES, amount_label, code_for
from buy_agent.payment import (
    Cart,
    PaymentError,
    RailUnreachableError,
    Receipt,
    cart_for,
    merchant_for,
    pay_for,
    terms_for,
    unattended,
)
from buy_agent.providers import PROVIDERS, provider_options
from buy_agent.rails import RAILS, rail_options
from buy_agent.ranking import ORDERINGS, RankingWeights, SortBy, rank_products, scale_of
from buy_agent.screenshots import ScreenshotError
from buy_agent.search import BACKENDS, SearchError, backend_options
from buy_agent.sources import Source, format_sources, parse_sources

if TYPE_CHECKING:
    from collections.abc import Sequence

    from buy_agent.journal import Change
    from buy_agent.models import RankedProduct
    from buy_agent.providers import InstalledModel
    from buy_agent.screenshots import Camera

logger = logging.getLogger(__name__)

_T = TypeVar("_T")
#: Constrained, so a number parser answers its own kind.
_Number = TypeVar("_Number", int, float)

#: Builds the agent :func:`run_search` uses; a test seam.
AgentFactory = Callable[[AgentConfig], BuyAgent]

SORT_OPTIONS: tuple[str, ...] = get_args(SortBy)
PROVIDER_OPTIONS: tuple[str, ...] = tuple(PROVIDERS)
RAIL_OPTIONS: tuple[str, ...] = tuple(RAILS)
BACKEND_OPTIONS: tuple[str, ...] = tuple(BACKENDS)
CURRENCY_OPTIONS: tuple[str, ...] = tuple(sorted(CODES))

_TRUE = frozenset({"true", "1", "yes", "on"})
_FALSE = frozenset({"false", "0", "no", "off"})

#: The HTTP status of each of the agent's three failure modes (ADR-0009).
_STATUS: dict[type[Exception], int] = {
    ValueError: 400,
    ModelUnavailableError: 503,
    SearchError: 502,
}

PAY_STATUS: dict[type[Exception], int] = {
    RailUnreachableError: 502,
    PaymentError: 400,
}


@dataclass(frozen=True, slots=True)
class _Reported:
    """How a finished run is reported (ADR-0035)."""

    top_n: int
    sort_by: str
    weights: RankingWeights
    currency: str | None = None


class ApiError(Exception):
    """A failure with the HTTP status, and the box, it is reported with (ADR-0033)."""

    def __init__(self, message: str, status: int = 400, field: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.field = field

    def payload(self) -> dict[str, Any]:
        return {"error": str(self), "field": self.field}


def _status_for(exc: Exception, table: Mapping[type[Exception], int]) -> int:
    return next(status for kind, status in table.items() if isinstance(exc, kind))


def parse_options(data: Mapping[str, Any]) -> tuple[AgentConfig, SortBy]:
    """Read an :class:`AgentConfig` and a sort criterion out of request data."""
    defaults = AgentConfig()
    settings: dict[str, Any] = {
        option.field: _read(data, option.key, option.unset(defaults), option.parse)
        for option in OPTIONS
    }
    settings["sources"] = _read_sources(data, defaults.sources)
    # Searching fewer pages than we report would cap the report.
    settings["search_results"] = max(settings["num_products"], settings["top_n"])
    sort_by = _read(data, "sort_by", "score", _among(SORT_OPTIONS))
    return _configured(**settings), cast(SortBy, sort_by)


def _configured(**settings: Any) -> AgentConfig:
    try:
        return AgentConfig(**settings)
    except ValueError as exc:
        raise ApiError(str(exc), field="merchant_url") from exc


def run_search(
    request: str,
    config: AgentConfig,
    *,
    sort_by: SortBy = "score",
    agent_factory: AgentFactory = BuyAgent,
    checkpoint: Checkpoint = every_step_passes,
) -> dict[str, Any]:
    """Run the pipeline and shape the answer as JSON-ready data (ADR-0034)."""
    agent = None
    removals: list[Removal] = []
    # Opened before the run, so it holds the previous one (ADR-0060).
    journal = journal_for(request, config)
    try:
        agent = agent_factory(config)
        ranked = agent.run(
            request, sort_by=sort_by, checkpoint=checkpoint, record=removals.append
        )
    # Built from ``_STATUS``, which pylint cannot read exceptions out of.
    except tuple(_STATUS) as exc:  # pylint: disable=catching-non-exception
        # The same tuple, so the same blind spot.
        raise ApiError(  # pylint: disable=bad-exception-cause
            str(exc), _status_for(exc, _STATUS)
        ) from exc
    finally:
        release(agent)

    return _run_payload(
        request,
        ranked,
        _Reported(config.top_n, sort_by, config.weights, config.currency or None),
        removals=removals,
        changes=journal.against([entry.product for entry in ranked]),
        compared_with=journal.compared_with(),
    )


def rank_again(data: Mapping[str, Any]) -> dict[str, Any]:
    """Re-sort a finished run's products without running it again (ADR-0035)."""
    defaults = AgentConfig()
    request = _read(data, "request", "", _as_text)
    sort_by = _read(data, "sort_by", "score", _among(SORT_OPTIONS))
    top_n = _read(data, "top", defaults.top_n, _bounded(int, "top_n"))
    weights = RankingWeights()
    # The run's own scale, so the set does not vote again (ADR-0056).
    currency = _read(data, "currency", "", parse_currency)
    scale = currency or _read(data, "scale", "", _as_code)
    ranked = rank_products(
        _read_products(data), weights=weights, sort_by=cast(SortBy, sort_by),
        currency=scale or None,
    )
    # No pipeline ran, so no removals or changes of its own.
    return _run_payload(request, ranked, _Reported(top_n, sort_by, weights, scale or None))


def mandate_support() -> bool:
    """Whether the optional AP2 SDK is installed."""
    # Deferred: the SDK is optional.
    # pylint: disable-next=import-outside-toplevel
    from buy_agent import mandates

    return mandates.available()


def pay_now(data: Mapping[str, Any]) -> dict[str, Any]:
    """Buy one product of a finished run, given proof of approval (ADR-0046)."""
    # Always paying, so a missing endpoint is a 400 on its box rather than a 502 later.
    config, _sort_by = parse_options({**data, "pay": True})
    products = _read_products(data)
    if not products:
        raise ApiError("There are no products to pay for.", field="products")
    product = products[_rank(data, len(products))]
    scale = _read(data, "scale", "", _as_code)
    try:
        cart = cart_for(product, products, config, scale=scale or None)
        if not unattended():
            _witnessed(data, cart)
        receipt = pay_for(cart, config)
    except PaymentError as exc:
        raise ApiError(str(exc), _status_for(exc, PAY_STATUS), field=exc.field) from exc
    return {"receipt": receipt_payload(receipt)}


def receipt_payload(receipt: Receipt) -> dict[str, Any]:
    return receipt.model_dump()


def _rank(data: Mapping[str, Any], count: int) -> int:
    return _read(data, "rank", 1, _as_number(int, 1, count)) - 1


def _witnessed(data: Mapping[str, Any], cart: Cart) -> None:
    """Refuse unless the request echoes the cart the server just built."""
    approved = data.get("approved")
    if not isinstance(approved, Mapping):
        raise ApiError(
            "Paying needs the approval the page was given: send back the title, "
            "price and currency that were shown.",
            field="approved",
        )
    shown = (
        str(approved.get("title", "")).strip(),
        str(approved.get("currency", "")).strip().upper(),
    )
    if shown != (cart.title, cart.currency) or not _same_price(approved, cart.price):
        raise ApiError(
            f"What was approved is not what this would buy: the cart is "
            f"{cart.title} at {cart.label()}. Nothing was paid.",
            status=409,
            field="approved",
        )


def _same_price(approved: Mapping[str, Any], price: float) -> bool:
    try:
        return abs(float(approved.get("price", "nan")) - price) < 0.005
    except (TypeError, ValueError):
        return False


def _run_payload(
    request: str,
    ranked: Sequence[RankedProduct],
    reported: _Reported,
    *,
    removals: Sequence[Removal] = (),
    changes: Sequence[Change] = (),
    compared_with: str | None = None,
) -> dict[str, Any]:
    """A finished run or re-sort (ADR-0055, ADR-0060)."""
    scale = _counted_in(ranked, reported.currency)
    return {
        "request": request.strip(),
        "count": len(ranked),
        "top_n": reported.top_n,
        "sort_by": reported.sort_by,
        "weights": reported.weights.fractions,
        "products": results_payload(ranked, scale),
        # Handed back by a re-sort or a payment, so the set is not voted on again.
        "scale": scale,
        "dropped": [removal.model_dump() for removal in removals],
        "changes": [change.model_dump() for change in changes],
        "compared_with": compared_with,
    }


def results_payload(
    ranked: Sequence[RankedProduct], named: str | None = None
) -> list[dict[str, Any]]:
    """A whole run's products as JSON, best first (ADR-0043, ADR-0056)."""
    currency = _counted_in(ranked, named)
    return [product_payload(entry, currency) for entry in ranked]


def _counted_in(ranked: Sequence[RankedProduct], named: str | None) -> str | None:
    """The one named, else the one it was ranked in -- never a second vote in rank
    order, which can break a tie the other way (ADR-0056)."""
    return dominant_currency((entry.product for entry in ranked), named or scale_of(ranked))


def offer_payload(offer: Offer) -> dict[str, Any]:
    return {**offer.model_dump(), "price_label": amount_label(offer.price, offer.currency)}


def product_payload(entry: RankedProduct, currency: str | None = None) -> dict[str, Any]:
    """One ranked product, with Python's labels (ADR-0012, ADR-0041, ADR-0058)."""
    terms, cannot_pay = terms_for(entry.product, currency)
    return {
        "cannot_pay": cannot_pay,
        "pay_currency": terms[1] if terms else None,
        "pay_label": amount_label(*terms) if terms else None,
        "pay_merchant": merchant_for(entry.product) if terms else None,
        "rank": entry.rank,
        "score": round(entry.score, 4),
        "breakdown": entry.breakdown.model_dump(),
        **entry.product.model_dump(),
        "offers": [offer_payload(offer) for offer in entry.product.offers],
        "price_label": entry.product.price_label(),
        "rating_label": entry.product.rating_label(),
        "offers_label": entry.product.offers_label(),
    }


def model_payload(model: InstalledModel) -> dict[str, Any]:
    return {"name": model.name, "completion": model.completion}


def defaults_payload(*, screenshots: bool = False) -> dict[str, Any]:
    """The form's defaults, the pickers' rows and each number's range (ADR-0033);
    ``screenshots`` says whether this server has a camera (ADR-0065)."""
    defaults = AgentConfig()
    return {
        **{option.key: getattr(defaults, option.field) for option in OPTIONS},
        "sources": format_sources(defaults.sources),
        "provider_options": provider_options(),
        "backend_options": backend_options(),
        "rail_options": rail_options(),
        "currency_options": list(CURRENCY_OPTIONS),
        "pay_available": mandate_support(),
        "screenshots": screenshots,
        "sort_by": "score",
        "sort_options": list(SORT_OPTIONS),
        # The report's own words: "price" alone cannot say cheapest from dearest.
        "sort_labels": {name: phrase.capitalize() for name, phrase in ORDERINGS.items()},
        "limits": limits_payload(),
    }


def limits_payload() -> dict[str, dict[str, int]]:
    """Each number's range, by request key (ADR-0033)."""
    return {
        option.key: dict(zip(("min", "max"), LIMITS[option.field], strict=True))
        for option in OPTIONS
        if option.field in LIMITS
    }


def bounds_payload(request: str) -> dict[str, Any]:
    """Bounds the request states in words, offered to the form and never applied
    (ADR-0059); a figure outside its setting's range is dropped."""
    return {
        "request": request,
        "noticed": [
            {"bound": seen.bound, "value": seen.value, "note": seen.note}
            for seen in notice(request)
            if takeable(seen)
        ],
    }


def takeable(seen: Noticed) -> bool:
    """Whether the figure is within its setting's range; asked by both doors."""
    minimum, maximum = LIMITS[seen.bound]
    return minimum <= seen.value <= maximum


def sources_payload(spec: str) -> dict[str, Any]:
    """Whether a Trusted-sources field names sources, and what is wrong if not."""
    error = ""
    try:
        parse_sources(spec)
    except ValueError as exc:
        error = str(exc)
    return {"sources": spec, "error": error}


def screenshot(url: str, camera: Camera | None) -> bytes:
    """A JPEG of the page at ``url`` (ADR-0065); 502 if it cannot be taken."""
    if camera is None:
        raise ApiError("This server takes no screenshots.", 404)
    if not _web_page(url):
        raise ApiError(f"Not a web page to photograph: {url!r}")
    try:
        return camera.shoot(url)
    except ScreenshotError as exc:
        raise ApiError(str(exc), 502) from exc


def _web_page(url: str) -> bool:
    """Not a ``file:`` URL, which would show this machine's disk, nor an unparseable one."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.hostname)


def installed_models(
    provider: str, base_url: str, *, unaskable: Callable[[str], str] | None = None
) -> dict[str, Any]:
    """What a model server is serving, for the model picker (ADR-0032). ``unaskable``,
    given the server's label, says why it is not asked."""
    label = PROVIDERS[provider].label if provider in PROVIDERS else provider
    status = {"provider": provider, "label": label, "base_url": base_url}
    if unaskable is not None:
        return {**status, "reachable": False, "models": [], "hint": unaskable(label)}
    config: AgentConfig | None = None
    try:
        config = AgentConfig(provider=provider, base_url=base_url)
        models = config.model_server.installed(config)
    # Any failure is reported as "not reachable", not raised.
    # pylint: disable-next=broad-exception-caught
    except Exception as exc:
        logger.debug("Could not list %s models at %s", label, base_url, exc_info=True)
        failed = {**status, "reachable": False, "models": [], "detail": str(exc)}
        # No config means an unknown provider, whose refusal is its own remedy.
        if config is not None:
            failed["hint"] = config.model_server.hint(config, exc)
        return failed
    return {**status, "reachable": True, "models": [model_payload(model) for model in models]}


def _read_sources(
    data: Mapping[str, Any], default: tuple[Source, ...]
) -> tuple[Source, ...]:
    if not _present(data, "sources"):
        return default
    value = data["sources"]
    specs = (
        [str(entry) for entry in value] if isinstance(value, (list, tuple)) else str(value)
    )
    try:
        return parse_sources(specs)
    except ValueError as exc:
        raise ApiError(str(exc), field="sources") from exc


def _present(data: Mapping[str, Any], key: str) -> bool:
    """Whether the key is set; an empty field is unset (ADR-0012)."""
    value = data.get(key)
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple)):
        return any(str(entry).strip() for entry in value)
    return True


def _read(data: Mapping[str, Any], key: str, default: _T, parse: Callable[[str], _T]) -> _T:
    """``key``'s value parsed, or ``default`` if unset; a refusal marks ``key``'s box."""
    if not _present(data, key):
        return default
    try:
        return parse(str(data[key]).strip())
    except _Unnamed as exc:
        raise ApiError(f"{key} {exc}.", field=key) from exc
    except ValueError as exc:
        raise ApiError(str(exc), field=key) from exc


def _read_products(data: Mapping[str, Any]) -> list[Product]:
    value = data.get("products")
    if not isinstance(value, list):
        raise ApiError(
            "products must be the list of products a run answered with.", field="products"
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


def _as_text(text: str) -> str:
    return text


def _as_code(text: str) -> str:
    """A currency folded as a page's is (ADR-0054), placeable or not."""
    return code_for(text) or ""


class _Unnamed(ValueError):
    """A refusal worded to follow the setting's name -- "must be a number; got 'x'" --
    which each door names its own way (ADR-0033)."""


def _among(options: tuple[str, ...]) -> Callable[[str], str]:
    def parse(text: str) -> str:
        if text not in options:
            raise _Unnamed(f"must be one of {', '.join(options)}; got {text!r}")
        return text

    return parse


def _as_bool(text: str) -> bool:
    lowered = text.lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise _Unnamed(f"must be true or false; got {text!r}")


def number_kind(kind: Callable[[str], object]) -> str:
    """What a number setting holds, in the words both doors refuse anything else with."""
    return "a whole number" if kind is int else "a number"


def _as_number(
    kind: Callable[[str], _Number],
    # Not ``_Number``: the int bounds of a float setting would force ints.
    minimum: float,
    maximum: float,
) -> Callable[[str], _Number]:
    def parse(text: str) -> _Number:
        try:
            number = kind(text)
        except ValueError as exc:
            raise _Unnamed(f"must be {number_kind(kind)}; got {text!r}") from exc
        if not minimum <= number <= maximum:
            raise _Unnamed(f"must be between {minimum} and {maximum}; got {number}")
        return number

    return parse


def _bounded(kind: Callable[[str], _Number], field: str) -> Callable[[str], _Number]:
    return _as_number(kind, *LIMITS[field])


# -- the settings a request carries --------------------------------------------


@dataclass(frozen=True, slots=True)
class Option:
    """One setting, read by both doors (ADR-0012, ADR-0033, ADR-0071): its request
    key, a parser that raises ``ValueError`` saying why not, and the
    :class:`AgentConfig` field it fills when that is not the key."""

    key: str
    parse: Callable[[str], Any]
    field: str = ""
    #: Unset means blank, for settings resolved per provider or rail (ADR-0012).
    blank: bool = False
    #: The table rows it names, for the CLI's ``choices``.
    choices: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if not self.field:
            object.__setattr__(self, "field", self.key)

    @property
    def switch(self) -> bool:
        """``--x``/``--no-x`` on the command line."""
        return self.parse is _as_bool

    def unset(self, defaults: AgentConfig) -> Any:
        return "" if self.blank else getattr(defaults, self.field)


def _number(key: str, kind: Callable[[str], Any], field: str = "") -> Option:
    return Option(key, _bounded(kind, field or key), field)


def _row(key: str, options: tuple[str, ...]) -> Option:
    return Option(key, _among(options), choices=options)


#: Every setting both doors fill in, in ``--help``'s order; ``sources`` is apart.
OPTIONS: tuple[Option, ...] = (
    _row("provider", PROVIDER_OPTIONS),
    Option("model", _as_text, blank=True),
    Option("base_url", _as_text, blank=True),
    _number("results", int, "num_products"),
    _number("top", int, "top_n"),
    Option("region", parse_region),
    _row("backend", BACKEND_OPTIONS),
    # Blank means "whatever the pages quote" (ADR-0056).
    Option("currency", parse_currency),
    # Blank is "no bound" (ADR-0039).
    _number("max_price", float),
    _number("min_rating", float),
    _number("min_reviews", int),
    _number("cache_ttl", float),
    Option("journal", _as_bool),
    Option("pay", _as_bool),
    _row("rail", RAIL_OPTIONS),
    Option("merchant_url", _as_text, blank=True),
    _number("spend_limit", float),
    _number("temperature", float),
    _number("num_ctx", int),
    _number("model_timeout", float),
    Option("think", _as_bool, "reasoning"),
    Option("cpu_only", _as_bool),
    Option("fetch", _as_bool, "fetch_pages"),
)
