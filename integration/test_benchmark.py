"""The benchmark, scored on the nightly run: how *well* did the model do?

The scorecard is printed and kept pass or fail by ``conftest.pytest_terminal_summary``
(ADR-0072); these tests are the tripwire under it (ADR-0036).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from benchmark.scoring import FLOORS, METRICS

if TYPE_CHECKING:
    from benchmark.compare import CaseRun
    from benchmark.scoring import Scorecard


@pytest.fixture(scope="session")
def scorecard(benchmarked: CaseRun) -> Scorecard:
    """The live run's scorecard. A run whose extraction could not be read has none, and
    the session's fixtures fail before it gets here."""
    card = benchmarked.scorecard
    assert card is not None, benchmarked.failure
    return card


@pytest.mark.parametrize("metric", sorted(METRICS))
def test_the_run_clears_the_floor_for_each_metric(scorecard: Scorecard, metric: str) -> None:
    """One test per metric rather than one for the lot, so a failing job names which half
    of the pipeline slipped instead of reporting a blended number that went down."""
    assert scorecard.metrics[metric] >= FLOORS[metric], scorecard.table()


def test_the_run_clears_the_overall_floor(scorecard: Scorecard) -> None:
    """The weighted score, which is the number worth tracking between runs: it
    moves when a metric moves, and it is the one a future floor is raised on."""
    assert scorecard.score >= FLOORS["score"], scorecard.table()


def test_every_metric_is_a_share(scorecard: Scorecard) -> None:
    """A scorecard is comparable between runs only while every metric is a share of
    something."""
    assert all(right <= out_of for right, out_of in scorecard.counts.values())
    assert 0.0 <= scorecard.score <= 1.0


def test_the_query_the_run_searched_with_was_scored(benchmarked: CaseRun) -> None:
    """The first of the two questions, judged off the same run rather than a second
    inference (ADR-0070): reported, not floored -- no run has yet said where a floor
    belongs."""
    assert benchmarked.query.checks
    assert 0.0 <= benchmarked.query.score <= 1.0
    assert set(benchmarked.seconds) == {"query", "extract"}
