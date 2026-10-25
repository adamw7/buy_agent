"""Run the pipeline over :mod:`benchmark.corpus` and score what comes back."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

from buy_agent import agent as agent_module
from buy_agent.agent import BuyAgent, every_step_passes
from buy_agent.fetch import condense
from benchmark.cases import HEADPHONES
from benchmark.corpus import PAGE_TEXT, PAGES
from benchmark.scoring import Scorecard, score_run

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

    from buy_agent.agent import Checkpoint
    from buy_agent.chat import ChatModel
    from buy_agent.config import AgentConfig
    from buy_agent.models import RankedProduct
    from buy_agent.search import SearchResult
    from benchmark.cases import Case


@dataclass(frozen=True, slots=True)
class Report:
    """One benchmark run: the scorecard, and enough of the run to explain it."""

    scorecard: Scorecard
    ranked: list[RankedProduct]
    pages: tuple[SearchResult, ...]


@contextlib.contextmanager
def serving_the_corpus(
    pages: Sequence[SearchResult] = PAGES, page_text: Mapping[str, str] = PAGE_TEXT
) -> Iterator[list[SearchResult]]:
    """Hand every agent the corpus instead of the web; yields the enriched results as
    the agent asks for them, to score against exactly what the run was given."""
    served: list[SearchResult] = []

    def search(query: str, *, max_results: int = 10, region: str = "us-en", **_: object) -> list:
        return [result.model_copy() for result in pages[:max_results]]

    def enrich(
        results: Sequence[SearchResult],
        *,
        max_chars: int = 1200,
        opinion_chars: int = 400,
        **_: object,
    ) -> list:
        condensed = partial(condense, max_chars=max_chars, opinion_chars=opinion_chars)
        served[:] = [
            result.model_copy(update={"content": condensed(page_text[result.url])})
            for result in results
        ]
        return list(served)

    original = agent_module.search_web, agent_module.enrich
    agent_module.search_web, agent_module.enrich = search, enrich
    try:
        yield served
    finally:
        agent_module.search_web, agent_module.enrich = original


def run_benchmark(
    *,
    llm: ChatModel | None = None,
    config: AgentConfig | None = None,
    case: Case = HEADPHONES,
    checkpoint: Checkpoint = every_step_passes,
) -> Report:
    """Run the agent over one case's pages and score it against that case's key.

    Args:
        llm: The model to score. None builds the provider's own, which is the
            only thing in here that touches a network.
        config: Settings to run with; the case's own by default. Widen
            ``num_products`` and the scorer widens its slots too.
        case: Which use of the agent to run (ADR-0070); the headphones by default.
        checkpoint: Handed to ``BuyAgent.run``, which calls it before each step.
    """
    config = config or case.settings()
    agent = BuyAgent(config, llm=llm)
    try:
        with serving_the_corpus(case.pages, case.page_text) as served:
            ranked = agent.run(case.request, checkpoint=checkpoint)
    finally:
        # Releases only a model the agent opened itself, never one handed in.
        agent.close()
    products = [entry.product for entry in ranked]
    card = score_run(products, served, key=case.key, slots=config.num_products)
    return Report(scorecard=card, ranked=ranked, pages=tuple(served))


__all__ = ["Report", "run_benchmark", "serving_the_corpus"]
