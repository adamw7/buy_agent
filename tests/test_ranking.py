"""Ranking is the part that decides the answer, so it gets the most tests."""

from __future__ import annotations

import pytest

from buy_agent.models import Product
from buy_agent.ranking import (
    CRITERIA,
    RankingWeights,
    rank_products,
    score_product,
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
        [
            product("unrated"),
            product("awful", rating=0.0),
            product("poor", rating=0.5),
        ],
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
        product("x", review_count=review_count),
        cheapest=None,
        priciest=None,
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
        Product(name="Best", rating=5.0, review_count=10_000, price=10.0),
        cheapest=10.0,
        priciest=100.0,
        weights=weights,
    )

    assert best.total == pytest.approx(1.0)


# -- one scale, one currency (ADR-0043) ----------------------------------------

EURO = Product(name="Elsewhere", price=90.0, currency="EUR")


# -- the currency the shopper named (ADR-0056) --------------------------------
