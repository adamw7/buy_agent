"""The bounds a shopper sets, and what they do and do not throw away."""

from __future__ import annotations

import logging

import pytest

from buy_agent.config import AgentConfig
from buy_agent.constraints import Constraints
from buy_agent.models import Product


def product(name: str, **figures) -> Product:
    return Product(name=name, **figures)


# -- what the bounds admit -----------------------------------------------------


def test_nothing_set_keeps_everything_and_says_nothing(caplog) -> None:
    """The default. A run nobody gave terms to is the run this always was."""
    products = [product("dear", price=900.0), product("cheap", price=9.0)]

    with caplog.at_level(logging.INFO, logger="buy_agent.constraints"):
        kept = Constraints().apply(products)

    assert kept == products
    assert caplog.records == []


def test_a_budget_drops_what_costs_more() -> None:
    kept = Constraints(max_price=200.0).apply(
        [product("dear", price=900.0), product("cheap", price=99.0)]
    )

    assert [entry.name for entry in kept] == ["cheap"]


def test_a_price_exactly_on_the_budget_is_inside_it() -> None:
    """"Under $200" is how a shopper says it and "at most 200" is what they mean:
    a listing at exactly the budget is the one they were hoping for."""
    kept = Constraints(max_price=200.0).apply([product("exact", price=200.0)])

    assert [entry.name for entry in kept] == ["exact"]


def test_a_rating_floor_drops_what_is_rated_lower() -> None:
    kept = Constraints(min_rating=4.0).apply(
        [product("bad", rating=3.9), product("good", rating=4.0)]
    )

    assert [entry.name for entry in kept] == ["good"]


def test_a_review_floor_drops_a_rating_too_few_people_gave() -> None:
    """A 5.0 from two people is not a rating, whatever the arithmetic says."""
    kept = Constraints(min_reviews=100).apply(
        [
            product("thin", rating=5.0, review_count=2),
            product("real", rating=4.2, review_count=8000),
        ]
    )

    assert [entry.name for entry in kept] == ["real"]


def test_every_bound_set_has_to_be_satisfied_at_once() -> None:
    """Each is a separate reason to drop something, not three votes."""
    limits = Constraints(max_price=200.0, min_rating=4.0, min_reviews=100)
    kept = limits.apply(
        [
            product("dear", price=900.0, rating=4.8, review_count=5000),
            product("poor", price=99.0, rating=2.0, review_count=5000),
            product("thin", price=99.0, rating=4.8, review_count=3),
            product("right", price=99.0, rating=4.8, review_count=5000),
        ]
    )

    assert [entry.name for entry in kept] == ["right"]


# -- the blanks, which are the whole judgement call -----------------------------


@pytest.mark.parametrize(
    "bounds",
    [
        Constraints(max_price=200.0),
        Constraints(min_rating=4.0),
        Constraints(min_reviews=100),
    ],
)
def test_a_figure_the_run_never_learned_is_not_a_violation(bounds: Constraints) -> None:
    """The rule that keeps this from punishing the extractor for its misses.

    ``ground`` blanks every figure the source pages did not back, so a blank here
    is as often "nothing was read" as "nothing was printed" -- and dropping those
    would reject real products for a model's bad afternoon. Neutral rather than
    zero, which is what ADR-0007 already decided for the same reason.
    """
    assert [entry.name for entry in bounds.apply([product("silent")])] == ["silent"]


def test_a_review_floor_ignores_a_product_with_no_count_but_a_rating() -> None:
    """The count is the blank, so the count is what goes unjudged -- the rating
    beside it is not evidence about how many people gave it."""
    kept = Constraints(min_reviews=1000).apply([product("rated", rating=4.5)])

    assert [entry.name for entry in kept] == ["rated"]


# -- what the run is told about it ---------------------------------------------


def test_the_count_is_logged_whenever_bounds_were_set(caplog) -> None:
    """Without it, a report of two products because eight were over budget looks
    exactly like a search that only found two."""
    with caplog.at_level(logging.INFO, logger="buy_agent.constraints"):
        Constraints(max_price=200.0).apply(
            [product("a", price=99.0), product("b", price=900.0)]
        )

    assert "1 of 2 product(s) are within the limits (at most 200.00)" in caplog.text


def test_a_bound_that_dropped_nothing_still_says_so(caplog) -> None:
    """"10 of 10" is the answer that says the bound did nothing, which is not the
    same answer as silence."""
    with caplog.at_level(logging.INFO, logger="buy_agent.constraints"):
        Constraints(min_rating=1.0).apply([product("a", rating=4.0)])

    assert "1 of 1 product(s) are within the limits" in caplog.text


def test_dropping_the_last_product_is_a_warning(caplog) -> None:
    """The one case worth interrupting for: the run found things and is about to
    report none of them."""
    with caplog.at_level(logging.INFO, logger="buy_agent.constraints"):
        kept = Constraints(max_price=10.0).apply([product("dear", price=900.0)])

    assert kept == []
    assert [record.levelname for record in caplog.records] == ["WARNING"]


@pytest.mark.parametrize(
    ("bounds", "expected"),
    [
        (Constraints(max_price=1500.0), "at most 1,500.00"),
        (Constraints(min_rating=4.5), "rated at least 4.5"),
        (Constraints(min_rating=4.0), "rated at least 4"),
        (Constraints(min_reviews=12500), "from at least 12,500 reviews"),
        (
            Constraints(max_price=200.0, min_reviews=100),
            "at most 200.00, from at least 100 reviews",
        ),
    ],
)
def test_only_the_bounds_that_were_set_are_named(bounds: Constraints, expected: str) -> None:
    """A run narrowed on price alone must not report two bounds it never had."""
    assert bounds.describe() == expected


# -- where they come from ------------------------------------------------------


def test_the_bounds_are_read_off_the_config_a_run_carries() -> None:
    config = AgentConfig(max_price=200.0, min_rating=4.0, min_reviews=100)

    assert Constraints.from_config(config) == Constraints(
        max_price=200.0, min_rating=4.0, min_reviews=100
    )


def test_a_config_nobody_narrowed_carries_no_bounds() -> None:
    assert Constraints.from_config(AgentConfig()) == Constraints()
    assert not Constraints.from_config(AgentConfig()).given


@pytest.mark.parametrize(
    "bounds",
    [
        Constraints(max_price=1.0),
        Constraints(min_rating=0.0),
        Constraints(min_reviews=0),
    ],
)
def test_a_bound_at_the_bottom_of_its_range_is_still_a_bound(bounds: Constraints) -> None:
    """``0`` and ``None`` are different answers, and only the second is "unset".
    Read as falsy, ``min_rating=0`` would stop being applied and stop being
    reported -- and 0 admits everything, so nothing would ever look wrong."""
    assert bounds.given
