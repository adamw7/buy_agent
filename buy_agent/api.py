"""Turning a web request into an :class:`~buy_agent.agent.BuyAgent` run."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
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
from buy_agent.money import CODES, amount_label
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
from buy_agent.ranking import ORDERINGS, RankingWeights, SortBy, rank_products
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

#: Constrained, so a number parser answers its own kind and the bounds check type-checks.
_Number = TypeVar("_Number", int, float)

#: Builds the agent :func:`run_search` uses; a test seam.
AgentFactory = Callable[[AgentConfig], BuyAgent]

#: The criteria a request may ask for, read off :data:`~buy_agent.ranking.SortBy`; values
#: checked against it are cast back to ``SortBy``.
SORT_OPTIONS: tuple[str, ...] = get_args(SortBy)

#: The model servers a request may name, off the registry.
PROVIDER_OPTIONS: tuple[str, ...] = tuple(PROVIDERS)

#: The rails a request may name, off the registry.
RAIL_OPTIONS: tuple[str, ...] = tuple(RAILS)

#: The search backends a request may name, off the registry (ADR-0057).
BACKEND_OPTIONS: tuple[str, ...] = tuple(BACKENDS)

#: Every currency a run may count in, sorted for the picker (ADR-0056).
CURRENCY_OPTIONS: tuple[str, ...] = tuple(sorted(CODES))

_TRUE = frozenset({"true", "1", "yes", "on"})
_FALSE = frozenset({"false", "0", "no", "off"})

#: Which HTTP status each of the agent's three failure modes deserves.
_STATUS: dict[type[Exception], int] = {
    ValueError: 400,
    ModelUnavailableError: 503,
    SearchError: 502,
}


#: Which HTTP status each payment failure deserves.
PAY_STATUS: dict[type[Exception], int] = {
    RailUnreachableError: 502,
    PaymentError: 400,
}


@dataclass(frozen=True, slots=True)
class _Reported:
    """How a finished run is reported: sort, highlight count, weights and currency
    (ADR-0035)."""

    top_n: int
    sort_by: str
    weights: RankingWeights
    currency: str | None = None


class ApiError(Exception):
    """A failure with the HTTP status the client should be told about (ADR-0033)."""

    def __init__(self, message: str, status: int = 400, field: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.field = field

    def payload(self) -> dict[str, Any]:
        return {"error": str(self), "field": self.field}


def _status_for(exc: Exception, table: Mapping[type[Exception], int]) -> int:
    """The status of ``table``'s first row matching this failure."""
    return next(status for kind, status in table.items() if isinstance(exc, kind))


def parse_options(data: Mapping[str, Any]) -> tuple[AgentConfig, SortBy]:
    """Read an :class:`AgentConfig` and a sort criterion out of request data."""
    defaults = AgentConfig()
    settings: dict[str, Any] = {
        option.field: _read(data, option.key, option.unset(defaults), option.parse)
        for option in OPTIONS
    }
    # The one list option, which ``_read`` cannot take.
    settings["sources"] = _read_sources(data, defaults.sources)
    # Searching fewer pages than we report would cap the report -- as in the CLI.
    settings["search_results"] = max(settings["num_products"], settings["top_n"])
    sort_by = _read(data, "sort_by", "score", _among(SORT_OPTIONS))
    return _configured(**settings), cast(SortBy, sort_by)


def _configured(**settings: Any) -> AgentConfig:
    """An :class:`AgentConfig`; its refusal becomes an :class:`ApiError`, not a 500
    (ADR-0033)."""
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
    # What the run removed, in order (ADR-0055).
    removals: list[Removal] = []
    # Opened before the run, so it holds the previous one (ADR-0060).
    journal = journal_for(request, config)
    try:
        agent = agent_factory(config)
        ranked = agent.run(
            request, sort_by=sort_by, checkpoint=checkpoint, record=removals.append
        )
    # Built from ``_STATUS`` at run time, which pylint cannot read exceptions out of.
    # pylint: disable=catching-non-exception, bad-exception-cause
    except tuple(_STATUS) as exc:
        raise ApiError(str(exc), _status_for(exc, _STATUS)) from exc
    finally:
        # One agent per request, released here.
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
    top_n = _read(data, "top", defaults.top_n, _bounded(int))
    # Explicit, so the answer reports the weights it ranked by.
    weights = RankingWeights()
    # The run's own scale, so the set does not vote again (ADR-0056).
    currency = _read(data, "currency", "", _checked(parse_currency))
    ranked = rank_products(
        _read_products(data),
        weights=weights,
        sort_by=cast(SortBy, sort_by),
        currency=currency or None,
    )
    # No pipeline ran, so no removals or changes; the page keeps the run's own
    # (ADR-0035, ADR-0055, ADR-0060).
    return _run_payload(request, ranked, _Reported(top_n, sort_by, weights, currency or None))


