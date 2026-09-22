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
from buy_agent.ranking import RankingWeights, SortBy, rank_products
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

#: Constrained, so ``minimum <= number <= maximum`` in :func:`_as_number` is a
#: comparison the type checker can see is valid for whichever kind it was given -- and so
#: that a parser reading a number answers that number's own kind rather than "a number".
_Number = TypeVar("_Number", int, float)

#: Builds the agent :func:`run_search` uses -- ``BuyAgent`` itself, unless a test hands
#: it a stub instead.
AgentFactory = Callable[[AgentConfig], BuyAgent]

#: The criteria a request may ask for: :data:`~buy_agent.ranking.SortBy` read back rather
#: than the same three names written down a second time. That is also why each door casts
#: what ``_among`` hands back -- a value checked against these is one of the three, which
#: is a fact the ``str`` a request arrives as cannot state and the cast can.
SORT_OPTIONS: tuple[str, ...] = get_args(SortBy)

#: The model servers a request may name, read off the registry rather than written down
#: again -- a provider added there is offered here on the same day.
PROVIDER_OPTIONS: tuple[str, ...] = tuple(PROVIDERS)

#: The rails a request may name, read off the registry for the reason the providers are.
RAIL_OPTIONS: tuple[str, ...] = tuple(RAILS)

#: The search backends a request may name, read off that registry for the same reason
#: (ADR-0057).
BACKEND_OPTIONS: tuple[str, ...] = tuple(BACKENDS)

#: Every currency a run may be told to count itself in, sorted so the picker is in an
#: order somebody can scan (ADR-0056). Off ``money``'s own table, never listed again.
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
    """How a finished run is being reported: what it was ranked by, how much of it is
    highlighted, and on which scale it is priced.

    One value rather than four arguments because both doors carry all four and neither
    carries one without the rest -- a run and a re-sort answer the same shape, and that
    is the shape (ADR-0035).
    """

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
    """The status ``table`` gives this failure, taking the first row it matches."""
    return next(status for kind, status in table.items() if isinstance(exc, kind))


def parse_options(data: Mapping[str, Any]) -> tuple[AgentConfig, SortBy]:
    """Read an :class:`AgentConfig` and a sort criterion out of request data."""
    defaults = AgentConfig()
    settings: dict[str, Any] = {
        option.field: _read(data, option.key, option.unset(defaults), option.parse)
        for option in OPTIONS
    }
    # The one option that is a list, and so the one that does not go through ``_read``.
    settings["sources"] = _read_sources(data, defaults.sources)
    # Searching fewer pages than we report would cap the report -- as in the CLI.
    settings["search_results"] = max(settings["num_products"], settings["top_n"])
    # Through ``_configured``, so the config's own refusal is answered like every other
    # unusable value rather than escaping as a 500 (ADR-0033).
    sort_by = _read(data, "sort_by", "score", _among(SORT_OPTIONS))
    return _configured(**settings), cast(SortBy, sort_by)


def _configured(**settings: Any) -> AgentConfig:
    """An :class:`AgentConfig`, with its own refusal answered like every other (ADR-0033)."""
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
    # What the run took out, in the order it took it: the answer says why a short report
    # is short, and nothing but this list is keeping it (ADR-0055).
    removals: list[Removal] = []
    # Opened before the run and asked after it, so what it hands back is the *last* run
    # of this search rather than this one (ADR-0060).
    journal = journal_for(request, config)
    try:
        agent = agent_factory(config)
        ranked = agent.run(
            request, sort_by=sort_by, checkpoint=checkpoint, record=removals.append
        )
    # The three failures are a table, so the clause catching them is built out of it
    # rather than written down again -- and a tuple assembled at run time is one pylint
    # cannot read exception classes out of, here or on the ``from`` beside it.
    # pylint: disable=catching-non-exception, bad-exception-cause
    except tuple(_STATUS) as exc:
        raise ApiError(str(exc), _status_for(exc, _STATUS)) from exc
    finally:
        # One request, one agent, its connection let go of here rather than whenever the
        # last reference falls.
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
    """Put a finished run's products in another order, without running it again (ADR-0035).
    """
    defaults = AgentConfig()
    request = _read(data, "request", "", _as_text)
    sort_by = _read(data, "sort_by", "score", _among(SORT_OPTIONS))
    top_n = _read(data, "top", defaults.top_n, _bounded(int))
    # Named rather than left to ``rank_products``'s own fallback, so the weights the
    # answer reports are the ones it ranked by: a re-sort takes no config.
    weights = RankingWeights()
    # The scale the run was counted on, sent back with its products: a re-sort that let
    # the set vote again would answer a different ordering for the same run (ADR-0056).
    currency = _read(data, "currency", "", _checked(parse_currency))
    ranked = rank_products(
        _read_products(data),
        weights=weights,
        sort_by=cast(SortBy, sort_by),
        currency=currency or None,
    )
    # A re-sort runs no pipeline, so it removed nothing and compared nothing: the page
    # keeps the lists the run itself reported rather than being handed empty ones
    # (ADR-0035, ADR-0055, ADR-0060).
    return _run_payload(request, ranked, _Reported(top_n, sort_by, weights, currency or None))


