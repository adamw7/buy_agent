"""Reading a bound out of the request, which is only ever an offer (ADR-0059)."""

from __future__ import annotations

import pytest

from buy_agent.bounds import Noticed, notice


def only(request: str) -> Noticed:
    """The one bound this request asks for, asserting that it asks for exactly one."""
    found = notice(request)
    assert len(found) == 1, f"{request!r} was read as {[seen.bound for seen in found]}"
    return found[0]


def test_a_count_that_is_not_whole_is_not_offered() -> None:
    """No box takes 4.5 reviews, so nothing is offered for one."""
    assert notice("headphones with at least 4.5 reviews") == []


def test_digits_that_are_no_number_are_not_offered() -> None:
    assert notice("firmware under 1.2.3") == []


def test_a_star_count_that_is_no_rating_does_not_hide_the_rating_after_it() -> None:
    """The first figure of a shape that fails its scale is skipped, not the end of the
    search: "the first of each shape wins" is the first one that could be a bound."""
    seen = only("a star projector with over 2000 stars, at least 4.5 stars")

    assert (seen.bound, seen.figure) == ("min_rating", "4.5")


def test_nothing_is_ever_offered_as_a_bound_of_zero() -> None:
    """A bound of nothing bounds nothing, and "under 0" is not a budget."""
    assert notice("headphones under 0") == []


@pytest.mark.parametrize(
    ("request_", "bound", "figure"),
    [
        # The smallest of each that is still something...
        ("a charging cable under $1", "max_price", "1"),
        ("headphones at least 1 star", "min_rating", "1"),
        ("headphones with at least 1 review", "min_reviews", "1"),
        # ...and the top of the one scale that has a top.
        ("headphones at least 5 stars", "min_rating", "5"),
    ],
)
def test_the_ends_of_each_scale_are_still_bounds(request_: str, bound: str, figure: str) -> None:
    """Positive is the whole of the floor and five stars is inside the rating's
    scale, not past it."""
    seen = only(request_)

    assert (seen.bound, seen.figure) == (bound, figure)