def mandate_support() -> bool:
    """Whether the optional AP2 SDK is installed."""
    # Deferred: the SDK is optional.
    # pylint: disable-next=import-outside-toplevel
    from buy_agent import mandates

    return mandates.available()


def pay_now(data: Mapping[str, Any]) -> dict[str, Any]:
    """Buy one product of a finished run, given proof of approval (ADR-0035, ADR-0012,
    ADR-0046)."""
    # Always a paying run, so ``AgentConfig`` refuses a missing endpoint as a 400 on its
    # box rather than a 502 later (ADR-0033).
    config, _sort_by = parse_options({**data, "pay": True})
    products = _read_products(data)
    if not products:
        raise ApiError("There are no products to pay for.", field="products")
    product = products[_rank(data, len(products))]

    try:
        cart = cart_for(product, products, config)
        if not unattended():
            _witnessed(data, cart)
        receipt = pay_for(cart, config)
    except PaymentError as exc:
        raise ApiError(str(exc), _status_for(exc, PAY_STATUS), field=exc.field) from exc
    return {"receipt": receipt_payload(receipt)}


def receipt_payload(receipt: Receipt) -> dict[str, Any]:
    """What came of a payment, as JSON (ADR-0046)."""
    return receipt.model_dump()


def _rank(data: Mapping[str, Any], count: int) -> int:
    """The index of the product to buy."""
    return _read(data, "rank", 1, partial(_as_number, int, 1, count)) - 1


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
    """Whether the echoed price is the cart's, to a hundredth."""
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
    """The payload of a finished run or re-sort (ADR-0055, ADR-0060)."""
    return {
        "request": request.strip(),
        "count": len(ranked),
        "top_n": reported.top_n,
        "sort_by": reported.sort_by,
        "weights": reported.weights.fractions,
        "products": results_payload(ranked, reported.currency),
        # Removed candidates, each with Python's reason (ADR-0055).
        "dropped": [removal.model_dump() for removal in removals],
        # What moved since the last run of this search (ADR-0060).
        "changes": [change.model_dump() for change in changes],
        # The day compared against; null on a first run or with the journal off.
        "compared_with": compared_with,
    }


def results_payload(
    ranked: Sequence[RankedProduct], named: str | None = None
) -> list[dict[str, Any]]:
    """A whole run's products as JSON, best first (ADR-0043, ADR-0056)."""
    currency = dominant_currency((entry.product for entry in ranked), named)
    return [product_payload(entry, currency) for entry in ranked]


def offer_payload(offer: Offer) -> dict[str, Any]:
    """One listing as JSON, with Python's ``price_label`` (ADR-0058, ADR-0012)."""
    return {**offer.model_dump(), "price_label": amount_label(offer.price, offer.currency)}


def product_payload(entry: RankedProduct, currency: str | None = None) -> dict[str, Any]:
    """One ranked product as JSON (ADR-0012, ADR-0033, ADR-0043, ADR-0058)."""
    terms, cannot_pay = terms_for(entry.product, currency)
    return {
        "cannot_pay": cannot_pay,
        "pay_currency": terms[1] if terms else None,
        "pay_label": amount_label(*terms) if terms else None,
        "pay_merchant": merchant_for(entry.product) if terms else None,
        "rank": entry.rank,
        "score": round(entry.score, 4),
        # The score's parts, and which were assumed (ADR-0041).
        "breakdown": entry.breakdown.model_dump(),
        **entry.product.model_dump(),
        # Overrides the dump's offers with labelled ones (ADR-0058).
        "offers": [offer_payload(offer) for offer in entry.product.offers],
        "price_label": entry.product.price_label(),
        "rating_label": entry.product.rating_label(),
        # "3 listings, 129.00-149.00 USD", or null for fewer than two.
        "offers_label": entry.product.offers_label(),
    }


