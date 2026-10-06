"""What may be paid for, for how much, and what comes back."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from buy_agent import mandates, payment
from buy_agent.config import AgentConfig
from buy_agent.models import Offer, Product
from buy_agent.payment import (
    PaymentError,
    RailUnreachableError,
    cart_for,
    merchant_for,
    terms_for,
    pay_for,
    unattended,
)
from tests.conftest import (
    enrolled_key,
    needs_ap2,
    open_mandate,
    payable_product,
)

SONY = payable_product(rating=4.6, review_count=1200)

BOSE = Product(name="Bose QC Ultra", price=379.0, currency="USD", url="https://x.example/b")


# -- what may be paid for ------------------------------------------------------


def refused(product: Product, currency: str | None) -> str:
    """Why this product may not be paid for, as a string to look into."""
    return terms_for(product, currency)[1] or ""


def test_a_product_whose_price_grounding_blanked_may_not_be() -> None:
    """The ranking rule turned around: never rank on an unverified number
    becomes never pay on one."""
    unpriced = SONY.model_copy(update={"price": None})

    assert "nothing to authorise" in refused(unpriced, "USD")
    assert terms_for(unpriced, "USD")[0] is None


def test_a_price_under_one_unit_is_still_an_amount() -> None:
    """The line is at nothing, not at a whole unit: a cable at 99 cents is owed."""
    cheap = SONY.model_copy(update={"price": 0.99})

    assert terms_for(cheap, "USD") == ((0.99, "USD"), None)


@pytest.mark.parametrize("price", [0.0, -42.5])
def test_a_price_that_is_no_amount_may_not_be_paid_either(price: float) -> None:
    """The other way a price can fail to be one."""
    odd = SONY.model_copy(update={"price": price})

    assert "not an amount to send" in refused(odd, "USD")
    assert terms_for(odd, "USD")[0] is None


def test_a_run_where_no_page_named_a_currency_has_no_amount_to_send() -> None:
    assert "not an amount" in refused(SONY, None)


@pytest.mark.parametrize("scale", ["¥", "BUCKS"])
def test_a_scale_that_names_no_currency_is_no_scale_to_send_an_amount_on(scale: str) -> None:
    """The run's own currency is whatever the pages spelled, which ``money.code_for``
    hands back as written: "¥" is the yen's sign and the yuan's alike (ADR-0054), and a
    small model reporting "bucks" names nothing at all. Ranking places neither and scores
    them ``NEUTRAL``; paying may not send an amount counted in hundredths of a unit
    nobody has said are hundredths."""
    priced = SONY.model_copy(update={"currency": scale})

    assert "names no currency" in refused(priced, scale)
    assert terms_for(priced, scale)[0] is None
    with pytest.raises(PaymentError, match="names no currency"):
        cart_for(priced, [priced], AgentConfig(pay=True))


def test_a_product_with_no_source_page_has_no_merchant_to_pay() -> None:
    unlinked = SONY.model_copy(update={"url": None})

    assert "no source page" in refused(unlinked, "USD")


def test_a_figure_the_cart_cannot_count_is_refused_as_a_payment_would_be() -> None:
    """``money.minor_units`` raises a ``ValueError``, knowing nothing about who is being
    paid; the refusal a shopper sees is this module's, and it names the field the form
    marks (ADR-0033)."""
    with pytest.raises(PaymentError, match="not a price") as excinfo:
        cart_for(
            Product(name="Odd", price=1e308, currency="USD", url="https://x.example/o"),
            [SONY],
            AgentConfig(pay=True),
        )

    assert excinfo.value.field == "products"


# -- the cart ------------------------------------------------------------------


# -- the offer a cart is actually for (ADR-0058) -------------------------------


#: The case the offers exist for: the winning listing printed the price and no shop,
#: and a merge filled that blank from a listing selling at something else entirely.
BORROWED = SONY.model_copy(
    update={
        "seller": "ShopB",
        "offers": [
            Offer(price=SONY.price, currency="USD", seller=None, url="https://a.example/p"),
            Offer(price=499.0, currency="USD", seller="ShopB", url="https://b.example/p"),
        ],
    }
)


@pytest.mark.parametrize(
    "decoy",
    [
        Offer(price=SONY.price, currency="EUR", seller="EuroShop", url="https://eu.example/p"),
        Offer(price=499.0, currency="USD", seller="ShopB", url="https://b.example/p"),
    ],
    ids=["same figure, other currency", "same currency, other figure"],
)
def test_an_offer_matching_only_half_of_the_pair_is_not_the_one_bought(decoy: Offer) -> None:
    """Listed ahead of the real one, so a match on either half alone would find it
    first and name its shop on the cart."""
    listed = SONY.model_copy(
        update={
            "seller": None,
            "offers": [
                decoy,
                Offer(price=SONY.price, currency="USD", seller="ShopA", url="https://a.example/p"),
            ],
        }
    )

    assert payment.offer_for(listed) == listed.offers[1]
    assert merchant_for(listed) == "ShopA"


def test_a_merchant_is_never_the_shop_that_quoted_another_price() -> None:
    """Before the offers, a winner that named no shop took the loser's -- so the cart
    named the seller of the 499 beside the price of the 329.99."""
    cart = cart_for(BORROWED, [BORROWED], AgentConfig(pay=True))

    assert (cart.merchant, cart.url) == ("a.example", "https://a.example/p")
    assert merchant_for(BORROWED) == "a.example"


# -- paying --------------------------------------------------------------------


@needs_ap2
def test_an_authorisation_is_logged_with_what_it_was_for_and_who_gave_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The one line a terminal keeps of a purchase: the item, the amount, whether a
    person or an open mandate authorised it, and the transaction a merchant would
    quote back."""
    config = AgentConfig(pay=True)

    with caplog.at_level(logging.INFO, logger="buy_agent.payment"):
        receipt = pay_for(cart_for(SONY, [SONY], config), config)

    assert (
        f"Authorised {SONY.name} at 329.99 USD (approved in person), "
        f"transaction {receipt.transaction_id}"
    ) in caplog.text


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
    assert f"the {config.rail_used.label} chain" in caplog.text


