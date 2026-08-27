"""Runtime configuration for the agent."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from buy_agent.ranking import RankingWeights
from buy_agent.sources import Source

DEFAULT_MODEL = os.getenv("OLLAMA_MODEL", "gemma4:12b")
DEFAULT_BASE_URL = os.getenv("OLLAMA_HOST", "http://localhost:11434")


@dataclass(slots=True)
class AgentConfig:
    """Everything the agent needs to know that is not the user's query.

    Attributes:
        model: Ollama model tag, e.g. ``gemma4:12b`` or ``qwen2.5:7b``.
        base_url: Where the Ollama server listens.
        temperature: Low by default; extraction is a copying task, not a creative one.
        num_ctx: Context window in tokens, or None to leave Ollama's default (4096)
            alone. The extraction prompt runs to ~4.3k tokens, so on the default a
            thinking model has no room left to answer -- see ``reasoning``. Defaults
            to 8192 because ``DEFAULT_MODEL`` is a thinking model.
        reasoning: Thinking mode. None sends nothing and leaves the model's own
            behaviour alone; False (the default) turns thinking off; True turns it
            on. Thinking models need False here: they spend the whole remaining
            context reasoning about a copying task and get cut off before emitting
            any JSON. A model that cannot think ignores it.
        search_results: How many raw web results to feed the extractor.
        num_products: How many products to keep after extraction.
        top_n: How many products to log at the end.
        region: DuckDuckGo region code, e.g. ``us-en``, ``pl-pl``.
        sources: The sites the shopper is willing to take facts from, if any.
            Empty -- the default -- searches the whole web. Given any, the
            search runs once per source and keeps only what came from one of
            them, so every figure and every quote in the report was printed by
            a page the shopper named (ADR-0027).
        fetch_pages: Read the result pages themselves. Off means snippets only,
            which is faster but rarely yields a price.
        page_chars: Per-page budget for the lines quoting a price or a rating.
        opinion_chars: Per-page budget for the lines saying what the product is
            like to own, on top of ``page_chars``. A budget of its own so a page
            listing forty prices still contributes a verdict, and a page of prose
            still contributes its price; 0 leaves the opinions unread.
        fetch_timeout: Seconds to wait on any single page.
        weights: Relative importance of rating, popularity and price when ranking.
    """

    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    temperature: float = 0.0
    num_ctx: int | None = 8192
    reasoning: bool | None = False
    search_results: int = 10
    num_products: int = 10
    top_n: int = 3
    region: str = "us-en"
    sources: tuple[Source, ...] = ()
    fetch_pages: bool = True
    page_chars: int = 1200
    opinion_chars: int = 400
    fetch_timeout: float = 8.0
    weights: RankingWeights = field(default_factory=RankingWeights)
