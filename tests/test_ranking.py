"""Ranking is the part that decides the answer, so it gets the most tests."""

from __future__ import annotations

from typing import get_args

import pytest

import buy_agent.ranking as ranking_module
from buy_agent.models import Limit, Product
from buy_agent.ranking import (
    CRITERIA, NEUTRAL, RankingWeights, SortBy, placement_note, rank_products, score_product,
)


def product(name: str, **kwargs: object) -> Product:
    return Product(name=name, **kwargs)


def test_cheaper_wins_when_rating_is_equal() -> None:
    ranked = rank_products(
        [
            product("pricey", price=500.0, rating=4.5, review_count=100),
            product("bargain", price=100.0, rating=4.5, review_count=100),
        ]
    )
    assert ranked[0].product.name == "bargain"


def test_sort_by_price_puts_a_free_product_first() -> None:
    """Nothing is a price, and the lowest there is: a price of 0 is not read as the
    absence of one, which the first half of the key already sorts."""
    ranked = rank_products(
        [product("cheap", price=0.5), product("free", price=0.0), product("unpriced")],
        sort_by="price",
    )
    assert [entry.product.name for entry in ranked] == ["free", "cheap", "unpriced"]


def test_sort_by_rating_puts_a_zero_rating_below_every_other() -> None:
    """0.0 is a rating somebody gave, and the worst one: it sorts under 0.5 and above
    a product nobody rated, rather than being read as the missing figure it is not."""
    ranked = rank_products(
        [product("unrated"), product("awful", rating=0.0), product("poor", rating=0.5)],
        sort_by="rating",
    )
    assert [entry.product.name for entry in ranked] == ["poor", "awful", "unrated"]


def test_zero_weights_do_not_divide_by_zero() -> None:
    weights = RankingWeights(rating=0.0, popularity=0.0, price=0.0)
    ranked = rank_products([product("anything", price=10.0)], weights=weights)
    assert ranked[0].score == 0.0


def test_every_criterion_a_score_has_is_one_the_weights_name() -> None:
    """``CRITERIA`` is what lets a weight be looked up beside the share it weighs,
    in the report and on the card alike -- a fourth criterion added to one of the
    two classes and not the other would be drawn with somebody else's weight."""
    scored = rank_products([product("anything")])[0].breakdown

    assert set(CRITERIA) == set(RankingWeights().fractions)
    assert set(CRITERIA) <= set(scored.model_dump())


def popularity_of(review_count: int | None) -> float:
    """The popularity term on its own, with the other criteria weighted out."""
    return score_product(
        product("x", review_count=review_count), cheapest=None, priciest=None,
        weights=RankingWeights(rating=0.0, popularity=1.0, price=0.0),
    ).total


def test_each_tenfold_more_reviews_is_a_third_of_the_way_to_saturation() -> None:
    """The curve itself, which the tests either side of this one describe the shape
    of: ``log10(n + 1) / 3``, so 9 reviews score a third and 99 two thirds. A curve
    three times as steep would still rise, still flatten and still saturate, and
    would pass every one of them while reaching the ceiling at two reviews."""
    assert popularity_of(9) == pytest.approx(1 / 3)
    assert popularity_of(99) == pytest.approx(2 / 3)


def test_early_reviews_are_worth_more_than_late_ones() -> None:
    """Ten reviews say much more than nothing; ten thousand say no more than a thousand."""
    assert popularity_of(10) - popularity_of(1) > popularity_of(10_000) - popularity_of(1_000)


def test_scores_are_normalised_by_the_total_weight() -> None:
    """Weights that do not sum to 1 must not push scores outside [0, 1]."""
    weights = RankingWeights(rating=2.0, popularity=1.0, price=1.0)
    best = score_product(
        Product(name="Best", rating=5.0, review_count=10_000, price=10.0), cheapest=10.0,
        priciest=100.0, weights=weights,
    )

    assert best.total == pytest.approx(1.0)


# -- one scale, one currency (ADR-0043) ----------------------------------------

EURO = Product(name="Elsewhere", price=90.0, currency="EUR")


# -- the currency the shopper named (ADR-0056) --------------------------------


# -- what a listing no page has in stock ranks as (ADR-0095) --------------------

#: First on score alone: cheapest, well rated, well reviewed -- and gone.
GONE = Product(
    name="Gone", price=89.0, currency="USD", rating=4.2, review_count=850,
    availability="out of stock",
)
STOCKED = Product(
    name="Stocked", price=328.0, currency="USD", rating=4.7, review_count=3200,
    availability="in stock",
)
UNSAID = Product(name="Unsaid", price=79.0, currency="USD", rating=4.4)


@pytest.mark.parametrize("sort_by", ["score", "price", "rating"])
def test_a_listing_no_page_has_in_stock_ranks_after_every_other(sort_by: SortBy) -> None:
    """Whatever the order, the first answer is one somebody can buy now -- and an
    unknown standing, which is most pages, is not evidence of anything."""
    ranked = rank_products([GONE, STOCKED, UNSAID], sort_by=sort_by)

    assert ranked[-1].product.name == "Gone"
    assert [entry.rank for entry in ranked] == [1, 2, 3]


def test_sinking_keeps_each_half_in_the_order_asked_for() -> None:
    other = GONE.model_copy(update={"name": "Also gone", "price": 95.0})
    ranked = rank_products([other, STOCKED, GONE, UNSAID], sort_by="price")

    assert [entry.product.name for entry in ranked] == ["Unsaid", "Stocked", "Gone", "Also gone"]


def test_a_listing_out_of_stock_keeps_the_score_it_would_have() -> None:
    """Only its place moves: the breakdown still says why it was a contender."""
    ranked = {entry.product.name: entry for entry in rank_products([GONE, STOCKED, UNSAID])}

    assert ranked["Gone"].score > ranked["Stocked"].score
    assert ranked["Gone"].rank == 3


