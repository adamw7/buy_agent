"""What may be paid for, for how much, and what comes back.

The rule under most of this is the ranking rule turned around: grounding blanks
every figure the sources did not print (ADR-0006), so a blank price is not a
product to buy. These are the tests that say the payment side reads those blanks
the way the rest of the pipeline writes them.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from buy_agent import mandates, payment
from buy_agent.config import AgentConfig
from buy_agent.models import Product
from buy_agent.payment import (
    Cart,
    PaymentError,
    RailUnreachableError,
    amount_for,
    amount_label,
    cart_for,
    minor_units,
    payable,
    pay_for,
    unattended,
)
from tests.conftest import needs_ap2, payable_product

SONY = payable_product(rating=4.6, review_count=1200)

BOSE = Product(name="Bose QC Ultra", price=379.0, currency="USD", url="https://x.example/b")


# -- minor units ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("price", "currency", "expected"),
    [
        (329.99, "USD", 32999),
        # The reason this goes through Decimal: 19.99 * 100 is
        # 1998.9999999999998, and a payment is not a place to truncate.
        (19.99, "EUR", 1999),
        (0.1 + 0.2, "USD", 30),
        # Half up, which is the rounding a price tag implies.
        (1.005, "USD", 101),
        # Currencies that are not counted in hundredths, which is the whole
        # reason the exponent is looked up rather than assumed.
        (4980.0, "JPY", 4980),
        (12.345, "KWD", 12345),
        # A currency nothing knows falls back to two places, which is what all
        # but two dozen of them use.
        (10.5, "XYZ", 1050),
    ],
)
def test_a_price_becomes_the_currencys_smallest_unit(
    price: float, currency: str, expected: int
) -> None:
    assert minor_units(price, currency) == expected


def test_a_price_that_is_not_a_number_is_refused_rather_than_sent() -> None:
    with pytest.raises(PaymentError, match="not a price"):
        minor_units(float("nan"), "USD")


# -- what may be paid for ------------------------------------------------------


def test_a_grounded_product_may_be_paid_for() -> None:
    assert payable(SONY, "USD") is None


def test_a_product_whose_price_grounding_blanked_may_not_be() -> None:
    """The ranking rule turned around: never rank on an unverified number
    becomes never pay on one."""
    unpriced = SONY.model_copy(update={"price": None})

    assert "nothing to authorise" in (payable(unpriced, "USD") or "")


def test_a_run_where_no_page_named_a_currency_has_no_amount_to_send() -> None:
    assert "not an amount" in (payable(SONY, None) or "")


def test_a_price_in_a_currency_the_run_cannot_place_may_not_be_paid(
) -> None:
    """The opposite of what the shopper's bounds do with the same fact, and
    deliberately: a bound that cannot judge a candidate keeps it (ADR-0039),
    but an amount nobody can place is not an amount to send (ADR-0043)."""
    reason = payable(SONY, "EUR")

    assert reason is not None
    assert "Nothing is converted" in reason


def test_a_product_with_no_source_page_has_no_merchant_to_pay() -> None:
    unlinked = SONY.model_copy(update={"url": None})

    assert "no source page" in (payable(unlinked, "USD") or "")


def test_what_a_purchase_would_be_for_is_asked_the_same_way_as_whether() -> None:
    """``amount_for`` is ``payable``'s other half, off the same check: a front
    door needs the amount as well as the verdict, because the currency a cart
    carries is frequently not the product's own -- a page that printed a bare
    figure is priced in the run's (ADR-0043)."""
    bare = SONY.model_copy(update={"currency": None})

    assert amount_for(SONY, "USD") == (329.99, "USD")
    assert amount_for(bare, "USD") == (329.99, "USD")
    assert bare.currency is None


def test_a_product_that_may_not_be_paid_for_is_worth_no_amount() -> None:
    assert amount_for(SONY.model_copy(update={"price": None}), "USD") is None
    assert amount_for(SONY, "EUR") is None


