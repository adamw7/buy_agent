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

DEFAULT_PROVIDER = os.getenv("BUY_AGENT_PROVIDER", "ollama")
DEFAULT_RAIL = os.getenv("BUY_AGENT_RAIL", "dry-run")
#: DuckDuckGo, needing no key (ADR-0057).
DEFAULT_BACKEND = os.getenv("BUY_AGENT_BACKEND", "ddg")

#: The range each numeric setting is held to, by the field it bounds.
LIMITS: dict[str, tuple[int, int]] = {
    "num_products": (1, 50),
    "top_n": (1, 50),
    "temperature": (0, 2),
    "num_ctx": (1, 1_000_000),
    "model_timeout": (1, 3600),
    "max_price": (1, 10_000_000),
    "alert_below": (1, 10_000_000),
    "min_rating": (0, 5),
    "min_reviews": (0, 10_000_000),
    # 0 is off; past 30 days a stored price is no evidence (ADR-0040).
    "cache_ttl": (0, 2_592_000),
    "spend_limit": (1, 10_000_000),
}

DEFAULT_REGION = "us-en"
#: Country then language, e.g. ``us-en``, ``hk-tzh`` (ADR-0031).
REGION = re.compile(r"[a-z]{2}-[a-z]{2,3}")


def parse_region(spec: str) -> str:
    region = spec.strip().lower()
    if not REGION.fullmatch(region):
        raise ValueError(
            f"{spec!r} is not a search region. Give a country and then a language, "
            f"hyphenated: {DEFAULT_REGION}, uk-en, pl-pl."
        )
    return region


def parse_currency(spec: str) -> str:
    """The run's currency code, or blank for the pages' vote (ADR-0056); spellings fold
    (``$``, ``usd``) and unplaceable ones are refused."""
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
    """Every setting of a run besides the request."""

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
    #: Told whether anything is at or under it, never applied (ADR-0080).
    alert_below: float | None = None
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
        return backend_for(self.backend)

    @property
    def rail_used(self) -> Rail:
        return rail_for(self.rail)

    @property
    def model_server(self) -> Provider:
        return provider_for(self.provider)

    def __post_init__(self) -> None:
        """Resolve per-row defaults and refuse unknown names (ADR-0012)."""
        server = self.model_server
        self.model = self.model or server.model
        self.base_url = self.base_url or server.base_url
        self.api_key = self.api_key or server.api_key
        self.region = parse_region(self.region)
        self.currency = parse_currency(self.currency)
        backend_for(self.backend)
        rail = self.rail_used
        self.merchant_url = (self.merchant_url or rail.endpoint).rstrip("/")
        if self.pay and rail.needs_endpoint and not self.merchant_url:
            raise ValueError(
                f"Paying through {rail.label} needs an address: give it a payment "
                f"endpoint, or set $BUY_AGENT_MERCHANT_URL."
            )