def model_payload(model: InstalledModel) -> dict[str, Any]:
    """One model a server is holding, as the picker needs it (ADR-0032)."""
    return {"name": model.name, "completion": model.completion}


def defaults_payload(*, screenshots: bool = False) -> dict[str, Any]:
    """The form's defaults (the ones ``--help`` shows), plus the pickers' rows and each
    number's range (ADR-0033). ``screenshots`` says whether this server has a camera
    (ADR-0065)."""
    defaults = AgentConfig()
    return {
        **{option.key: getattr(defaults, option.field) for option in OPTIONS},
        # As the form's one text field.
        "sources": format_sources(defaults.sources),
        # Each with its model and address, so the form can fill them in.
        "provider_options": provider_options(),
        "backend_options": backend_options(),
        "rail_options": rail_options(),
        # Blank, the default, means the pages' vote (ADR-0043).
        "currency_options": list(CURRENCY_OPTIONS),
        "pay_available": mandate_support(),
        # Not a setting: whether this server has a camera.
        "screenshots": screenshots,
        "sort_by": "score",
        "sort_options": list(SORT_OPTIONS),
        # Each criterion as the order it puts a run in, the report's own words: a
        # picker reading "price" cannot say cheapest from dearest.
        "sort_labels": {name: phrase.capitalize() for name, phrase in ORDERINGS.items()},
        "limits": limits_payload(),
    }


def limits_payload() -> dict[str, dict[str, int]]:
    """Each number's range, by request key (ADR-0033)."""
    return {
        key: dict(zip(("min", "max"), LIMITS[field], strict=True))
        for key, field in _BOUNDED.items()
    }


def bounds_payload(request: str) -> dict[str, Any]:
    """Bounds the request states in words, offered for the form to fill in (ADR-0059).

    Never applied. A figure outside its setting's range is dropped (ADR-0033).
    """
    return {
        "request": request,
        "noticed": [
            {"bound": seen.bound, "value": seen.value, "note": seen.note}
            for seen in notice(request)
            if takeable(seen)
        ],
    }


def takeable(seen: Noticed) -> bool:
    """Whether the figure is within its setting's range; asked by both doors (ADR-0033)."""
    minimum, maximum = LIMITS[seen.bound]
    return minimum <= seen.value <= maximum


def sources_payload(spec: str) -> dict[str, Any]:
    """Whether a Trusted-sources field names sources, and what is wrong if not (ADR-0033)."""
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
    parsed = urlparse(url)
    # Web pages only: a ``file:`` URL would show this machine's disk.
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ApiError(f"Not a web page to photograph: {url!r}")
    try:
        return camera.shoot(url)
    except ScreenshotError as exc:
        raise ApiError(str(exc), 502) from exc


def installed_models(provider: str, base_url: str) -> dict[str, Any]:
    """Ask a model server what it is serving, for the UI's model picker (ADR-0032,
    ADR-0012)."""
    label = PROVIDERS[provider].label if provider in PROVIDERS else provider
    status = {"provider": provider, "label": label, "base_url": base_url}
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
    return {
        **status,
        "reachable": True,
        "models": [model_payload(model) for model in models],
    }


def _read_sources(
    data: Mapping[str, Any], default: tuple[Source, ...]
) -> tuple[Source, ...]:
    """The sources the request named, if any (ADR-0033)."""
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


def _read(
    data: Mapping[str, Any],
    key: str,
    default: _T,
    parse: Callable[[str, str], _T],
) -> _T:
    """``key``'s value parsed, or ``default`` if unset."""
    if not _present(data, key):
        return default
    return parse(key, str(data[key]).strip())


def _read_products(data: Mapping[str, Any]) -> list[Product]:
    """The products of a finished run, off the request."""
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
    """Text as is (already stripped)."""
    return text


def _among(options: tuple[str, ...]) -> Callable[[str, str], str]:
    """A parser for a setting naming a table row (ADR-0033)."""

    def parse(key: str, text: str) -> str:
        if text not in options:
            raise ApiError(
                f"{key} must be one of {', '.join(options)}; got {text!r}.", field=key
            )
        return text

    return parse


