"""The AP2 seam: a cart in, a signed mandate chain out, and the check back (ADR-0046)."""

# The SDK is optional, so every import of it is deferred into the function needing it.
# pylint: disable=import-outside-toplevel

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from buy_agent.payment import Cart

#: Where the agent's own signing key lives (ADR-0046).
KEY_PATH = "BUY_AGENT_AP2_KEY"

#: Where a pre-signed open mandate lives.
MANDATE_PATH = "BUY_AGENT_AP2_MANDATE"

#: What to type when the SDK is not installed.
INSTALL = (
    "pip install -r requirements-ap2-deps.txt && "
    "pip install --no-deps -r requirements-ap2.txt"
)

#: How long a mandate signed here stays valid.
TTL_SECONDS = 600

#: Who each mandate is signed for.
MERCHANT_AUDIENCE = "merchant"
CREDENTIAL_PROVIDER_AUDIENCE = "credential-provider"


class MandateError(Exception):
    """Anything AP2-shaped that did not work: no SDK, no key, no verification."""


@dataclass(frozen=True, slots=True)
class SignedCheckout:
    """A merchant's signed quote for a cart, and the hash both mandates bind to."""

    jwt: str
    hash: str


@dataclass(frozen=True, slots=True)
class Authorisation:
    """What the agent presents to be allowed to pay, once."""

    checkout: str
    payment: str
    reference: str
    transaction_id: str
    autonomous: bool


def _sdk() -> Any:
    """The AP2 SDK, imported now rather than at module import."""
    try:
        # Importing them is the availability check.
        import ap2.sdk.jwt_helper
        import ap2.sdk.mandate
        import ap2.sdk.utils

        return ap2.sdk
    except ImportError as exc:
        raise MandateError(_missing(exc)) from exc


def _jwk_class() -> Any:
    """jwcrypto's key type, imported like the SDK."""
    try:
        from jwcrypto.jwk import JWK  # part of the deferred SDK stack
    except ImportError as exc:
        raise MandateError(_missing(exc)) from exc
    return JWK


def _missing(exc: ImportError) -> str:
    """What to say when part of the signing stack will not import."""
    # A bare ``ImportError`` may carry no ``name``.
    missing = repr(exc.name) if exc.name else "part of it"
    return (
        f"Paying needs the AP2 SDK and what it imports, and {missing} is not "
        f"there. Install them with:  {INSTALL}"
    )


def available() -> bool:
    """Whether the SDK is installed."""
    try:
        _sdk()
    except MandateError:
        return False
    return True


def _jwk(private_key: Any, kid: str) -> Any:
    """A jwcrypto key with a key id."""
    # Lower-case: pylint's ``invalid-name`` verdict on ``JWK`` depends on whether the
    # optional SDK is installed.
    jwk_class = _jwk_class()

    material = json.loads(jwk_class.from_pyca(private_key).export())
    material["kid"] = kid
    return jwk_class(**material)


def generate_key(kid: str = "agent-ephemeral") -> Any:
    """A fresh P-256 key for this process only."""
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
    except ImportError as exc:
        raise MandateError(_missing(exc)) from exc
    return _jwk(ec.generate_private_key(ec.SECP256R1()), kid)


def load_key(*, required: bool) -> tuple[Any, bool]:
    """The agent's signing key, and whether it came off disk."""
    _sdk()  # jwcrypto is the SDK's own stack: fail with its sentence, not an ImportError
    jwk_class = _jwk_class()

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
        key = jwk_class.from_pem(Path(location).read_bytes())
    except (OSError, ValueError) as exc:
        raise MandateError(f"Could not read the signing key at {location} ({exc}).") from exc
    if not key.has_private:
        raise MandateError(
            f"The key at {location} is a public key; signing needs the private one."
        )
    key["kid"] = "agent"
    return key, True


def challenge() -> str:
    """A counterparty's nonce, for a rail that has no way to issue one itself."""
    return secrets.token_urlsafe(24)


def checkout_document(cart: Cart, *, order_id: str) -> dict[str, Any]:
    """The checkout a merchant signs, as AP2's UCP-shaped payload."""
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
    """Sign a checkout as the merchant would, and hash it as a mandate binds it."""
    sdk = _sdk()
    token = sdk.jwt_helper.create_jwt({"alg": "ES256", "typ": "JWT"}, document, key)
    return SignedCheckout(jwt=token, hash=checkout_hash(token))


def checkout_hash(token: str) -> str:
    """The base64url SHA-256 of a checkout JWT: what both mandates bind to."""
    return _sdk().utils.compute_sha256_b64url(token)


def open_mandate() -> tuple[str, Any] | None:
    """The pre-signed open mandate and its issuer's key, if one is configured."""
    location = os.getenv(MANDATE_PATH, "").strip()
    if not location:
        return None

    try:
        document = json.loads(Path(location).read_text(encoding="utf-8"))
        token = str(document["mandate"])
        # After reading the file, so a bad file is not reported as a missing SDK.
        issuer = _jwk_class()(**document["issuer_jwk"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise MandateError(
            f"Could not read the open mandate at {location} ({exc}). It is a JSON "
            f'document holding {{"mandate": "<SD-JWT>", "issuer_jwk": {{...}}}}.'
        ) from exc
    return token, issuer


def authorise(cart: Cart, checkout: SignedCheckout, *, key: Any, nonce: str) -> Authorisation:
    """Sign what this cart needs to be paid for, in whichever mode applies."""
    # Before the SDK, so a bad mandate file reports itself.
    configured = open_mandate()
    sdk = _sdk()
    now = int(time.time())
    client = sdk.mandate.MandateClient()
    payload = _payment_mandate(cart, checkout, now)

    if configured is None:
        # Human present: sign the closed Payment Mandate directly.
        payment = client.create(payloads=[payload], issuer_key=key)
    else:
        # Human not present: close the open mandate the shopper signed, within it.
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
    """The closed Payment Mandate for this cart."""
    from ap2.sdk.generated.payment_mandate import PaymentMandate
    from ap2.sdk.generated.types.amount import Amount
    from ap2.sdk.generated.types.merchant import Merchant
    from ap2.sdk.generated.types.payment_instrument import PaymentInstrument

    return PaymentMandate(
        transaction_id=checkout.hash,
        payee=Merchant(**cart.merchant_payload()),
        payment_amount=Amount(amount=cart.amount, currency=cart.currency),
        # A reference, never card digits.
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
    """Check a two-hop payment chain and report what it breaks, if anything."""
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
