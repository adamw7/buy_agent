"""The AP2 seam: keys, checkouts, and the two shapes of authorisation.

Nothing here is mocked at the crypto layer. A mandate that verifies only against
a fake verifier is a mandate nobody else would take, so these sign real SD-JWTs
and read them back through the SDK's own verifier -- which is what a merchant or
a credential provider would run.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

import pytest

from buy_agent import mandates
from buy_agent.mandates import MandateError
from buy_agent.payment import Cart
from tests.conftest import needs_ap2

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


def write_key(path: Path) -> Path:
    """An EC P-256 private key on disk, as ``$BUY_AGENT_AP2_KEY`` names one."""
    key = mandates.generate_key("agent")
    path.write_bytes(key.export_to_pem(private_key=True, password=None))
    return path


def open_mandate_file(path: Path, *, maximum: int, payee: str = "audiosite.example") -> Any:
    """A pre-signed open Payment Mandate, and the agent key it delegates to.

    Built the way a bank or an agent provider would build one: an amount range
    and an allow-list of payees, with ``cnf`` naming the key allowed to close it.
    """
    from ap2.sdk.generated.open_payment_mandate import (
        AllowedPayees,
        AmountRange,
        OpenPaymentMandate,
    )
    from ap2.sdk.generated.types.merchant import Merchant
    from ap2.sdk.mandate import MandateClient

    issuer = mandates.generate_key("issuer")
    agent = mandates.generate_key("agent")
    now = int(time.time())
    token = MandateClient().create(
        payloads=[
            OpenPaymentMandate(
                constraints=[
                    AmountRange(currency="USD", min=0, max=maximum),
                    AllowedPayees(
                        allowed=[
                            Merchant(id=payee, name="AudioSite", website=f"https://{payee}")
                        ]
                    ),
                ],
                cnf={"jwk": json.loads(agent.export_public())},
                iat=now,
                exp=now + 3600,
            )
        ],
        issuer_key=issuer,
    )
    path.write_text(
        json.dumps({"mandate": token, "issuer_jwk": json.loads(issuer.export_public())}),
        encoding="utf-8",
    )
    return agent, issuer


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
def test_a_key_is_read_off_the_path_the_environment_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(mandates.KEY_PATH, str(write_key(tmp_path / "agent.pem")))

    key, enrolled = mandates.load_key(required=True)

    assert enrolled is True
    assert key.has_private


@needs_ap2
def test_a_rail_that_moves_money_refuses_to_sign_without_an_enrolled_key() -> None:
    """An ephemeral key authorises nothing a counterparty could have agreed to
    trust, so a rail with a counterparty must not be handed one."""
    with pytest.raises(MandateError, match="openssl ecparam"):
        mandates.load_key(required=True)


@needs_ap2
def test_the_dry_run_generates_a_key_rather_than_refusing() -> None:
    key, enrolled = mandates.load_key(required=False)

    assert enrolled is False
    assert key.has_private


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
def test_a_signed_checkout_carries_the_hash_the_mandates_bind_to() -> None:
    signed = signed_checkout()

    assert signed.hash == mandates.checkout_hash(signed.jwt)


@needs_ap2
def test_the_checkout_is_a_ucp_checkout_the_sdk_validates() -> None:
    """The document is hand-built rather than constructed through the model, so
    this is what says it is still the shape the schema describes."""
    from ap2.sdk.generated.types.checkout import Checkout

    Checkout.model_validate(mandates.checkout_document(CART, order_id="order-1"))


# -- human present -------------------------------------------------------------


@needs_ap2
def test_approving_in_person_signs_both_mandates_directly() -> None:
    key = mandates.generate_key("agent")
    checkout = signed_checkout()

    authorisation = mandates.authorise(CART, checkout, key=key, nonce="n")

    assert authorisation.autonomous is False
    assert authorisation.transaction_id == checkout.hash
    assert authorisation.reference


@needs_ap2
def test_the_payment_mandate_is_bound_to_the_checkout_by_its_hash() -> None:
    """AP2 binds the two by making the Payment Mandate's ``transaction_id`` the
    Checkout Mandate's ``checkout_hash``, so neither half can be paired with
    another cart's other half."""
    from ap2.sdk.generated.checkout_mandate import CheckoutMandate
    from ap2.sdk.generated.payment_mandate import PaymentMandate
    from ap2.sdk.mandate import MandateClient

    key = mandates.generate_key("agent")
    checkout = signed_checkout()

    authorisation = mandates.authorise(CART, checkout, key=key, nonce="n")

    # A single token takes the key itself and the type it should read back as;
    # the provider callable is for a chain, whose root hop it is asked about.
    client = MandateClient()
    payment = client.verify(
        token=authorisation.payment, key_or_provider=key, payload_type=PaymentMandate
    )
    signed = client.verify(
        token=authorisation.checkout, key_or_provider=key, payload_type=CheckoutMandate
    )

    assert payment.mandate_payload.transaction_id == checkout.hash
    assert signed.mandate_payload.checkout_hash == checkout.hash


