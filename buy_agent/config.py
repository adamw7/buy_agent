"""Runtime configuration for the agent."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from buy_agent.cache import DEFAULT_TTL
from buy_agent.providers import Provider, provider_for
from buy_agent.rails import Rail, rail_for
from buy_agent.ranking import RankingWeights
from buy_agent.sources import Source

#: Which model server a run talks to when nothing says otherwise. Ollama, per
#: ADR-0003; vLLM is the same pipeline over a server someone already runs
#: (ADR-0028). What each defaults to is its own row in ``providers.PROVIDERS``.
DEFAULT_PROVIDER = os.getenv("BUY_AGENT_PROVIDER", "ollama")

#: Which rail a payment goes through when nothing says otherwise. The dry run,
#: which signs a real authorisation and charges nobody. What each rail defaults
#: to is its own row in :data:`buy_agent.rails.RAILS`.
DEFAULT_RAIL = os.getenv("BUY_AGENT_RAIL", "dry-run")

#: The range each numeric setting is held to, by the field it bounds. Declared
#: beside the fields because both front ends enforce it, and a bound written twice
#: is a CLI that accepts what the API refuses. Whole even where the field is
#: decimal, the rejection quoting them back: "between 0 and 2" is a temperature.
LIMITS: dict[str, tuple[int, int]] = {
    "num_products": (1, 50),
    "top_n": (1, 50),
    "temperature": (0, 2),
    "num_ctx": (1, 1_000_000),
    # The longest one question may take. A ceiling of an hour rather than of a
    # day: past that the shopper has gone, and a floor of 1 because a second is
    # a legitimate thing to ask for of a server that is meant to be warm.
    "model_timeout": (1, 3600),
    # The shopper's own three (ADR-0039). Their ranges are what a *number* may be
    # rather than what a sensible bound is; the ceilings make a slip on the
    # keyboard a usage error rather than an empty report.
    "max_price": (1, 10_000_000),
    "min_rating": (0, 5),
    "min_reviews": (0, 10_000_000),
    # 0 is off -- every page read fresh -- and the ceiling is 30 days, past
    # which a stored price is not evidence of anything (ADR-0040).
    "cache_ttl": (0, 2_592_000),
    # The most one payment may be. Its range is ``max_price``'s and its promise
    # the opposite: that one admits a product it cannot judge, this one refuses.
    "spend_limit": (1, 10_000_000),
}

#: Where the search looks when nothing says otherwise. Named rather than written
#: into the field below, two other places quoting it: the shape a mistyped one is
#: measured against, and the warning that tells a suspect region from a safe one.
DEFAULT_REGION = "us-en"

#: What a search region looks like: a country then a language, hyphenated --
#: ``us-en``, ``pl-pl``, the three-letter ``hk-tzh``. A shape and not the list of
#: codes that exist, ddgs asking several engines that each read the halves their
#: own way -- DuckDuckGo's list refuses ``de-de``, which Google takes (ADR-0031).
REGION = re.compile(r"[a-z]{2}-[a-z]{2,3}")


def parse_region(spec: str) -> str:
    """``spec`` as a region code: lower-cased, and shaped like one.

    Lower-cased rather than kept as typed: the halves reach an engine as written, and
    ``US-EN`` asks Google for a language it calls ``lang_EN``, which is nothing.

    Raises:
        ValueError: if it is not a country and a language, hyphenated, naming the
            shape and three codes that have it. It is the one search setting that
            otherwise fails silently, a region no engine knows reading as the web
            having nothing to say (ADR-0031).
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
            overridable with ``$BUY_AGENT_PROVIDER``. It decides the meaning of the
            three fields below it, the two servers taking different model names,
            ports and commands.
        model: The model to use, empty for the provider's own default: an Ollama tag
            (``gemma4:12b``) or a vLLM repository id (``Qwen/Qwen3-8B``).
        base_url: Where that server listens, empty for the provider's own default.
            vLLM's includes the ``/v1`` its OpenAI API is served under.
        api_key: Sent to vLLM when it was started with ``--api-key``; Ollama has no
            notion of one. Empty takes ``$VLLM_API_KEY``, and a vLLM given none is
            sent the placeholder one checking no key expects.
        temperature: Low by default: extraction is copying, not creation.
        num_ctx: Context window in tokens, or None to leave the server's own alone.
            The extraction prompt runs to ~4.3k tokens, so on Ollama's default 4096 a
            thinking model has no room left to answer; 16384 because Ollama's default
            model is one and the JSON for ten products is the answer on top of that
            prompt (ADR-0050). **Ollama only** -- vLLM fixes its window at startup,
            which ``Provider.takes_num_ctx`` declares.
        model_timeout: The longest to wait for one answer, in seconds. Both servers
            are given it, neither asked twice: a question that timed out is a hint
            about the model rather than a question to repeat, and a setting a retry
            could multiply would not be the wait it names (ADR-0051). It is not part
            of what makes an answer reusable, a slow answer and a fast one being the
            same answer (ADR-0044).
        reasoning: Thinking mode: None sends nothing, False (the default) turns
            thinking off, True on. Thinking models need False, spending the remaining
            context reasoning about a copying task; one that cannot think ignores it.
            Ollama takes it as ``think``, vLLM as ``enable_thinking``.
        search_results: How many raw web results to feed the extractor.
        num_products: How many products to keep after extraction.
        top_n: How many products to log at the end.
        max_price: The most the shopper will pay, or None for no bound. Applied after
            grounding and before ranking, so what is reported is what was asked for
            (ADR-0039). Read in the currency the run's prices are counted in, and
            nothing is converted, so a price in another currency is one this bound
            cannot judge and does not (ADR-0043).
        min_rating: The lowest average review score worth reporting, on
            ``Product.rating``'s 0-5 scale, or None for no bound.
        min_reviews: How many reviews a rating has to be averaged over, or None. A
            product whose figure is unknown passes all three: a blank is the extractor
            having missed something, not a violation.
        region: Search region -- a country then a language, e.g. ``us-en``, ``pl-pl``.
            Checked and lower-cased by :func:`parse_region`.
        sources: The sites the shopper will take facts from. Empty searches the whole
            web; given any, the search runs once per source and keeps only what came
            from one, so every figure and quote was printed by a page the shopper
            named (ADR-0027).
        fetch_pages: Read the result pages. Off is snippets only -- faster, but they
            rarely quote a price.
        page_chars: Per-page budget for the lines quoting a price or a rating.
        opinion_chars: Per-page budget for the lines saying what the product is like
            to own, on top of ``page_chars`` -- its own, so a page listing forty
            prices still contributes a verdict; 0 leaves the opinions unread.
        fetch_timeout: Seconds to wait on any single page.
        cache_ttl: How many seconds a fetched page stays usable on disk; 0 reads every
            page off the web. A day by default, most of a run being the same ten pages
            it opened last time (ADR-0040). Where they are kept is
            ``$BUY_AGENT_CACHE_DIR``, which has no flag and no form field.
        pay: Whether the agent may pay for what it found. False, and nothing in a run
            changes: paying happens after one, to one product, on a separate decision.
        rail: Who a payment goes through -- ``"dry-run"`` or ``"http"``, overridable
            with ``$BUY_AGENT_RAIL``. The default signs a real AP2 authorisation and
            charges nobody, so ``pay`` is not by itself a way to spend money.
        merchant_url: The AP2-speaking endpoint a paying rail talks to, empty for the
            rail's own default (``$BUY_AGENT_MERCHANT_URL``). No merchant is named
            anywhere in this project; the address is the integration.
        spend_limit: The most one payment may be, or None -- read in the currency the
            run's prices are counted in. Unlike the shopper's bounds, a price this run
            cannot place fails it rather than passing: an amount nobody can place is
            not an amount to send.
        weights: Relative importance of rating, popularity and price when ranking.

    Two settings a payment needs have no field, no flag and no form field, for the
    reason ``$VLLM_API_KEY`` and ``$BUY_AGENT_CACHE_DIR`` have none:
    ``$BUY_AGENT_AP2_KEY`` is the key mandates are signed with, and
    ``$BUY_AGENT_AP2_MANDATE`` the pre-signed open mandate whose presence lets the
    agent buy unattended.

    Raises:
        ValueError: if ``provider`` names a server this agent cannot talk to, ``rail``
            one it cannot pay through, ``region`` is not shaped like a region code, or
            a paying rail was given no address.
    """

    provider: str = DEFAULT_PROVIDER
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    temperature: float = 0.0
    num_ctx: int | None = 16384
    model_timeout: float = 600.0
    reasoning: bool | None = False
    search_results: int = 10
    num_products: int = 10
    top_n: int = 3
    max_price: float | None = None
    min_rating: float | None = None
    min_reviews: int | None = None
    region: str = DEFAULT_REGION
    sources: tuple[Source, ...] = ()
    fetch_pages: bool = True
    page_chars: int = 1200
    opinion_chars: int = 400
    fetch_timeout: float = 8.0
    cache_ttl: float = DEFAULT_TTL
    pay: bool = False
    rail: str = DEFAULT_RAIL
    merchant_url: str = ""
    spend_limit: float | None = None
    weights: RankingWeights = field(default_factory=RankingWeights)

    @property
    def rail_used(self) -> Rail:
        """The rail this config names, and everything that differs about it.

        The one place a rail name becomes behaviour -- where the checkout is signed, what
        a settlement means, which failures mean "not there" -- exactly as for a provider.
        """
        return rail_for(self.rail)

    @property
    def model_server(self) -> Provider:
        """The server this config names, and everything that differs about it.

        The one place a provider name becomes behaviour: the chat model, the failures
        meaning "not there", the sentence one carries and the listing behind the model
        picker all hang off it (ADR-0029).
        """
        return provider_for(self.provider)

    def __post_init__(self) -> None:
        """Fill in whichever of the three the provider decides.

        None can be a plain field default: which value is right depends on a *sibling*
        field, ``gemma4:12b`` on port 11434 being nonsense for a vLLM. So an unset one is
        the empty string (ADR-0012), resolved here where both front ends and every Python
        caller go through it -- and the region is checked here for the same reason.
        """
        server = self.model_server  # raises for a name nothing can serve
        self.model = self.model or server.model
        self.base_url = self.base_url or server.base_url
        self.api_key = self.api_key or server.api_key
        self.region = parse_region(self.region)

        rail = self.rail_used  # raises for a name nothing can pay through
        self.merchant_url = (self.merchant_url or rail.endpoint).rstrip("/")
        if self.pay and rail.needs_endpoint and not self.merchant_url:
            raise ValueError(
                f"Paying through {rail.label} needs an address: give --merchant-url "
                f"or set $BUY_AGENT_MERCHANT_URL."
            )
