"""What a run says while it is thinning its own results, held in one place."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from buy_agent.constraints import Constraints
from buy_agent.extraction import clean_products, deduplicate
from buy_agent.models import Product, Removal, nothing_recorded
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

    from buy_agent.models import Recorder

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
    """One step that takes something away, and what one run of it took."""

    drive: Callable[[Recorder], object]
    count: int
    casualty: str
    #: The name this step hands to the run's recorder, or ``None`` where it hands over
    #: nothing: five of the eight remove a whole product and three only blank a field on
    #: one, which stays in the report and says so on its own card (ADR-0055).
    removal: str | None = None


#: The eight, each driven over the one page above.
HEURISTICS = {
    "a headline reported as a product": Heuristic(
        drive=lambda record: clean_products(
            [Product(name=REAL), Product(name="The 5 best headphones of 2026")],
            record=record,
        ),
        count=1,
        casualty="The 5 best headphones of 2026",
        removal="The 5 best headphones of 2026",
    ),
    "a name identifying nothing": Heuristic(
        drive=lambda record: deduplicate(
            [Product(name=REAL), Product(name="   ")], 10, record=record
        ),
        count=1,
        casualty="'   '",
        removal="   ",
    ),
    # The one where nothing was dropped at all, and the one a reader could not
    # otherwise reconstruct: the folded entry keeps the shorter of the two names,
    # so the longer is simply gone from the report with nothing to say where.
    "a listing folded into another": Heuristic(
        drive=lambda record: deduplicate(
            [Product(name=REAL), Product(name=f"{REAL} Wireless Headphones")],
            10,
            record=record,
        ),
        count=1,
        casualty=f"{REAL} Wireless Headphones",
        removal=f"{REAL} Wireless Headphones",
    ),
    "a product no page mentions": Heuristic(
        drive=lambda record: drop_ungrounded(
            [Product(name=REAL), Product(name="Bonavita Gooseneck Kettle")],
            HAYSTACK,
            record=record,
        ),
        count=1,
        casualty="Bonavita Gooseneck Kettle",
        removal="Bonavita Gooseneck Kettle",
    ),
    "a figure no page printed": Heuristic(
        drive=lambda _record: verify_numbers(
            [Product(name=REAL, price=999.0, currency="USD")], HAYSTACK
        ),
        count=1,
        casualty=REAL,
    ),
    "a quote nobody wrote": Heuristic(
        drive=lambda _record: verify_opinions(
            [Product(name=REAL, opinions=said("battery life is poor"))], SOURCES
        ),
        count=1,
        casualty=REAL,
    ),
    "a link to a page nobody searched": Heuristic(
        drive=lambda _record: attribute_sources(
            [Product(name=REAL, url="https://invented.example/xyz")], SOURCES
        ),
        count=1,
        casualty="https://invented.example/xyz",
    ),
    "a product outside the shopper's bounds": Heuristic(
        drive=lambda record: Constraints(max_price=100.0).apply(
            [
                Product(name=REAL, price=129.0, currency="USD"),
                Product(name="Anker Q45", price=50.0, currency="USD"),
            ],
            record=record,
        ),
        count=1,
        casualty=REAL,
        removal=REAL,
    ),
}

CASES = pytest.mark.parametrize(
    "heuristic", HEURISTICS.values(), ids=list(HEURISTICS)
)


def _where(removing: bool) -> pytest.MarkDecorator:
    """The cases that remove a whole product, or the ones that only blank a field --
    a partition of the eight rather than a skip inside a test that has been handed the
    other half (ADR-0055)."""
    chosen = {
        name: case
        for name, case in HEURISTICS.items()
        if (case.removal is not None) is removing
    }
    return pytest.mark.parametrize("heuristic", chosen.values(), ids=list(chosen))


#: The five that take a product out of the report, and the three that leave it in.
REMOVES = _where(True)
BLANKS = _where(False)


def at(caplog, level: int, heuristic: Heuristic) -> list[logging.LogRecord]:
    """Drive one step with the package logger at ``level``, and hand back what it said."""
    with caplog.at_level(level, logger="buy_agent"):
        heuristic.drive(nothing_recorded)
    return caplog.records


@CASES
def test_every_step_that_takes_something_away_says_how_many(caplog, heuristic) -> None:
    """The count is what a quiet run gets, and what it is for."""
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
    """The other half of the pair, and the half a stray line breaks silently."""
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


def removals(heuristic: Heuristic) -> list[Removal]:
    """Drive one step with somebody keeping what it took out (ADR-0055)."""
    kept: list[Removal] = []
    heuristic.drive(kept.append)
    return kept


@REMOVES
def test_a_step_that_removes_a_product_hands_it_to_the_recorder(heuristic) -> None:
    """The count and the DEBUG line are for a terminal. This is the same removal as
    data, which is what the browser shows and what a ``-v`` nobody ran cannot."""
    handed = removals(heuristic)

    assert [entry.name for entry in handed] == [heuristic.removal]
    assert handed[0].step, "a removal with no step cannot be grouped"
    assert handed[0].reason.endswith("."), (
        f"the reason is shown as a sentence: {handed[0].reason!r}"
    )


@BLANKS
def test_a_step_that_only_blanks_a_field_removes_nothing(heuristic) -> None:
    """The other half of that line, and the half that would quietly fill the panel
    with products that are still in the report: a blanked figure, quote or link
    leaves the product where it was, saying so on its own card (ADR-0055)."""
    assert removals(heuristic) == []


@CASES
def test_nobody_recording_is_the_default_and_changes_nothing(heuristic) -> None:
    """Every step is called with no recorder all over this suite and by any Python
    caller holding an agent: the survivors are the same either way."""
    with_nobody = heuristic.drive(nothing_recorded)
    with_somebody = heuristic.drive([].append)

    assert with_nobody == with_somebody
