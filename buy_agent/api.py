"""Turning a web request into an :class:`~buy_agent.agent.BuyAgent` run.

The whole HTTP-facing half worth testing -- reading options off a request, running
the pipeline, shaping the answer as JSON; :mod:`buy_agent.server` is only the
socket around it. Options arrive as a JSON body or as query parameters, so every
value is coerced from text, which either carrier can spell.

:func:`rank_again` and :func:`pay_now` run no pipeline, and are POST only: a query
string cannot carry a list of products.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from functools import partial
from typing import TYPE_CHECKING, Any, TypeVar, cast, get_args

from pydantic import ValidationError

from buy_agent.agent import (
    BuyAgent,
    Checkpoint,
    ModelUnavailableError,
    every_step_passes,
)
from buy_agent.chat import release
from buy_agent.config import LIMITS, AgentConfig, parse_region
from buy_agent.models import Product, dominant_currency
from buy_agent.payment import (
    Cart,
    PaymentError,
    RailUnreachableError,
    Receipt,
    amount_for,
    amount_label,
    cart_for,
    pay_for,
    payable,
    unattended,
)
from buy_agent.providers import PROVIDERS, provider_options
from buy_agent.rails import RAILS, rail_options
from buy_agent.ranking import RankingWeights, SortBy, rank_products
from buy_agent.search import SearchError
from buy_agent.sources import Source, format_sources, parse_sources

if TYPE_CHECKING:
    from collections.abc import Sequence

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

#: The rails a request may name, read off the registry for the reason the
#: providers are: a rail added there is offered here on the same day.
RAIL_OPTIONS: tuple[str, ...] = tuple(RAILS)

_TRUE = frozenset({"true", "1", "yes", "on"})
_FALSE = frozenset({"false", "0", "no", "off"})

#: The config field whose range holds each number a request may carry, by the key
#: it arrives under -- ``results`` is what a request calls ``num_products``. Read
#: twice: :func:`_bounded` holds an incoming value to the range and
#: :func:`limits_payload` ships the same ranges to the form, so the browser can
#: refuse 100 products without a second copy of the bounds (ADR-0033).
_BOUNDED: dict[str, str] = {
    "results": "num_products",
    "top": "top_n",
    # The rest are asked for under the name of the field they bound, which is
    # said once here rather than as a row apiece repeating itself.
    **{
        field: field
        for field in (
            "temperature",
            "num_ctx",
            "max_price",
            "min_rating",
            "min_reviews",
            "cache_ttl",
            "spend_limit",
        )
    },
}

#: Which HTTP status each of the agent's three failure modes deserves. It raises
#: exactly these (see ``BuyAgent.run``), so a new one has to be added here and to
#: ``__main__.main`` or it reaches the client as a 500.
_STATUS: dict[type[Exception], int] = {
    ValueError: 400,
    ModelUnavailableError: 503,
    SearchError: 502,
}


#: Which HTTP status each payment failure deserves. A table of its own rather
#: than rows in :data:`_STATUS`: those three are what a *run* raises, while a
#: payment fails at its own door. Subclass-first, the lookup taking the first.
PAY_STATUS: dict[type[Exception], int] = {
    RailUnreachableError: 502,
    PaymentError: 400,
}


class ApiError(Exception):
    """A failure with the HTTP status the client should be told about.

    ``field`` is the request key the unusable value arrived under, so the browser can
    mark that input rather than only writing the sentence into a banner (ADR-0033).
    ``None`` where the failure is about the run rather than a value.
    """

    def __init__(self, message: str, status: int = 400, field: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.field = field

    def payload(self) -> dict[str, Any]:
        return {"error": str(self), "field": self.field}


def _status_for(exc: Exception, table: Mapping[type[Exception], int]) -> int:
    """The status ``table`` gives this failure, taking the first row it matches.

    Both tables are ordered subclass-first and each is the tuple its ``except`` clause
    catches, so a caught exception always matches a row.
    """
    return next(status for kind, status in table.items() if isinstance(exc, kind))


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
    num_products = _read(data, "results", defaults.num_products, _bounded(int))
    top_n = _read(data, "top", defaults.top_n, _bounded(int))
    sort_by = _read(data, "sort_by", "score", _as_sort_by)

    provider = _read(data, "provider", defaults.provider, _as_text)
    if provider not in PROVIDER_OPTIONS:
        raise ApiError(
            f"provider must be one of {', '.join(PROVIDER_OPTIONS)}; got {provider!r}.",
            field="provider",
        )

    # ``AgentConfig`` refuses a paying rail with nowhere to pay, the one thing
    # here no range can judge. Its sentence is the useful one; the field is this
    # door's to name, and the address is the only one left, the provider, rail
    # and region each being refused above. Uncaught it was a 500 reading
    # "Unexpected failure" with no box marked (ADR-0033).
    config = _configured(
        provider=provider,
        # Blank rather than ``defaults``, which was built for whichever provider
        # the server starts on: an empty string is what ``AgentConfig`` resolves
        # per provider, so a form that chose one and left these alone gets its pair.
        model=_read(data, "model", "", _as_text),
        base_url=_read(data, "base_url", "", _as_text),
        temperature=_read(data, "temperature", defaults.temperature, _bounded(float)),
        num_ctx=_read(data, "num_ctx", defaults.num_ctx, _bounded(int)),
        reasoning=_read(data, "think", defaults.reasoning, _as_bool),
        # Searching fewer pages than we report would cap the report -- as in the CLI.
        search_results=max(num_products, top_n),
        num_products=num_products,
        top_n=top_n,
        region=_read(data, "region", defaults.region, _as_region),
        sources=_read_sources(data, defaults.sources),
        fetch_pages=_read(data, "fetch", defaults.fetch_pages, _as_bool),
        # A blank is "no bound" here rather than "the default" -- which is the
        # same thing, these three defaulting to None (ADR-0012, ADR-0039).
        max_price=_read(data, "max_price", defaults.max_price, _bounded(float)),
        min_rating=_read(data, "min_rating", defaults.min_rating, _bounded(float)),
        min_reviews=_read(data, "min_reviews", defaults.min_reviews, _bounded(int)),
        cache_ttl=_read(data, "cache_ttl", defaults.cache_ttl, _bounded(float)),
        # Paying is off unless a request asks for it, and the rail decides what
        # asking costs -- the default one charges nobody.
        pay=_read(data, "pay", defaults.pay, _as_bool),
        rail=_read(data, "rail", defaults.rail, _as_rail),
        merchant_url=_read(data, "merchant_url", "", _as_text),
        spend_limit=_read(data, "spend_limit", defaults.spend_limit, _bounded(float)),
    )
    return config, sort_by


def _configured(**settings: Any) -> AgentConfig:
    """An :class:`AgentConfig`, with its own refusal answered like every other.

    The config checks one thing no range can: a rail that moves money and has nowhere
    to send it. That is a value a request carried, so it earns the status and the
    field every other unusable value gets rather than the 500 an escaping
    ``ValueError`` becomes.

    Raises:
        ApiError: naming ``merchant_url``, the only setting left for the config to
            refuse once this door has checked the rest.
    """
    try:
        return AgentConfig(**settings)
    except ValueError as exc:
        raise ApiError(str(exc), field="merchant_url") from exc


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
        checkpoint: Handed to ``BuyAgent.run``, which calls it at each step boundary.
            A caller whose client has gone raises from it to end the run there
            (ADR-0034); what it raises stays an exception, there being nobody left to
            answer an ``ApiError`` to.

    Returns:
        ``{"request", "count", "top_n", "sort_by", "products"}``, best first, each
        product carrying its rank and score.

    Raises:
        ApiError: for every failure the agent raises, with the status it deserves.
    """
    agent = None
    try:
        agent = agent_factory(config)  # type: ignore[arg-type]
        ranked = agent.run(request, sort_by=sort_by, checkpoint=checkpoint)
    except tuple(_STATUS) as exc:
        raise ApiError(str(exc), _status_for(exc, _STATUS)) from exc
    finally:
        # One request, one agent, and its connection let go of here rather than
        # whenever the last reference falls. Both halves are inside the guard, so
        # a config the provider refuses is still the ``ApiError`` it was and
        # ``None`` is an agent never built. Asked rather than called outright, so
        # a stand-in is still a class with a ``run`` and nothing else.
        release(agent)

    return _run_payload(request, ranked, config.top_n, sort_by, config.weights)


