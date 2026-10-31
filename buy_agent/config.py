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

#: How a switch is spelt on and off, in the environment and in a request.
SWITCHED_ON = frozenset({"1", "true", "yes", "on"})
SWITCHED_OFF = frozenset({"0", "false", "no", "off"})


def _switched(value: str) -> bool:
    """The environment's switches are on as spelt, and off however else they are."""
    return value.strip().lower() in SWITCHED_ON


@dataclass(frozen=True, slots=True)
class ServerEnvironment:
    """The web server's own settings, off the environment it is deployed in, each the
    default of its flag (ADR-0092): an image's command line is the one place an operator
    cannot reach. Blank is unset, and the server's own default stands."""

    host: str = ""
    port: str = ""
    allowed_hosts: tuple[str, ...] = ()
    max_runs: str = ""
    behind_proxy: bool = False
    access_log: bool = False
    log_format: str = ""
    #: Asked of every request but the probes when set. A secret: no flag, no field.
    token: str = field(default="", repr=False)


def server_environment() -> ServerEnvironment:
    """Read when the server starts, not when this module is imported."""
    return ServerEnvironment(
        host=os.getenv("BUY_AGENT_HOST", "").strip(), port=os.getenv("BUY_AGENT_PORT", "").strip(),
        allowed_hosts=tuple(os.getenv("BUY_AGENT_ALLOWED_HOSTS", "").replace(",", " ").split()),
        max_runs=os.getenv("BUY_AGENT_MAX_RUNS", "").strip(),
        behind_proxy=_switched(os.getenv("BUY_AGENT_BEHIND_PROXY", "")),
        access_log=_switched(os.getenv("BUY_AGENT_ACCESS_LOG", "")),
        log_format=os.getenv("BUY_AGENT_LOG_FORMAT", "").strip(),
        token=os.getenv("BUY_AGENT_API_TOKEN", "").strip(),
    )


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
    #: 0 is as many as the report needs: never fewer pages than products found or shown.
    search_results: int = 0
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
        self.search_results = self.search_results or max(self.num_products, self.top_n)
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
