"""Ranking is the part that decides the answer, so it gets the most tests."""

from __future__ import annotations

import pytest

from buy_agent.models import Product
from buy_agent.ranking import NEUTRAL, RankingWeights, rank_products, score_product


def product(name: str, **kwargs: object) -> Product:
    return Product(name=name, **kwargs)


def test_ranks_are_sequential_and_sorted_by_score() -> None:
    ranked = rank_products(
        [
            product("cheap and loved", price=50.0, rating=4.8, review_count=5000),
            product("dear and mediocre", price=900.0, rating=3.1, review_count=12),
        ]
    )
    assert [entry.rank for entry in ranked] == [1, 2]
    assert ranked[0].product.name == "cheap and loved"
    assert ranked[0].score > ranked[1].score


def test_higher_rating_wins_when_price_is_equal() -> None:
    ranked = rank_products(
        [
            product("meh", price=100.0, rating=3.0, review_count=100),
            product("great", price=100.0, rating=4.9, review_count=100),
        ]
    )
    assert ranked[0].product.name == "great"


def test_cheaper_wins_when_rating_is_equal() -> None:
    ranked = rank_products(
        [
            product("pricey", price=500.0, rating=4.5, review_count=100),
            product("bargain", price=100.0, rating=4.5, review_count=100),
        ]
    )
    assert ranked[0].product.name == "bargain"


def test_missing_data_scores_neutral_not_last() -> None:
    """A listing that published no price should beat one that is genuinely bad."""
    ranked = rank_products(
        [
            product("no data at all"),
            product("bad and expensive", price=1000.0, rating=1.0, review_count=3),
            product("good and cheap", price=10.0, rating=5.0, review_count=9000),
        ]
    )
    assert [entry.product.name for entry in ranked] == [
        "good and cheap",
        "no data at all",
        "bad and expensive",
    ]


def test_single_price_ties_on_the_price_criterion() -> None:
    score = score_product(
        product("only one", price=42.0),
        cheapest=42.0,
        priciest=42.0,
        weights=RankingWeights(),
    )
    assert score == pytest.approx(NEUTRAL)


def test_scores_stay_within_zero_and_one() -> None:
    products = [
        product("floor", price=999.0, rating=0.0, review_count=1),
        product("ceiling", price=1.0, rating=5.0, review_count=1_000_000),
    ]
    for entry in rank_products(products):
        assert 0.0 <= entry.score <= 1.0


def test_weights_change_the_winner() -> None:
    products = [
        product("cheap, poorly rated", price=10.0, rating=2.0, review_count=100),
        product("costly, well rated", price=800.0, rating=5.0, review_count=100),
    ]
    price_first = RankingWeights(rating=0.0, popularity=0.0, price=1.0)
    rating_first = RankingWeights(rating=1.0, popularity=0.0, price=0.0)
    assert rank_products(products, weights=price_first)[0].product.name.startswith("cheap")
    assert rank_products(products, weights=rating_first)[0].product.name.startswith("costly")


def test_sort_by_price_puts_unpriced_products_last() -> None:
    ranked = rank_products(
        [
            product("unpriced", rating=5.0),
            product("dear", price=300.0),
            product("cheap", price=30.0),
        ],
        sort_by="price",
    )
    assert [entry.product.name for entry in ranked] == ["cheap", "dear", "unpriced"]


def test_sort_by_rating_puts_unrated_products_last() -> None:
    ranked = rank_products(
        [
            product("unrated", price=5.0),
            product("ok", rating=3.0),
            product("excellent", rating=4.9),
        ],
        sort_by="rating",
    )
    assert [entry.product.name for entry in ranked] == ["excellent", "ok", "unrated"]


def test_equal_scores_break_ties_by_name() -> None:
    ranked = rank_products([product("beta"), product("alpha")])
    assert [entry.product.name for entry in ranked] == ["alpha", "beta"]


def test_empty_input_gives_empty_ranking() -> None:
    assert rank_products([]) == []


def test_zero_weights_do_not_divide_by_zero() -> None:
    weights = RankingWeights(rating=0.0, popularity=0.0, price=0.0)
    ranked = rank_products([product("anything", price=10.0)], weights=weights)
    assert ranked[0].score == 0.0
