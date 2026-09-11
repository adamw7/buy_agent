"""The AP2 seam: a cart in, a signed mandate chain out, and the check back.

The **only** module that imports ``ap2``, the way :mod:`buy_agent.chat` is the
only one that talks to a model. Everything above deals in carts and receipts;
everything AP2 calls a mandate, a disclosure, an SD-JWT or a ``vct`` stops here.

Two shapes of authorisation, which is the whole of AP2's two modes:

* **Human present.** The shopper looked at this exact cart, so the closed Checkout
  and Payment Mandates are signed directly by the surface that asked -- a root
  SD-JWT apiece, no delegation.
* **Human not present.** The shopper signed an *open* mandate earlier, carrying
  constraints (an amount range, the payees allowed, an expiry), and the agent
  closes it: a two-hop chain, the open mandate as issued plus a closed Payment
  Mandate signed by the agent's key, which the open mandate's ``cnf`` delegates
  to. The constraints are checked here by the same evaluator a credential
  provider would run, so a cart outside them is refused before anything is sent.

What binds the two mandates is not a field either invented: the Payment Mandate's
``transaction_id`` **is** the Checkout Mandate's ``checkout_hash``, the base64url
SHA-256 of the merchant's signed checkout JWT. So a Payment Mandate cannot be
paired with another cart's checkout. The ``nonce`` is a different thing -- the
counterparty's own challenge against replay -- and comes from the rail.

The ``ap2`` import is deferred to :func:`_sdk`: the SDK is an optional install and
this module is reached from :mod:`buy_agent.rails`, which the config imports on
every run, paying or not. A missing SDK has to be a sentence naming one command,
not an ``ImportError`` out of ``--help``.
"""

# Said once rather than on each of the eleven lines below: every import of the SDK
# here is deferred into the function that needs it, which is what the paragraph
# above is about.
# pylint: disable=import-outside-toplevel

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover -- import-time typing only
    from buy_agent.rails import Cart

#: Where the agent's own signing key lives. A path and not a flag, for the reason
#: ``$BUY_AGENT_CACHE_DIR`` is one -- and this one signs payments.
KEY_PATH = "BUY_AGENT_AP2_KEY"

#: Where a pre-signed open mandate lives. Its presence is what turns on the
#: autonomous mode -- the protocol says the open mandate *is* the authority, so
#: there is no second switch, which would only fail without the file anyway.
MANDATE_PATH = "BUY_AGENT_AP2_MANDATE"

#: What to type when the SDK is not installed. One line, so it can be pasted;
#: ``--no-deps`` is load-bearing and the requirements file says why.
INSTALL = (
    "pip install -r requirements-ap2-deps.txt && "
    "pip install --no-deps -r requirements-ap2.txt"
)

#: How long a mandate signed here stays valid. Minutes: long enough for a slow
#: merchant, short enough that a chain left in a log is not a bearer token for
#: the rest of the afternoon.
TTL_SECONDS = 600

#: Who each mandate is signed for. AP2 binds a presentation to its audience, so
#: the Payment Mandate meant for the credential provider cannot be replayed at
#: the merchant, or the other way round.
MERCHANT_AUDIENCE = "merchant"
CREDENTIAL_PROVIDER_AUDIENCE = "credential-provider"


class MandateError(Exception):
    """Anything AP2-shaped that did not work: no SDK, no key, no verification.

    One class rather than one per cause, for the reason
    :class:`~buy_agent.agent.ModelUnavailableError` is one: to the shopper these are
    all "the authorisation could not be made", and only the sentence differs.
    """


@dataclass(frozen=True, slots=True)
class SignedCheckout:
    """A merchant's signed quote for a cart, and the hash both mandates bind to.

    ``jwt`` is the merchant's own token -- AP2 requires the Checkout Mandate to carry
    it and bind to it by hash, which makes "the price I approved" and "the price you
    charged" one claim rather than two.
    """

    jwt: str
    hash: str


