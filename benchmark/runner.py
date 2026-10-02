"""Run the pipeline over :mod:`benchmark.corpus` and score what comes back."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
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
    """Hand every agent the corpus instead of the web, for as long as this is open.

    Yields:
        The enriched results, filled in as the agent asks for them, so a caller
        can score against exactly the text the run was given.
    """
    served: list[SearchResult] = []

    def search(
        query: str, *, max_results: int = 10, region: str = "us-en", **_: object
    ) -> list:
        return [result.model_copy() for result in pages[:max_results]]

    def enrich(
        results: Sequence[SearchResult],
        *,
        max_chars: int = 1200,
        opinion_chars: int = 400,
        **_: object,
    ) -> list:
        served[:] = [
            result.model_copy(
                update={
                    "content": condense(
                        page_text[result.url], max_chars=max_chars, opinion_chars=opinion_chars
                    )
                }
            )
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
    return Report(
        scorecard=score_run(
            [entry.product for entry in ranked],
            served,
            key=case.key,
            slots=config.num_products,
        ),
        ranked=ranked,
        pages=tuple(served),
    )


__all__ = ["Report", "run_benchmark", "serving_the_corpus"]