def rank_again(data: Mapping[str, Any]) -> dict[str, Any]:
    """Put a finished run's products in another order, without running it again.

    Ranking needs no model and no network, so re-ordering is that ordering asked for
    on its own (ADR-0035). The judgement stays here: the scores the browser holds are
    ignored and recomputed, a score being a fact about the whole candidate set.

    Args:
        data: ``{"request", "products", "sort_by", "top"}`` -- the products as
            :func:`product_payload` wrote them (extra keys ignored), and the two
            settings shaping the answer. How many may arrive is the server's to cap.

    Returns:
        The shape a finished run answers with, so the page shows it the same way.

    Raises:
        ApiError: if the criterion, the count or the products are unusable.
    """
    defaults = AgentConfig()
    request = _read(data, "request", "", _as_text)
    sort_by = _read(data, "sort_by", "score", _as_sort_by)
    top_n = _read(data, "top", defaults.top_n, _bounded(int))
    # Named rather than left to ``rank_products``'s own fallback, so the weights
    # the answer reports are the ones it was ranked by: a re-sort takes no config,
    # being the one entry point that runs no pipeline.
    weights = RankingWeights()
    ranked = rank_products(
        _read_products(data), weights=weights, sort_by=cast(SortBy, sort_by)
    )
    return _run_payload(request, ranked, top_n, sort_by, weights)