def test_every_surface_says_an_amount_the_same_way() -> None:
    """One wording for the CLI's prompt, the card's button and the receipt: the
    cart's label *is* this function, so a second spelling cannot appear."""
    cart = cart_for(SONY, [SONY, BOSE], AgentConfig(pay=True))

    assert amount_label(329.99, "USD") == "329.99 USD"
    assert cart.label() == amount_label(cart.price, cart.currency)


# -- the cart ------------------------------------------------------------------


def test_a_cart_is_built_from_the_run_it_was_found_in() -> None:
    cart = cart_for(SONY, [SONY, BOSE], AgentConfig(pay=True))

    assert cart.title == "Sony WH-1000XM5"
    assert cart.price == 329.99
    assert cart.currency == "USD"
    assert cart.amount == 32999
    assert cart.merchant == "AudioSite"
    assert cart.url == SONY.url


def test_a_cart_falls_back_to_the_site_when_no_seller_was_printed() -> None:
    anonymous = SONY.model_copy(update={"seller": None})

    assert cart_for(anonymous, [anonymous], AgentConfig(pay=True)).merchant == "audiosite.example"


def test_the_currency_is_the_runs_and_not_the_products() -> None:
    """Two listings in EUR and one in USD make this a EUR run, so the USD one is
    a price it cannot place."""
    euros = [
        SONY.model_copy(update={"currency": "EUR"}),
        BOSE.model_copy(update={"currency": "EUR"}),
    ]
    odd_one_out = SONY.model_copy(update={"currency": "USD", "name": "Odd"})

    with pytest.raises(PaymentError, match="counts in EUR"):
        cart_for(odd_one_out, [*euros, odd_one_out], AgentConfig(pay=True))


def test_the_spend_limit_refuses_a_cart_over_it() -> None:
    with pytest.raises(PaymentError, match="over the 100.00 USD spend limit"):
        cart_for(SONY, [SONY], AgentConfig(pay=True, spend_limit=100))


def test_the_spend_limit_names_the_field_so_the_form_can_mark_it() -> None:
    with pytest.raises(PaymentError) as excinfo:
        cart_for(SONY, [SONY], AgentConfig(pay=True, spend_limit=100))

    assert excinfo.value.field == "spend_limit"


def test_a_cart_inside_the_spend_limit_is_built() -> None:
    assert cart_for(SONY, [SONY], AgentConfig(pay=True, spend_limit=500)).amount == 32999


# -- paying --------------------------------------------------------------------


