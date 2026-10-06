"""The rail table, and each row's half of a payment."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from buy_agent import mandates, rails
from buy_agent.config import AgentConfig
from buy_agent.payment import PaymentError, RailUnreachableError
from tests.conftest import needs_ap2
from tests.test_mandates import CART, signed_checkout


class FakeResponse:
    """What ``httpx.post`` answers with, as much of it as a rail reads."""

    def __init__(self, payload: Any = None, *, status: int = 200, text: str | None = None) -> None:
        self.payload = payload
        self.status = status
        self.text = text

    def raise_for_status(self) -> None:
        if self.status >= 400:  # httpx's own boundary
            raise httpx.HTTPStatusError(
                f"{self.status}", request=httpx.Request("POST", "http://x"), response=None  # type: ignore[arg-type]
            )

    def json(self) -> Any:
        if self.text is not None:
            raise ValueError("not JSON")
        return self.payload


class Endpoint:
    """The far end of the HTTP rail: what it was sent, and what it answers."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.answers: list[FakeResponse] = []

    def post(self, url: str, *, json: dict[str, Any], timeout: float) -> FakeResponse:
        del timeout
        self.calls.append((url, json))
        return self.answers.pop(0) if self.answers else FakeResponse({})


@pytest.fixture
def posted(monkeypatch: pytest.MonkeyPatch) -> Endpoint:
    """Patch the transport where ``rails`` imported it, and record the calls."""
    endpoint = Endpoint()
    monkeypatch.setattr(rails.httpx, "post", endpoint.post)
    return endpoint


def http_config(**extra: Any) -> AgentConfig:
    return AgentConfig(pay=True, rail="http", merchant_url="https://pay.example", **extra)


# -- the table -----------------------------------------------------------------


