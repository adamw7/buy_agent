"""Runtime configuration for the agent."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from buy_agent.ranking import RankingWeights

DEFAULT_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")
DEFAULT_BASE_URL = "http://localhost:11434"


@dataclass(slots=True)
class AgentConfig:
    """Everything the agent needs to know that is not the user's query.

    Attributes:
        model: Ollama model tag, e.g. ``llama3.2`` or ``qwen2.5:7b``.
        base_url: Where the Ollama server listens.
        temperature: Low by default; extraction is a copying task, not a creative one.
        num_ctx: Context window in tokens, or None to leave Ollama's default (4096)
            alone. The extraction prompt runs to ~3.3k tokens, so on the default a
            thinking model has no room left to answer -- see ``reasoning``.
        reasoning: Thinking mode. None (default) sends nothing and leaves the model's
            own behaviour alone; False turns thinking off; True turns it on. Thinking
            models need False here: they spend the whole remaining context reasoning
            about a copying task and get cut off before emitting any JSON.
        search_results: How many raw web results to feed the extractor.
        num_products: How many products to keep after extraction.
        top_n: How many products to log at the end.
        region: DuckDuckGo region code, e.g. ``us-en``, ``pl-pl``.
        fetch_pages: Read the result pages themselves. Off means snippets only,
            which is faster but rarely yields a price.
        page_chars: Per-page budget for condensed text in the prompt.
        fetch_timeout: Seconds to wait on any single page.
        weights: Relative importance of rating, popularity and price when ranking.
    """

    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    temperature: float = 0.0
    num_ctx: int | None = None
    reasoning: bool | None = None
    search_results: int = 10
    num_products: int = 10
    top_n: int = 3
    region: str = "us-en"
    fetch_pages: bool = True
    page_chars: int = 1200
    fetch_timeout: float = 8.0
    weights: RankingWeights = field(default_factory=RankingWeights)