def mandate_support() -> bool:
    """Is the optional AP2 SDK installed? Imported here so nothing else asks."""
    from buy_agent import mandates  # noqa: PLC0415 -- see mandates' module docstring

    return mandates.available()


def pay_now(data: Mapping[str, Any]) -> dict[str, Any]:
    """Buy one product of a finished run, having been shown that it was approved.

    Runs no pipeline, the way :func:`rank_again` runs none (ADR-0035): the products
    travel in the body because the browser is already holding them, a server-side run
    store being a lifetime and an eviction policy on a server that is stdlib on
    purpose.

    The browser decides nothing here either (ADR-0012). It sends no cart -- it sends
    the run, which product of it, and ``approved``: an echo of the title, price and
    currency it put in front of a person. The cart is built here and the echo has to
    match it, so a stale page cannot buy at its stale price and a page that asked
    nobody cannot guess the echo. Where a pre-signed open mandate authorises the run
    there is no echo, that being the whole meaning of the autonomous mode, and the
    mandate's own constraints are what the cart is held to.

    Args:
        data: ``{"products", "rank", "approved", ...}`` plus the run settings, read
            by :func:`parse_options` so a payment is configured as a search is.

    Returns:
        ``{"receipt": ...}`` -- what came of the payment.

    Raises:
        ApiError: if the request is unusable, or the payment did not happen.
    """
    config, _sort_by = parse_options(data)
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
    """What came of a payment, as JSON.

    Never the mandate chain: a chain authorises this purchase to whoever holds it, and
    this payload is logged and handed to a browser. ``reference`` is the hash pointing
    back at it, which is what AP2 says a receipt binds by.
    """
    return receipt.model_dump()


def _rank(data: Mapping[str, Any], count: int) -> int:
    """Which product of the run to buy, as an index into the list that arrived.

    One-based, as a card shows it, defaulting to the first. The bound is the run's own
    length rather than a row of :data:`_BOUNDED`: holding it to ``top``'s 1..50 too
    meant two refusals for one mistake, the wider quoting a ceiling nothing here has.

    Raises:
        ApiError: if it is not a whole number naming one of the products that arrived.
    """
    return _read(data, "rank", 1, partial(_as_number, int, 1, count)) - 1


def _witnessed(data: Mapping[str, Any], cart: Cart) -> None:
    """Refuse unless the request echoes the cart the server just built.

    The title, the price and the currency: what a person was shown, and the three a
    stale page would get wrong. Prices compared as numbers, so "329.99" and "329.990"
    are one approval.

    Raises:
        ApiError: naming what differs, so the page can show it.
    """
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
        return abs(float(approved.get("price", "nan")) - price) < 0.005  # noqa: PLR2004
    except (TypeError, ValueError):
        return False


def _run_payload(
    request: str,
    ranked: Sequence[RankedProduct],
    top_n: int,
    sort_by: str,
    weights: RankingWeights,
) -> dict[str, Any]:
    """The shape a finished run answers with, however it was finished.

    ``weights`` travels with the products because a breakdown cannot be read without
    it: three shares beside a total invite being added up, and nothing on a card says
    which one the placing turned on. Sent as fractions of the blend, and as a fact
    about the run rather than a fourth field on every product.
    """
    return {
        "request": request.strip(),
        "count": len(ranked),
        "top_n": top_n,
        "sort_by": sort_by,
        "weights": weights.fractions,
        "products": results_payload(ranked),
    }


