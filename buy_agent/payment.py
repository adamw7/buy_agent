"""Paying for a product the run already found, once somebody has said so.

Deliberately *not* a step in :meth:`~buy_agent.agent.BuyAgent.run`: the pipeline
ends with a ranked report and raises exactly three failures (ADR-0009), while a
purchase happens afterwards, to one product, on a separate decision. Both front
doors call :func:`pay_for` themselves, after a person approved a cart or an open
mandate authorised one.

The rule here is the ranking rule turned around. Grounding already blanks every
figure the sources did not print (ADR-0006) and links only pages that were
searched (ADR-0017), so **a product whose price is a blank is a product nothing
may be paid for**. :func:`payable` therefore asks only for a price that is an
amount, a currency the run can place it in (ADR-0043) and a link.

The money never becomes a float on the wire: AP2 counts in minor units, so
:func:`minor_units` converts once through :class:`~decimal.Decimal`.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from pydantic import BaseModel

from buy_agent import mandates
from buy_agent.models import Product, comparable_price, dominant_currency

if TYPE_CHECKING:
    from buy_agent.config import AgentConfig

logger = logging.getLogger(__name__)

#: Currencies not counted in hundredths. ISO 4217 gives most an exponent of 2, so
#: only the exceptions are written down: a table of every currency is one to keep
#: current for no gain, while one of these wrong is a payment a hundredfold.
_ZERO_DECIMAL = frozenset(
    {
        "BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW",
        "PYG", "RWF", "UGX", "UYI", "VND", "VUV", "XAF", "XOF", "XPF",
    }
)
_THREE_DECIMAL = frozenset({"BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"})

#: What a payment instrument is called when the credential provider has not named
#: one. A reference and never a number: what funds a payment is theirs to hold.
DEFAULT_INSTRUMENT = "default"


class PaymentError(Exception):
    """A purchase that did not happen, and why.

    The one failure both front doors report, whatever went wrong underneath. It is
    not one of the three a *run* raises -- a payment is not a run, which is why it is
    caught in its own place at both doors. ``field`` names the request key an unusable
    value arrived under, so the browser can mark that box (ADR-0033).
    """

    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


class RailUnreachableError(PaymentError):
    """The counterparty could not be reached, or refused to answer at all.

    A subclass rather than a flag: it is the one payment failure that is nothing to do
    with the request, so it deserves its own HTTP status, and the CLI still catches
    both by catching the parent.
    """


@dataclass(frozen=True, slots=True)
class Settlement:
    """What a rail says came of presenting the mandates.

    Deliberately small: a rail does transport, and composing the receipt is this
    module's job. ``paid`` is the whole verdict -- a rail that refused says so in
    ``detail`` rather than in a status vocabulary every caller would have to know.
    """

    paid: bool
    detail: str


class Cart(BaseModel):
    """One product, priced, as the thing a mandate can be signed for.

    Every field was printed by a page that was searched, which is what makes a cart
    something the agent may authorise at all. ``amount`` is minor units (cents, yen,
    fils) because that is what AP2 counts in; ``price`` and ``currency`` sit beside it
    so a receipt reads without anyone dividing by a hundred.
    """

    title: str
    price: float
    currency: str
    amount: int
    merchant: str
    url: str
    item_id: str
    instrument: str = DEFAULT_INSTRUMENT

    def merchant_payload(self) -> dict[str, str]:
        """The merchant as AP2 names one: an id, a name and where it lives.

        The id is the site the page came from, that being the only identity this pipeline
        knows -- a seller's name is what a page printed, and two pages print two
        spellings. Read by :func:`_site`, so an address carrying a user and a password
        does not put them in a payload that is signed and sent. A URL with no site to read
        is used as it stands: half an identity is better than none.
        """
        host = _site(self.url) or self.url
        return {"id": host, "name": self.merchant, "website": f"https://{host}"}

    def label(self) -> str:
        """The price as a person reads it -- the wording Python owns, not the page."""
        return amount_label(self.price, self.currency)


class Receipt(BaseModel):
    """What came of a payment, in the shape both front doors report it.

    Never carries the mandate chain: a chain is a credential authorising this purchase
    to whoever holds it, and a receipt is logged, sent to a browser and saved.
    ``reference`` -- the SHA-256 of the closed leaf -- is what points back at it,
    which is what AP2 says a receipt binds by.
    """

    paid: bool
    rail: str
    merchant: str
    title: str
    price: float
    currency: str
    amount: int
    price_label: str
    transaction_id: str
    reference: str
    #: Whether an open mandate authorised this rather than a person approving the
    #: cart. Reported because they are not the same promise.
    autonomous: bool
    #: Whether the signature was made with an enrolled key or one this process
    #: invented. False means the chain shows the shape of an authorisation without
    #: being one, which is the dry run's whole point.
    enrolled_key: bool
    detail: str


def minor_units(price: float, currency: str) -> int:
    """``price`` in the currency's smallest unit, rounded half up.

    Through :class:`~decimal.Decimal` and not by multiplying a float: ``19.99 * 100``
    is 1998.9999999999998, and a payment is no place to truncate. Half up rather than
    banker's rounding, that being what a price tag implies.

    Raises:
        PaymentError: if the price cannot be counted at all -- a NaN or an infinity
            out of a page nobody should have believed.
    """
    exponent = 0 if currency in _ZERO_DECIMAL else 3 if currency in _THREE_DECIMAL else 2
    try:
        scaled = Decimal(str(price)).scaleb(exponent).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        # Inside the guard because this is where a NaN or an infinity fails:
        # ``quantize`` answers NaN happily, and only ``int`` refuses it.
        return int(scaled)
    # ``decimal.InvalidOperation`` is an ``ArithmeticError`` and so is every other
    # ``DecimalException`` -- naming it as well would be one class caught twice and
    # the rest of them, ``Overflow`` included, still caught only by accident.
    except (ArithmeticError, ValueError) as exc:
        raise PaymentError(f"{price!r} is not a price this can pay.") from exc


def _check(product: Product, currency: str | None) -> tuple[float, str]:
    """The price and the currency this product may be paid in, or a refusal.

    Every branch names something that is not an amount to send -- what the *sources*
    did not establish, or a figure that is no amount whatever they printed -- and each
    carries a sentence: "cannot pay for this" with no reason reads as a broken button.

    ``currency`` is the run's own (:func:`~buy_agent.models.dominant_currency`), so a
    price outside it is one this run cannot place (ADR-0043) and cannot authorise.
    That is deliberately the opposite of the shopper's bounds, which keep a product
    they cannot judge rather than punish the extractor's miss (ADR-0039): money is not
    a filter, and an amount nobody can place is not an amount to send.

    Raises:
        PaymentError: naming what is missing.
    """
    if product.price is None:
        raise PaymentError(
            f"No source printed a price for {product.name}, so there is nothing to "
            f"authorise. Grounding blanks a figure the pages do not back.",
            field="products",
        )
    if product.price <= 0:
        raise PaymentError(
            f"{product.name} is priced at {product.price:,.2f}, which is not an amount "
            f"to send: a payment is something somebody is owed. A figure like that is "
            f"blanked on the way in from the model, so one here came off a request.",
            field="products",
        )
    if currency is None:
        raise PaymentError(
            f"None of the pages named a currency, so {product.price:,.2f} is a number "
            f"and not an amount.",
            field="products",
        )
    price = comparable_price(product, currency)
    if price is None:
        raise PaymentError(
            f"{product.name} is priced in {product.currency}, and this run counts in "
            f"{currency}. Nothing is converted, so that is not an amount this can pay.",
            field="products",
        )
    if not product.url:
        raise PaymentError(
            f"{product.name} has no source page, so there is no merchant to pay.",
            field="products",
        )
    return price, currency


def payable(product: Product, currency: str | None) -> str | None:
    """Why this product cannot be paid for, or ``None`` if it can.

    The judgement :func:`cart_for` makes, asked without making a cart -- what a front
    door needs to offer a Pay button only for a product it can pay for. One rule asked
    twice, rather than a second reading of it in TypeScript (ADR-0033).
    """
    try:
        _check(product, currency)
    except PaymentError as exc:
        return str(exc)
    return None


def amount_for(product: Product, currency: str | None) -> tuple[float, str] | None:
    """What a cart for this product would be worth, or ``None`` where there is none.

    The other half of :func:`payable`: that one says why a product may not be bought,
    this one in what money it would be if it may. Both are :func:`_check`, the amount
    being decided by the reasoning that decides whether there is one (ADR-0043).

    A front door needs it because a cart's currency is frequently not the product's
    own: a page printing a bare "329.00" leaves ``Product.currency`` null while the
    cart is in USD. A surface restating the product's figure showed an amount with no
    unit and echoed a null currency back, which is not an approval of anything.
    """
    try:
        return _check(product, currency)
    except PaymentError:
        return None


def amount_label(price: float, currency: str) -> str:
    """An amount as a person reads it, which is how every surface must write it.

    One wording for the CLI's prompt, the card's confirmation and the receipt.
    """
    return f"{price:,.2f} {currency}"


def cart_for(product: Product, products: Sequence[Product], config: AgentConfig) -> Cart:
    """The cart for one product of a finished run.

    ``products`` is the rest of the run, because two judgements here are about the set:
    which currency the run counts in, and whether this price is on that scale at all.

    Raises:
        PaymentError: if the product is not payable, or costs more than the shopper's
            spend limit.
    """
    currency = dominant_currency(products)
    price, currency = _check(product, currency)

    limit = config.spend_limit
    if limit is not None and price > limit:
        raise PaymentError(
            f"{product.name} costs {price:,.2f} {currency}, over the "
            f"{limit:,.2f} {currency} spend limit.",
            field="spend_limit",
        )
    return Cart(
        title=product.name,
        price=price,
        currency=currency,
        amount=minor_units(price, currency),
        merchant=product.seller or _host(product.url),
        url=product.url or "",
        item_id=product.dedup_key.replace(" ", "-")[:120],
    )


def unattended() -> bool:
    """Is there a pre-signed open mandate to buy on, or must a person be asked?

    Here rather than at each front door, so both ask it the same way and a malformed
    mandate file arrives as the one failure a payment has.

    Raises:
        PaymentError: if a mandate is configured and cannot be read. Read as "no
            mandate", a broken file would quietly drop an unattended run back to
            waiting for a person who is not there.
    """
    try:
        return mandates.open_mandate() is not None
    except mandates.MandateError as exc:
        raise PaymentError(str(exc)) from exc


def pay_for(cart: Cart, config: AgentConfig) -> Receipt:
    """Authorise this cart and present it to the rail, returning what came back.

    The order is the protocol's: the merchant signs a checkout first, because only
    then is there a price to bind a mandate to; the mandates are signed against that
    hash; and only a chain that verified is presented. Nothing here decides whether
    the shopper agreed -- that happened at a front door, an open mandate being the
    other way of having agreed.

    Raises:
        PaymentError: for anything that stopped the purchase, with the sentence the
            rail or the mandate layer wrote.
    """
    rail = config.rail_used
    try:
        key, enrolled = mandates.load_key(required=rail.needs_key)
        signed, nonce = rail.checkout(cart, config)
        authorisation = mandates.authorise(cart, signed, key=key, nonce=nonce)
    except rail.transport_errors as exc:
        # Both calls to the rail translate the same way: an endpoint down while
        # being asked for a price is one down while being paid.
        raise RailUnreachableError(rail.hint(config, exc)) from exc
    except mandates.MandateError as exc:
        raise PaymentError(str(exc)) from exc

    if not enrolled:
        logger.warning(
            "Signed with a key this process generated, so the %s chain demonstrates an "
            "authorisation without being one. Set $%s to sign with an enrolled key.",
            rail.label,
            mandates.KEY_PATH,
        )
    logger.info(
        "Authorised %s at %s (%s), transaction %s",
        cart.title,
        cart.label(),
        "an open mandate" if authorisation.autonomous else "approved in person",
        authorisation.transaction_id,
    )

    try:
        settlement = rail.settle(cart, authorisation, config)
    except rail.transport_errors as exc:
        raise RailUnreachableError(rail.hint(config, exc)) from exc

    return Receipt(
        paid=settlement.paid,
        rail=rail.name,
        merchant=cart.merchant,
        title=cart.title,
        price=cart.price,
        currency=cart.currency,
        amount=cart.amount,
        price_label=cart.label(),
        transaction_id=authorisation.transaction_id,
        reference=authorisation.reference,
        autonomous=authorisation.autonomous,
        enrolled_key=enrolled,
        detail=settlement.detail,
    )


def _host(url: str | None) -> str:
    """The site a page came from, which is the only seller identity a run knows."""
    return _site(url) or "unknown merchant"


def _site(url: str | None) -> str:
    """A page's host and port, without the credentials some addresses carry.

    Read with :func:`~urllib.parse.urlsplit` rather than by counting slashes:
    ``https://user:pw@shop.com/p`` split on ``/`` hands back ``user:pw@shop.com``,
    which became the merchant on the surface a person approves, in a *signed* payload,
    and in a receipt that is logged and handed to a browser.

    The port stays, being part of where a site is, and so does the case -- folding it
    would be this function deciding a merchant's identity. ``""`` for an address with
    no host to read: nothing at all, one with no scheme, and one malformed enough to
    make ``urlsplit`` raise, as an unclosed IPv6 bracket does.
    """
    if not url:
        return ""
    try:
        netloc = urlsplit(url).netloc
    except ValueError:
        return ""
    # Everything up to the last "@" is userinfo; a host cannot contain one.
    return netloc.rpartition("@")[2]