def mandate_support() -> bool:
    """Is the optional AP2 SDK installed? Imported here so nothing else asks."""
    # Deferred, for the reason mandates' own module docstring gives: the SDK is an
    # optional install and this is the question of whether it is installed.
    # pylint: disable-next=import-outside-toplevel
    from buy_agent import mandates

    return mandates.available()


def pay_now(data: Mapping[str, Any]) -> dict[str, Any]:
    """Buy one product of a finished run, having been shown that it was approved
    (ADR-0035, ADR-0012, ADR-0046)."""
    # A request to this endpoint is a paying run whatever the payload says, and the page
    # sends no ``pay`` here -- it is asking for a purchase, not describing one. Read off
    # the payload instead, ``pay`` came back ``False`` and took ``AgentConfig``'s refusal
    # of a paying rail with no address down with it: the endpoint the rail was about to
    # be asked for a price at was empty, and the failure arrived as a 502 naming no
    # address rather than as the 400 marking the box (ADR-0033).
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
    """Which product of the run to buy, as an index into the list that arrived."""
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
    """Is the echoed price the cart's, to the nearest hundredth of a unit?"""
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
    """The shape a finished run answers with, however it was finished (ADR-0055,
    ADR-0060)."""
    return {
        "request": request.strip(),
        "count": len(ranked),
        "top_n": reported.top_n,
        "sort_by": reported.sort_by,
        "weights": reported.weights.fractions,
        "products": results_payload(ranked, reported.currency),
        # Beside the products rather than among them: these are the candidates that are
        # not products of this run any more, each with Python's own sentence saying what
        # took it (ADR-0055).
        "dropped": [removal.model_dump() for removal in removals],
        # And what the last run of this same search said, where there was one: the
        # reason to run a search twice is that a price moved (ADR-0060).
        "changes": [change.model_dump() for change in changes],
        # The day being compared against, as the panel's heading names it -- null where
        # no run of this search was written down, which is a first run and a journal
        # that is off alike.
        "compared_with": compared_with,
    }


def results_payload(
    ranked: Sequence[RankedProduct], named: str | None = None
) -> list[dict[str, Any]]:
    """A whole run's products as JSON, best first (ADR-0043, ADR-0056)."""
    currency = dominant_currency((entry.product for entry in ranked), named)
    return [product_payload(entry, currency) for entry in ranked]


def offer_payload(offer: Offer) -> dict[str, Any]:
    """One listing the sources printed, as JSON (ADR-0058).

    Shaped here rather than dumped, for ``price_label``'s reason: how an amount is
    written is Python's (ADR-0012), and an offer's figure is written the way the
    headline's is or the card has two spellings of one thing.
    """
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
        # What that score is made of, and which criteria it was placed on nothing
        # (ADR-0041).
        "breakdown": entry.breakdown.model_dump(),
        **entry.product.model_dump(),
        # After the dump, which carries the offers as they are: what the card needs is
        # each one's amount written out, which is Python's to write (ADR-0058).
        "offers": [offer_payload(offer) for offer in entry.product.offers],
        "price_label": entry.product.price_label(),
        "rating_label": entry.product.rating_label(),
        # "3 listings, 129.00-149.00 USD", or null where one page priced it and a
        # spread would be the headline price said twice.
        "offers_label": entry.product.offers_label(),
    }


def model_payload(model: InstalledModel) -> dict[str, Any]:
    """One model a server is holding, as the picker needs it (ADR-0032)."""
    return {"name": model.name, "completion": model.completion}


