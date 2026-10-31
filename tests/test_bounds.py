"""Reading a bound out of the request, which the doors apply and say so (ADR-0094)."""

from __future__ import annotations

import time

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


@pytest.mark.parametrize(
    ("request_", "figure"),
    [
        ("headphones under 1500", "1500"),
        ("headphones under $1,299.99", "1299.99"),
        ("espresso machine under 1.299,99 €", "1299.99"),
        ("laptop under 1 500 zł", "1500"),
        ("laptop under 1\u00a0299,50 €", "1299.5"),
    ],
)
def test_a_figure_is_read_whole_in_each_convention(request_: str, figure: str) -> None:
    """Never part of one: "1500" is no budget of 150."""
    assert only(request_).figure == figure


@pytest.mark.parametrize("request_", ["headphones with .5+ stars", "headphones x4.5+ stars"])
def test_no_reading_starts_inside_a_figure(request_: str) -> None:
    """Half a star was offered as five, read from after the dot."""
    assert notice(request_) == []


@pytest.mark.parametrize("unit", ["1.", "1,", "1\u00a0"])
def test_a_request_as_long_as_a_request_line_is_read_at_once(unit: str) -> None:
    """``GET /api/bounds`` takes a request line of up to 64 KiB, and a regular expression
    holds the interpreter while it reads: 4,000 characters of "1.1.1" took a minute and
    a half, and every other request to the server waited with it."""
    request = (unit * 65536)[:65536] + " or less"

    started = time.perf_counter()
    notice(request)

    assert time.perf_counter() - started < 2