def test_a_set_with_nothing_in_stock_ranks_as_it_would_have() -> None:
    other = GONE.model_copy(update={"name": "Also gone", "price": 300.0, "rating": 3.0})
    ranked = rank_products([other, GONE])

    assert [entry.product.name for entry in ranked] == ["Gone", "Also gone"]


def test_condition_does_not_move_a_listing() -> None:
    """Refurbished is what some shoppers want; the card says which."""
    refurbished = UNSAID.model_copy(update={"condition": "refurbished"})
    ranked = rank_products([STOCKED, refurbished])

    assert ranked[0].product.name == "Unsaid"


# -- what a limit could not check ranks as, and why each place is what it is (ADR-0097)

#: Scores highest of the three, and no limit had a figure to check it by.
UNJUDGED = Product(name="Unjudged", rating=4.9, review_count=9000, unchecked=["max_price"])
WITHIN = Product(name="Within", price=150.0, currency="USD", rating=4.2, review_count=300)


@pytest.mark.parametrize("sort_by", ["score", "price", "rating"])
def test_a_product_no_limit_could_check_ranks_after_those_one_could(sort_by: SortBy) -> None:
    ranked = rank_products([UNJUDGED, WITHIN, GONE], sort_by=sort_by)

    assert [entry.product.name for entry in ranked] == ["Within", "Unjudged", "Gone"]


def test_out_of_stock_comes_last_even_among_the_unchecked() -> None:
    gone_and_unjudged = GONE.model_copy(update={"unchecked": ["max_price"]})

    ranked = rank_products([gone_and_unjudged, UNJUDGED, WITHIN])

    assert [entry.product.name for entry in ranked] == ["Within", "Unjudged", "Gone"]


def test_the_unchecked_keep_the_score_they_would_have() -> None:
    ranked = {entry.product.name: entry for entry in rank_products([UNJUDGED, WITHIN])}

    assert ranked["Unjudged"].score > ranked["Within"].score
    assert ranked["Unjudged"].rank == 2


def test_each_place_a_score_did_not_decide_is_said_in_a_sentence() -> None:
    assert placement_note(GONE) == "Ranked last: a page says it is out of stock."
    assert placement_note(UNJUDGED) == (
        "Ranked after the products this run's limits could check: its price is unknown "
        "or in another currency."
    )
    every = Product(name="x", unchecked=["max_price", "min_rating", "min_reviews"])
    assert placement_note(every) == (
        "Ranked after the products this run's limits could check: its price is unknown "
        "or in another currency, it has no rating and its review count is unknown."
    )
    assert placement_note(WITHIN) is None
    # Out of stock is the place it takes, whatever else is unknown.
    assert placement_note(GONE.model_copy(update={"unchecked": ["min_rating"]})) == (
        "Ranked last: a page says it is out of stock."
    )


def test_every_limit_has_its_phrase() -> None:
    """A limit added to ``models.Limit`` without one would fail on the card."""
    assert set(ranking_module._UNCHECKED) == set(get_args(Limit))


# -- what an unknown price scores (ADR-0096) -------------------------------------


def test_an_unknown_price_scores_what_the_dearest_price_does() -> None:
    """0.5 on a price is the middle of the set: a typical price. An unknown one scored
    so beat a known product that was merely the dearest; it now scores the dearest's
    share, and is still named as assumed."""
    unpriced = Product(name="Unpriced", rating=4.6, review_count=12_000)
    dearest = Product(name="Dearest", price=328.0, currency="USD", rating=4.7, review_count=3200)
    cheap = Product(name="Cheap", price=79.0, currency="USD", rating=4.4, review_count=100)

    ranked = {entry.product.name: entry for entry in rank_products([unpriced, dearest, cheap])}

    assert ranked["Unpriced"].breakdown.price == 0.0
    assert ranked["Unpriced"].breakdown.price == ranked["Dearest"].breakdown.price
    assert ranked["Unpriced"].breakdown.neutral == ["price"]
    assert ranked["Dearest"].rank < ranked["Unpriced"].rank


def test_an_unknown_price_is_neutral_where_every_price_ties() -> None:
    """With one distinct price there is no dearest to take after: every share is
    neutral, the unknown one too."""
    ranked = rank_products(
        [
            Product(name="A", price=50.0, currency="USD"),
            Product(name="B", price=50.0, currency="USD"), Product(name="Unpriced"),
        ]
    )

    assert {entry.breakdown.price for entry in ranked} == {NEUTRAL}


def test_an_unknown_price_is_neutral_where_nothing_is_priced() -> None:
    ranked = rank_products([Product(name="A", rating=4.0), Product(name="B", rating=3.0)])

    assert {entry.breakdown.price for entry in ranked} == {NEUTRAL}


def test_a_price_in_another_currency_scores_as_the_dearest_too() -> None:
    """Unplaced on the run's scale (ADR-0043), so unknown on it."""
    ranked = {
        entry.product.name: entry
        for entry in rank_products(
            [
                Product(name="Here", price=100.0, currency="USD"),
                Product(name="Dear", price=300.0, currency="USD"), EURO,
            ]
        )
    }

    assert ranked["Elsewhere"].breakdown.price == 0.0
    assert ranked["Elsewhere"].breakdown.neutral == ["rating", "popularity", "price"]


def test_an_unknown_rating_and_count_still_score_neutral() -> None:
    scored = score_product(
        Product(name="Blank", price=10.0), cheapest=10.0, priciest=20.0, weights=RankingWeights()
    )

    assert (scored.rating, scored.popularity, scored.price) == (NEUTRAL, NEUTRAL, 1.0)
