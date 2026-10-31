"""Turning a web request into an :class:`~buy_agent.agent.BuyAgent` run."""

from __future__ import annotations

import dataclasses
import hmac
import json
import logging
import secrets
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar, cast, get_args
from urllib.parse import urlparse

from pydantic import ValidationError

from buy_agent.agent import (
    BuyAgent, Checkpoint, ModelUnavailableError, every_step_passes, journal_for,
)
from buy_agent.alerts import price_alert
from buy_agent.bounds import Noticed, notice
from buy_agent.chat import release
from buy_agent.config import SWITCHED_OFF, SWITCHED_ON, AgentConfig, parse_currency, parse_region
from buy_agent.models import Offer, Product, Removal, dominant_currency
from buy_agent.money import CODES, amount_label, code_for
from buy_agent.payment import (
    Cart, PaymentError, RailUnreachableError, cart_for, merchant_for, pay_for, terms_for,
    unattended,
)
from buy_agent.providers import PROVIDERS, provider_options
from buy_agent.rails import RAILS, rail_options
from buy_agent.ranking import (
    ORDERINGS, RankingWeights, SortBy, placement_note, rank_products, scale_of,
)
from buy_agent.screenshots import ScreenshotError
from buy_agent.search import BACKENDS, SearchError, backend_options
from buy_agent.sources import Source, format_sources, parse_sources
from buy_agent.version import current

if TYPE_CHECKING:
    from collections.abc import Sequence

    from buy_agent.alerts import Alert
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

#: The HTTP status of each of the agent's three failure modes (ADR-0009).
_STATUS: dict[type[Exception], int] = {
    ValueError: 400,
    ModelUnavailableError: 503,
    SearchError: 502,
}

PAY_STATUS: dict[type[Exception], int] = {RailUnreachableError: 502, PaymentError: 400}


@dataclass(frozen=True, slots=True)
class _Reported:
    """How a finished run is reported (ADR-0035)."""

    top_n: int
    sort_by: str
    weights: RankingWeights
    currency: str | None = None
    #: What a re-sort or a payment over HTTP hands back (ADR-0092).
    seal: str | None = None


