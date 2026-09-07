"""What a run says while it is thinning its own results, held in one place.

Eight steps of the pipeline take something away: a product that was a headline
rather than a thing, one no page mentions, one outside the shopper's bounds, a
figure the sources do not back, a quote nobody printed, a link to a page nobody
searched, a name identifying nothing, and a listing folded into another. Every one
of them says how many at INFO and which at DEBUG, and the pair is the rule --
neither half is worth much alone. The count is what tells a short report from a
thin web: three products out of a search that found ten is a filtered answer, and
without the line it reads as all there was. The name is what makes a wrong drop
arguable, and it costs a line per product, which is why it waits for ``-v``.

Each step's own test file already pins its wording. What none of them can see is
the *set*: a ninth step, or an eighth that quietly stopped saying anything, leaves
every other file green. This is the rule stated once, driven rather than read --
the two things a reader of a log actually does are run it quiet and run it ``-v``,
so that is what these do.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from buy_agent.constraints import Constraints
from buy_agent.extraction import clean_products, deduplicate
from buy_agent.models import Product
from buy_agent.search import SearchResult
from buy_agent.verification import (
    attribute_sources,
    build_haystack,
    drop_ungrounded,
    verify_numbers,
    verify_opinions,
)
from tests.conftest import said

if TYPE_CHECKING:
    from collections.abc import Callable

#: One page, standing in for the whole web: it names a product, prices it, rates
#: it and passes a verdict on it, which is every kind of thing the steps below
#: check a claim against.
PAGE = SearchResult(
    title="Sony WH-CH720N review",
    url="https://audiosite.example/ch720n",
    snippet="Now $129, rated 4.3 out of 5 from 12,500 shoppers. Reviewers found "
    "the noise cancelling uncanny for the money.",
)
SOURCES = [PAGE]
HAYSTACK = build_haystack(SOURCES)

#: A product that page backs, so a step that drops something drops the *other* one.
REAL = "Sony WH-CH720N"


@dataclass(frozen=True)
class Heuristic:
    """One step that takes something away, and what one run of it took.

    ``casualty`` is what the DEBUG line has to name -- the product, the link or
    the name that went. Three of these name the product rather than the figure or
    the quote they blanked, which is the same answer to the same question: the
    thing a reader has to be able to go and look at.
    """

    drive: Callable[[], object]
    count: int
    casualty: str


#: The eight, each driven over the one page above.
HEURISTICS = {
    "a headline reported as a product": Heuristic(
        drive=lambda: clean_products(
            [Product(name=REAL), Product(name="The 5 best headphones of 2026")]
        ),
        count=1,
        casualty="The 5 best headphones of 2026",
    ),
    "a name identifying nothing": Heuristic(
        drive=lambda: deduplicate([Product(name=REAL), Product(name="   ")], 10),
        count=1,
        casualty="'   '",
    ),
    # The one where nothing was dropped at all, and the one a reader could not
    # otherwise reconstruct: the folded entry keeps the shorter of the two names,
    # so the longer is simply gone from the report with nothing to say where.
    "a listing folded into another": Heuristic(
        drive=lambda: deduplicate(
            [Product(name=REAL), Product(name=f"{REAL} Wireless Headphones")], 10
        ),
        count=1,
        casualty=f"{REAL} Wireless Headphones",
    ),
    "a product no page mentions": Heuristic(
        drive=lambda: drop_ungrounded(
            [Product(name=REAL), Product(name="Bonavita Gooseneck Kettle")], HAYSTACK
        ),
        count=1,
        casualty="Bonavita Gooseneck Kettle",
    ),
    "a figure no page printed": Heuristic(
        drive=lambda: verify_numbers(
            [Product(name=REAL, price=999.0, currency="USD")], HAYSTACK
        ),
        count=1,
        casualty=REAL,
    ),
    "a quote nobody wrote": Heuristic(
        drive=lambda: verify_opinions(
            [Product(name=REAL, opinions=said("battery life is poor"))], SOURCES
        ),
        count=1,
        casualty=REAL,
    ),
    "a link to a page nobody searched": Heuristic(
        drive=lambda: attribute_sources(
            [Product(name=REAL, url="https://invented.example/xyz")], SOURCES
        ),
        count=1,
        casualty="https://invented.example/xyz",
    ),
    "a product outside the shopper's bounds": Heuristic(
        drive=lambda: Constraints(max_price=100.0).apply(
            [
                Product(name=REAL, price=129.0, currency="USD"),
                Product(name="Anker Q45", price=50.0, currency="USD"),
            ]
        ),
        count=1,
        casualty=REAL,
    ),
}

CASES = pytest.mark.parametrize(
    "heuristic", HEURISTICS.values(), ids=list(HEURISTICS)
)


def at(caplog, level: int, heuristic: Heuristic) -> list[logging.LogRecord]:
    """Drive one step with the package logger at ``level``, and hand back what it said."""
    with caplog.at_level(level, logger="buy_agent"):
        heuristic.drive()
    return caplog.records


@CASES
def test_every_step_that_takes_something_away_says_how_many(caplog, heuristic) -> None:
    """The count is what a quiet run gets, and what it is for.

    A report of three where the search found ten reads exactly like a search that
    found three, and the two are different situations: one is a filter doing its
    job and the other is a reason to search differently. Only the count tells them
    apart, so it goes out at INFO -- where somebody who did not ask for detail,
    which is everybody by default, will see it.
    """
    said_out_loud = [
        record for record in at(caplog, logging.INFO, heuristic) if record.levelno >= logging.INFO
    ]

    assert said_out_loud, "the step took something away and said nothing about it"
    assert any(str(heuristic.count) in record.getMessage() for record in said_out_loud), (
        f"nothing says how many went: {[r.getMessage() for r in said_out_loud]}"
    )


@CASES
def test_every_step_that_takes_something_away_names_it_at_debug(caplog, heuristic) -> None:
    """"Why is the one I had in mind not in there?" is the question a count cannot
    answer, and ``-v`` is where the answer is affordable: a line per product is
    unreadable on an ordinary run and is exactly what a wrong drop needs."""
    detail = [
        record for record in at(caplog, logging.DEBUG, heuristic) if record.levelno == logging.DEBUG
    ]

    assert any(heuristic.casualty in record.getMessage() for record in detail), (
        f"nothing names {heuristic.casualty!r}: {[r.getMessage() for r in detail]}"
    )


@CASES
def test_a_quiet_run_is_told_how_many_and_not_which(caplog, heuristic) -> None:
    """The other half of the pair, and the half a stray line breaks silently.

    A step that named its casualties at INFO would put ten lines into every run
    that dropped ten products, burying the report the run exists to print -- and
    it would still pass the two tests above. That is the split between the count
    and the names, asserted from the side that has something to lose.
    """
    quiet = at(caplog, logging.INFO, heuristic)

    assert not any(heuristic.casualty in record.getMessage() for record in quiet), (
        f"{heuristic.casualty!r} is named on a run that did not ask for detail"
    )


@CASES
def test_every_line_reaches_the_package_logger(caplog, heuristic) -> None:
    """A line logged anywhere else is missing from the browser's progress panel
    and from the stdout/stderr split, both of which hang off that one name -- and
    missing from nothing else, so a terminal shows it and no other test sees it."""
    for record in at(caplog, logging.DEBUG, heuristic):
        assert record.name.split(".")[0] == "buy_agent", record.name
