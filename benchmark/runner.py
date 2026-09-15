"""Run the pipeline over :mod:`benchmark.corpus` and score what comes back."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from buy_agent import agent as agent_module
from buy_agent.agent import BuyAgent
from buy_agent.fetch import condense
from benchmark.corpus import PAGE_TEXT, PAGES, REQUEST, settings
from benchmark.scoring import Scorecard, score_run

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

    from buy_agent.chat import ChatModel
    from buy_agent.config import AgentConfig
    from buy_agent.models import RankedProduct
    from buy_agent.search import SearchResult


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
    *, llm: ChatModel | None = None, config: AgentConfig | None = None
) -> Report:
    """Run the agent over the corpus and score it.

    Args:
        llm: The model to score. None builds the provider's own, which is the
            only thing in here that touches a network.
        config: Settings to run with; :func:`benchmark.corpus.settings` by
            default. Widen ``num_products`` and the scorer widens its slots too.
    """
    config = config or settings()
    with serving_the_corpus() as served:
        ranked = BuyAgent(config, llm=llm).run(REQUEST)
    return Report(
        scorecard=score_run(
            [entry.product for entry in ranked], served, slots=config.num_products
        ),
        ranked=ranked,
        pages=tuple(served),
    )


__all__ = ["Report", "run_benchmark", "serving_the_corpus"]
