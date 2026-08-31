"""Runtime configuration for the agent."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from buy_agent.providers import Provider, provider_for
from buy_agent.ranking import RankingWeights
from buy_agent.sources import Source

#: Which model server a run talks to when nothing says otherwise. Ollama, per
#: ADR-0003; vLLM is the same pipeline over a server someone already runs
#: (ADR-0028). The only default here that is not one server's own -- what each of
#: them defaults to is its row in :data:`buy_agent.providers.PROVIDERS`.
DEFAULT_PROVIDER = os.getenv("BUY_AGENT_PROVIDER", "ollama")

#: The range each numeric setting is held to, by the name of the field it bounds.
#: Declared here, beside the fields, because both front ends enforce it and a
#: bound written down twice is a CLI that accepts what the API refuses: a
#: ``--results 0`` that searches the web, reads ten pages and then asks the model
#: for no products at all is a minute spent on an answer the browser rejects
#: before starting. Whole even where the field is decimal, because the rejection
#: quotes them back and "between 0 and 2" is what a temperature is.
LIMITS: dict[str, tuple[int, int]] = {
    "num_products": (1, 50),
    "top_n": (1, 50),
    "temperature": (0, 2),
    "num_ctx": (1, 1_000_000),
}

#: Where the search looks when nothing says otherwise. Named rather than written
#: into the field below, because two other places quote it: the shape a mistyped
#: one is measured against uses it as its example, and the warning for a search
#: that matched nothing uses it to tell a region worth suspecting from the one
#: value known to work.
DEFAULT_REGION = "us-en"

#: What a search region looks like: a country and then a language, hyphenated --
#: ``us-en``, ``pl-pl``, and the three-letter ``hk-tzh``. A shape and not the list
#: of codes that exist, because ddgs puts the query to several engines and each
#: reads the two halves its own way -- DuckDuckGo's own list would refuse
#: ``de-de``, which Google takes (ADR-0031). Held to here, beside the field, for
#: the reason :data:`LIMITS` is: both front ends enforce it, and a rule written
#: down twice is a CLI that accepts what the API refuses.
REGION = re.compile(r"[a-z]{2}-[a-z]{2,3}")


def parse_region(spec: str) -> str:
    """``spec`` as a region code: lower-cased, and shaped like one.

    Lower-cased rather than kept as typed, because the halves reach an engine as
    they were written -- ``US-EN`` asks Google for results in a language it calls
    ``lang_EN``, which is nothing -- and a search that quietly returns nothing is
    what this check exists to stop.

    Raises:
        ValueError: if it is not a country and a language, hyphenated, naming the
            shape and three codes that have it. It is the one search setting that
            otherwise fails silently: a region no engine knows returns nothing,
            which reads as the web having nothing to say (ADR-0031).
    """
    region = spec.strip().lower()
    if not REGION.fullmatch(region):
        raise ValueError(
            f"{spec!r} is not a search region. Give a country and then a language, "
            f"hyphenated: {DEFAULT_REGION}, uk-en, pl-pl."
        )
    return region


@dataclass(slots=True)
class AgentConfig:
    """Everything the agent needs to know that is not the user's query.

    Attributes:
        provider: Which model server to talk to -- ``"ollama"`` or ``"vllm"``,
            overridable with ``$BUY_AGENT_PROVIDER``. It decides the meaning of
            every field below it: the two take different model names, listen on
            different ports and are restarted with different commands.
        model: The model to use, empty for the provider's own default: an Ollama
            tag (``gemma4:12b``) or a vLLM repository id (``Qwen/Qwen3-8B``).
        base_url: Where that server listens, empty for the provider's own default.
            vLLM's includes the ``/v1`` its OpenAI API is served under.
        api_key: Sent to vLLM when it was started with ``--api-key``; Ollama has
            no notion of one. Empty takes the provider's own (``$VLLM_API_KEY``),
            and a vLLM given none at all is sent the placeholder one checking no
            key expects.
        temperature: Low by default: extraction is copying, not creation.
        num_ctx: Context window in tokens, or None to leave the server's own
            default alone. The extraction prompt runs to ~4.3k tokens, so on
            Ollama's default 4096 a thinking model has no room left to answer;
            8192 because Ollama's default model is one. **Ollama only** -- vLLM
            fixes its window at startup, which ``Provider.takes_num_ctx`` declares.
        reasoning: Thinking mode: None sends nothing and leaves the model alone,
            False (the default) turns thinking off, True on. Thinking models need
            False -- they spend the remaining context reasoning about a copying
            task and are cut off before emitting any JSON -- and a model that
            cannot think ignores it. Ollama takes it as ``think``, vLLM as the
            ``enable_thinking`` its chat templates read.
        search_results: How many raw web results to feed the extractor.
        num_products: How many products to keep after extraction.
        top_n: How many products to log at the end.
        region: Search region -- a country and then a language, e.g. ``us-en``,
            ``uk-en``, ``pl-pl``. Checked and lower-cased by :func:`parse_region`,
            since a code no engine knows returns nothing rather than failing.
        sources: The sites the shopper is willing to take facts from. Empty --
            the default -- searches the whole web; given any, the search runs once
            per source and keeps only what came from one, so every figure and
            quote was printed by a page the shopper named (ADR-0027).
        fetch_pages: Read the result pages. Off is snippets only -- faster, but
            they rarely quote a price.
        page_chars: Per-page budget for the lines quoting a price or a rating.
        opinion_chars: Per-page budget for the lines saying what the product is
            like to own, on top of ``page_chars``. Its own, so a page listing forty
            prices still contributes a verdict and a page of prose still
            contributes its price; 0 leaves the opinions unread.
        fetch_timeout: Seconds to wait on any single page.
        weights: Relative importance of rating, popularity and price when ranking.

    Raises:
        ValueError: if ``provider`` names a server this agent cannot talk to, or
            ``region`` is not shaped like a region code.
    """

    provider: str = DEFAULT_PROVIDER
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    temperature: float = 0.0
    num_ctx: int | None = 8192
    reasoning: bool | None = False
    search_results: int = 10
    num_products: int = 10
    top_n: int = 3
    region: str = DEFAULT_REGION
    sources: tuple[Source, ...] = ()
    fetch_pages: bool = True
    page_chars: int = 1200
    opinion_chars: int = 400
    fetch_timeout: float = 8.0
    weights: RankingWeights = field(default_factory=RankingWeights)

    @property
    def model_server(self) -> Provider:
        """The server this config names, and everything that differs about it.

        The one place a provider name becomes behaviour -- the chat model, the
        failures that mean "not there", the sentence one carries and the listing
        behind the model picker all hang off this (ADR-0029). Nothing above it
        branches on which server is answering.
        """
        return provider_for(self.provider)

    def __post_init__(self) -> None:
        """Fill in whichever of the three the provider decides.

        None can be a plain field default, because which value is right depends on
        a *sibling* field: ``gemma4:12b`` on port 11434 is nonsense for a vLLM. So
        an unset one is the empty string (ADR-0012), resolved here where both front
        ends and every Python caller go through it. The region is checked here for
        the same reason: it is one dataclass every door builds.
        """
        server = self.model_server  # raises for a name nothing can serve
        self.model = self.model or server.model
        self.base_url = self.base_url or server.base_url
        self.api_key = self.api_key or server.api_key
        # Refused here as well as at each front door, so a Python caller building
        # a config gets the same answer the CLI and the API give -- and gets it
        # before a search rather than as an empty report a minute later.
        self.region = parse_region(self.region)
