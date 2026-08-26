"""The sentinel-to-None conversion is what keeps small models from breaking a run."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from buy_agent.models import ExtractedProduct, Product, ProductList, SearchQuery


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


def test_a_rating_just_over_the_scale_is_discarded() -> None:
    """A 5.1 is a score off some other scale, not a product that beat this one.

    The obvious cases -- 9.2, 88 -- are rejected by a bound anywhere above 5, so
    the value that actually pins the bound is the one a step past it.
    """
    assert ExtractedProduct(name="Thing", rating=5.1).to_product().rating is None


def test_a_single_review_is_still_a_review_count() -> None:
    """0 is the sentinel for unknown; 1 is a product with one review."""
    assert ExtractedProduct(name="Thing", review_count=1).to_product().review_count == 1


def test_the_seller_and_the_notes_survive_conversion() -> None:
    converted = ExtractedProduct(
        name="Thing", seller="  Amazon ", url=" https://shop.example ", notes="\n Quiet. "
    ).to_product()

    assert converted.seller == "Amazon"
    assert converted.url == "https://shop.example"
    assert converted.notes == "Quiet."


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


def test_a_zero_price_is_free_not_unknown() -> None:
    """-1 is the sentinel; 0 is a real figure and must survive the conversion."""
    assert ExtractedProduct(name="Freebie", price=0.0).to_product().price == 0.0


def test_the_rating_scale_includes_its_own_endpoints() -> None:
    assert ExtractedProduct(name="Thing", rating=0.0).to_product().rating == 0.0
    assert ExtractedProduct(name="Thing", rating=5.0).to_product().rating == 5.0


def test_a_negative_review_count_is_treated_as_unknown() -> None:
    assert ExtractedProduct(name="Thing", review_count=-4).to_product().review_count is None


def test_only_the_name_is_required() -> None:
    """Every other field has a sentinel default, so a sparse answer still parses."""
    converted = ExtractedProduct(name="Thing").to_product()

    assert converted.name == "Thing"
    assert converted.price is None
    assert converted.rating is None
    assert converted.seller is None


def test_a_nameless_product_is_a_validation_error() -> None:
    with pytest.raises(ValidationError):
        ExtractedProduct()


def test_names_are_flattened_onto_one_line() -> None:
    """A model copies a name straight off a page, newlines and tabs included."""
    assert ExtractedProduct(name="Sony\n WH-1000XM5\t").to_product().name == "Sony WH-1000XM5"


def test_whitespace_only_text_fields_become_none() -> None:
    converted = ExtractedProduct(name="Thing", seller="  ", url=" ", notes="\n").to_product()

    assert converted.seller is None
    assert converted.url is None
    assert converted.notes is None


def test_an_empty_product_list_is_the_default() -> None:
    """A model that finds nothing answers with an empty list, not a failure."""
    assert ProductList().products == []


def test_the_query_schema_carries_one_query() -> None:
    assert SearchQuery(query="tent price review").query == "tent price review"


def test_price_label_without_a_currency_is_just_the_number() -> None:
    assert Product(name="Thing", price=99.0).price_label() == "99.00"


def test_rating_label_omits_a_review_count_it_does_not_have() -> None:
    assert Product(name="Thing", rating=4.0).rating_label() == "4.0/5"


def test_a_free_product_does_not_read_as_unpriced() -> None:
    assert Product(name="Thing", price=0.0).price_label() == "0.00"


def test_dedup_key_of_a_punctuation_only_name_is_empty() -> None:
    """deduplicate() drops these entries, and the empty key is what tells it to."""
    assert Product(name="!!! ---").dedup_key == ""


def test_dedup_key_separates_genuinely_different_models() -> None:
    assert Product(name="Sony WH-1000XM4").dedup_key != Product(name="Sony WH-1000XM5").dedup_key


def test_quoted_opinions_survive_conversion_tidied() -> None:
    converted = ExtractedProduct(
        name="Thing", opinions=["  the fit   is snug ", "", "the case is bulky"]
    ).to_product()

    assert converted.opinions == ["the fit is snug", "the case is bulky"]


def test_no_opinions_is_an_empty_list_and_not_a_none() -> None:
    """The one unknown that is not converted: "nobody said anything" and "no
    quote survived grounding" are the same answer, spelled one way."""
    assert ExtractedProduct(name="Thing").to_product().opinions == []
    assert Product(name="Thing").opinions == []


def test_the_same_opinion_twice_is_reported_once() -> None:
    """A model listing a page's verdict once per paragraph it appeared in."""
    converted = ExtractedProduct(
        name="Thing", opinions=["The fit is snug", "the FIT is snug"]
    ).to_product()

    assert converted.opinions == ["The fit is snug"]


def test_more_opinions_than_a_card_can_hold_are_cut_to_the_first_few() -> None:
    converted = ExtractedProduct(
        name="Thing", opinions=["one", "two", "three", "four"]
    ).to_product()

    assert converted.opinions == ["one", "two", "three"]


def test_a_paragraph_is_not_a_quote() -> None:
    """Dropped rather than cut short: half a sentence attributed to a reviewer
    says something the reviewer did not, the way half a name is another product."""
    retold = "The reviewers were impressed. " * 10

    assert ExtractedProduct(name="Thing", opinions=[retold]).to_product().opinions == []
