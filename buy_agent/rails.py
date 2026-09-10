"""Who the agent actually pays through: one row per rail, and nothing else.

The same shape :mod:`buy_agent.providers` gives a model server, for the same
reason. AP2 secures *what* is authorised and says nothing about who you send it to
-- "the exact details of the Commerce Protocol are outside the scope of AP2" -- so
the counterparty is a choice, and one row per option is a choice a third party can
be added to without an ``if`` anywhere above this module. A :class:`Rail` answers
where it **listens**, whether it needs an **address** and an **enrolled key**, how
a cart becomes a **merchant-signed checkout**, how an authorisation is
**presented**, which transport failures mean "not there", and **how one is
phrased**.

Two rails ship, and the default moves no money:

* ``dry-run`` plays every role. It signs the checkout itself, so the mandates are
  signed against a real hash and verify like any others, and then reports that
  nothing was charged. That is what makes the feature safe to turn on.
* ``http`` speaks to whatever AP2-speaking endpoint the operator names --
  ``POST {url}/checkout`` for the merchant's signed quote, ``POST {url}/payment``
  to present the mandates. No merchant, wallet, processor or cloud is named
  anywhere; the address is the whole of the integration.

Nothing is imported from :mod:`buy_agent.config`: a config is what this module is
handed, and ``AgentConfig.rail_used`` is the one place a rail's name becomes
behaviour. :class:`Cart` and :class:`~buy_agent.payment.Settlement` live beside
:mod:`buy_agent.payment`'s other domain types rather than here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

import httpx

from buy_agent import mandates
from buy_agent.payment import Cart, PaymentError, Settlement

if TYPE_CHECKING:
    from buy_agent.config import AgentConfig
    from buy_agent.mandates import Authorisation, SignedCheckout

#: How long to wait on a counterparty. Longer than a page fetch and far longer
#: than a model listing: a payment processor is entitled to think, and a request
#: this side gave up on may still have been acted on at the other end -- which is
#: the one timeout in this project it is worth being patient about.
_TIMEOUT = 30.0

#: The order id the dry run stamps on the checkout it signs itself. Fixed rather
#: than random: nothing downstream of a dry run refers to it, and a stable one
#: makes two runs of the same cart diff cleanly.
_DRY_RUN_ORDER = "dry-run-order"


@dataclass(frozen=True, slots=True)
class Rail:
    """One way of paying: where it is, and how it is spoken to.

    Attributes:
        name: What the CLI, the API and ``$BUY_AGENT_RAIL`` call it.
        label: What a person reads -- "Dry run", "HTTP endpoint".
        endpoint: Where it listens when a config names no address.
        needs_endpoint: Whether an empty address is a usage error. False for the dry
            run, which has nowhere to be.
        needs_key: Whether signing requires a key somebody enrolled. False for the dry
            run, which has no counterparty to have trusted one.
        moves_money: Whether a settlement here can actually charge anybody. Said on
            the row rather than worked out from the name, so both front doors can warn
            before a run rather than after one.
        checkout: Turns a cart into a merchant-signed checkout and the nonce the
            mandates are to be bound to.
        settle: Presents an authorisation and answers what came of it.
        transport_errors: The exceptions meaning the rail could not be reached.
        hint: Turns one of those into something the shopper can act on.
    """

    name: str
    label: str
    endpoint: str
    needs_endpoint: bool
    needs_key: bool
    moves_money: bool
    checkout: Callable[[Cart, AgentConfig], tuple[SignedCheckout, str]]
    settle: Callable[[Cart, Authorisation, AgentConfig], Settlement]
    transport_errors: tuple[type[BaseException], ...]
    hint: Callable[[AgentConfig, Exception], str]


def _dry_run_checkout(cart: Cart, config: AgentConfig) -> tuple[SignedCheckout, str]:
    """Sign the checkout as the merchant, because here we are also the merchant.

    A key generated for this call and thrown away with it: the point of the dry run is
    that the chain is real in shape -- a genuine hash of a genuine signed checkout,
    verifiable by the code a merchant would run -- while being a credential nobody
    could redeem.
    """
    del config
    document = mandates.checkout_document(cart, order_id=_DRY_RUN_ORDER)
    signed = mandates.sign_checkout(document, mandates.generate_key("dry-run-merchant"))
    return signed, mandates.challenge()


def _dry_run_settle(
    cart: Cart, authorisation: Authorisation, config: AgentConfig
) -> Settlement:
    """Report what would have happened, and charge nobody."""
    del config
    return Settlement(
        paid=False,
        detail=(
            f"Nothing was charged: the dry-run rail signed and verified an "
            f"authorisation for {cart.label()} and stopped there. Point --rail at a "
            f"real one to present it."
        ),
    )


def _post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    """One JSON call to a counterparty, with the answer read as JSON.

    A body that is not JSON is a :class:`~buy_agent.payment.PaymentError` and not a
    transport failure: something answered, so "could not reach it" would send the
    shopper to check a connection that is fine.
    """
    response = httpx.post(url, json=payload, timeout=_TIMEOUT)
    response.raise_for_status()
    try:
        answer = response.json()
    except ValueError as exc:
        raise PaymentError(f"{url} answered with something that is not JSON ({exc}).") from exc
    if not isinstance(answer, dict):
        raise PaymentError(f"{url} answered with {type(answer).__name__}, not an object.")
    return answer


def _http_checkout(cart: Cart, config: AgentConfig) -> tuple[SignedCheckout, str]:
    """Ask the merchant to price this cart and sign the result.

    The hash is computed from what came back rather than taken from the answer: it is
    what the mandates bind to, and a hash the counterparty chose would let it bind
    them to a document nobody here has seen.
    """
    document = mandates.checkout_document(cart, order_id="")
    answer = _post(f"{config.merchant_url}/checkout", {"checkout": document})
    token = answer.get("checkout_jwt")
    if not isinstance(token, str) or not token:
        raise PaymentError(
            f"{config.merchant_url} did not return a signed checkout "
            f"(no 'checkout_jwt' in its answer), so there is no price to authorise."
        )
    # A counterparty that issues its own challenge gets to; one that does not is
    # given ours, so the presentation is still bound to a single use.
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
        f"Check --merchant-url, or sign without paying with:  --rail {DRY_RUN.name}"
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
    # Nothing leaves the process, so nothing can fail in transit. An empty tuple
    # rather than a guess: ``except ()`` catches nothing, which is correct here
    # and is what ``pay_for`` will do with it.
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
    # ``HTTPError`` is httpx's root: a refused connection, a timeout, and the
    # statuses ``raise_for_status`` turns into one. ``OSError`` for the socket
    # failures that never reach httpx's own hierarchy.
    transport_errors=(httpx.HTTPError, OSError),
    hint=_http_hint,
)

#: Every rail, by the name the CLI, the API and ``$BUY_AGENT_RAIL`` use. The one
#: table -- each row carries both where a rail is and how it is spoken to, so a
#: third is a row here and nothing anywhere else.
RAILS: dict[str, Rail] = {rail.name: rail for rail in (DRY_RUN, HTTP)}


def rail_for(name: str) -> Rail:
    """The rail called ``name``.

    Raises:
        ValueError: naming the ones that do exist, since this is reached from a
            CLI flag, a form field and an environment variable alike.
    """
    try:
        return RAILS[name]
    except KeyError:
        raise ValueError(
            f"Unknown payment rail {name!r}; expected one of {', '.join(RAILS)}."
        ) from None


def rail_options() -> list[dict[str, object]]:
    """Every rail a payment can be pointed at, as the form's picker needs it.

    Carries each one's address and whether it needs one, so choosing a rail in the
    browser fills the field in and marks it where it is required.
    """
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
