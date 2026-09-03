"""The benchmark, scored on the nightly run: how *well* did the model do?

``test_live_pipeline.py`` asks whether the promises held -- every name in the
sources, every figure in the sources, nothing listed twice -- and those hold
however badly the model read the pages. They are the right bar for a job running
a 0.6B model on four cores, and they are also why that job cannot tell a run
that got better from one that got worse. Nothing in it knows what the right
answer was.

This file does, because :mod:`benchmark.answers` writes it down. It scores the
*same* run -- one model call, one corpus, two questions -- and fails under
:data:`benchmark.scoring.FLOORS`.

Three failures live here and nowhere else, being the three the invariant tests
structurally cannot see (ADR-0036):

* a figure copied off another product's line, which ``verify_numbers`` grounds
  against the pooled pages and therefore accepts;
* a product listed twice under names ``deduplicate`` does not merge, which
  ``test_no_product_is_reported_twice`` re-runs the merge to check and so agrees
  with;
* a ranking in the wrong order, which is ordered and numbered either way.

The floors are a tripwire rather than a target, and deliberately low --
:data:`~benchmark.scoring.FLOORS` says why, and what raising one is worth. The
whole scorecard is logged whether the job passes or not, since a scheduled run
that reports only pass or fail cannot be argued with.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from benchmark.scoring import FLOORS, WEIGHTS, score_run

if TYPE_CHECKING:
    from buy_agent.config import AgentConfig

    from benchmark.scoring import Scorecard
    from integration.conftest import LiveRun

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def scorecard(live_run: LiveRun, live_config: AgentConfig) -> Scorecard:
    """The live run, scored, and written to the log before anything asserts.

    Logged from the fixture rather than from a test, so the numbers reach the
    job summary even on the run where an assertion below fails -- which is the
    run somebody is going to want them for.
    """
    card = score_run(
        [entry.product for entry in live_run.ranked],
        live_run.pages,
        slots=live_config.num_products,
    )
    logger.info("Benchmark scorecard for %s:\n%s", live_config.model, card.table())
    return card


@pytest.mark.parametrize("metric", sorted(WEIGHTS))
def test_the_run_clears_the_floor_for_each_metric(scorecard: Scorecard, metric: str) -> None:
    """One test per metric rather than one for the lot, so a failing job names
    which half of the pipeline slipped instead of reporting a blended number that
    went down."""
    assert scorecard.metrics[metric] >= FLOORS[metric], scorecard.table()


def test_the_run_clears_the_overall_floor(scorecard: Scorecard) -> None:
    """The weighted score, which is the number worth tracking between runs: it
    moves when a metric moves, and it is the one a future floor is raised on."""
    assert scorecard.score >= FLOORS["score"], scorecard.table()


def test_every_metric_is_a_share(scorecard: Scorecard) -> None:
    """The scorecard is only comparable between runs while every metric is a
    share of something. A count that outgrew its denominator -- more figures
    right than figures possible -- would read as a better run rather than as the
    arithmetic mistake it is."""
    assert all(0.0 <= value <= 1.0 for value in scorecard.metrics.values())
    assert 0.0 <= scorecard.score <= 1.0