@needs_ap2
def test_the_dry_run_signs_a_real_authorisation_and_charges_nobody(
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = AgentConfig(pay=True)
    cart = cart_for(SONY, [SONY], config)

    with caplog.at_level(logging.INFO):
        receipt = pay_for(cart, config)

    assert receipt.paid is False
    assert receipt.rail == "dry-run"
    assert receipt.autonomous is False
    assert receipt.enrolled_key is False
    assert receipt.reference
    assert receipt.price_label == "329.99 USD"
    assert "Nothing was charged" in receipt.detail


@needs_ap2
def test_an_ephemeral_signature_says_so_rather_than_passing_for_one(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The chain demonstrates the shape of an authorisation without being one,
    and a receipt that hid that would be lying."""
    config = AgentConfig(pay=True)

    with caplog.at_level(logging.WARNING):
        pay_for(cart_for(SONY, [SONY], config), config)

    assert "generated" in caplog.text
    assert mandates.KEY_PATH in caplog.text


@needs_ap2
def test_a_real_rail_will_not_sign_without_an_enrolled_key() -> None:
    config = AgentConfig(pay=True, rail="http", merchant_url="https://pay.example")

    with pytest.raises(PaymentError, match="No signing key"):
        pay_for(cart_for(SONY, [SONY], config), config)


@needs_ap2
def test_a_rail_that_cannot_be_reached_is_its_own_kind_of_failure(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An endpoint that is down is nothing to do with the request, which is why
    it is a subclass -- and why the API answers 502 rather than 400."""
    import httpx

    from buy_agent import rails
    from tests.test_mandates import write_key

    monkeypatch.setenv(mandates.KEY_PATH, str(write_key(tmp_path / "agent.pem")))

    def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(rails.httpx, "post", refuse)
    config = AgentConfig(pay=True, rail="http", merchant_url="https://pay.example")

    with pytest.raises(RailUnreachableError, match="Could not reach the payment endpoint"):
        pay_for(cart_for(SONY, [SONY], config), config)


@needs_ap2
def test_a_receipt_never_carries_the_mandate_chain() -> None:
    """A chain authorises this purchase to whoever holds it until it expires,
    and a receipt is logged, sent to a browser and saved to a file."""
    config = AgentConfig(pay=True)

    receipt = pay_for(cart_for(SONY, [SONY], config), config)

    assert "~" not in receipt.model_dump_json()


@needs_ap2
def test_an_open_mandate_makes_the_run_unattended(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_mandates import open_mandate_file

    assert unattended() is False

    open_mandate_file(tmp_path / "mandate.json", maximum=40000)
    monkeypatch.setenv(mandates.MANDATE_PATH, str(tmp_path / "mandate.json"))

    assert unattended() is True


def test_a_broken_mandate_file_is_the_one_failure_a_payment_has(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both front doors catch ``PaymentError`` and nothing else, so a mandate
    that cannot be read must arrive as one rather than as its own vocabulary."""
    broken = tmp_path / "mandate.json"
    broken.write_text("nonsense", encoding="utf-8")
    monkeypatch.setenv(mandates.MANDATE_PATH, str(broken))

    with pytest.raises(PaymentError, match="Could not read the open mandate"):
        unattended()


@needs_ap2
def test_paying_on_an_open_mandate_is_reported_as_autonomous(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_mandates import open_mandate_file

    agent, _issuer = open_mandate_file(tmp_path / "mandate.json", maximum=40000)
    (tmp_path / "agent.pem").write_bytes(agent.export_to_pem(private_key=True, password=None))
    monkeypatch.setenv(mandates.MANDATE_PATH, str(tmp_path / "mandate.json"))
    monkeypatch.setenv(mandates.KEY_PATH, str(tmp_path / "agent.pem"))
    config = AgentConfig(pay=True)

    receipt = pay_for(cart_for(SONY, [SONY], config), config)

    assert receipt.autonomous is True
    assert receipt.enrolled_key is True


@needs_ap2
def test_a_cart_the_open_mandate_does_not_cover_is_refused_before_anything_is_sent(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_mandates import open_mandate_file

    agent, _issuer = open_mandate_file(tmp_path / "mandate.json", maximum=1000)
    (tmp_path / "agent.pem").write_bytes(agent.export_to_pem(private_key=True, password=None))
    monkeypatch.setenv(mandates.MANDATE_PATH, str(tmp_path / "mandate.json"))
    monkeypatch.setenv(mandates.KEY_PATH, str(tmp_path / "agent.pem"))
    config = AgentConfig(pay=True)

    with pytest.raises(PaymentError, match="does not authorise this purchase"):
        pay_for(cart_for(SONY, [SONY], config), config)


def test_the_price_label_is_pythons_wording_and_not_the_pages() -> None:
    cart = Cart(
        title="x",
        price=1234.5,
        currency="USD",
        amount=123450,
        merchant="Shop",
        url="https://shop.example/x",
        item_id="x",
    )

    assert cart.label() == "1,234.50 USD"


def test_the_module_never_reaches_the_config_at_import_time() -> None:
    """`config` imports `rails`, which imports this: an import back the other way
    would be a cycle, which is why `pay_for` is handed the config instead."""
    source = (payment.__file__ or "").replace("payment.py", "payment.py")
    with open(source, encoding="utf-8") as handle:
        body = handle.read()

    runtime = body.split("if TYPE_CHECKING:")[0]
    assert "from buy_agent.config" not in runtime
    assert "from buy_agent.rails" not in runtime


@needs_ap2
def test_a_rail_that_goes_away_between_the_price_and_the_payment_says_so(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second call to the rail translates the same way the first does: an
    endpoint down while it is being paid is the same failure as one down while
    it is being asked for a price."""
    import httpx

    from buy_agent import rails
    from tests.test_mandates import signed_checkout, write_key

    monkeypatch.setenv(mandates.KEY_PATH, str(write_key(tmp_path / "agent.pem")))
    merchant = signed_checkout()
    calls: list[str] = []

    def post(url: str, **_kwargs: Any) -> Any:
        calls.append(url)
        if url.endswith("/checkout"):
            return _Answer({"checkout_jwt": merchant.jwt})
        raise httpx.ReadTimeout("gone")

    monkeypatch.setattr(rails.httpx, "post", post)
    config = AgentConfig(pay=True, rail="http", merchant_url="https://pay.example")

    with pytest.raises(RailUnreachableError) as excinfo:
        pay_for(cart_for(SONY, [SONY], config), config)

    # The endpoint *and* the transport's own words: which of a refused
    # connection, a bad name and a timeout it was is what says what to fix.
    assert "Could not reach the payment endpoint" in str(excinfo.value)
    assert "gone" in str(excinfo.value)
    assert calls == ["https://pay.example/checkout", "https://pay.example/payment"]


class _Answer:
    """The little of an ``httpx`` response the rail reads."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self.payload


def test_a_page_with_no_scheme_names_no_merchant_of_its_own() -> None:
    """`Product.url` comes from a searched result, so this is the shape nothing
    upstream produces -- and it still has to read as something."""
    odd = SONY.model_copy(update={"seller": None, "url": "audiosite.example/xm5"})

    assert cart_for(odd, [odd], AgentConfig(pay=True)).merchant == "unknown merchant"


def test_a_password_in_an_address_never_becomes_the_merchant() -> None:
    """The merchant is read off the page's address, and an address can carry a
    user and a password.

    Counted by slashes, ``https://shopper:hunter2@audiosite.example/xm5`` made
    the merchant ``shopper:hunter2@audiosite.example`` -- which is the name shown
    on the surface a person approves, the name in a *signed* AP2 payload, and the
    name written into a receipt that is logged and handed to a browser. Three
    places a password has no business being.
    """
    odd = SONY.model_copy(
        update={"seller": None, "url": "https://shopper:hunter2@audiosite.example/xm5"}
    )

    cart = cart_for(odd, [odd], AgentConfig(pay=True))

    assert cart.merchant == "audiosite.example"
    payload = cart.merchant_payload()
    assert payload == {
        "id": "audiosite.example",
        "name": "audiosite.example",
        "website": "https://audiosite.example",
    }
    assert "hunter2" not in str(payload)


def test_a_port_is_part_of_where_a_site_is_and_stays() -> None:
    """Only the credentials are a secret. A shop served on another port is a
    different address, and dropping it would name a site nobody is serving."""
    odd = SONY.model_copy(
        update={"seller": None, "url": "https://audiosite.example:8443/xm5"}
    )

    cart = cart_for(odd, [odd], AgentConfig(pay=True))

    assert cart.merchant == "audiosite.example:8443"
    assert cart.merchant_payload()["website"] == "https://audiosite.example:8443"


def test_an_address_that_names_nothing_at_all_names_no_merchant() -> None:
    """``Product.url`` is checked before a cart is built, so a run cannot get
    here -- but ``_host`` is the function that answers "who would we be paying",
    and the honest answer to a blank is not a crash."""
    assert payment._host(None) == "unknown merchant"
    assert payment._host("") == "unknown merchant"


def test_an_address_too_malformed_to_read_names_no_merchant() -> None:
    """An unclosed IPv6 bracket makes ``urlsplit`` itself raise.

    Counted by slashes this used to answer with whatever sat between the second
    and third one; read properly it has to be caught, or a page with a mangled
    address turns a payment into a ``ValueError`` nothing above it catches -- and
    ``PaymentError`` is the one failure paying has.
    """
    assert payment._site("https://[::1/p") == ""
    assert payment._host("https://[::1/p") == "unknown merchant"


def test_an_address_with_no_site_in_it_is_used_as_it_stands() -> None:
    """``merchant_payload`` has always fallen back to the whole address, and a
    cart built from a schemeless URL still has to name something rather than
    write ``https://`` and stop."""
    odd = SONY.model_copy(update={"seller": "AudioSite", "url": "audiosite.example/xm5"})

    payload = cart_for(odd, [odd], AgentConfig(pay=True)).merchant_payload()

    assert payload["id"] == "audiosite.example/xm5"


# -- the edges the ranges and the rounding meet --------------------------------


def test_a_cart_priced_exactly_at_the_spend_limit_is_allowed() -> None:
    """The bound is a *limit*, so the number itself is inside it. Off by one the
    other way, a shopper who set their budget to the price they had in mind could
    never buy the thing they set it for."""
    cart = cart_for(SONY, [SONY], AgentConfig(pay=True, spend_limit=329.99))

    assert cart.amount == 32999


def test_a_penny_over_the_spend_limit_is_refused() -> None:
    with pytest.raises(PaymentError, match="over the"):
        cart_for(SONY, [SONY], AgentConfig(pay=True, spend_limit=329.98))


def test_a_cart_is_counted_in_the_currencys_own_units_and_not_always_hundredths() -> None:
    """`minor_units` knows JPY has no minor unit; this is what says `cart_for`
    actually tells it which currency. Told nothing, a 4,980 yen pair of
    headphones becomes an authorisation for 498,000."""
    yen = payable_product(price=4980.0, currency="JPY")

    assert cart_for(yen, [yen], AgentConfig(pay=True)).amount == 4980


def test_an_item_id_is_the_dedup_key_with_no_spaces_in_it() -> None:
    """It goes into the merchant's checkout as a line item id, so it is the
    product's identity spelled the way an id is spelled."""
    cart = cart_for(SONY, [SONY], AgentConfig(pay=True))

    assert cart.item_id == "sony-wh-1000xm5"
    assert " " not in cart.item_id


def test_a_very_long_name_is_cut_to_an_id_a_merchant_can_hold() -> None:
    long_name = Product(
        name="Sony " + "Extremely " * 40 + "Long",
        price=10.0,
        currency="USD",
        url="https://audiosite.example/x",
    )

    item_id = cart_for(long_name, [long_name], AgentConfig(pay=True)).item_id

    assert len(item_id) == 120


@pytest.mark.parametrize(
    ("product", "expected"),
    [
        (SONY.model_copy(update={"price": None}), "products"),
        (SONY.model_copy(update={"currency": None}), "products"),
        (SONY.model_copy(update={"url": None}), "products"),
    ],
    ids=["unpriced", "no currency", "no page"],
)
def test_every_refusal_names_the_field_the_browser_should_mark(
    product: Product, expected: str
) -> None:
    """`field` is what marks the box (ADR-0033). A refusal that names none is a
    sentence in a banner with nothing on the page to attach it to."""
    with pytest.raises(PaymentError) as excinfo:
        cart_for(product, [product], AgentConfig(pay=True))

    assert excinfo.value.field == expected


def test_a_price_off_the_runs_scale_names_the_field_too() -> None:
    euros = [
        SONY.model_copy(update={"currency": "EUR"}),
        BOSE.model_copy(update={"currency": "EUR"}),
    ]
    odd = SONY.model_copy(update={"currency": "USD", "name": "Odd"})

    with pytest.raises(PaymentError) as excinfo:
        cart_for(odd, [*euros, odd], AgentConfig(pay=True))

    assert excinfo.value.field == "products"


@needs_ap2
def test_an_unreachable_rail_carries_the_transports_own_words(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"Could not reach it" with no reason is a sentence nobody can act on: which
    of a refused connection, a DNS failure and a timeout it was is the whole of
    what tells someone what to fix."""
    import httpx

    from buy_agent import rails
    from tests.test_mandates import write_key

    monkeypatch.setenv(mandates.KEY_PATH, str(write_key(tmp_path / "agent.pem")))
    monkeypatch.setattr(
        rails.httpx, "post", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("nowhere"))
    )
    config = AgentConfig(pay=True, rail="http", merchant_url="https://pay.example")

    with pytest.raises(RailUnreachableError, match="nowhere"):
        pay_for(cart_for(SONY, [SONY], config), config)
