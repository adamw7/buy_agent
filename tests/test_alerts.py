"""Whether a run found the price the shopper is waiting for (ADR-0080)."""

from __future__ import annotations

from buy_agent.alerts import price_alert
from buy_agent.models import Product
from tests.conftest import ranked_product


def ranked(*products: Product):
    return [
        ranked_product(product, score=1 - index / 10, rank=index + 1)
        for index, product in enumerate(products)
    ]


def test_the_products_at_or_under_the_line_are_named_cheapest_first() -> None:
    alert = price_alert(
        ranked(
            Product(name="Dear", price=250.0, currency="USD"),
            Product(name="Exactly", price=180.0, currency="USD"),
            Product(name="Cheap", price=150.0, currency="USD"),
        ),
        180,
    )

    assert alert.met == ["Cheap", "Exactly"]
    assert alert.below_label == "180.00 USD"
    assert alert.detail == "At or under 180.00 USD: Cheap at 150.00 USD and Exactly at 180.00 USD."
    assert alert.payload() == {
        "below": 180,
        "below_label": "180.00 USD",
        "met": ["Cheap", "Exactly"],
        "detail": alert.detail,
    }


def test_nothing_under_the_line_names_the_cheapest() -> None:
    alert = price_alert(
        ranked(
            Product(name="Dear", price=250.0, currency="USD"),
            Product(name="Less dear", price=199.0, currency="USD"),
        ),
        180,
    )

    assert alert.met == []
    assert alert.detail == (
        "Nothing found is at or under 180.00 USD; the cheapest is Less dear at 199.00 USD."
    )


def test_an_out_of_stock_listing_never_meets_it_and_is_said_to_be_under_it() -> None:
    """The price a page printed beside "out of stock" is not one anybody can pay
    (ADR-0079), so it would be a false alarm."""
    alert = price_alert(
        ranked(
            Product(name="Gone", price=99.0, currency="USD", availability="out of stock"),
            Product(name="Here", price=120.0, currency="USD", availability="in stock"),
        ),
        100,
    )

    assert alert.met == []
    assert alert.detail == (
        "Nothing found is at or under 100.00 USD; the cheapest is Here at 120.00 USD. "
        "Gone is at or under it, but out of stock where it was priced."
    )


def test_everything_priced_being_out_of_stock_is_said_so() -> None:
    alert = price_alert(
        ranked(
            Product(name="Gone", price=99.0, currency="USD", availability="out of stock"),
            Product(name="Also gone", price=95.0, currency="USD", availability="out of stock"),
        ),
        100,
    )

    assert alert.detail == (
        "Nothing found that can be bought is at or under 100.00 USD. "
        "Also gone and Gone are at or under it, but out of stock where it was priced."
    )


def test_a_price_in_another_currency_cannot_meet_it() -> None:
    """Nothing is converted (ADR-0043): 90 EUR is not under 100 USD, nor over it."""
    alert = price_alert(
        ranked(Product(name="Euro", price=90.0, currency="EUR")),
        100,
        "USD",
    )

    assert alert.met == []
    assert alert.detail == (
        "Nothing found is at or under 100.00 USD: no page printed a price this run can "
        "count in USD."
    )


def test_an_unknown_price_cannot_meet_it() -> None:
    alert = price_alert(ranked(Product(name="Unpriced")), 100)

    assert alert.met == []
    assert alert.below_label == "100.00"
    assert alert.detail == "Nothing found is at or under 100.00: no page printed a price."


def test_a_run_that_found_nothing_meets_nothing() -> None:
    assert price_alert([], 100).met == []