@needs_ap2
def test_the_payment_mandate_carries_the_amount_and_never_an_instrument_number() -> None:
    from ap2.sdk.generated.payment_mandate import PaymentMandate
    from ap2.sdk.mandate import MandateClient

    key = mandates.generate_key("agent")

    authorisation = mandates.authorise(CART, signed_checkout(), key=key, nonce="n")
    payload = (
        MandateClient()
        .verify(token=authorisation.payment, key_or_provider=key, payload_type=PaymentMandate)
        .mandate_payload
    )

    assert payload.payment_amount.amount == 32999
    assert payload.payment_amount.currency == "USD"
    assert payload.payment_instrument.id == "default"
    assert "4242" not in payload.model_dump_json()


# -- human not present ---------------------------------------------------------


@needs_ap2
def test_an_open_mandate_authorises_a_cart_inside_its_constraints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, _issuer = open_mandate_file(tmp_path / "mandate.json", maximum=40000)
    monkeypatch.setenv(mandates.MANDATE_PATH, str(tmp_path / "mandate.json"))

    authorisation = mandates.authorise(CART, signed_checkout(), key=agent, nonce="n")

    assert authorisation.autonomous is True
    assert authorisation.reference


@needs_ap2
def test_an_open_mandate_refuses_a_cart_over_its_amount_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The constraint is evaluated by the SDK's own evaluator -- the one a
    credential provider runs -- so the refusal is the real one and not a second
    reading of the budget written here."""
    agent, _issuer = open_mandate_file(tmp_path / "mandate.json", maximum=10000)
    monkeypatch.setenv(mandates.MANDATE_PATH, str(tmp_path / "mandate.json"))

    with pytest.raises(MandateError, match="exceeds maximum"):
        mandates.authorise(CART, signed_checkout(), key=agent, nonce="n")


@needs_ap2
def test_an_open_mandate_refuses_a_merchant_it_does_not_allow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, _issuer = open_mandate_file(
        tmp_path / "mandate.json", maximum=40000, payee="somewhere-else.example"
    )
    monkeypatch.setenv(mandates.MANDATE_PATH, str(tmp_path / "mandate.json"))

    with pytest.raises(MandateError, match="does not authorise"):
        mandates.authorise(CART, signed_checkout(), key=agent, nonce="n")


@needs_ap2
def test_an_open_mandate_cannot_be_closed_with_the_wrong_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``cnf`` names the one key allowed to close it, which is the whole of the
    delegation: another key signing on top is not a chain that verifies."""
    open_mandate_file(tmp_path / "mandate.json", maximum=40000)
    monkeypatch.setenv(mandates.MANDATE_PATH, str(tmp_path / "mandate.json"))

    with pytest.raises(MandateError, match="did not verify"):
        mandates.authorise(
            CART, signed_checkout(), key=mandates.generate_key("stranger"), nonce="n"
        )


def test_no_mandate_configured_is_the_attended_mode() -> None:
    assert mandates.open_mandate() is None


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
    monkeypatch.setenv(mandates.MANDATE_PATH, str(tmp_path / "gone.json"))

    with pytest.raises(MandateError, match="Could not read the open mandate"):
        mandates.open_mandate()


# -- verification --------------------------------------------------------------


@needs_ap2
def test_a_chain_presented_to_the_wrong_audience_does_not_verify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AP2 binds a presentation to who it is for, so one meant for the credential
    provider cannot be replayed at the merchant."""
    agent, issuer = open_mandate_file(tmp_path / "mandate.json", maximum=40000)
    monkeypatch.setenv(mandates.MANDATE_PATH, str(tmp_path / "mandate.json"))
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
    agent, issuer = open_mandate_file(tmp_path / "mandate.json", maximum=40000)
    monkeypatch.setenv(mandates.MANDATE_PATH, str(tmp_path / "mandate.json"))
    authorisation = mandates.authorise(CART, signed_checkout(), key=agent, nonce="first")

    with pytest.raises(MandateError, match="did not verify"):
        mandates.verify(
            authorisation.payment,
            issuer=issuer,
            audience=mandates.CREDENTIAL_PROVIDER_AUDIENCE,
            nonce="second",
            transaction_id=authorisation.transaction_id,
        )


def test_a_challenge_is_not_the_same_twice() -> None:
    assert mandates.challenge() != mandates.challenge()


# -- what a mandate is worth, and for how long ---------------------------------


@needs_ap2
def test_both_mandates_expire() -> None:
    """A chain is a credential: it authorises this purchase to whoever holds it.
    Without an expiry, one left in a log is a bearer token for the afternoon --
    which is why ``TTL_SECONDS`` exists and why it has to reach the payload."""
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


def test_the_ttl_is_minutes_rather_than_hours() -> None:
    """A price is not evidence of anything for long, and this authorises one."""
    assert 0 < mandates.TTL_SECONDS <= 3600


def test_the_checkout_is_for_one_of_the_thing() -> None:
    """Nothing upstream can ask for two, so a quantity that was not 1 would be a
    shopper charged twice for a cart they approved once."""
    document = mandates.checkout_document(CART, order_id="order-1")

    assert document["line_items"][0]["quantity"] == 1
    assert len(document["line_items"]) == 1


def test_the_totals_and_the_line_item_agree_with_the_cart() -> None:
    document = mandates.checkout_document(CART, order_id="order-1")

    assert {total["amount"] for total in document["totals"]} == {CART.amount}
    assert document["line_items"][0]["totals"] == document["totals"]


def test_the_order_id_is_the_one_it_was_given() -> None:
    assert mandates.checkout_document(CART, order_id="order-77")["id"] == "order-77"


@needs_ap2
def test_a_signing_key_read_off_disk_is_identified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every signature here is traceable to a key id; a key with none leaves a
    verifier with a signature and no way to say whose it was."""
    monkeypatch.setenv(mandates.KEY_PATH, str(write_key(tmp_path / "agent.pem")))

    key, _enrolled = mandates.load_key(required=True)

    assert key.get("kid") == "agent"