def _checked(check: Callable[[str], str]) -> Callable[[str, str], str]:
    """A parser wrapping a :mod:`buy_agent.config` check, adding the box to mark
    (ADR-0031, ADR-0056, ADR-0033)."""

    def parse(key: str, text: str) -> str:
        try:
            return check(text)
        except ValueError as exc:
            raise ApiError(str(exc), field=key) from exc

    return parse


def _as_bool(key: str, text: str) -> bool:
    """A checkbox, query parameter or JSON boolean."""
    lowered = text.lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ApiError(f"{key} must be true or false; got {text!r}.", field=key)


def _bounded(kind: Callable[[str], _Number]) -> Callable[[str, str], _Number]:
    """A parser for a number within its key's ``LIMITS`` range."""

    def parse(key: str, text: str) -> _Number:
        minimum, maximum = LIMITS[_BOUNDED[key]]
        return _as_number(kind, minimum, maximum, key, text)

    return parse


def _as_number(
    kind: Callable[[str], _Number],
    # Not ``_Number``: the int bounds of a float setting would force ints.
    minimum: float,
    maximum: float,
    key: str,
    text: str,
) -> _Number:
    """One number parser for both kinds: convert, then check the bounds."""
    try:
        number = kind(text)
    except ValueError as exc:
        described = "a whole number" if kind is int else "a number"
        raise ApiError(f"{key} must be {described}; got {text!r}.", field=key) from exc
    if not minimum <= number <= maximum:
        raise ApiError(
            f"{key} must be between {minimum} and {maximum}; got {number}.", field=key
        )
    return number


# -- the settings a request carries --------------------------------------------


@dataclass(frozen=True, slots=True)
class _Option:
    """One setting: its request key, the :class:`AgentConfig` field it fills, and its
    parser (ADR-0012, ADR-0033). Read by both doors, the form's seed and the ranges."""

    key: str
    field: str
    parse: Callable[[str, str], Any]
    #: Unset means blank, for settings resolved per provider or rail (ADR-0012).
    blank: bool = False

    def unset(self, defaults: AgentConfig) -> Any:
        """What this key means when a request does not carry it."""
        return "" if self.blank else getattr(defaults, self.field)


#: Every setting both doors fill in. ``sources``, a list, is handled apart (ADR-0027).
OPTIONS: tuple[_Option, ...] = (
    _Option("provider", "provider", _among(PROVIDER_OPTIONS)),
    _Option("model", "model", _as_text, blank=True),
    _Option("base_url", "base_url", _as_text, blank=True),
    _Option("temperature", "temperature", _bounded(float)),
    _Option("num_ctx", "num_ctx", _bounded(int)),
    _Option("model_timeout", "model_timeout", _bounded(float)),
    _Option("think", "reasoning", _as_bool),
    _Option("cpu_only", "cpu_only", _as_bool),
    _Option("results", "num_products", _bounded(int)),
    _Option("top", "top_n", _bounded(int)),
    # Blank is "no bound" (ADR-0012, ADR-0039).
    _Option("max_price", "max_price", _bounded(float)),
    _Option("min_rating", "min_rating", _bounded(float)),
    _Option("min_reviews", "min_reviews", _bounded(int)),
    _Option("cache_ttl", "cache_ttl", _bounded(float)),
    # ADR-0060.
    _Option("journal", "journal", _as_bool),
    _Option("region", "region", _checked(parse_region)),
    # Blank is the default and means "whatever the pages quote" (ADR-0056).
    _Option("currency", "currency", _checked(parse_currency)),
    _Option("backend", "backend", _among(BACKEND_OPTIONS)),
    _Option("fetch", "fetch_pages", _as_bool),
    # Off unless asked for; the default rail charges nobody.
    _Option("pay", "pay", _as_bool),
    _Option("rail", "rail", _among(RAIL_OPTIONS)),
    _Option("merchant_url", "merchant_url", _as_text, blank=True),
    _Option("spend_limit", "spend_limit", _bounded(float)),
)

#: The ``LIMITS`` field bounding each numeric request key (ADR-0033).

_BOUNDED: dict[str, str] = {
    option.key: option.field for option in OPTIONS if option.field in LIMITS
}
