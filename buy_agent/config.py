"""Runtime configuration for the agent."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from buy_agent.cache import DEFAULT_TTL
from buy_agent.money import CODES, placeable
from buy_agent.providers import Provider, provider_for
from buy_agent.rails import Rail, rail_for
from buy_agent.ranking import RankingWeights
from buy_agent.search import Backend, backend_for
from buy_agent.sources import Source

#: Which model server a run talks to when nothing says otherwise: Ollama (ADR-0003),
#: vLLM being the same pipeline over a server someone already runs (ADR-0028), and a
#: LiteLLM proxy the same again over whatever that someone routes it to (ADR-0068).
DEFAULT_PROVIDER = os.getenv("BUY_AGENT_PROVIDER", "ollama")

#: Which rail a payment goes through when nothing says otherwise.
DEFAULT_RAIL = os.getenv("BUY_AGENT_RAIL", "dry-run")

#: Which search backend a run asks when nothing says otherwise: DuckDuckGo, which needs
#: no key and no account (ADR-0057).
DEFAULT_BACKEND = os.getenv("BUY_AGENT_BACKEND", "ddg")

#: The range each numeric setting is held to, by the field it bounds.
LIMITS: dict[str, tuple[int, int]] = {
    "num_products": (1, 50),
    "top_n": (1, 50),
    "temperature": (0, 2),
    "num_ctx": (1, 1_000_000),
    # The longest one question may take.
    "model_timeout": (1, 3600),
    # The shopper's own three (ADR-0039).
    "max_price": (1, 10_000_000),
    "min_rating": (0, 5),
    "min_reviews": (0, 10_000_000),
    # 0 is off -- every page read fresh -- and the ceiling is 30 days, past which a
    # stored price is not evidence of anything (ADR-0040).
    "cache_ttl": (0, 2_592_000),
    # The most one payment may be.
    "spend_limit": (1, 10_000_000),
}

#: Where the search looks when nothing says otherwise.
DEFAULT_REGION = "us-en"

#: What a search region looks like: a country then a language, hyphenated -- ``us-en``,
#: ``pl-pl``, the three-letter ``hk-tzh`` (ADR-0031).
REGION = re.compile(r"[a-z]{2}-[a-z]{2,3}")


def parse_region(spec: str) -> str:
    """``spec`` as a region code: lower-cased, and shaped like one (ADR-0031)."""
    region = spec.strip().lower()
    if not REGION.fullmatch(region):
        raise ValueError(
            f"{spec!r} is not a search region. Give a country and then a language, "
            f"hyphenated: {DEFAULT_REGION}, uk-en, pl-pl."
        )
    return region


def parse_currency(spec: str) -> str:
    """``spec`` as the currency a run counts itself in, or nothing at all (ADR-0056).

    Blank is the default and means "whatever the pages quote", which is the vote
    ADR-0043 settled the scale by. A spelling is folded the way a page's is -- ``$``
    and ``usd`` are both USD -- and anything this run could never place is refused
    here rather than silently leaving every price off the scale.
    """
    named = spec.strip()
    if not named:
        return ""
    code = placeable(named)
    if code is None:
        raise ValueError(
            f"{spec!r} is not a currency this run can count in. Leave it empty to "
            f"count in whatever the pages quote, or name one of: "
            f"{', '.join(sorted(CODES))}."
        )
    return code


@dataclass(slots=True)
class AgentConfig:
    """Everything the agent needs to know that is not the user's query (ADR-0050,
    ADR-0051, ADR-0044, ADR-0039, ADR-0043, ADR-0027, ADR-0040, ADR-0046,
    ADR-0056, ADR-0057, ADR-0060)."""

    provider: str = DEFAULT_PROVIDER
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    temperature: float = 0.0
    num_ctx: int | None = 16384
    model_timeout: float = 600.0
    reasoning: bool | None = False
    cpu_only: bool = False
    search_results: int = 10
    num_products: int = 10
    top_n: int = 3
    max_price: float | None = None
    min_rating: float | None = None
    min_reviews: int | None = None
    region: str = DEFAULT_REGION
    currency: str = ""
    backend: str = DEFAULT_BACKEND
    sources: tuple[Source, ...] = ()
    fetch_pages: bool = True
    page_chars: int = 1200
    opinion_chars: int = 400
    fetch_timeout: float = 8.0
    cache_ttl: float = DEFAULT_TTL
    journal: bool = True
    pay: bool = False
    rail: str = DEFAULT_RAIL
    merchant_url: str = ""
    spend_limit: float | None = None
    weights: RankingWeights = field(default_factory=RankingWeights)

    @property
    def search_backend(self) -> Backend:
        """The backend this config names, and everything that differs about it (ADR-0057)."""
        return backend_for(self.backend)

    @property
    def rail_used(self) -> Rail:
        """The rail this config names, and everything that differs about it (ADR-0046)."""
        return rail_for(self.rail)

    @property
    def model_server(self) -> Provider:
        """The server this config names, and everything that differs about it (ADR-0029)."""
        return provider_for(self.provider)

    def __post_init__(self) -> None:
        """Fill in whichever of the three the provider decides (ADR-0012)."""
        server = self.model_server  # raises for a name nothing can serve
        self.model = self.model or server.model
        self.base_url = self.base_url or server.base_url
        self.api_key = self.api_key or server.api_key
        self.region = parse_region(self.region)
        self.currency = parse_currency(self.currency)
        # Resolved here for the reason the provider is: a name nothing can search is
        # refused where the config is built rather than a minute into the run.
        backend_for(self.backend)

        rail = self.rail_used  # raises for a name nothing can pay through
        self.merchant_url = (self.merchant_url or rail.endpoint).rstrip("/")
        if self.pay and rail.needs_endpoint and not self.merchant_url:
            # Named as the setting and not as the flag: both front doors show this
            # sentence, and the browser is handed it under a box labelled "Payment
            # endpoint" with no command line to type a flag into.
            raise ValueError(
                f"Paying through {rail.label} needs an address: give it a payment "
                f"endpoint, or set $BUY_AGENT_MERCHANT_URL."
            )