@dataclass(frozen=True, slots=True)
class Authorisation:
    """What the agent presents to be allowed to pay, once.

    Attributes:
        checkout: The Checkout Mandate, for the merchant.
        payment: The Payment Mandate, for the credential provider. A single token
            when a person approved the cart, a two-hop chain when an open mandate did.
        reference: The SHA-256 of the closed leaf JWT, which is what a receipt points
            back at, stable across delegation depth and disclosure choices.
        transaction_id: The checkout hash both mandates carry, tying them to each
            other and to the merchant's price.
        autonomous: Whether an open mandate authorised this rather than a person
            looking at the cart. Reported, the two not being the same promise.
    """

    checkout: str
    payment: str
    reference: str
    transaction_id: str
    autonomous: bool


def _sdk() -> Any:
    """The AP2 SDK, imported now rather than at module import.

    Raises:
        MandateError: naming the one command that installs it.
    """
    try:
        # Two of the three are imported for whether they import at all, which is
        # the question being asked; the third is what the answer is read off.
        import ap2.sdk.jwt_helper
        import ap2.sdk.mandate
        import ap2.sdk.utils

        return ap2.sdk
    except ImportError as exc:
        raise MandateError(_missing(exc)) from exc


def _jwk_class() -> Any:
    """jwcrypto's key type, imported through the same translation the SDK is.

    Its own helper because the signing stack is reached from four functions that do
    not otherwise touch the SDK, and an ``ImportError`` escaping one is a traceback
    where every other missing piece is a sentence. A half-installed stack --
    ``cryptography`` present, its ``cffi`` not -- fails exactly here.
    """
    try:
        from jwcrypto.jwk import JWK  # part of the deferred SDK stack
    except ImportError as exc:
        raise MandateError(_missing(exc)) from exc
    return JWK


def _missing(exc: ImportError) -> str:
    """What to say when part of the signing stack will not import.

    Names the module that actually failed rather than only "the SDK": installed with
    ``--no-deps`` over the wrong file, ``cryptography`` arrives without the ``cffi``
    it is built on, and "the AP2 SDK is not installed" then sends somebody to re-run
    the command that just broke it.
    """
    # ``name`` is what a ``ModuleNotFoundError`` carries and a bare
    # ``ImportError`` does not, so the sentence still reads without one.
    missing = repr(exc.name) if exc.name else "part of it"
    return (
        f"Paying needs the AP2 SDK and what it imports, and {missing} is not "
        f"there. Install them with:  {INSTALL}"
    )


def available() -> bool:
    """Is the SDK installed? Asked by both front doors, which say so up front."""
    try:
        _sdk()
    except MandateError:
        return False
    return True


def _jwk(private_key: Any, kid: str) -> Any:
    """A jwcrypto key carrying a key id, which every signature here is traced by."""
    JWK = _jwk_class()  # a class, and named as the SDK names it

    material = json.loads(JWK.from_pyca(private_key).export())
    material["kid"] = kid
    return JWK(**material)


def generate_key(kid: str = "agent-ephemeral") -> Any:
    """A fresh P-256 key, living exactly as long as this process does."""
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
    except ImportError as exc:
        raise MandateError(_missing(exc)) from exc
    return _jwk(ec.generate_private_key(ec.SECP256R1()), kid)


def load_key(*, required: bool) -> tuple[Any, bool]:
    """The agent's signing key, and whether it came off disk.

    ``required`` is the difference between the two rails. A rail that moves money must
    sign with a key somebody enrolled, an ephemeral one authorising nothing a
    counterparty could trust. The dry run has no counterparty, so it generates one,
    says so, and writes a demonstration rather than a credential.

    Returns:
        The key, and True if it was read from ``$BUY_AGENT_AP2_KEY``.

    Raises:
        MandateError: if the variable is unset and a key was required, or names a file
            that is not a private key this can sign with.
    """
    _sdk()  # jwcrypto is the SDK's own stack: fail with its sentence, not an ImportError
    JWK = _jwk_class()  # a class, and named as the SDK names it

    location = os.getenv(KEY_PATH, "").strip()
    if not location:
        if required:
            raise MandateError(
                f"No signing key: set ${KEY_PATH} to an EC P-256 private key in PEM form. "
                f"Make one with:  openssl ecparam -genkey -name prime256v1 -noout "
                f"-out agent-key.pem"
            )
        return generate_key(), False

    try:
        key = JWK.from_pem(Path(location).read_bytes())
    except (OSError, ValueError) as exc:
        raise MandateError(f"Could not read the signing key at {location} ({exc}).") from exc
    if not key.has_private:
        raise MandateError(
            f"The key at {location} is a public key; signing needs the private one."
        )
    key["kid"] = "agent"
    return key, True


