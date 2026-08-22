"""Numbers the search results never mentioned must not survive into the ranking."""

from __future__ import annotations

import pytest

from buy_agent.models import Product
from buy_agent.search import SearchResult
from buy_agent.verification import (
    build_haystack,
    drop_ungrounded,
    ground,
    mentions_name,
    mentions_number,
    mentions_rating,
    verify_numbers,
)

SOURCES = [
    SearchResult(
        title="Sony WH-CH720N deal",
        snippet="Now $129, rated 4.3 out of 5 from 12,500 shoppers.",
    ),
]
HAYSTACK = build_haystack(SOURCES)


def test_supported_figures_are_kept() -> None:
    product = Product(
        name="Sony WH-CH720N", price=129.0, currency="USD", rating=4.3, review_count=12500
    )

    verified = verify_numbers([product], HAYSTACK)[0]

    assert verified.price == 129.0
    assert verified.currency == "USD"
    assert verified.rating == 4.3
    assert verified.review_count == 12500


def test_an_invented_price_is_dropped() -> None:
    """The classic failure: a figure carried over from the prompt's own example."""
    product = Product(name="Sony WH-CH720N", price=99.0, currency="USD", rating=4.3)

    verified = verify_numbers([product], HAYSTACK)[0]

    assert verified.price is None
    assert verified.currency is None
    assert verified.rating == 4.3


def test_an_invented_review_count_is_dropped() -> None:
    product = Product(name="Sony WH-CH720N", price=129.0, review_count=90000)

    verified = verify_numbers([product], HAYSTACK)[0]

    assert verified.review_count is None
    assert verified.price == 129.0


def test_verification_never_drops_the_product_itself() -> None:
    products = [Product(name="Ghost Model", price=1.0, rating=1.0, review_count=1)]

    verified = verify_numbers(products, HAYSTACK)

    assert len(verified) == 1
    assert verified[0].name == "Ghost Model"


def test_thousands_separators_are_normalised() -> None:
    assert build_haystack(SOURCES).count("12500") == 1


@pytest.mark.parametrize("value", [129, 129.0, 4.3])
def test_numbers_present_in_the_text_are_found(value: float) -> None:
    assert mentions_number(HAYSTACK, value)


@pytest.mark.parametrize("value", [12, 29, 1129, 4.5, 99])
def test_digits_inside_a_longer_number_do_not_count(value: float) -> None:
    assert not mentions_number(HAYSTACK, value)


def test_a_price_written_with_decimals_still_matches() -> None:
    sources = [SearchResult(title="", snippet="On sale for $179.99 today")]

    assert mentions_number(build_haystack(sources), 179.99)


@pytest.mark.parametrize(
    ("snippet", "rating"),
    [
        ("Scores 4.6/5 overall", 4.6),
        ("Rated 4.6 out of 5", 4.6),
        ("A solid 4.6 stars", 4.6),
        ("Rating: 4.6", 4.6),
        ("Rated 5 stars", 5.0),
    ],
)
def test_ratings_are_recognised_however_they_are_written(snippet, rating) -> None:
    assert mentions_rating(build_haystack([SearchResult(snippet=snippet)]), rating)


def test_a_bare_number_is_not_a_rating() -> None:
    """'out of 5' must not vouch for a claimed 5.0, or every fake rating passes."""
    haystack = build_haystack([SearchResult(snippet="Rated 4.7 out of 5 by 500 people")])

    assert not mentions_rating(haystack, 5.0)
    assert not mentions_rating(haystack, 500.0)


def test_a_rounded_rating_is_not_treated_as_supported() -> None:
    haystack = build_haystack([SearchResult(snippet="Rated 4.3 out of 5")])

    assert not mentions_rating(haystack, 4.0)


def test_a_rounded_price_is_still_accepted() -> None:
    """Prices are quoted loosely ('about $180'), so rounding stays acceptable."""
    assert mentions_number(build_haystack([SearchResult(snippet="$179.99")]), 179.0)


def test_a_four_figure_price_with_decimals_survives() -> None:
    """Six significant digits are not enough: 12999.95 must not become "13000"."""
    sources = [SearchResult(snippet="The rig is $12,999.95 with the upgrade.")]

    assert mentions_number(build_haystack(sources), 12999.95)


def test_a_decimal_price_matches_a_trailing_zero() -> None:
    """A page writes 10000.5 as "$10,000.50"."""
    sources = [SearchResult(snippet="Yours for $10,000.50 today")]

    assert mentions_number(build_haystack(sources), 10000.50)
    assert not mentions_number(build_haystack(sources), 10000.55)


def test_an_expensive_product_keeps_its_price() -> None:
    sources = [SearchResult(title="Studio rig", snippet="Ampex ATR-102 -- $12,999.95")]
    product = Product(name="Ampex ATR-102", price=12999.95, currency="USD")

    verified = verify_numbers([product], build_haystack(sources))[0]

    assert verified.price == 12999.95
    assert verified.currency == "USD"


def test_a_score_out_of_ten_does_not_vouch_for_a_rating_out_of_five() -> None:
    """4.5/10 means 2.25/5; taking it as a 4.5 puts a mediocre product on top."""
    haystack = build_haystack([SearchResult(snippet="Our testers scored it 4.5 out of 10")])

    assert not mentions_rating(haystack, 4.5)
    assert not mentions_rating(build_haystack([SearchResult(snippet="Scores 4.5/10")]), 4.5)


def test_a_product_absent_from_the_sources_is_dropped() -> None:
    kept = drop_ungrounded(
        [Product(name="Sony WH-CH720N"), Product(name="Bonavita Gooseneck Kettle")], HAYSTACK
    )

    assert [product.name for product in kept] == ["Sony WH-CH720N"]


def test_descriptive_words_do_not_have_to_appear() -> None:
    """The page says 'WH-CH720N'; the model wrote a longer marketing name."""
    assert mentions_name(HAYSTACK, "Sony WH-CH720N Wireless Noise Cancelling Headphones")


def test_a_name_of_only_generic_words_is_not_grounded() -> None:
    assert not mentions_name(HAYSTACK, "Wireless Headphones")


def test_a_partly_matching_name_still_counts() -> None:
    """Two of three distinctive words is over the coverage floor."""
    assert mentions_name(HAYSTACK, "Sony WH-CH720N Studio")


def test_ground_drops_the_product_and_then_its_figures() -> None:
    grounded = ground(
        [
            Product(name="Sony WH-CH720N", price=1.0, rating=4.3),
            Product(name="Bonavita Gooseneck", price=80.0),
        ],
        SOURCES,
    )

    assert len(grounded) == 1
    assert grounded[0].price is None, "1.0 appears nowhere in the sources"
    assert grounded[0].rating == 4.3


def test_page_content_counts_as_a_source() -> None:
    """Figures come from the fetched page, not just the snippet."""
    sources = [SearchResult(title="Shop", snippet="Headphones", content="JBL Live 780NC\n$149")]

    assert mentions_name(build_haystack(sources), "JBL Live 780NC")
    assert mentions_number(build_haystack(sources), 149)