def defaults_payload(*, screenshots: bool = False) -> dict[str, Any]:
    """The form's starting values: the same defaults the CLI shows in ``--help``.

    Every setting under the key it is sent back under, read off the one table that says
    which field carries it -- and then what the form needs besides a value: the rows of
    each table a setting names one of, every currency a run may be counted in, the
    criteria it may be sorted by, and the range each number is held to, so the form can
    refuse 51 products itself (ADR-0033). ``screenshots`` is whether the server answering
    has a camera, which is the server's to know and not a setting (ADR-0065).
    """
    defaults = AgentConfig()
    return {
        **{option.key: getattr(defaults, option.field) for option in OPTIONS},
        # One text field's worth, written the way the form sends it back.
        "sources": format_sources(defaults.sources),
        # Each provider's model and server travel with it, so choosing one in the form
        # fills in its pair rather than leaving an Ollama tag for a vLLM.
        "provider_options": provider_options(),
        "backend_options": backend_options(),
        "rail_options": rail_options(),
        # Blank is the default and means "whatever the pages quote", which is the vote
        # ADR-0043 settled the scale by; the picker offers every code a run could be
        # counted in.
        "currency_options": list(CURRENCY_OPTIONS),
        "pay_available": mandate_support(),
        # Whether a card may ask for a picture of its page. Not a setting: a server
        # either has a camera or it has not, and the page asks only one that has.
        "screenshots": screenshots,
        "sort_by": "score",
        "sort_options": list(SORT_OPTIONS),
        "limits": limits_payload(),
    }


def limits_payload() -> dict[str, dict[str, int]]:
    """The range each number a request carries is held to, by the key it uses (ADR-0033)."""
    return {
        key: dict(zip(("min", "max"), LIMITS[field], strict=True))
        for key, field in _BOUNDED.items()
    }


def bounds_payload(request: str) -> dict[str, Any]:
    """What the request itself asks for, offered for the form to fill in (ADR-0059).

    The one endpoint besides ``/api/sources`` that runs nothing, and the only one that
    answers with a value rather than a verdict: these numbers are not applied, are not
    a refusal, and mark no box. The form puts each in the box that would enforce it and
    the shopper submits it or clears it.

    A figure outside what that setting takes is dropped here rather than offered: the
    ranges live at the doors (ADR-0033), and pre-filling a box with a number the form
    would then mark is a mark on something nobody typed.
    """
    return {
        "request": request,
        "noticed": [
            {"bound": seen.bound, "value": seen.value, "note": seen.note}
            for seen in notice(request)
            if _takeable(seen)
        ],
    }


def _takeable(seen: Noticed) -> bool:
    """Whether the setting this was noticed for would accept the figure (ADR-0033)."""
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
    """A picture of the page at ``url``, for the card that links to it (ADR-0065).

    Runs no pipeline, like a re-sort: the card already holds the address, and what it
    wants is a look at it. A page that will not be photographed is 502, the server that
    was asked having done nothing wrong -- and the card, which asked only because the
    defaults said it could, drops the frame rather than showing a broken one.
    """
    if camera is None:
        raise ApiError("This server takes no screenshots.", 404)
    parsed = urlparse(url)
    # A web page and nothing else: a ``file:`` address is this machine's own disk, which
    # a browser that will draw anything would draw too.
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
    # Any transport failure means "not there", which is the answer being written: this
    # endpoint reports an unreachable server rather than failing with it.
    # pylint: disable-next=broad-exception-caught
    except Exception as exc:
        logger.debug("Could not list %s models at %s", label, base_url, exc_info=True)
        failed = {**status, "reachable": False, "models": [], "detail": str(exc)}
        # A config that never got built named a provider nothing can serve, so there is
        # no row to ask for a remedy -- and its own refusal is one.
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
    """The sources the request named, if any -- the one option that is a list (ADR-0033)."""
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
    """Is the key set to something? An empty form field counts as unset (ADR-0012)."""
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
    """The value of ``key``, parsed -- or ``default`` where it is not set at all."""
    if not _present(data, key):
        return default
    return parse(key, str(data[key]).strip())


def _read_products(data: Mapping[str, Any]) -> list[Product]:
    """The products of a finished run, read back off the request that carried them."""
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


def _among(options: tuple[str, ...]) -> Callable[[str, str], str]:
    """A parser for the three settings that name a row of a table (ADR-0033)."""

    def parse(key: str, text: str) -> str:
        if text not in options:
            raise ApiError(
                f"{key} must be one of {', '.join(options)}; got {text!r}.", field=key
            )
        return text

    return parse


