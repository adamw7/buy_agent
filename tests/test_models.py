"""The sentinel-to-None conversion is what keeps small models from breaking a run."""

from __future__ import annotations

import pytest

from buy_agent.models import (
    _MAX_OPINION_LENGTH,
    ExtractedProduct,
    Offer,
    Product,
    dominant_currency,
)
from tests.conftest import said


def test_a_currency_without_a_price_never_becomes_one() -> None:
    """The other half of ADR-0022's rule, at the stage the review count keeps it."""
    converted = ExtractedProduct(name="Thing", price=-1, currency="EUR").to_product()

    assert converted.price is None
    assert converted.currency is None


def test_a_rating_just_over_the_scale_is_discarded() -> None:
    """A 5.1 is a score off some other scale, not a product that beat this one."""
    assert ExtractedProduct(name="Thing", rating=5.1).to_product().rating is None


def test_a_single_review_is_still_a_review_count() -> None:
    """0 is the sentinel for unknown; 1 is a product with one review."""
    converted = ExtractedProduct(name="Thing", rating=4.2, review_count=1).to_product()

    assert converted.review_count == 1


def test_the_seller_and_the_notes_survive_conversion() -> None:
    converted = ExtractedProduct(
        name="Thing", seller="  Amazon ", url=" https://shop.example ", notes="\n Quiet. "
    ).to_product()

    assert converted.seller == "Amazon"
    assert converted.url == "https://shop.example"
    assert converted.notes == "Quiet."


def test_a_zero_price_is_unknown_not_free() -> None:
    """Zero is what a model writes when it has forgotten the -1 sentinel."""
    assert ExtractedProduct(name="Freebie", price=0.0).to_product().price is None
    assert ExtractedProduct(name="Freebie", price=0.0).to_product().price_label() == (
        "price unknown"
    )


def test_the_smallest_real_price_still_survives() -> None:
    """Unknown is zero and below, not "small": a cheap thing keeps its price."""
    assert ExtractedProduct(name="Cable", price=0.01).to_product().price == 0.01


def test_the_rating_scale_includes_its_own_endpoints() -> None:
    assert ExtractedProduct(name="Thing", rating=0.0).to_product().rating == 0.0
    assert ExtractedProduct(name="Thing", rating=5.0).to_product().rating == 5.0


def test_rating_label_omits_a_review_count_it_does_not_have() -> None:
    assert Product(name="Thing", rating=4.0).rating_label() == "4.0/5"


def test_quoted_opinions_survive_conversion_tidied() -> None:
    converted = ExtractedProduct(
        name="Thing", opinions=["  the fit   is snug ", "", "the case is bulky"]
    ).to_product()

    assert converted.opinions == said("the fit is snug", "the case is bulky")


def test_a_quote_exactly_as_long_as_a_card_holds_is_kept() -> None:
    """The limit is what a quote may run to, not what it must stay under -- and
    the boundary is the only length at which the two readings differ."""
    quote = (
        "Reviewers found the fit snug, the battery life excellent and the case a little "
        "bulky for a coat pocket, but said that the noise cancelling here is the best "
        "they have tested anywhere near this price and easily worth the money all on its own."
    )
    assert len(quote) == _MAX_OPINION_LENGTH

    assert ExtractedProduct(name="Thing", opinions=[quote]).to_product().opinions == said(
        quote
    )


def test_a_review_count_of_zero_is_no_count_at_all() -> None:
    """Nobody has reviewed it yet, which is the absence of the figure rather than
    the figure zero -- and the schema's sentinels are meant to arrive as ``None``
    whichever of them the model reached for."""
    converted = ExtractedProduct(name="Thing", rating=4.5, review_count=0).to_product()

    assert converted.review_count is None
    assert converted.rating == 4.5


# -- which currency a set of prices is counted in (ADR-0043) -------------------

EURO = Product(name="Euro", price=90.0, currency="EUR")
DOLLAR = Product(name="Dollar", price=100.0, currency="USD")
UNPRICED = Product(name="Unpriced", currency="EUR")


@pytest.mark.parametrize(
    ("products", "expected", "why"),
    [
        pytest.param([DOLLAR, EURO, DOLLAR], "USD", "the commonest one named", id="commonest"),
        # Ties go to the one seen first, which is the search's own order and the tie-break
        # every other merge here makes -- so a set is counted in the same currency twice.
        pytest.param([EURO, DOLLAR], "EUR", "ties go to the first seen", id="tie, euro first"),
        pytest.param([DOLLAR, EURO], "USD", "ties go to the first seen", id="tie, dollar first"),
        pytest.param(
            [DOLLAR, UNPRICED, UNPRICED],
            "USD",
            "a currency with no price beside it describes nothing (ADR-0022), so "
            "it does not get to decide what the set is counted in",
            id="a currency qualifying nothing",
        ),
        pytest.param([Product(name="Bare", price=100.0)], None, "nobody named one", id="bare"),
        pytest.param([], None, "nothing to count", id="empty"),
    ],
)
def test_which_currency_a_set_is_counted_in(
    products: list[Product], expected: str | None, why: str
) -> None:
    assert dominant_currency(products) == expected, why


# -- the offers a product was priced at (ADR-0058) -----------------------------


def offer(price: float, currency: str | None = "USD", **rest: object) -> Offer:
    return Offer(price=price, currency=currency, **rest)


def test_several_listings_are_reported_as_a_range() -> None:
    priced = Product(
        name="Sony WH-1000XM5",
        price=329.0,
        currency="USD",
        offers=[offer(329.0), offer(149.0), offer(349.0)],
    )

    assert priced.offers_label() == "3 listings, 149.00-349.00 USD"


@pytest.mark.parametrize(
    ("elsewhere", "expected"),
    [
        # The laptops demo's third card: one shop in dollars, one in euros.
        pytest.param([offer(689.0, "EUR")], "2 listings: 749.00 USD, and 1 in EUR", id="one"),
        pytest.param(
            [offer(689.0, "EUR"), offer(699.0, "EUR")],
            "3 listings: 749.00 USD, and 2 in EUR",
            id="one currency, twice",
        ),
        pytest.param(
            [offer(689.0, "EUR"), offer(599.0, "GBP")],
            "3 listings: 749.00 USD, and 2 in other currencies",
            id="two currencies",
        ),
        # Not a currency at all, so not "another" one.
        pytest.param(
            [offer(749.0, None)], "2 listings: 749.00 USD, and 1 with no currency printed", id="bare"
        ),
    ],
)
def test_a_listing_off_the_scale_says_where_it_was(elsewhere: list[Offer], expected: str) -> None:
    priced = Product(
        name="Lenovo IdeaPad Slim 5 14", price=749.0, currency="USD", offers=[offer(749.0), *elsewhere]
    )

    assert priced.offers_label() == expected


def test_listings_none_of_which_are_on_the_scale_are_only_counted() -> None:
    priced = Product(
        name="Sony WH-1000XM5",
        price=None,
        currency=None,
        offers=[offer(329.0), offer(299.0, "EUR")],
    )

    assert priced.offers_label() == "2 listings"
