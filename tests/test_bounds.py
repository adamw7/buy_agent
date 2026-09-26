"""Reading a bound out of the request, which is only ever an offer (ADR-0059)."""

from __future__ import annotations

import pytest

from buy_agent.bounds import Noticed, notice


def only(request: str) -> Noticed:
    """The one bound this request asks for, asserting that it asks for exactly one."""
    found = notice(request)
    assert len(found) == 1, f"{request!r} was read as {[seen.bound for seen in found]}"
    return found[0]


@pytest.mark.parametrize(
    ("request_", "figure", "phrase"),
    [
        ("wireless headphones under $200", "200", "under $200"),
        ("gaming laptop below 1500", "1500", "below 1500"),
        ("a kettle less than 40", "40", "less than 40"),
        ("running shoes cheaper than 90", "90", "cheaper than 90"),
        ("a monitor no more than £900", "900", "no more than £900"),
        ("a desk up to 350", "350", "up to 350"),
        ("a mouse at most 60", "60", "at most 60"),
        ("a laptop maximum of $1,500", "1500", "maximum of $1,500"),
        ("headphones 200 EUR or less", "200", "200 EUR or less"),
        ("a chair $150 or under", "150", "$150 or under"),
        ("an espresso machine under 1500 dollars", "1500", "under 1500 dollars"),
        ("a laptop under $999.99", "999.99", "under $999.99"),
    ],
)
def test_a_budget_is_read_however_a_shopper_writes_it(
    request_: str, figure: str, phrase: str
) -> None:
    """The shapes people actually type, each quoted back in their own words."""
    seen = only(request_)

    assert (seen.bound, seen.figure, seen.phrase) == ("max_price", figure, phrase)


@pytest.mark.parametrize(
    ("request_", "bound", "figure"),
    [
        ("a kettle under 129,99 €", "max_price", "129.99"),
        ("a kettle under €49,5", "max_price", "49.5"),
        ("a laptop under 1.299,99 €", "max_price", "1299.99"),
        ("a laptop under 1.299 zł", "max_price", "1299"),
        ("a phone under €1.000", "max_price", "1000"),
        ("headphones at least 4,5 stars", "min_rating", "4.5"),
        ("headphones with at least 1.200 reviews", "min_reviews", "1200"),
    ],
)
def test_a_figure_is_read_in_either_convention(request_: str, bound: str, figure: str) -> None:
    """Read the way a page's figures are (``money.plain_figures``): as digits and commas,
    "under 129,99 €" offered a budget of 12999 and "1.200 reviews" a count of 1.2."""
    seen = only(request_)

    assert (seen.bound, seen.figure) == (bound, figure)


def test_a_count_that_is_not_whole_is_not_offered() -> None:
    """No box takes 4.5 reviews, so nothing is offered for one."""
    assert notice("headphones with at least 4.5 reviews") == []


def test_digits_that_are_no_number_are_not_offered() -> None:
    assert notice("firmware under 1.2.3") == []


@pytest.mark.parametrize(
    ("request_", "figure"),
    [
        ("headphones at least 4 stars", "4"),
        ("headphones minimum of 4.5 stars", "4.5"),
        ("headphones over 4 stars", "4"),
        ("headphones above 3.5 stars", "3.5"),
        ("headphones better than 4 stars", "4"),
        ("headphones 4.5+ stars", "4.5"),
        ("headphones 4 stars or better", "4"),
        ("headphones at least 4.2/5", "4.2"),
        ("headphones at least 4 out of 5", "4"),
    ],
)
def test_a_rating_is_read_only_beside_its_scale(request_: str, figure: str) -> None:
    """The rule ``verification`` already holds for a figure off a page: a small number
    is a rating only where something says what it is out of."""
    seen = only(request_)

    assert (seen.bound, seen.figure) == ("min_rating", figure)


@pytest.mark.parametrize(
    ("request_", "figure"),
    [
        ("headphones with 500 reviews", "500"),
        ("headphones at least 1,200 ratings", "1200"),
        ("headphones over 300 reviews", "300"),
        ("headphones more than 300 reviews", "300"),
        ("headphones 250+ reviews", "250"),
    ],
)
def test_a_review_count_is_read_only_where_somebody_is_counted(
    request_: str, figure: str
) -> None:
    seen = only(request_)

    assert (seen.bound, seen.figure) == ("min_reviews", figure)


@pytest.mark.parametrize(
    "request_",
    [
        # The case the whole design is arranged around: a number about something else.
        "headphones with 200 hours of battery",
        "a laptop under 20 hours of battery",
        "a monitor under 32 inches",
        # No figure at all.
        "wireless noise cancelling headphones",
        # A bound phrase with nothing to be a bound.
        "something under the sink",
    ],
)
def test_a_number_about_something_else_is_left_alone(request_: str) -> None:
    """Offered or not, a bound read out of "200 hours of battery" is a run that reports
    nothing and says only that nothing was found."""
    assert notice(request_) == []


def test_a_budget_survives_the_rest_of_the_sentence() -> None:
    """A request does not end at the figure, and a guard that said it did dropped every
    budget somebody wrote a second clause after."""
    seen = only("gaming laptop under 1500 and quiet")

    assert (seen.bound, seen.figure) == ("max_price", "1500")


def test_a_figure_is_read_whole_or_not_at_all() -> None:
    """Without the lookahead in ``_NUMBER`` the engine backtracked to "150", the "0"
    after it satisfying "nothing else follows", and a request about battery life came
    back offering a budget nobody had written."""
    assert [seen.figure for seen in notice("laptop under 1500 hours")] == []
    assert only("laptop under 1,500").figure == "1500"


def test_all_three_are_read_out_of_one_request() -> None:
    found = notice("headphones under $200 with at least 500 reviews and 4.5+ stars")

    assert [(seen.bound, seen.figure) for seen in found] == [
        ("max_price", "200"),
        ("min_rating", "4.5"),
        ("min_reviews", "500"),
    ]


def test_the_first_of_each_shape_wins() -> None:
    """A request naming two budgets is one somebody reworded mid-sentence, and picking
    between them is a judgement this deliberately does not make."""
    assert only("a laptop under 1500, well under 900").figure == "1500"


def test_a_count_of_stars_is_not_a_rating() -> None:
    """Five is the top of the scale, so "over 2000 stars" is a review count written
    oddly rather than a rating nothing could ever be inside."""
    assert notice("headphones over 2000 stars") == []


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


def test_the_note_quotes_the_words_it_read_and_says_it_applies_nothing() -> None:
    """Python's sentence, shown under the box: the whole of the offer is that it is
    arguable and can be cleared."""
    note = only("headphones under $200").note

    assert 'under $200' in note
    assert "Clear the box to search without it." in note