class ApiError(Exception):
    """A failure with the HTTP status, and the box, it is reported with (ADR-0033)."""

    def __init__(self, message: str, status: int = 400, field: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.field = field

    def payload(self) -> dict[str, Any]:
        return {"error": str(self), "field": self.field}


#: What a seal is keyed by beside a token, so that replicas sharing one agree on it.
_SEAL_CONTEXT = b"buy_agent: the products a run answered with"

_UNSEALED = (
    "These are not the products a search on this server answered with -- it may have "
    "restarted since -- so nothing was re-sorted or paid for. Search again first."
)


@dataclass(frozen=True, slots=True)
class Sealer:
    """Signs the products a run answered with, so a re-sort or a payment sent back over
    HTTP is taken for those products and no others (ADR-0092). They travel in the body
    (ADR-0035); the seal is what says this server is where they came from."""

    key: bytes = dataclasses.field(repr=False)

    @classmethod
    def keyed_by(cls, token: str = "") -> Sealer:
        """Off the server's token, which replicas share; else afresh for this process."""
        if not token:
            return cls(secrets.token_bytes(32))
        return cls(hmac.digest(token.encode("utf-8"), _SEAL_CONTEXT, "sha256"))

    def seal(self, products: Iterable[Product]) -> str:
        return hmac.new(self.key, _canonical(products), "sha256").hexdigest()

    def check(self, products: Sequence[Product], seal: str) -> None:
        """Refuse products this server did not answer with, in any order."""
        given = seal.encode("utf-8")
        if not hmac.compare_digest(self.seal(products).encode("utf-8"), given):
            raise ApiError(_UNSEALED, 409, field="products")


def _canonical(products: Iterable[Product]) -> bytes:
    """The set rather than the order, so a re-sort answers the seal it was handed."""
    entries = sorted(
        json.dumps(product.model_dump(mode="json"), sort_keys=True) for product in products
    )
    return json.dumps(entries).encode("utf-8")


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
    sort_by = _read(data, "sort_by", "score", _among(SORT_OPTIONS))
    try:
        return AgentConfig(**settings), cast(SortBy, sort_by)
    except ValueError as exc:
        raise ApiError(str(exc), field="merchant_url") from exc


@dataclass(frozen=True, slots=True)
class Finished:
    """A run that returned, and what both doors report beside its ranking: what it took
    out (ADR-0055), what moved since the last run of it (ADR-0060) and its price alert
    (ADR-0080)."""

    ranked: list[RankedProduct]
    removals: list[Removal]
    changes: list[Change]
    compared_with: str | None
    alert: Alert | None
    #: Why nothing came back, in the agent's own sentence; "" when something did.
    why_empty: str = ""


def run_agent(
    request: str, config: AgentConfig, *, sort_by: SortBy = "score",
    agent_factory: AgentFactory = BuyAgent, checkpoint: Checkpoint = every_step_passes,
) -> Finished:
    """One run, as both doors make it: one agent built and let go of, between opening
    the search's journal and asking it what moved. Raises what :meth:`BuyAgent.run`
    raises, for each door to report in its own way (ADR-0009)."""
    removals: list[Removal] = []
    # Opened before the run, so it holds the previous one (ADR-0060).
    journal = journal_for(request, config)
    agent = None
    try:
        agent = agent_factory(config)
        ranked = agent.run(request, sort_by=sort_by, checkpoint=checkpoint, record=removals.append)
    finally:
        release(agent)
    # Always recorded, so the next run has something to compare with.
    changes = journal.against([entry.product for entry in ranked])
    # A stand-in agent may not say; the browser then falls back to its own sentence.
    why_empty = "" if ranked else str(getattr(agent, "nothing", ""))
    return Finished(
        ranked, removals, changes, journal.compared_with(), alert_for(ranked, config), why_empty,
    )


def run_search(
    request: str, config: AgentConfig, *, sort_by: SortBy = "score",
    agent_factory: AgentFactory = BuyAgent, checkpoint: Checkpoint = every_step_passes,
    sealer: Sealer | None = None,
) -> dict[str, Any]:
    """Run the pipeline and shape the answer as JSON-ready data (ADR-0034); ``sealer``
    signs its products for a re-sort or a payment over HTTP (ADR-0092)."""
    try:
        finished = run_agent(
            request, config, sort_by=sort_by, agent_factory=agent_factory, checkpoint=checkpoint,
        )
    # Built from ``_STATUS``, which pylint cannot read exceptions out of.
    except tuple(_STATUS) as exc:  # pylint: disable=catching-non-exception
        # The same tuple, so the same blind spot.
        raise ApiError(  # pylint: disable=bad-exception-cause
            str(exc), _status_for(exc, _STATUS)
        ) from exc

    seal = sealer.seal(entry.product for entry in finished.ranked) if sealer else None
    reported = _Reported(config.top_n, sort_by, config.weights, config.currency or None, seal)
    return _run_payload(request, finished, reported)


def alert_for(ranked: Sequence[RankedProduct], config: AgentConfig) -> Alert | None:
    """The run's price alert, counted in the run's own currency; ``None`` unset
    (ADR-0080). Asked by both doors."""
    if config.alert_below is None:
        return None
    return price_alert(ranked, config.alert_below, _counted_in(ranked, config.currency or None))


def rank_again(data: Mapping[str, Any], *, sealer: Sealer | None = None) -> dict[str, Any]:
    """Re-sort a finished run's products without running it again (ADR-0035); with a
    ``sealer``, only products it sealed (ADR-0092)."""
    defaults = AgentConfig()
    request = _read(data, "request", "", str)
    sort_by = _read(data, "sort_by", "score", _among(SORT_OPTIONS))
    top_n = _read(data, "top", defaults.top_n, _as_number(int, *LIMITS["top_n"]))
    weights = RankingWeights()
    # The run's own scale, so the set does not vote again (ADR-0056).
    currency = _read(data, "currency", "", parse_currency)
    scale = currency or _read(data, "scale", "", _as_code)
    products = _read_products(data)
    if sealer:
        sealer.check(products, _read(data, "seal", "", str))
    ranked = rank_products(
        products, weights=weights, sort_by=cast(SortBy, sort_by), currency=scale or None
    )
    seal = sealer.seal(products) if sealer else None
    # No pipeline ran, so no removals, changes or alert of its own.
    finished = Finished(ranked, [], [], None, None)
    return _run_payload(request, finished, _Reported(top_n, sort_by, weights, scale or None, seal))


def mandate_support() -> bool:
    """Whether the optional AP2 SDK is installed."""
    # Deferred: the SDK is optional.
    # pylint: disable-next=import-outside-toplevel
    from buy_agent import mandates

    return mandates.available()


def pay_now(data: Mapping[str, Any], *, sealer: Sealer | None = None) -> dict[str, Any]:
    """Buy one product of a finished run, given proof of approval (ADR-0046); with a
    ``sealer``, only one of the products it sealed (ADR-0092)."""
    # Always paying, so a missing endpoint is a 400 on its box rather than a 502 later.
    config, _sort_by = parse_options({**data, "pay": True})
    products = _read_products(data)
    if not products:
        raise ApiError("There are no products to pay for.", field="products")
    if sealer:
        sealer.check(products, _read(data, "seal", "", str))
    product = products[_rank(data, len(products))]
    scale = _read(data, "scale", "", _as_code)
    try:
        cart = cart_for(product, products, config, scale=scale or None)
        if not unattended():
            _witnessed(data, cart)
        receipt = pay_for(cart, config)
    except PaymentError as exc:
        raise ApiError(str(exc), _status_for(exc, PAY_STATUS), field=exc.field) from exc
    return {"receipt": receipt.model_dump()}


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
        str(approved.get("title", "")).strip(), str(approved.get("currency", "")).strip().upper(),
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


def _run_payload(request: str, finished: Finished, reported: _Reported) -> dict[str, Any]:
    """A finished run or re-sort (ADR-0055, ADR-0060, ADR-0080, ADR-0092)."""
    scale = _counted_in(finished.ranked, reported.currency)
    return {
        "request": request.strip(),
        "count": len(finished.ranked),
        "top_n": reported.top_n,
        "sort_by": reported.sort_by,
        "weights": reported.weights.fractions,
        "products": results_payload(finished.ranked, scale),
        # Handed back by a re-sort or a payment, so the set is not voted on again.
        "scale": scale,
        "dropped": [removal.model_dump() for removal in finished.removals],
        "changes": [change.model_dump() for change in finished.changes],
        "compared_with": finished.compared_with,
        "alert": finished.alert.payload() if finished.alert else None,
        # Python's sentence for an empty run, so the page need not guess (ADR-0012).
        "why_empty": finished.why_empty or None,
        # Handed back by a re-sort or a payment over HTTP (ADR-0092).
        "seal": reported.seal,
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
        "listing_label": entry.product.listing_label(),
        # Why it sits below what may score less (ADR-0095, ADR-0097).
        "placement_note": placement_note(entry.product),
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
        # Which build answered, for a bug report to quote (ADR-0083).
        "version": current().label,
    }


def limits_payload() -> dict[str, dict[str, int]]:
    """Each number's range, by request key (ADR-0033)."""
    return {
        option.key: dict(zip(("min", "max"), option.limits, strict=True))
        for option in OPTIONS
        if option.limits
    }


def bounds_payload(request: str) -> dict[str, Any]:
    """Bounds the request states in words, for the form to fill in and show (ADR-0094);
    a figure outside its setting's range is dropped."""
    return {
        "request": request,
        "noticed": [
            {"bound": seen.bound, "value": seen.value, "phrase": seen.phrase, "note": seen.note}
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


def _read_sources(data: Mapping[str, Any], default: tuple[Source, ...]) -> tuple[Source, ...]:
    if not _present(data, "sources"):
        return default
    value = data["sources"]
    specs = [str(entry) for entry in value] if isinstance(value, (list, tuple)) else str(value)
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
    if lowered in SWITCHED_ON:
        return True
    if lowered in SWITCHED_OFF:
        return False
    raise _Unnamed(f"must be true or false; got {text!r}")


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
            held = "a whole number" if kind is int else "a number"
            raise _Unnamed(f"must be {held}; got {text!r}") from exc
        if not minimum <= number <= maximum:
            raise _Unnamed(f"must be between {minimum} and {maximum}; got {number}")
        return number

    return parse


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
    #: A number's range, in whole numbers, which the form binds its box to (ADR-0033).
    limits: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        if not self.field:
            object.__setattr__(self, "field", self.key)

    @property
    def switch(self) -> bool:
        """``--x``/``--no-x`` on the command line."""
        return self.parse is _as_bool

    def unset(self, defaults: AgentConfig) -> Any:
        return "" if self.blank else getattr(defaults, self.field)


def _number(
    key: str, kind: Callable[[str], Any], limits: tuple[int, int], field: str = ""
) -> Option:
    return Option(key, _as_number(kind, *limits), field, limits=limits)


def _row(key: str, options: tuple[str, ...]) -> Option:
    return Option(key, _among(options), choices=options)


#: Every setting both doors fill in, in ``--help``'s order; ``sources`` is apart.
OPTIONS: tuple[Option, ...] = (
    _row("provider", PROVIDER_OPTIONS),
    Option("model", str, blank=True),
    Option("base_url", str, blank=True),
    _number("results", int, (1, 50), "num_products"),
    _number("top", int, (1, 50), "top_n"),
    Option("region", parse_region),
    _row("backend", BACKEND_OPTIONS),
    # Blank means "whatever the pages quote" (ADR-0056).
    Option("currency", parse_currency),
    # Blank is "no bound" (ADR-0039).
    _number("max_price", float, (1, 10_000_000)),
    _number("min_rating", float, (0, 5)),
    _number("min_reviews", int, (0, 10_000_000)),
    # Told, never applied (ADR-0080).
    _number("alert_below", float, (1, 10_000_000)),
    # 0 is off; past 30 days a stored price is no evidence (ADR-0040).
    _number("cache_ttl", float, (0, 2_592_000)),
    Option("journal", _as_bool),
    Option("pay", _as_bool),
    _row("rail", RAIL_OPTIONS),
    Option("merchant_url", str, blank=True),
    _number("spend_limit", float, (1, 10_000_000)),
    _number("temperature", float, (0, 2)),
    _number("num_ctx", int, (1, 1_000_000)),
    _number("model_timeout", float, (1, 3600)),
    Option("think", _as_bool, "reasoning"),
    Option("cpu_only", _as_bool),
    Option("fetch", _as_bool, "fetch_pages"),
)

#: Each number's range, by the field it bounds, off its ``OPTIONS`` row.
LIMITS: dict[str, tuple[int, int]] = {
    option.field: option.limits for option in OPTIONS if option.limits
}