def test_the_http_rail_reads_its_address_off_its_own_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rail's default is its own row's, off its own environment variable --
    the rule ``providers.PROVIDERS`` follows for a model server."""
    import importlib

    monkeypatch.setenv("BUY_AGENT_MERCHANT_URL", "https://from-the-environment.example")
    reloaded = importlib.reload(rails)
    try:
        assert reloaded.RAILS["http"].endpoint == "https://from-the-environment.example"
    finally:
        monkeypatch.delenv("BUY_AGENT_MERCHANT_URL")
        importlib.reload(rails)


# -- the dry run ---------------------------------------------------------------


# -- the HTTP rail -------------------------------------------------------------


@needs_ap2
def test_the_http_rail_asks_the_merchant_to_sign_the_checkout(
    posted: Endpoint,
) -> None:
    merchant_signed = signed_checkout()
    posted.answers.append(
        FakeResponse({"checkout_jwt": merchant_signed.jwt, "nonce": "from-the-merchant"})
    )

    signed, nonce = rails.HTTP.checkout(CART, http_config())

    url, body = posted.calls[0]
    assert url == "https://pay.example/checkout"
    assert body["checkout"]["currency"] == "USD"
    assert signed.jwt == merchant_signed.jwt
    assert nonce == "from-the-merchant"


def test_an_answer_that_is_not_json_is_the_counterpartys_failure_and_not_the_shoppers(
    posted: Endpoint,
) -> None:
    """Something answered, but not with an answer -- so this is a 502 beside the
    connection nothing accepted, and not the 400 that would send the shopper off to
    correct a form with nothing wrong with it. The subclass is what pins that: both
    are ``PaymentError``s, which is what keeps the CLI catching one name."""
    posted.answers.append(FakeResponse(text="<html>"))

    with pytest.raises(RailUnreachableError, match="not JSON"):
        rails.HTTP.checkout(CART, http_config())


@needs_ap2
def test_settling_presents_both_mandates_and_the_transaction_they_share(
    posted: Endpoint,
) -> None:
    authorisation = mandates.authorise(
        CART, signed_checkout(), key=mandates.generate_key("agent"), nonce="n"
    )
    posted.answers.append(FakeResponse({"paid": True, "detail": "Charged."}))

    settlement = rails.HTTP.settle(CART, authorisation, http_config())

    url, body = posted.calls[0]
    assert url == "https://pay.example/payment"
    assert body["transaction_id"] == authorisation.transaction_id
    assert body["checkout_mandate"] == authorisation.checkout
    assert body["payment_mandate"] == authorisation.payment
    assert settlement.paid is True
    assert settlement.detail == "Charged."


@needs_ap2
def test_a_refused_payment_carries_the_far_ends_own_reason(
    posted: Endpoint,
) -> None:
    authorisation = mandates.authorise(
        CART, signed_checkout(), key=mandates.generate_key("agent"), nonce="n"
    )
    posted.answers.append(
        FakeResponse({"paid": False, "detail": "Card declined"})
    )

    with pytest.raises(PaymentError, match="Card declined"):
        rails.HTTP.settle(CART, authorisation, http_config())


@needs_ap2
def test_a_refusal_with_no_reason_still_says_what_did_not_happen(
    posted: Endpoint,
) -> None:
    authorisation = mandates.authorise(
        CART, signed_checkout(), key=mandates.generate_key("agent"), nonce="n"
    )
    posted.answers.append(FakeResponse({"paid": False}))

    with pytest.raises(PaymentError, match="did not complete the payment") as excinfo:
        rails.HTTP.settle(CART, authorisation, http_config())

    # The line the three failures above sit on the other side of: a counterparty that
    # understood the request and declined it is answering about the request, so this
    # one stays the 400 it reads as.
    assert not isinstance(excinfo.value, RailUnreachableError)
    # And with no colon hanging off the end promising a reason that never comes.
    assert str(excinfo.value).endswith(f"did not complete the payment for {CART.label()}.")


@needs_ap2
def test_a_settlement_that_says_nothing_about_the_detail_still_reads(
    posted: Endpoint,
) -> None:
    authorisation = mandates.authorise(
        CART, signed_checkout(), key=mandates.generate_key("agent"), nonce="n"
    )
    posted.answers.append(FakeResponse({"paid": True}))

    assert rails.HTTP.settle(CART, authorisation, http_config()).detail == "Paid."


# -- what a counterparty can answer that is not an answer ----------------------


def test_a_signed_checkout_that_is_not_text_is_refused(posted: Endpoint) -> None:
    posted.answers.append(FakeResponse({"checkout_jwt": {"jwt": "..."}}))

    with pytest.raises(RailUnreachableError, match="no 'checkout_jwt'"):
        rails.HTTP.checkout(CART, http_config())


@needs_ap2
@pytest.mark.parametrize("offered", [123, "", None, {"nonce": "x"}], ids=str)
def test_a_nonce_that_is_not_usable_text_is_replaced_with_one_of_ours(
    posted: Endpoint, offered: Any
) -> None:
    """The nonce is what stops a presentation being replayed, so a counterparty
    that answers with nothing usable does not get to leave the run without one."""
    posted.answers.append(
        FakeResponse({"checkout_jwt": signed_checkout().jwt, "nonce": offered})
    )

    _signed, nonce = rails.HTTP.checkout(CART, http_config())

    assert isinstance(nonce, str)
    assert nonce not in ("", str(offered))


@needs_ap2
def test_a_payment_is_never_sent_without_a_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """The one timeout in this project worth being patient about, and the one it
    would be worst to leave off: a request this side waits on forever is a
    payment nobody can say happened or not."""
    timeouts: list[float] = []

    def record(url: str, *, json: dict[str, Any], timeout: float) -> FakeResponse:
        del url, json
        timeouts.append(timeout)
        return FakeResponse({"checkout_jwt": signed_checkout().jwt})

    monkeypatch.setattr(rails.httpx, "post", record)
    rails.HTTP.checkout(CART, http_config())

    assert timeouts == [rails._TIMEOUT]
    assert rails._TIMEOUT > 0


def test_an_answer_of_the_wrong_shape_says_what_shape_it_was(posted: Endpoint) -> None:
    """Naming what came back is what tells an integrator they are pointed at the
    wrong endpoint rather than at a broken one."""
    posted.answers.append(FakeResponse([1, 2, 3]))

    with pytest.raises(RailUnreachableError, match="answered with list"):
        rails.HTTP.checkout(CART, http_config())


@needs_ap2
def test_the_dry_run_stamps_its_own_order_on_the_checkout_it_signs() -> None:
    """It is standing in for the merchant, and a merchant's checkout has an order
    id -- so the document it signs is the shape a real one would be."""
    import base64
    import json as jsonlib

    signed, _nonce = rails.DRY_RUN.checkout(CART, AgentConfig())

    # Read straight out of the token rather than off what was signed: what
    # matters is what a merchant would receive.
    payload = signed.jwt.split(".")[1]
    decoded = jsonlib.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))

    assert decoded["id"] == rails._DRY_RUN_ORDER
    assert decoded["totals"][-1] == {"type": "total", "amount": CART.amount}