def test_a_generated_key_is_identified_by_the_name_it_was_asked_for() -> None:
    """The dry run signs as two parties with two throwaway keys, so the key id is
    what tells one signature from the other when reading a chain back."""
    assert mandates.generate_key("dry-run-merchant").get("kid") == "dry-run-merchant"


@needs_ap2
def test_the_instrument_says_who_holds_it_and_never_what_it_is() -> None:
    """AP2 exists so the agent does not carry the funding instrument. The mandate
    references one; what it actually is stays with the credential provider."""
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


def test_a_generated_key_is_a_different_key_every_time() -> None:
    """Ephemeral means ephemeral: two dry runs are two authorisations, and one
    that reused a key would let the first be replayed as the second."""
    first = mandates.generate_key("one")
    second = mandates.generate_key("one")

    assert first.export_public() != second.export_public()


# -- verification, asked the questions it exists to answer ---------------------


@needs_ap2
def test_a_chain_bound_to_another_checkout_reports_a_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Payment Mandate's ``transaction_id`` *is* the checkout hash, so asking
    about a different one is asking whether this payment pays for that cart --
    and the answer has to be no."""
    agent, issuer = open_mandate_file(tmp_path / "mandate.json", maximum=40000)
    monkeypatch.setenv(mandates.MANDATE_PATH, str(tmp_path / "mandate.json"))
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


@needs_ap2
def test_a_chain_asked_about_its_own_checkout_reports_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, issuer = open_mandate_file(tmp_path / "mandate.json", maximum=40000)
    monkeypatch.setenv(mandates.MANDATE_PATH, str(tmp_path / "mandate.json"))
    authorisation = mandates.authorise(CART, signed_checkout(), key=agent, nonce="n")

    assert (
        mandates.verify(
            authorisation.payment,
            issuer=issuer,
            audience=mandates.CREDENTIAL_PROVIDER_AUDIENCE,
            nonce="n",
            transaction_id=authorisation.transaction_id,
        )
        == []
    )


@needs_ap2
def test_an_autonomous_authorisation_carries_two_real_mandates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both halves are what a counterparty is handed, so both have to be tokens
    that verify -- not just the one the constraints were checked on."""
    from ap2.sdk.generated.checkout_mandate import CheckoutMandate
    from ap2.sdk.mandate import MandateClient

    agent, issuer = open_mandate_file(tmp_path / "mandate.json", maximum=40000)
    monkeypatch.setenv(mandates.MANDATE_PATH, str(tmp_path / "mandate.json"))
    checkout = signed_checkout()

    authorisation = mandates.authorise(CART, checkout, key=agent, nonce="n")

    assert (
        mandates.verify(
            authorisation.payment,
            issuer=issuer,
            audience=mandates.CREDENTIAL_PROVIDER_AUDIENCE,
            nonce="n",
            transaction_id=checkout.hash,
        )
        == []
    )
    signed = MandateClient().verify(
        token=authorisation.checkout, key_or_provider=agent, payload_type=CheckoutMandate
    )
    assert signed.mandate_payload.checkout_hash == checkout.hash


@pytest.mark.parametrize("broken", ["jwcrypto", "cryptography"])
def test_a_half_installed_signing_stack_names_what_is_missing(
    monkeypatch: pytest.MonkeyPatch, broken: str
) -> None:
    """`--no-deps` over the wrong file leaves `cryptography` without the `cffi`
    it is built on. Reported as "the AP2 SDK is not installed" that sends
    somebody to re-run the command that just broke it, so the module that
    actually failed is what the sentence names -- and both halves of the signing
    stack are reached from functions that never touch the SDK itself."""
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


def test_the_install_command_installs_the_deps_before_the_sdk() -> None:
    """Two commands in the right order: `--no-deps` is what the SDK needs and
    what its dependencies must not get, so the file that resolves normally goes
    first. Reversed, the flag is applied to the thing that needs resolving."""
    deps = mandates.INSTALL.index("requirements-ap2-deps.txt")
    sdk = mandates.INSTALL.index("--no-deps")

    assert deps < sdk