def challenge() -> str:
    """A counterparty's nonce, for a rail that has no way to issue one itself.

    From :mod:`secrets`: it exists so a presentation cannot be replayed, and a
    predictable one would not.
    """
    return secrets.token_urlsafe(24)


def checkout_document(cart: Cart, *, order_id: str) -> dict[str, Any]:
    """The checkout a merchant signs, as AP2's UCP-shaped payload.

    Assembled here rather than in a rail because both need the same document: the dry
    run signs it itself, the HTTP rail sends it to a merchant. Amounts are minor units
    throughout, which is what the schema means and why no float goes on the wire.
    """
    totals = [
        {"type": "subtotal", "amount": cart.amount},
        {"type": "total", "amount": cart.amount},
    ]
    return {
        "id": order_id,
        "merchant": cart.merchant_payload(),
        "line_items": [
            {
                "id": "line_1",
                "item": {"id": cart.item_id, "title": cart.title, "price": cart.amount},
                "quantity": 1,
                "totals": totals,
            }
        ],
        "status": "ready_for_complete",
        "currency": cart.currency,
        "totals": totals,
        "links": [{"type": "product", "url": cart.url}],
    }


def sign_checkout(document: dict[str, Any], key: Any) -> SignedCheckout:
    """Sign a checkout as the merchant would, and hash it as a mandate binds it.

    Used by the dry run, which plays every role. A real merchant signs its own and
    this side only hashes what came back, which is why :func:`checkout_hash` is
    reachable on its own.
    """
    sdk = _sdk()
    token = sdk.jwt_helper.create_jwt({"alg": "ES256", "typ": "JWT"}, document, key)
    return SignedCheckout(jwt=token, hash=checkout_hash(token))


def checkout_hash(token: str) -> str:
    """The base64url SHA-256 of a checkout JWT: what both mandates bind to."""
    return _sdk().utils.compute_sha256_b64url(token)


