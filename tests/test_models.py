"""The sentinel-to-None conversion is what keeps small models from breaking a run."""

from __future__ import annotations

from buy_agent.models import ExtractedProduct, Product


def test_sentinels_become_none() -> None:
    converted = ExtractedProduct(
        name="Thing", price=-1, currency="", rating=-1, review_count=0
    ).to_product()
    assert converted.price is None
    assert converted.currency is None
    assert converted.rating is None
    assert converted.review_count is None


def test_real_values_survive_conversion() -> None:
    converted = ExtractedProduct(
        name="  Sony   WH-1000XM5 ",
        price=328.5,
        currency="usd",
        rating=4.7,
        review_count=12000,
        seller="Amazon",
    ).to_product()
    assert converted.name == "Sony WH-1000XM5"
    assert converted.currency == "USD"
    assert converted.price == 328.5
    assert converted.review_count == 12000


def test_out_of_range_rating_is_discarded() -> None:
    """Models sometimes report a 0-10 or percentage score despite the instruction."""
    assert ExtractedProduct(name="Thing", rating=9.2).to_product().rating is None
    assert ExtractedProduct(name="Thing", rating=88).to_product().rating is None


def test_dedup_key_ignores_case_punctuation_and_spacing() -> None:
    a = Product(name="Sony WH-1000XM5")
    b = Product(name="sony  wh 1000xm5!!")
    assert a.dedup_key == b.dedup_key


def test_labels_read_well_when_data_is_missing() -> None:
    bare = Product(name="Thing")
    assert bare.price_label() == "price unknown"
    assert bare.rating_label() == "unrated"


def test_labels_format_known_data() -> None:
    full = Product(name="Thing", price=1234.5, currency="USD", rating=4.25, review_count=9000)
    assert full.price_label() == "1,234.50 USD"
    assert full.rating_label() == "4.2/5 (9,000 reviews)"
