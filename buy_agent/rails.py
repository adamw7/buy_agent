"""Who the agent actually pays through: one row per rail, and nothing else (ADR-0046)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

from buy_agent import mandates
from buy_agent.payment import Cart, PaymentError, RailUnreachableError, Settlement

if TYPE_CHECKING:
    from collections.abc import Callable

    from buy_agent.config import AgentConfig
    from buy_agent.mandates import Authorisation, SignedCheckout

#: How long to wait on a counterparty.
_TIMEOUT = 30.0

#: The order id the dry run stamps on the checkout it signs itself.
_DRY_RUN_ORDER = "dry-run-order"


@dataclass(frozen=True, slots=True)
class Rail:
    """One way of paying: where it is, and how it is spoken to."""

    name: str
    label: str
    endpoint: str
    needs_endpoint: bool
    needs_key: bool
    moves_money: bool
    checkout: Callable[[Cart, AgentConfig], tuple[SignedCheckout, str]]
    settle: Callable[[Cart, Authorisation, AgentConfig], Settlement]
    #: What "the counterparty is not there" looks like from this row's own client, as the
    #: ``except`` clause that catches it binds it. ``Exception`` and not ``BaseException``: every
    #: class any row names is one, and a wider declaration is what left the ``hint``
    #: beside it handed a value its own signature refuses.
    transport_errors: tuple[type[Exception], ...]
    hint: Callable[[AgentConfig, Exception], str]


def _dry_run_checkout(cart: Cart, config: AgentConfig) -> tuple[SignedCheckout, str]:
    """Sign the checkout as the merchant, because here we are also the merchant."""
    del config
    document = mandates.checkout_document(cart, order_id=_DRY_RUN_ORDER)
    signed = mandates.sign_checkout(document, mandates.generate_key("dry-run-merchant"))
    return signed, mandates.challenge()


def _dry_run_settle(
    cart: Cart, authorisation: Authorisation, config: AgentConfig
) -> Settlement:
    """Report what would have happened, and charge nobody."""
    del authorisation, config
    return Settlement(
        paid=False,
        detail=(
            f"Nothing was charged: the dry-run rail signed and verified an "
            f"authorisation for {cart.label()} and stopped there. Pay through a "
            f"rail that moves money to present it."
        ),
    )


def _post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    """One JSON call to a counterparty, with the answer read as JSON.

    An answer that is not one is the counterparty's failure and not the request's, so it
    is a :class:`RailUnreachableError` beside the connection that was refused: the far
    end being up is no answer at all if what it sent back cannot be read, and 400 would
    send a shopper off to correct a form with nothing wrong with it.
    """
    response = httpx.post(url, json=payload, timeout=_TIMEOUT)
    response.raise_for_status()
    try:
        answer = response.json()
    except ValueError as exc:
        raise RailUnreachableError(
            f"{url} answered with something that is not JSON ({exc})."
        ) from exc
    if not isinstance(answer, dict):
        raise RailUnreachableError(f"{url} answered with {type(answer).__name__}, not an object.")
    return answer


def _http_checkout(cart: Cart, config: AgentConfig) -> tuple[SignedCheckout, str]:
    """Ask the merchant to price this cart and sign the result."""
    document = mandates.checkout_document(cart, order_id="")
    answer = _post(f"{config.merchant_url}/checkout", {"checkout": document})
    token = answer.get("checkout_jwt")
    if not isinstance(token, str) or not token:
        # Readable, and still not an answer to what was asked -- so the same failure as
        # an unreadable one rather than anything the shopper can put right.
        raise RailUnreachableError(
            f"{config.merchant_url} did not return a signed checkout "
            f"(no 'checkout_jwt' in its answer), so there is no price to authorise."
        )
    # A counterparty that issues its own challenge gets to; one that does not is given
    # ours, so the presentation is still bound to a single use.
    nonce = answer.get("nonce")
    return (
        mandates.SignedCheckout(jwt=token, hash=mandates.checkout_hash(token)),
        nonce if isinstance(nonce, str) and nonce else mandates.challenge(),
    )


def _http_settle(cart: Cart, authorisation: Authorisation, config: AgentConfig) -> Settlement:
    """Present both mandates and report what the far end made of them."""
    answer = _post(
        f"{config.merchant_url}/payment",
        {
            "transaction_id": authorisation.transaction_id,
            "checkout_mandate": authorisation.checkout,
            "payment_mandate": authorisation.payment,
        },
    )
    if not answer.get("paid"):
        # Deliberately the plain failure: a counterparty that understood the request and
        # declined it is answering about the request, which is what 400 is for.
        raise PaymentError(
            f"{config.merchant_url} did not complete the payment for {cart.label()}"
            f"{_because(answer)}."
        )
    return Settlement(paid=True, detail=str(answer.get("detail") or "Paid."))


def _because(answer: dict[str, Any]) -> str:
    """The far end's own reason, where it gave one. Its words, not ours."""
    detail = str(answer.get("detail") or "").strip()
    return f": {detail}" if detail else ""


def _http_hint(config: AgentConfig, exc: Exception) -> str:
    """Nothing answered, so the endpoint itself is what is missing."""
    return (
        f"Could not reach the payment endpoint at {config.merchant_url} ({exc}). "
        f"Check the address, or sign without paying by paying through {DRY_RUN.label}."
    )


DRY_RUN = Rail(
    name="dry-run",
    label="Dry run",
    endpoint="",
    needs_endpoint=False,
    needs_key=False,
    moves_money=False,
    checkout=_dry_run_checkout,
    settle=_dry_run_settle,
    # Nothing leaves the process, so nothing can fail in transit.
    transport_errors=(),
    hint=lambda config, exc: str(exc),
)

HTTP = Rail(
    name="http",
    label="HTTP endpoint",
    endpoint=os.getenv("BUY_AGENT_MERCHANT_URL", ""),
    needs_endpoint=True,
    needs_key=True,
    moves_money=True,
    checkout=_http_checkout,
    settle=_http_settle,
    # ``HTTPError`` is httpx's root: a refused connection, a timeout, and the statuses
    # ``raise_for_status`` turns into one.
    transport_errors=(httpx.HTTPError, OSError),
    hint=_http_hint,
)

#: Every rail, by the name the CLI, the API and ``$BUY_AGENT_RAIL`` use (ADR-0046).
RAILS: dict[str, Rail] = {rail.name: rail for rail in (DRY_RUN, HTTP)}


def rail_for(name: str) -> Rail:
    """The rail called ``name``."""
    try:
        return RAILS[name]
    except KeyError:
        raise ValueError(
            f"Unknown payment rail {name!r}; expected one of {', '.join(RAILS)}."
        ) from None


def rail_options() -> list[dict[str, object]]:
    """Every rail a payment can be pointed at, as the form's picker needs it."""
    return [
        {
            "name": rail.name,
            "label": rail.label,
            "endpoint": rail.endpoint,
            "needs_endpoint": rail.needs_endpoint,
            "moves_money": rail.moves_money,
        }
        for rail in RAILS.values()
    ]