def results_payload(ranked: Sequence[RankedProduct]) -> list[dict[str, Any]]:
    """A whole run's products as JSON, best first.

    One shape for every way a run leaves the process: the API's answer, the file
    ``--json`` writes, and the file Download results hands over. The currency is
    worked out once here and handed to every product -- whether one *can* be paid for
    is partly a fact about the set it was found in (ADR-0043).
    """
    currency = dominant_currency(entry.product for entry in ranked)
    return [product_payload(entry, currency) for entry in ranked]


def product_payload(entry: RankedProduct, currency: str | None = None) -> dict[str, Any]:
    """One ranked product as JSON.

    The raw fields *and* the labels ``Product`` already knows how to write, so the
    browser never reinvents how a blank price reads -- ``cannot_pay`` among them, the
    sentence saying why this product may not be bought, made by the same function the
    payment goes through so a Pay button is never offered for what the server would
    refuse (ADR-0012, ADR-0033).

    ``pay_currency`` and ``pay_label`` are what that purchase would be *for*, which is
    frequently not the product's own figures: a page printing a bare "329.00" leaves
    ``currency`` null while the cart is in the run's currency (ADR-0043). A card
    restating ``price_label`` showed unnamed money and echoed a null currency back,
    which no approval matches. Both are null exactly when ``cannot_pay`` is a
    sentence.
    """
    terms = amount_for(entry.product, currency)
    return {
        "cannot_pay": payable(entry.product, currency),
        "pay_currency": terms[1] if terms else None,
        "pay_label": amount_label(*terms) if terms else None,
        "rank": entry.rank,
        "score": round(entry.score, 4),
        # What that score is made of, so a card can say why a product placed
        # where it did and which criteria it was placed on nothing (ADR-0041).
        "breakdown": entry.breakdown.model_dump(),
        **entry.product.model_dump(),
        "price_label": entry.product.price_label(),
        "rating_label": entry.product.rating_label(),
    }


