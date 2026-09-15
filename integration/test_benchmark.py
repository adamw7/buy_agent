"""The benchmark, scored on the nightly run: how *well* did the model do?"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from benchmark.scoring import FLOORS, METRICS, score_run

if TYPE_CHECKING:
    from buy_agent.config import AgentConfig

    from benchmark.scoring import Scorecard
    from integration.conftest import LiveRun

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def scorecard(live_run: LiveRun, live_config: AgentConfig) -> Scorecard:
    """The live run, scored, and written to the log before anything asserts."""
    card = score_run(
        [entry.product for entry in live_run.ranked],
        live_run.pages,
        slots=live_config.num_products,
    )
    logger.info("Benchmark scorecard for %s:\n%s", live_config.model, card.table())
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