def _checked(check: Callable[[str], str]) -> Callable[[str, str], str]:
    """A parser for the two settings whose value is judged in :mod:`buy_agent.config`.

    A region's shape (ADR-0031) and a currency the run can count in (ADR-0056) are each
    checked in exactly one place and reached through it by both doors. All this door adds
    is the box to mark, which is the same addition twice -- so it is written once, and
    spelled the way ``__main__._checked`` spells the other half of it (ADR-0033).
    """

    def parse(key: str, text: str) -> str:
        try:
            return check(text)
        except ValueError as exc:
            raise ApiError(str(exc), field=key) from exc

    return parse


def _as_bool(key: str, text: str) -> bool:
    """A checkbox, a query parameter or a JSON boolean, all read the same way."""
    lowered = text.lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ApiError(f"{key} must be true or false; got {text!r}.", field=key)


def _bounded(kind: Callable[[str], _Number]) -> Callable[[str, str], _Number]:
    """A parser for a number within the bounds whatever key it arrives under has."""

    def parse(key: str, text: str) -> _Number:
        minimum, maximum = LIMITS[_BOUNDED[key]]
        return _as_number(kind, minimum, maximum, key, text)

    return parse


def _as_number(
    kind: Callable[[str], _Number],
    # The bound is a number and the value is whichever kind ``kind`` reads, which are two
    # things and not one: ``config.LIMITS`` holds every range as a pair of ints, so tying
    # them together said that ``temperature`` -- read with ``float`` against ``(0, 2)`` --
    # could only ever be a whole number.
    minimum: float,
    maximum: float,
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


# -- the settings a request carries --------------------------------------------


@dataclass(frozen=True, slots=True)
class _Option:
    """One setting as a request carries it: the key it arrives under, the
    :class:`AgentConfig` field it fills in, and how its text is read (ADR-0012,
    ADR-0033).

    Said once, and read by everything that has to agree about it -- what a request may
    set, what the form is seeded with, which range holds which key, and the CLI, whose
    flags land under these same names. Written down per reader instead, a setting
    reaches one door and not the other.
    """

    key: str
    field: str
    parse: Callable[[str, str], Any]
    #: Read as blank rather than as the config's own default, for the three settings
    #: resolved per provider or per rail: a default built for whichever server this
    #: process happens to start on is the wrong one to answer a form that chose the
    #: other (ADR-0012).
    blank: bool = False

    def unset(self, defaults: AgentConfig) -> Any:
        """What this key means when a request does not carry it."""
        return "" if self.blank else getattr(defaults, self.field)


#: Every setting both doors fill in, in the order the form draws them. ``sources`` is
#: deliberately not here: it is the one option that is a list, which ``_read`` would
#: render as a Python repr (ADR-0027).
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
    # A blank is "no bound" for these three, which is what "the default" means when the
    # default is None (ADR-0012, ADR-0039).
    _Option("max_price", "max_price", _bounded(float)),
    _Option("min_rating", "min_rating", _bounded(float)),
    _Option("min_reviews", "min_reviews", _bounded(int)),
    _Option("cache_ttl", "cache_ttl", _bounded(float)),
    # What this run reported, kept so the next one can say what moved (ADR-0060).
    _Option("journal", "journal", _as_bool),
    _Option("region", "region", _checked(parse_region)),
    # Blank is the default and means "whatever the pages quote" (ADR-0056).
    _Option("currency", "currency", _checked(parse_currency)),
    _Option("backend", "backend", _among(BACKEND_OPTIONS)),
    _Option("fetch", "fetch_pages", _as_bool),
    # Paying is off unless a request asks for it, and the rail decides what asking
    # costs -- the default one charges nobody.
    _Option("pay", "pay", _as_bool),
    _Option("rail", "rail", _among(RAIL_OPTIONS)),
    _Option("merchant_url", "merchant_url", _as_text, blank=True),
    _Option("spend_limit", "spend_limit", _bounded(float)),
)

#: The config field whose range holds each number a request may carry, by the key it
#: arrives under -- ``results`` is what a request calls ``num_products``. Read off the
#: rows rather than listed again: a setting with a row in ``LIMITS`` is one both doors
#: hold to that range, and there is nothing else to say about which (ADR-0033).
_BOUNDED: dict[str, str] = {
    option.key: option.field for option in OPTIONS if option.field in LIMITS
}
