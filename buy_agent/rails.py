"""The payment rails, one row each (ADR-0046)."""

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

_TIMEOUT = 30.0
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
    transport_errors: tuple[type[Exception], ...]
    hint: Callable[[AgentConfig, Exception], str]


def _dry_run_checkout(cart: Cart, config: AgentConfig) -> tuple[SignedCheckout, str]:
    """Sign the checkout ourselves: the dry run is also the merchant."""
    del config
    document = mandates.checkout_document(cart, order_id=_DRY_RUN_ORDER)
    signed = mandates.sign_checkout(document, mandates.generate_key("dry-run-merchant"))
    return signed, mandates.challenge()


def _dry_run_settle(cart: Cart, authorisation: Authorisation, config: AgentConfig) -> Settlement:
    """Charge nobody."""
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
    """One JSON call to a counterparty; an unreadable answer is its failure (502)."""
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
        raise RailUnreachableError(
            f"{config.merchant_url} did not return a signed checkout "
            f"(no 'checkout_jwt' in its answer), so there is no price to authorise."
        )
    # The counterparty's challenge if it sent one, else ours.
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
        # A decline is about the request: a plain 400.
        raise PaymentError(
            f"{config.merchant_url} did not complete the payment for {cart.label()}"
            f"{_because(answer)}."
        )
    return Settlement(paid=True, detail=str(answer.get("detail") or "Paid."))


def _because(answer: dict[str, Any]) -> str:
    detail = str(answer.get("detail") or "").strip()
    return f": {detail}" if detail else ""


def _http_hint(config: AgentConfig, exc: Exception) -> str:
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
    # Outside httpx's root: ``InvalidURL``, and ``UnicodeError`` for ``pay..example``.
    transport_errors=(httpx.HTTPError, httpx.InvalidURL, OSError, UnicodeError),
    hint=_http_hint,
)

#: By the name the CLI, the API and ``$BUY_AGENT_RAIL`` use (ADR-0046).
RAILS: dict[str, Rail] = {rail.name: rail for rail in (DRY_RUN, HTTP)}


def rail_for(name: str) -> Rail:
    try:
        return RAILS[name]
    except KeyError:
        raise ValueError(
            f"Unknown payment rail {name!r}; expected one of {', '.join(RAILS)}."
        ) from None


#: What the form's picker is told about each rail.
_OFFERED = ("name", "label", "endpoint", "needs_endpoint", "moves_money")


def rail_options() -> list[dict[str, object]]:
    return [{key: getattr(rail, key) for key in _OFFERED} for rail in RAILS.values()]
