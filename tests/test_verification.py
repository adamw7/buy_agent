"""Numbers the search results never mentioned must not survive into the ranking."""

from __future__ import annotations

import logging

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


def test_an_empty_source_set_grounds_nothing() -> None:
    """No sources means no support -- not a free pass."""
    assert build_haystack([]) == ""
    assert drop_ungrounded([Product(name="Sony WH-CH720N")], "") == []


def test_grounding_nothing_yields_nothing() -> None:
    assert ground([], SOURCES) == []


def test_a_wholly_invented_listing_keeps_only_its_name() -> None:
    product = Product(
        name="Sony WH-CH720N", price=11.0, currency="USD", rating=1.1, review_count=7
    )

    verified = verify_numbers([product], HAYSTACK)[0]

    assert verified.name == "Sony WH-CH720N"
    assert verified.price is None
    assert verified.currency is None
    assert verified.rating is None
    assert verified.review_count is None


def test_verification_copies_rather_than_editing_in_place() -> None:
    original = Product(name="Sony WH-CH720N", price=99.0)

    verified = verify_numbers([original], HAYSTACK)[0]

    assert original.price == 99.0
    assert verified.price is None


def test_a_free_extra_keeps_its_price() -> None:
    """0 is a real price; blanking it would score a giveaway as unknown."""
    haystack = build_haystack([SearchResult(snippet="Bundled adapter: $0 with purchase")])

    assert mentions_number(haystack, 0)


def test_a_price_inside_a_longer_price_is_not_a_match() -> None:
    haystack = build_haystack([SearchResult(snippet="Was $1299.99, now less")])

    assert not mentions_number(haystack, 129.0)


def test_a_rating_lead_in_out_of_ten_is_rejected() -> None:
    """'scored it 8 out of 10' means 4/5, and must not vouch for a claimed 8."""
    haystack = build_haystack([SearchResult(snippet="Our testers scored it 8 out of 10")])

    assert not mentions_rating(haystack, 8)


def test_a_rating_below_the_claimed_one_does_not_vouch_for_it() -> None:
    haystack = build_haystack([SearchResult(snippet="Rated 4.3 out of 5")])

    assert not mentions_rating(haystack, 4.7)


def test_one_distinctive_word_in_four_is_not_a_mention() -> None:
    assert not mentions_name(HAYSTACK, "Bose QuietComfort Ultra Sony")


def test_a_name_whose_only_distinctive_word_is_present_counts() -> None:
    assert mentions_name(HAYSTACK, "Wireless Sony Headphones")
    assert not mentions_name(HAYSTACK, "Wireless Bose Headphones")


def test_a_nameless_product_is_never_grounded() -> None:
    assert not mentions_name(HAYSTACK, "")
    assert not mentions_name(HAYSTACK, "--- !!!")


def test_the_title_is_searched_as_well_as_the_snippet() -> None:
    sources = [SearchResult(title="Ampex ATR-102 for sale", snippet="Studio gear")]

    assert mentions_name(build_haystack(sources), "Ampex ATR-102")


def test_figures_are_verified_across_all_of_the_results() -> None:
    """One page named the product, another quoted the price; both are sources."""
    sources = [
        SearchResult(title="JBL Live 780NC hands-on", snippet="A solid pair."),
        SearchResult(title="Deals roundup", snippet="The 780NC is $149 this week."),
    ]

    grounded = ground([Product(name="JBL Live 780NC", price=149.0)], sources)

    assert grounded[0].price == 149.0


def test_grounding_reports_what_it_dropped(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="buy_agent.verification"):
        ground([Product(name="Bonavita Gooseneck Kettle", price=80.0)], SOURCES)

    assert "Dropped 1 product(s) absent from the search results" in caplog.text


def test_a_slash_rating_needs_no_lead_in_word() -> None:
    """"4.6/5" is a rating on its own; nothing has to introduce it."""
    haystack = build_haystack([SearchResult(snippet="The Sony sits at 4.6/5 overall")])

    assert mentions_rating(haystack, 4.6)


def test_a_rating_written_as_of_five_is_recognised() -> None:
    """Pages drop the "out": "a steady 4.6 of 5"."""
    haystack = build_haystack([SearchResult(snippet="A steady 4.6 of 5 across the panel")])

    assert mentions_rating(haystack, 4.6)


def test_a_rating_is_recognised_whatever_its_case() -> None:
    """Shop pages shout their figures in headings, with no lead-in word to lean on."""
    assert mentions_rating(build_haystack([SearchResult(snippet="A SOLID 4.6 STARS")]), 4.6)
    assert mentions_rating(build_haystack([SearchResult(snippet="4.6 OUT OF 5")]), 4.6)


def test_a_scored_lead_in_is_a_rating() -> None:
    """"scored it 4.6" names a rating without ever writing the scale."""
    assert mentions_rating(build_haystack([SearchResult(snippet="Reviewers scored it 4.6")]), 4.6)
    assert mentions_rating(build_haystack([SearchResult(snippet="We score it 4.6 here")]), 4.6)


def test_a_lead_in_word_far_from_the_figure_does_not_vouch_for_it() -> None:
    """"rated" and a number a sentence apart are not one claim about one product."""
    near = build_haystack([SearchResult(snippet="rated a solid 4.6 by our testers")])
    far = build_haystack([SearchResult(snippet="rated by our whole panel of testers, 4.6")])

    assert mentions_rating(near, 4.6)
    assert not mentions_rating(far, 4.6)


def test_a_rating_that_is_only_the_tail_of_a_longer_number_is_rejected() -> None:
    """As with prices, a figure must not be verified by digits it merely ends."""
    haystack = build_haystack([SearchResult(snippet="Rated 14.6 out of 5")])

    assert not mentions_rating(haystack, 4.6)


def test_the_coverage_bar_is_three_distinctive_words_in_five() -> None:
    """Where the 0.6 floor falls decides whether an invented name can pass."""
    haystack = build_haystack([SearchResult(snippet="The Anker Soundcore Life sits at $99.")])

    assert mentions_name(haystack, "Anker Soundcore Life Q30 Pro"), "3 of 5 clears the bar"
    assert not mentions_name(haystack, "Anker Soundcore Boost Max Pro"), "2 of 5 does not"
