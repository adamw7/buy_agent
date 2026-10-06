"""The AP2 seam: keys, checkouts, and the two shapes of authorisation."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

import pytest

from buy_agent import mandates
from buy_agent.mandates import MandateError
from buy_agent.payment import Cart
from tests.conftest import enrolled_key, needs_ap2, open_mandate

if TYPE_CHECKING:
    from pathlib import Path


CART = Cart(
    title="Sony WH-1000XM5",
    price=329.99,
    currency="USD",
    amount=32999,
    merchant="AudioSite",
    url="https://audiosite.example/xm5",
    item_id="sony-wh-1000xm5",
)


def signed_checkout() -> mandates.SignedCheckout:
    """A checkout signed as the merchant, which is what the dry run does."""
    document = mandates.checkout_document(CART, order_id="order-1")
    return mandates.sign_checkout(document, mandates.generate_key("merchant"))


# -- the SDK, and doing without it ---------------------------------------------


@needs_ap2
def test_the_sdk_is_reported_as_available_when_it_imports() -> None:
    assert mandates.available() is True


def test_a_missing_sdk_is_one_command_and_not_an_import_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Paying is optional and so is what it needs, so the absence has to be a
    sentence somebody can act on rather than a traceback out of ``--help``."""
    import builtins

    real = builtins.__import__

    def refuse(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith("ap2"):
            raise ImportError("no module named ap2")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    monkeypatch.delitem(__import__("sys").modules, "ap2.sdk.mandate", raising=False)
    monkeypatch.delitem(__import__("sys").modules, "ap2.sdk", raising=False)
    monkeypatch.delitem(__import__("sys").modules, "ap2", raising=False)

    assert mandates.available() is False
    with pytest.raises(MandateError, match="requirements-ap2.txt"):
        mandates.load_key(required=False)


# -- keys ----------------------------------------------------------------------


@needs_ap2
def test_a_key_that_is_not_there_is_refused_by_its_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(mandates.KEY_PATH, str(tmp_path / "nothing.pem"))

    with pytest.raises(MandateError, match="Could not read the signing key"):
        mandates.load_key(required=True)


@needs_ap2
def test_a_public_key_is_refused_since_signing_needs_the_private_half(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    public = tmp_path / "public.pem"
    public.write_bytes(mandates.generate_key("agent").export_to_pem())
    monkeypatch.setenv(mandates.KEY_PATH, str(public))

    with pytest.raises(MandateError, match="public key"):
        mandates.load_key(required=True)


@needs_ap2
@pytest.mark.parametrize("kind", ["rsa", "p-384", "ed25519"])
def test_a_key_that_cannot_sign_es256_is_refused_before_anything_is_signed(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each reads as a private key, and then failed inside the SDK with a ``TypeError``
    or jwcrypto's own error, naming neither the key nor where it came from."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

    made = {
        "rsa": lambda: rsa.generate_private_key(public_exponent=65537, key_size=2048),
        "p-384": lambda: ec.generate_private_key(ec.SECP384R1()),
        "ed25519": ed25519.Ed25519PrivateKey.generate,
    }[kind]()
    path = tmp_path / "agent.pem"
    path.write_bytes(
        made.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    monkeypatch.setenv(mandates.KEY_PATH, str(path))

    with pytest.raises(MandateError, match="not an EC P-256 key"):
        mandates.load_key(required=True)


# -- the checkout --------------------------------------------------------------


def test_the_checkout_document_counts_in_minor_units_and_names_the_merchant() -> None:
    document = mandates.checkout_document(CART, order_id="order-1")

    assert document["currency"] == "USD"
    assert document["totals"] == [
        {"type": "subtotal", "amount": 32999},
        {"type": "total", "amount": 32999},
    ]
    assert document["line_items"][0]["item"]["price"] == 32999
    assert document["merchant"]["website"] == "https://audiosite.example"
    assert document["links"][0]["url"] == CART.url


@needs_ap2
def test_the_checkout_is_a_ucp_checkout_the_sdk_validates() -> None:
    """The document is hand-built rather than constructed through the model, so
    this is what says it is still the shape the schema describes."""
    from ap2.sdk.generated.types.checkout import Checkout

    Checkout.model_validate(mandates.checkout_document(CART, order_id="order-1"))


# -- human present -------------------------------------------------------------


# -- human not present ---------------------------------------------------------


@needs_ap2
def test_a_closed_open_mandate_is_checked_against_this_very_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The check before a chain is handed on. Handed ``None`` for any of these, the SDK
    checks nothing of the kind, so a chain closed for another purchase, nonce or
    audience would pass: what is asked is the point of asking."""
    agent, issuer = open_mandate(tmp_path, monkeypatch)
    checkout = signed_checkout()
    real = mandates.verify
    asked: list[dict[str, Any]] = []

    def verify(chain: str, **expected: Any) -> list[str]:
        asked.append(expected)
        return real(chain, **expected)

    monkeypatch.setattr(mandates, "verify", verify)

    mandates.authorise(CART, checkout, key=agent, nonce="n")

    assert asked == [
        {
            "issuer": asked[0]["issuer"],
            "audience": mandates.CREDENTIAL_PROVIDER_AUDIENCE,
            "nonce": "n",
            "transaction_id": checkout.hash,
        }
    ]
    assert asked[0]["issuer"].thumbprint() == issuer.thumbprint()


@needs_ap2
def test_an_open_mandate_cannot_be_closed_with_the_wrong_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``cnf`` names the one key allowed to close it, which is the whole of the
    delegation: another key signing on top is not a chain that verifies."""
    open_mandate(tmp_path, monkeypatch)

    with pytest.raises(MandateError, match="did not verify"):
        mandates.authorise(
            CART, signed_checkout(), key=mandates.generate_key("stranger"), nonce="n"
        )


def test_a_mandate_file_that_is_not_one_is_refused_rather_than_read_as_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read as "no mandate", a broken file would quietly drop an unattended run
    back to waiting for a person who is not there."""
    broken = tmp_path / "mandate.json"
    broken.write_text("{}", encoding="utf-8")
    monkeypatch.setenv(mandates.MANDATE_PATH, str(broken))

    with pytest.raises(MandateError, match="Could not read the open mandate"):
        mandates.open_mandate()


def test_a_mandate_file_that_is_missing_is_refused_by_its_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gone = tmp_path / "gone.json"
    monkeypatch.setenv(mandates.MANDATE_PATH, str(gone))

    with pytest.raises(MandateError) as refused:
        mandates.open_mandate()

    _names_the_file_and_why(refused.value, gone)


@needs_ap2
def test_an_issuer_key_the_signing_stack_cannot_use_is_refused_by_its_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """jwcrypto refuses key material with an error of its own, not a ``ValueError``,
    and it went past every handler to the shopper as an unexpected failure."""
    _agent, _issuer = open_mandate(tmp_path, monkeypatch)
    path = tmp_path / "mandate.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**document, "issuer_jwk": {"kty": "EC"}}), encoding="utf-8")

    with pytest.raises(MandateError) as refused:
        mandates.open_mandate()

    _names_the_file_and_why(refused.value, path)


def _names_the_file_and_why(refusal: MandateError, path: Path) -> None:
    """The sentence a shopper fixes the file by: which file, and what was wrong with it
    in the reader's own words. The path is asked for where the sentence puts it, since
    a missing file's own error repeats it."""
    said = str(refusal)
    assert said.startswith(f"Could not read the open mandate at {path} (")
    assert f"({refusal.__cause__})" in said


@needs_ap2
def test_an_open_mandate_that_is_no_sd_jwt_is_refused_as_the_mandate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The SDK refuses the token with a plain ``ValueError`` when the purchase is signed,
    well after the file was read."""
    agent, _issuer = open_mandate(tmp_path, monkeypatch)
    path = tmp_path / "mandate.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**document, "mandate": "not-a-token"}), encoding="utf-8")

    with pytest.raises(MandateError, match="open mandate .* could not be closed"):
        mandates.authorise(CART, signed_checkout(), key=agent, nonce="n")


# -- verification --------------------------------------------------------------


@needs_ap2
def test_a_chain_presented_to_the_wrong_audience_does_not_verify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AP2 binds a presentation to who it is for, so one meant for the credential
    provider cannot be replayed at the merchant."""
    agent, issuer = open_mandate(tmp_path, monkeypatch)
    authorisation = mandates.authorise(CART, signed_checkout(), key=agent, nonce="n")

    with pytest.raises(MandateError, match="did not verify"):
        mandates.verify(
            authorisation.payment,
            issuer=issuer,
            audience=mandates.MERCHANT_AUDIENCE,
            nonce="n",
            transaction_id=authorisation.transaction_id,
        )


@needs_ap2
def test_a_chain_replayed_with_another_nonce_does_not_verify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, issuer = open_mandate(tmp_path, monkeypatch)
    authorisation = mandates.authorise(CART, signed_checkout(), key=agent, nonce="first")

    with pytest.raises(MandateError, match="did not verify"):
        mandates.verify(
            authorisation.payment,
            issuer=issuer,
            audience=mandates.CREDENTIAL_PROVIDER_AUDIENCE,
            nonce="second",
            transaction_id=authorisation.transaction_id,
        )


# -- what a mandate is worth, and for how long ---------------------------------


@needs_ap2
def test_both_mandates_expire() -> None:
    """A chain is a credential: it authorises this purchase to whoever holds it."""
    from ap2.sdk.generated.checkout_mandate import CheckoutMandate
    from ap2.sdk.generated.payment_mandate import PaymentMandate
    from ap2.sdk.mandate import MandateClient

    key = mandates.generate_key("agent")
    before = int(time.time())

    authorisation = mandates.authorise(CART, signed_checkout(), key=key, nonce="n")

    client = MandateClient()
    for token, kind in (
        (authorisation.payment, PaymentMandate),
        (authorisation.checkout, CheckoutMandate),
    ):
        payload = client.verify(
            token=token, key_or_provider=key, payload_type=kind
        ).mandate_payload
        assert payload.iat is not None
        assert payload.iat >= before
        assert payload.exp == payload.iat + mandates.TTL_SECONDS


def test_the_checkout_is_for_one_of_the_thing() -> None:
    """Nothing upstream can ask for two, so a quantity that was not 1 would be a
    shopper charged twice for a cart they approved once."""
    document = mandates.checkout_document(CART, order_id="order-1")

    assert document["line_items"][0]["quantity"] == 1
    assert len(document["line_items"]) == 1


@needs_ap2
def test_a_signing_key_read_off_disk_is_identified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every signature here is traceable to a key id; a key with none leaves a
    verifier with a signature and no way to say whose it was."""
    enrolled_key(tmp_path, monkeypatch)

    key, _enrolled = mandates.load_key(required=True)

    assert key.get("kid") == "agent"


@needs_ap2
def test_a_generated_key_is_identified_by_the_name_it_was_asked_for() -> None:
    """The dry run signs as two parties with two throwaway keys, so the key id is
    what tells one signature from the other when reading a chain back."""
    assert mandates.generate_key("dry-run-merchant").get("kid") == "dry-run-merchant"


@needs_ap2
def test_the_instrument_says_who_holds_it_and_never_what_it_is() -> None:
    """AP2 exists so the agent does not carry the funding instrument."""
    from ap2.sdk.generated.payment_mandate import PaymentMandate
    from ap2.sdk.mandate import MandateClient

    key = mandates.generate_key("agent")
    authorisation = mandates.authorise(CART, signed_checkout(), key=key, nonce="n")

    instrument = (
        MandateClient()
        .verify(token=authorisation.payment, key_or_provider=key, payload_type=PaymentMandate)
        .mandate_payload.payment_instrument
    )

    assert instrument.description == "Held by the credential provider"
    assert instrument.type == "card"


# -- verification, asked the questions it exists to answer ---------------------


@needs_ap2
def test_a_chain_bound_to_another_checkout_reports_a_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Payment Mandate's ``transaction_id`` *is* the checkout hash, so asking
    about a different one is asking whether this payment pays for that cart --
    and the answer has to be no."""
    agent, issuer = open_mandate(tmp_path, monkeypatch)
    authorisation = mandates.authorise(CART, signed_checkout(), key=agent, nonce="n")

    violations = mandates.verify(
        authorisation.payment,
        issuer=issuer,
        audience=mandates.CREDENTIAL_PROVIDER_AUDIENCE,
        nonce="n",
        transaction_id="some-other-checkout",
    )

    assert violations
    assert any("transaction_id" in violation for violation in violations)


@pytest.mark.parametrize("broken", [pytest.param("jwcrypto", marks=needs_ap2), "cryptography"])
def test_a_half_installed_signing_stack_names_what_is_missing(
    monkeypatch: pytest.MonkeyPatch, broken: str
) -> None:
    """`--no-deps` over the wrong file leaves `cryptography` without the `cffi` it is
    built on."""
    import builtins

    real = builtins.__import__

    def refuse(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith(broken):
            raise ModuleNotFoundError("No module named '_cffi_backend'", name="_cffi_backend")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    for module in list(__import__("sys").modules):
        if module.startswith(broken):
            monkeypatch.delitem(__import__("sys").modules, module, raising=False)

    with pytest.raises(MandateError) as excinfo:
        mandates.generate_key("agent")

    assert "_cffi_backend" in str(excinfo.value)
    assert "requirements-ap2-deps.txt" in str(excinfo.value)


@needs_ap2
def test_an_import_failure_with_no_module_name_still_reads(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare `ImportError` carries no `name`; the sentence has to survive that."""
    import builtins

    real = builtins.__import__

    def refuse(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith("jwcrypto"):
            raise ImportError("something went wrong")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    monkeypatch.delitem(__import__("sys").modules, "jwcrypto.jwk", raising=False)
    monkeypatch.delitem(__import__("sys").modules, "jwcrypto", raising=False)

    with pytest.raises(MandateError, match="part of it is not there"):
        mandates.generate_key("agent")
