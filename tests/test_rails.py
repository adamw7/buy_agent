"""The rail table, and each row's half of a payment.

Nothing here reaches the network. The HTTP rail's transport is patched where
:mod:`buy_agent.rails` imported it -- ``rails.httpx.post`` -- by the same rule
that makes the provider fakes patch ``providers.Client``: patching
``httpx.post`` itself would work today and stop working the moment the import
moved.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from buy_agent import mandates, rails
from buy_agent.config import AgentConfig
from buy_agent.payment import Cart, PaymentError
from tests.conftest import needs_ap2
from tests.test_mandates import CART, signed_checkout


class FakeResponse:
    """What ``httpx.post`` answers with, as much of it as a rail reads."""

    def __init__(self, payload: Any = None, *, status: int = 200, text: str | None = None) -> None:
        self.payload = payload
        self.status = status
        self.text = text

    def raise_for_status(self) -> None:
        if self.status >= 400:  # noqa: PLR2004 -- httpx's own boundary
            raise httpx.HTTPStatusError(
                f"{self.status}", request=httpx.Request("POST", "http://x"), response=None  # type: ignore[arg-type]
            )

    def json(self) -> Any:
        if self.text is not None:
            raise ValueError("not JSON")
        return self.payload


class Endpoint:
    """The far end of the HTTP rail: what it was sent, and what it answers.

    ``answers`` is a queue rather than one canned reply, because a payment is two
    calls -- the checkout and then the settlement -- and the interesting cases
    are the ones where the second answers differently from the first.
    """

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


def test_every_rail_is_reachable_by_the_name_it_carries() -> None:
    # Through the module rather than through names imported from it, for the
    # reason `tests/test_providers.py` reaches for `providers_module.OLLAMA`:
    # these tests reload `rails` to re-read its environment-derived defaults,
    # and a name bound at import time would still hold the old rows.
    for name, rail in rails.RAILS.items():
        assert rails.rail_for(name) is rail
        assert rail.name == name


def test_an_unknown_rail_names_the_ones_there_are() -> None:
    """Reached from a flag, a form field and an environment variable alike, so
    the sentence has to say what to type instead."""
    with pytest.raises(ValueError, match="dry-run, http"):
        rails.rail_for("paypal")


def test_the_default_rail_moves_no_money() -> None:
    """The whole reason paying can be turned on safely: ``--pay`` alone signs a
    real authorisation and charges nobody."""
    assert AgentConfig().rail_used is rails.DRY_RUN
    assert rails.DRY_RUN.moves_money is False
    assert rails.DRY_RUN.needs_key is False
    assert rails.DRY_RUN.needs_endpoint is False


def test_the_picker_is_shipped_every_rail_and_no_more() -> None:
    options = rails.rail_options()

    assert [option["name"] for option in options] == list(rails.RAILS)
    assert options[0] == {
        "name": "dry-run",
        "label": "Dry run",
        "endpoint": "",
        "needs_endpoint": False,
        "moves_money": False,
    }


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


@needs_ap2
def test_the_dry_run_signs_a_checkout_of_its_own() -> None:
    """It plays every role there is, so the mandates are signed against a real
    hash of a really signed document rather than a placeholder."""
    signed, nonce = rails.DRY_RUN.checkout(CART, AgentConfig())

    assert signed.hash == mandates.checkout_hash(signed.jwt)
    assert nonce


@needs_ap2
def test_the_dry_run_settles_by_charging_nobody() -> None:
    authorisation = mandates.authorise(
        CART, signed_checkout(), key=mandates.generate_key("agent"), nonce="n"
    )

    settlement = rails.DRY_RUN.settle(CART, authorisation, AgentConfig())

    assert settlement.paid is False
    assert "Nothing was charged" in settlement.detail


def test_the_dry_run_has_no_transport_to_fail() -> None:
    """``except ()`` catches nothing, which is right for a rail that sends
    nothing: an empty tuple is the honest answer, not a guess."""
    assert rails.DRY_RUN.transport_errors == ()


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


@needs_ap2
def test_the_checkout_hash_is_computed_here_and_never_taken_from_the_answer(
    posted: Endpoint,
) -> None:
    """It is what the mandates bind to, so a hash the counterparty chose would
    let it bind them to a document nobody on this side has seen."""
    merchant_signed = signed_checkout()
    posted.answers.append(
        FakeResponse({"checkout_jwt": merchant_signed.jwt, "checkout_hash": "not-this"})
    )

    signed, _nonce = rails.HTTP.checkout(CART, http_config())

    assert signed.hash == mandates.checkout_hash(merchant_signed.jwt)


@needs_ap2
def test_a_counterparty_that_issues_no_challenge_is_given_one(
    posted: Endpoint,
) -> None:
    posted.answers.append(FakeResponse({"checkout_jwt": signed_checkout().jwt}))

    _signed, nonce = rails.HTTP.checkout(CART, http_config())

    assert nonce


def test_an_answer_with_no_signed_checkout_is_refused(
    posted: Endpoint,
) -> None:
    posted.answers.append(FakeResponse({"status": "ok"}))

    with pytest.raises(PaymentError, match="no 'checkout_jwt'"):
        rails.HTTP.checkout(CART, http_config())


def test_an_answer_that_is_not_json_is_a_payment_failure_and_not_a_transport_one(
    posted: Endpoint,
) -> None:
    """Something answered, so "could not reach it" would send the shopper off to
    check a connection that is fine."""
    posted.answers.append(FakeResponse(text="<html>"))

    with pytest.raises(PaymentError, match="not JSON"):
        rails.HTTP.checkout(CART, http_config())


def test_an_answer_that_is_not_an_object_is_refused(
    posted: Endpoint,
) -> None:
    posted.answers.append(FakeResponse([1, 2, 3]))

    with pytest.raises(PaymentError, match="not an object"):
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

    with pytest.raises(PaymentError, match="did not complete the payment"):
        rails.HTTP.settle(CART, authorisation, http_config())


@needs_ap2
def test_a_settlement_that_says_nothing_about_the_detail_still_reads(
    posted: Endpoint,
) -> None:
    authorisation = mandates.authorise(
        CART, signed_checkout(), key=mandates.generate_key("agent"), nonce="n"
    )
    posted.answers.append(FakeResponse({"paid": True}))

    assert rails.HTTP.settle(CART, authorisation, http_config()).detail == "Paid."


def test_a_status_the_endpoint_answers_with_is_one_of_its_transport_failures() -> None:
    assert httpx.HTTPStatusError in _classes(rails.HTTP.transport_errors)
    assert OSError in rails.HTTP.transport_errors


def test_the_hint_names_the_address_and_the_way_to_sign_without_paying() -> None:
    hint = rails.HTTP.hint(http_config(), httpx.ConnectError("refused"))

    assert "https://pay.example" in hint
    assert "--rail dry-run" in hint


def _classes(errors: tuple[type[BaseException], ...]) -> set[type[BaseException]]:
    """Every exception class those tuples cover, subclasses included."""
    covered: set[type[BaseException]] = set()
    for error in errors:
        covered.add(error)
        covered.update(error.__subclasses__())
    return covered


def test_a_cart_names_the_merchant_by_the_site_the_page_came_from() -> None:
    """The only identity this pipeline actually knows: a seller's *name* is what
    a page printed, and two pages print two spellings of it."""
    assert CART.merchant_payload() == {
        "id": "audiosite.example",
        "name": "AudioSite",
        "website": "https://audiosite.example",
    }


def test_a_cart_with_no_scheme_still_names_something() -> None:
    cart = Cart(
        title="x",
        price=1.0,
        currency="USD",
        amount=100,
        merchant="Shop",
        url="shop.example",
        item_id="x",
    )

    assert cart.merchant_payload()["id"] == "shop.example"


# -- what a counterparty can answer that is not an answer ----------------------


def test_an_empty_signed_checkout_is_refused_like_a_missing_one(posted: Endpoint) -> None:
    """An empty string is a string, so the check has to be about the *value* and
    not only the type -- otherwise the mandates bind to a hash of nothing."""
    posted.answers.append(FakeResponse({"checkout_jwt": ""}))

    with pytest.raises(PaymentError, match="no 'checkout_jwt'"):
        rails.HTTP.checkout(CART, http_config())


def test_a_signed_checkout_that_is_not_text_is_refused(posted: Endpoint) -> None:
    posted.answers.append(FakeResponse({"checkout_jwt": {"jwt": "..."}}))

    with pytest.raises(PaymentError, match="no 'checkout_jwt'"):
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
def test_a_counterpartys_own_challenge_is_used_as_it_stands(posted: Endpoint) -> None:
    posted.answers.append(
        FakeResponse({"checkout_jwt": signed_checkout().jwt, "nonce": "theirs"})
    )

    assert rails.HTTP.checkout(CART, http_config())[1] == "theirs"


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

    with pytest.raises(PaymentError, match="answered with list"):
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
