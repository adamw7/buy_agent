"""Paying for a product the run already found, once somebody has said so (ADR-0009,
ADR-0046, ADR-0006, ADR-0017, ADR-0043)."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from pydantic import BaseModel

from buy_agent import mandates
from buy_agent.models import Product, comparable_price, dominant_currency
from buy_agent.money import amount_label, minor_units, placeable

if TYPE_CHECKING:
    from buy_agent.config import AgentConfig

logger = logging.getLogger(__name__)

#: What a payment instrument is called when the credential provider has not named one.
DEFAULT_INSTRUMENT = "default"


class PaymentError(Exception):
    """A purchase that did not happen, and why (ADR-0009, ADR-0046, ADR-0033)."""

    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


class RailUnreachableError(PaymentError):
    """The counterparty could not be reached, or answered with nothing an answer can be
    read out of -- HTML where JSON was asked for, an array rather than an object, a
    checkout with no signed token in it. The line is drawn at whose failure it is rather
    than at the transport: all of those are the far end's, so the API answers 502 and
    the shopper is not sent off to correct a form with nothing wrong with it. A
    counterparty that understood the request and declined it is the other thing, and
    stays a plain :class:`PaymentError`."""


@dataclass(frozen=True, slots=True)
class Settlement:
    """What a rail says came of presenting the mandates."""

    paid: bool
    detail: str


class Cart(BaseModel):
    """One product, priced, as the thing a mandate can be signed for (ADR-0046)."""

    title: str
    price: float
    currency: str
    amount: int
    merchant: str
    url: str
    item_id: str
    instrument: str = DEFAULT_INSTRUMENT

    def merchant_payload(self) -> dict[str, str]:
        """The merchant as AP2 names one: an id, a name and where it lives."""
        host = _site(self.url) or self.url
        return {"id": host, "name": self.merchant, "website": f"https://{host}"}

    def label(self) -> str:
        """The price as a person reads it -- the wording Python owns, not the page."""
        return amount_label(self.price, self.currency)


class Receipt(BaseModel):
    """What came of a payment, in the shape both front doors report it (ADR-0046)."""

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
    #: Whether an open mandate authorised this rather than a person approving the cart.
    autonomous: bool
    #: Whether the signature was made with an enrolled key or one this process invented.
    enrolled_key: bool
    detail: str


def _check(product: Product, currency: str | None) -> tuple[float, str]:
    """The price and the currency this product may be paid in, or a refusal (ADR-0043,
    ADR-0039)."""
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
    if placeable(currency) is None:
        # The scale the vote landed on is whatever the pages spelled, which is not
        # always a currency: "¥" is the yen's sign and the yuan's alike (ADR-0054), and
        # a small model hands back whatever else it read. Ranking has an answer for a
        # figure it cannot place -- NEUTRAL -- and paying is the opposite rule: an
        # amount nobody can place is not one to send, and sent anyway it would be
        # counted in hundredths of a unit nothing says are hundredths.
        raise PaymentError(
            f"{product.name} is priced in {currency}, which names no currency this can "
            f"count in. Nothing is converted and no scale is guessed, so that is a "
            f"figure and not an amount to send.",
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


def terms_for(
    product: Product, currency: str | None
) -> tuple[tuple[float, str] | None, str | None]:
    """What a cart for this product would be worth, and why there is none if there is not
    (ADR-0033, ADR-0043)."""
    try:
        return _check(product, currency), None
    except PaymentError as exc:
        return None, str(exc)


def merchant_for(product: Product) -> str:
    """Who a payment for this product would go to, as the cart will name them (ADR-0046)."""
    return product.seller or _host(product.url)


def cart_for(product: Product, products: Sequence[Product], config: AgentConfig) -> Cart:
    """The cart for one product of a finished run."""
    currency = dominant_currency(products, config.currency or None)
    price, currency = _check(product, currency)

    limit = config.spend_limit
    if limit is not None and price > limit:
        raise PaymentError(
            f"{product.name} costs {price:,.2f} {currency}, over the "
            f"{limit:,.2f} {currency} spend limit.",
            field="spend_limit",
        )
    try:
        amount = minor_units(price, currency)
    # ``money`` knows what an amount is and nothing about who is being paid, so the
    # refusal is translated here rather than raised there -- and it names ``products``
    # on the way past, which is the box the form marks (ADR-0033).
    except ValueError as exc:
        raise PaymentError(str(exc), field="products") from exc
    return Cart(
        title=product.name,
        price=price,
        currency=currency,
        amount=amount,
        merchant=merchant_for(product),
        url=product.url or "",
        item_id=product.dedup_key.replace(" ", "-")[:120],
    )


def unattended() -> bool:
    """Is there a pre-signed open mandate to buy on, or must a person be asked?"""
    try:
        return mandates.open_mandate() is not None
    except mandates.MandateError as exc:
        raise PaymentError(str(exc)) from exc


def pay_for(cart: Cart, config: AgentConfig) -> Receipt:
    """Authorise this cart and present it to the rail, returning what came back (ADR-0046).
    """
    rail = config.rail_used
    try:
        key, enrolled = mandates.load_key(required=rail.needs_key)
        signed, nonce = rail.checkout(cart, config)
        authorisation = mandates.authorise(cart, signed, key=key, nonce=nonce)
    except rail.transport_errors as exc:
        # Both calls to the rail translate the same way: an endpoint down while being
        # asked for a price is one down while being paid.
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
    """A page's host and port, without the credentials some addresses carry."""
    if not url:
        return ""
    try:
        netloc = urlsplit(url).netloc
    except ValueError:
        return ""
    # Everything up to the last "@" is userinfo; a host cannot contain one.
    return netloc.rpartition("@")[2]