def model_payload(model: InstalledModel) -> dict[str, Any]:
    """One model a server is holding, as the picker needs it.

    ``completion`` is sent rather than acted on: the form *marks* a model that cannot
    answer a prompt instead of hiding it, so a tag pulled by mistake stays visible
    (ADR-0032).
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
        # None, which the form shows as an empty box meaning "no bound" -- the
        # answer a shopper who set none of them gives (ADR-0039).
        "max_price": defaults.max_price,
        "min_rating": defaults.min_rating,
        "min_reviews": defaults.min_reviews,
        "cache_ttl": defaults.cache_ttl,
        "region": defaults.region,
        # One text field's worth, written the way the form sends it back.
        # Empty -- the default -- is the whole web.
        "sources": format_sources(defaults.sources),
        "fetch": defaults.fetch_pages,
        # Paying, and who through. ``pay_available`` is whether the optional AP2
        # SDK is installed at all: the page says so rather than offering a button
        # whose only outcome is a sentence about pip.
        "pay": defaults.pay,
        "pay_available": mandate_support(),
        "rail": defaults.rail,
        "rail_options": rail_options(),
        "merchant_url": defaults.merchant_url,
        "spend_limit": defaults.spend_limit,
        "sort_by": "score",
        "sort_options": list(SORT_OPTIONS),
        # What each number field may hold, so the form can refuse 51 products
        # itself rather than opening a stream to be told (ADR-0033).
        "limits": limits_payload(),
    }


def limits_payload() -> dict[str, dict[str, int]]:
    """The range each number a request carries is held to, by the key it uses.

    Shipped rather than written into the form (:data:`_BOUNDED`): the browser applies
    these and does not choose them (ADR-0033). Paired strictly, a range being exactly
    two numbers, so a row that grew a third cannot ship a bound nothing applies.
    """
    return {
        key: dict(zip(("min", "max"), LIMITS[field], strict=True))
        for key, field in _BOUNDED.items()
    }


def sources_payload(spec: str) -> dict[str, Any]:
    """Whether a Trusted-sources field names sources, and what is wrong if not.

    The one option the form cannot judge itself: a source is whatever
    :func:`~buy_agent.sources.parse_sources` reads, and writing that again in
    TypeScript is the drift ADR-0031 refused for the region. So the browser asks and
    gets the sentence the CLI prints (ADR-0033).

    Args:
        spec: What the field holds -- one string, which may name several sources.
            Empty is the whole web, and fine.

    Returns:
        ``{"sources", "error"}``. ``error`` is empty for a field with nothing wrong
        with it; ``sources`` is the spec as given, so a form typed into since can drop
        an answer about what it held a keystroke ago.
    """
    error = ""
    try:
        parse_sources(spec)
    except ValueError as exc:
        error = str(exc)
    return {"sources": spec, "error": error}


def installed_models(provider: str, base_url: str) -> dict[str, Any]:
    """Ask a model server what it is serving, for the UI's model picker.

    An unreachable server is an answer, not an error: the UI shows it as a status
    rather than refusing to draw a form. ``label`` travels with it, since "Ollama
    unreachable" over a vLLM address is a lie the browser could not catch, and each
    model carries what it can do beside its name (ADR-0032).

    A failure carries ``detail``, the transport's own reason, and ``hint``, the
    sentence the provider would have raised had a run hit the same failure. Written
    here rather than in TypeScript: that sentence already exists and is tested.
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

    Not through :func:`_read`, which renders every value with ``str`` and would turn a
    JSON array into its Python repr. A query string spells several as one separated
    string; a JSON body may send either. Each entry is rendered with ``str`` all the
    same: ``["rtings.com", 5]`` otherwise asked an ``int`` for its ``strip``, an
    ``AttributeError`` out of the door where every other unusable value gets a
    sentence (ADR-0033).
    """
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
    """Is the key set to something? An empty form field counts as unset.

    A list holding nothing but blanks says what ``""`` says, which is "use the
    default" (ADR-0012) -- read as set, they left the run searching the whole web,
    the widening ``parse_named_sources`` refuses on the command line. Said here
    rather than there, because over the wire a blank really is how "unset" is spelled.
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
    :func:`_as_bool` reads back), so one parser serves both carriers. ``parse`` is
    given the key because its refusal names the field.

    Raises:
        ApiError: if the value is present but ``parse`` cannot make sense of it.
    """
    if not _present(data, key):
        return default
    return parse(key, str(data[key]).strip())


def _read_products(data: Mapping[str, Any]) -> list[Product]:
    """The products of a finished run, read back off the request that carried them.

    Not through :func:`_read`, for the reason :func:`_read_sources` is not.
    ``Product`` validates them, so what comes back is the domain model rather than
    whatever JSON was posted: extra keys are ignored, and a missing name is refused.
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

    Read by both doors into the ranking, so a fourth is offered by both the day it is
    added.
    """
    if text not in SORT_OPTIONS:
        raise ApiError(
            f"sort_by must be one of {', '.join(SORT_OPTIONS)}; got {text!r}.", field=key
        )
    return text


def _as_rail(key: str, text: str) -> str:
    """A payment rail, checked against the ones there are.

    Refused here rather than in ``AgentConfig``'s own ``ValueError`` so the answer
    carries the field it came out of, which is what marks the box (ADR-0033).
    """
    if text not in RAIL_OPTIONS:
        raise ApiError(
            f"rail must be one of {', '.join(RAIL_OPTIONS)}; got {text!r}.", field=key
        )
    return text


def _as_region(key: str, text: str) -> str:
    """A region code, checked for shape the way a source is checked for a site.

    A 400 naming the shape, rather than a run that reports the web as having nothing
    to say: this is the one setting a typo makes look like an empty web (ADR-0031).
    The key travels beside the message so the form can mark the box (ADR-0033).
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


def _bounded(kind: Callable[[str], _Number]) -> Callable[[str, str], _Number]:
    """A parser for a number within the bounds whatever key it arrives under has.

    The key is not an argument because :func:`_read` already hands it to the parser,
    and one named here too is two chances to disagree -- the way they disagree being a
    value silently held to another setting's range. The range is read off
    :data:`buy_agent.config.LIMITS` through :data:`_BOUNDED`, so the CLI, this and the
    form cannot disagree, and is quoted back as declared.
    """
    return partial(_declared_number, kind)


def _declared_number(kind: Callable[[str], _Number], key: str, text: str) -> _Number:
    """A number held to the range :data:`_BOUNDED` declares for this key."""
    minimum, maximum = LIMITS[_BOUNDED[key]]
    return _as_number(kind, minimum, maximum, key, text)


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