def open_mandate() -> tuple[str, Any] | None:
    """The pre-signed open mandate and its issuer's key, if one is configured.

    The file is a small JSON document -- ``{"mandate": "<SD-JWT>", "issuer_jwk":
    {...}}`` -- because verifying an open mandate needs the key it was issued under
    and a bare token carries no way to find one. AP2 leaves how a verifier reaches an
    issuer key to the deployment; a file naming both is the smallest honest thing.

    Raises:
        MandateError: if the variable names a file that is not that document. Read as
            absent, a malformed one would quietly drop an unattended run back to
            waiting for a person who is not there.
    """
    location = os.getenv(MANDATE_PATH, "").strip()
    if not location:
        return None

    try:
        document = json.loads(Path(location).read_text(encoding="utf-8"))
        token = str(document["mandate"])
        # Asked for after the file has been read: reading JSON needs none of the
        # signing stack, and asked first it answered a malformed mandate with the
        # sentence about installing the SDK -- pip, over a path that is wrong.
        issuer = _jwk_class()(**document["issuer_jwk"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise MandateError(
            f"Could not read the open mandate at {location} ({exc}). It is a JSON "
            f'document holding {{"mandate": "<SD-JWT>", "issuer_jwk": {{...}}}}.'
        ) from exc
    return token, issuer


def authorise(cart: Cart, checkout: SignedCheckout, *, key: Any, nonce: str) -> Authorisation:
    """Sign what this cart needs in order to be paid for, in whichever mode applies.

    An open mandate at ``$BUY_AGENT_AP2_MANDATE`` makes this the autonomous mode, and
    the cart is held to that mandate's constraints before anything leaves the process.
    Without one, the caller has already asked a person.

    The **Payment** Mandate is the whole of the difference: a root SD-JWT where a
    person approved this cart, the second hop of the open mandate's chain where one
    authorised it. Everything after is the same either way -- the Checkout Mandate is
    this side's signature on the merchant's price whoever agreed to it, and the
    reference, the transaction id and the binding are the protocol's, not the mode's.

    Raises:
        MandateError: if an open mandate is configured and does not authorise this
            cart, naming the constraint it broke.
    """
    # Asked before the SDK is, so a mandate file that will not read fails with
    # its own sentence rather than with the one about installing ``ap2``.
    configured = open_mandate()
    sdk = _sdk()
    now = int(time.time())
    client = sdk.mandate.MandateClient()
    payload = _payment_mandate(cart, checkout, now)

    if configured is None:
        # Human present: the shopper looked at this exact cart, so the closed
        # Payment Mandate is signed directly by the surface that asked.
        payment = client.create(payloads=[payload], issuer_key=key)
    else:
        # Human not present: close the open mandate the shopper signed, within
        # it. The constraints go through the SDK's own evaluator -- the one a
        # credential provider runs -- rather than a second reading written here.
        open_token, issuer = configured
        payment = client.present(
            holder_key=key,
            mandate_token=open_token,
            payloads=[payload],
            nonce=nonce,
            aud=CREDENTIAL_PROVIDER_AUDIENCE,
        )
        violations = verify(
            payment,
            issuer=issuer,
            audience=CREDENTIAL_PROVIDER_AUDIENCE,
            nonce=nonce,
            transaction_id=checkout.hash,
        )
        if violations:
            raise MandateError(
                "The open mandate does not authorise this purchase: " + "; ".join(violations)
            )

    return Authorisation(
        checkout=client.create(payloads=[_checkout_mandate(checkout, now)], issuer_key=key),
        payment=payment,
        reference=sdk.utils.compute_sha256_b64url(client.get_closed_mandate_jwt(payment)),
        transaction_id=checkout.hash,
        autonomous=configured is not None,
    )


def _payment_mandate(cart: Cart, checkout: SignedCheckout, now: int) -> Any:
    """The closed Payment Mandate for this cart.

    ``transaction_id`` is the checkout hash and not an identifier of our own: that is
    how the specification binds a payment to the checkout it pays for, and an invented
    one would leave the two halves free to describe different carts.
    """
    from ap2.sdk.generated.payment_mandate import PaymentMandate
    from ap2.sdk.generated.types.amount import Amount
    from ap2.sdk.generated.types.merchant import Merchant
    from ap2.sdk.generated.types.payment_instrument import PaymentInstrument

    return PaymentMandate(
        transaction_id=checkout.hash,
        payee=Merchant(**cart.merchant_payload()),
        payment_amount=Amount(amount=cart.amount, currency=cart.currency),
        # The instrument is a reference and never a number: an agent carrying the
        # digits would be the stored card AP2 exists to do without.
        payment_instrument=PaymentInstrument(
            id=cart.instrument, type="card", description="Held by the credential provider"
        ),
        iat=now,
        exp=now + TTL_SECONDS,
    )


def _checkout_mandate(checkout: SignedCheckout, now: int) -> Any:
    """The closed Checkout Mandate: this cart, at this price, from this merchant."""
    from ap2.sdk.generated.checkout_mandate import CheckoutMandate

    return CheckoutMandate(
        checkout_jwt=checkout.jwt,
        checkout_hash=checkout.hash,
        iat=now,
        exp=now + TTL_SECONDS,
    )


def verify(
    chain: str, *, issuer: Any, audience: str, nonce: str, transaction_id: str
) -> list[str]:
    """Check a two-hop payment chain and report what it breaks, if anything.

    Returns:
        The constraint violations, empty for a chain that is authorised. A list rather
        than a raise, because what a violation is worth is the caller's.

    Raises:
        MandateError: if the chain does not verify at all -- a bad signature, a hop
            signed by the wrong key, an audience it was not meant for. That is not a
            constraint being broken; it is not a mandate.
    """
    sdk = _sdk()
    from ap2.sdk.payment_mandate_chain import PaymentMandateChain

    try:
        payloads = sdk.mandate.MandateClient().verify(
            token=chain,
            key_or_provider=lambda _token: issuer,
            expected_aud=audience,
            expected_nonce=nonce,
        )
        parsed = PaymentMandateChain.parse(payloads)
    except Exception as exc:  # every failure here is the one thing
        raise MandateError(f"The mandate chain did not verify ({exc}).") from exc
    return parsed.verify(expected_transaction_id=transaction_id)