@needs_ap2
def test_a_real_rail_will_not_sign_without_an_enrolled_key() -> None:
    config = AgentConfig(pay=True, rail="http", merchant_url="https://pay.example")

    with pytest.raises(PaymentError, match="No signing key"):
        pay_for(cart_for(SONY, [SONY], config), config)


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
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    agent, _issuer = open_mandate(tmp_path, monkeypatch)
    enrolled_key(tmp_path, monkeypatch, agent)
    config = AgentConfig(pay=True)

    with caplog.at_level(logging.INFO, logger="buy_agent.payment"):
        receipt = pay_for(cart_for(SONY, [SONY], config), config)

    assert receipt.autonomous is True
    assert receipt.enrolled_key is True
    assert "(an open mandate)" in caplog.text


@needs_ap2
def test_a_cart_the_open_mandate_does_not_cover_is_refused_before_anything_is_sent(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, _issuer = open_mandate(tmp_path, monkeypatch, maximum=1000)
    enrolled_key(tmp_path, monkeypatch, agent)
    config = AgentConfig(pay=True)

    with pytest.raises(PaymentError, match="does not authorise this purchase"):
        pay_for(cart_for(SONY, [SONY], config), config)


@needs_ap2
def test_a_rail_that_goes_away_between_the_price_and_the_payment_says_so(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second call to the rail translates the same way the first does: an
    endpoint down while it is being paid is the same failure as one down while
    it is being asked for a price."""
    import httpx

    from buy_agent import rails
    from tests.test_mandates import signed_checkout

    enrolled_key(tmp_path, monkeypatch)
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


def test_a_password_in_an_address_never_becomes_the_merchant() -> None:
    """The merchant is read off the page's address, and an address can carry a user and a
    password."""
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


def test_an_address_that_names_nothing_at_all_names_no_merchant() -> None:
    """``Product.url`` is checked before a cart is built, so a run cannot get
    here -- but ``_host`` is the function that answers "who would we be paying",
    and the honest answer to a blank is not a crash."""
    assert payment._host(None) == "unknown merchant"
    assert payment._host("") == "unknown merchant"


def test_an_address_too_malformed_to_read_names_no_merchant() -> None:
    """An unclosed IPv6 bracket makes ``urlsplit`` itself raise."""
    assert payment._site("https://[::1/p") == ""
    assert payment._host("https://[::1/p") == "unknown merchant"


# -- the edges the ranges and the rounding meet --------------------------------


def test_a_cart_priced_exactly_at_the_spend_limit_is_allowed() -> None:
    """The bound is a *limit*, so the number itself is inside it."""
    cart = cart_for(SONY, [SONY], AgentConfig(pay=True, spend_limit=329.99))

    assert cart.amount == 32999


def test_a_cart_is_counted_in_the_currencys_own_units_and_not_always_hundredths() -> None:
    """`minor_units` knows JPY has no minor unit; this is what says `cart_for` actually
    tells it which currency."""
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
        (SONY.model_copy(update={"price": 0.0}), "products"),
        (SONY.model_copy(update={"currency": "¥"}), "products"),
    ],
    ids=["unpriced", "no currency", "no page", "no amount", "no scale"],
)
def test_every_refusal_names_the_field_the_browser_should_mark(
    product: Product, expected: str
) -> None:
    """`field` is what marks the box (ADR-0033)."""
    with pytest.raises(PaymentError) as excinfo:
        cart_for(product, [product], AgentConfig(pay=True))

    assert excinfo.value.field == expected


@needs_ap2
def test_an_unreachable_rail_carries_the_transports_own_words(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"Could not reach it" with no reason is a sentence nobody can act on: which
    of a refused connection, a DNS failure and a timeout it was is the whole of
    what tells someone what to fix."""
    import httpx

    from buy_agent import rails

    enrolled_key(tmp_path, monkeypatch)
    monkeypatch.setattr(
        rails.httpx, "post", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("nowhere"))
    )
    config = AgentConfig(pay=True, rail="http", merchant_url="https://pay.example")

    with pytest.raises(RailUnreachableError, match="nowhere"):
        pay_for(cart_for(SONY, [SONY], config), config)
