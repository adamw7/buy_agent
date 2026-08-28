"""Runtime configuration for the agent."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from buy_agent.providers import PROVIDERS, provider_for
from buy_agent.ranking import RankingWeights
from buy_agent.sources import Source

#: Which model server a run talks to when nothing says otherwise. Ollama, because
#: that is what ADR-0003 chose and what the README's first run uses; vLLM is the
#: same pipeline over a server someone already runs (ADR-0028).
DEFAULT_PROVIDER = os.getenv("BUY_AGENT_PROVIDER", "ollama")

DEFAULT_MODEL = os.getenv("OLLAMA_MODEL", "gemma4:12b")
DEFAULT_BASE_URL = os.getenv("OLLAMA_HOST", "http://localhost:11434")

#: vLLM's half of the same pair. A repository id rather than a tag, because that
#: is what ``vllm serve`` is given and what ``/v1/models`` reports back.
VLLM_MODEL = os.getenv("VLLM_MODEL", "Qwen/Qwen3-8B")
#: The API root and not the host: vLLM serves the OpenAI API under ``/v1``, and
#: the OpenAI client appends its paths to whatever it is given.
VLLM_BASE_URL = os.getenv("VLLM_HOST", "http://localhost:8000/v1")
#: Only needed by a vLLM started with ``--api-key``. Read from the environment
#: and from nowhere else: it is a secret, so it is not a CLI flag that lands in a
#: shell history and not a field the API sends to a browser.
VLLM_API_KEY = os.getenv("VLLM_API_KEY", "")

#: The model and the server each provider falls back to, by provider name. This
#: is the one table that reads the environment, which is why it lives here and
#: not in :mod:`buy_agent.providers` -- that module acts on a config, it does not
#: decide what an unset field means. ``tests/test_conventions.py`` checks these
#: keys against :data:`buy_agent.providers.PROVIDERS`.
PROVIDER_DEFAULTS: dict[str, tuple[str, str]] = {
    "ollama": (DEFAULT_MODEL, DEFAULT_BASE_URL),
    "vllm": (VLLM_MODEL, VLLM_BASE_URL),
}


@dataclass(slots=True)
class AgentConfig:
    """Everything the agent needs to know that is not the user's query.

    Attributes:
        provider: Which model server to talk to -- ``"ollama"`` or ``"vllm"``.
            Overridable with ``$BUY_AGENT_PROVIDER``. It is what decides the
            meaning of every field below it: the two servers take different model
            names, listen on different ports, and are restarted with different
            commands when they are not there.
        model: The model to use, empty for the provider's own default. An Ollama
            tag (``gemma4:12b``) or the repository id a vLLM was started with
            (``Qwen/Qwen3-8B``).
        base_url: Where that server listens, empty for the provider's own
            default. vLLM's includes the ``/v1`` its OpenAI API is served under.
        api_key: Sent to vLLM when it was started with ``--api-key``; Ollama
            ignores it. Blank -- the default unless ``$VLLM_API_KEY`` is set --
            sends a placeholder, which is what a vLLM checking no key expects.
        temperature: Low by default; extraction is a copying task, not a creative one.
        num_ctx: Context window in tokens, or None to leave the server's own
            default alone. The extraction prompt runs to ~4.3k tokens, so on
            Ollama's default 4096 a thinking model has no room left to answer --
            see ``reasoning``. Defaults to 8192 because ``DEFAULT_MODEL`` is a
            thinking model. **Ollama only**: vLLM fixes its window when it starts
            (``--max-model-len``), so this is not sent there -- which is what
            ``Provider.takes_num_ctx`` declares.
        reasoning: Thinking mode. None sends nothing and leaves the model's own
            behaviour alone; False (the default) turns thinking off; True turns it
            on. Thinking models need False here: they spend the whole remaining
            context reasoning about a copying task and get cut off before emitting
            any JSON. A model that cannot think ignores it. Both providers take
            it -- Ollama as its own ``think`` option, vLLM as the
            ``enable_thinking`` its chat templates read.
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

    Raises:
        ValueError: if ``provider`` names a server this agent cannot talk to.
    """

    provider: str = DEFAULT_PROVIDER
    model: str = ""
    base_url: str = ""
    api_key: str = VLLM_API_KEY
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

    def __post_init__(self) -> None:
        """Fill in whichever of the model and the server the provider decides.

        The two cannot be plain field defaults, because which value is right
        depends on a *sibling* field: ``gemma4:12b`` on port 11434 is nonsense
        for a vLLM, and ``Qwen/Qwen3-8B`` on port 8000 is nonsense for an Ollama.
        So an unset one is the empty string -- the same "unset" a blank form
        field means (ADR-0012) -- and is resolved here, once, where both front
        ends and every Python caller go through it.
        """
        provider_for(self.provider)  # raises for a name nothing can serve
        model, base_url = PROVIDER_DEFAULTS[self.provider]
        self.model = self.model or model
        self.base_url = self.base_url or base_url


def provider_options() -> list[dict[str, object]]:
    """Every provider a run can be pointed at, as the form's picker needs it.

    Carries each one's defaults, so choosing a provider in the browser can fill
    in the model and the server that go with it rather than leaving an Ollama tag
    in a field a vLLM will refuse.
    """
    return [
        {
            "name": name,
            "label": PROVIDERS[name].label,
            "model": model,
            "base_url": base_url,
            "takes_num_ctx": PROVIDERS[name].takes_num_ctx,
        }
        for name, (model, base_url) in PROVIDER_DEFAULTS.items()
    ]
