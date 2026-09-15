"""The agent itself: request -> search query -> web search -> products -> ranked top N."""

from __future__ import annotations

import logging
from time import sleep
from typing import TYPE_CHECKING, Any, TypeAlias

from buy_agent.cache import remember_answers
from buy_agent.chat import UnreadableAnswerError, release
from buy_agent.config import DEFAULT_REGION, AgentConfig
from buy_agent.constraints import Constraints
from buy_agent.extraction import (
    build_extraction_chain,
    build_query_chain,
    clean_products,
    deduplicate,
    format_results,
)
from buy_agent.fetch import enrich
from buy_agent.logging_setup import log_top_products
from buy_agent.ranking import rank_products
from buy_agent.search import search_web
from buy_agent.verification import ground

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from buy_agent.chat import Chain, ChatModel
    from buy_agent.models import Product, RankedProduct
    from buy_agent.ranking import SortBy
    from buy_agent.search import SearchResult

logger = logging.getLogger(__name__)

#: Called at each of a run's step boundaries with the name of the step about to start
#: (ADR-0034).
Checkpoint: TypeAlias = "Callable[[str], None]"

#: How the two steps that talk to the web wait before asking a second time (ADR-0053).
Wait: TypeAlias = "Callable[[float], None]"


def every_step_passes(_step: str) -> None:
    """The default checkpoint: nobody is watching, so every boundary passes."""


class ModelUnavailableError(RuntimeError):
    """Raised when the model could not be used: no server, no model, or no answer
    (ADR-0028, ADR-0009)."""


def _asks_the_same_question(config: AgentConfig) -> dict[str, object]:
    """Everything besides the prompt that decides what a model answers (ADR-0044)
    (ADR-0051)."""
    fingerprint: dict[str, object] = {
        "provider": config.provider,
        "model": config.model,
        "base_url": config.base_url,
        "reasoning": config.reasoning,
    }
    if config.model_server.takes_num_ctx:
        fingerprint["num_ctx"] = config.num_ctx
    return fingerprint


def _and_list(items: list[str]) -> str:
    """``a``, ``a and b``, ``a, b and c`` -- a list somebody reads rather than parses."""
    if len(items) < 2:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


class BuyAgent:
    """Finds products for a shopper, ranks them, and logs the best few (ADR-0002,
    ADR-0028)."""

    def __init__(
        self, config: AgentConfig | None = None, *, llm: ChatModel | None = None
    ) -> None:
        """Build an agent."""
        self.config = config or AgentConfig()
        # The remembering goes here rather than in ``providers``: it has nothing to do
        # with which server is answering, so neither row declares it.
        self.llm = llm or remember_answers(
            self.config.model_server.chat_model(self.config),
            fingerprint=_asks_the_same_question(self.config),
            ttl=self.config.cache_ttl,
            deterministic=self.config.temperature == 0,
        )
        #: What :meth:`close` lets go of: the model this agent opened, never one it was
        #: handed -- closing that would be this agent deciding somebody else's lifetime.
        self._opened = None if llm else self.llm
        self.query_chain = build_query_chain(self.llm)
        self.extraction_chain = build_extraction_chain(self.llm)

    def close(self) -> None:
        """Let go of the connection to the model server this agent opened."""
        release(self._opened)

    def run(
        self,
        request: str,
        *,
        sort_by: SortBy = "score",
        checkpoint: Checkpoint = every_step_passes,
    ) -> list[RankedProduct]:
        """Search for what the shopper asked for and log the top products (ADR-0009,
        ADR-0034).

        Raises:
            ValueError: if the request is empty.
            ModelUnavailableError: if the model server or the model is missing.
            SearchError: if the web search backend could not be reached.
        """
        request = request.strip()
        if not request:
            raise ValueError("Nothing to shop for: the request is empty.")

        logger.info("Shopping for: %s", request)
        query = self._refine_query(request)
        checkpoint("search")
        results = self._search(query)
        if not results:
            logger.warning(
                "Search returned nothing for %r%s", query, self._empty_search_note()
            )
            return []

        if self.config.fetch_pages:
            checkpoint("fetch")
            results = enrich(
                results,
                max_chars=self.config.page_chars,
                opinion_chars=self.config.opinion_chars,
                timeout=self.config.fetch_timeout,
                cache_ttl=self.config.cache_ttl,
                wait=sleep,
            )

        checkpoint("extract")
        products = self._extract_products(request, results)
        if not products:
            logger.warning("No products could be extracted from the search results.")
            return []

        # After the merging: ``deduplicate`` fills a listing's gaps from another listing
        # of the same product, so a price known only once the two are merged would be
        # judged here on a blank (ADR-0039).
        products = Constraints.from_config(self.config).apply(products)
        if not products:
            return []

        # Ranking is cheap, but it ends in ``log_top_products``, and a report is worth
        # not writing for a run nobody is reading any more.
        checkpoint("rank")
        ranked = rank_products(products, weights=self.config.weights, sort_by=sort_by)
        log_top_products(
            ranked, self.config.top_n, weights=self.config.weights, sort_by=sort_by
        )
        return ranked

    def _search(self, query: str) -> list[SearchResult]:
        """Search the web, or only the sources the shopper named (ADR-0027, ADR-0053)."""
        sources = self.config.sources
        width = self.config.search_results
        if not sources:
            return self._ask_the_web(query, width)

        logger.info(
            "Searching %d named source(s): %s",
            len(sources),
            ", ".join(source.spec for source in sources),
        )
        share = -(-width // len(sources))  # ceiling: every source gets at least one
        pooled: dict[str, SearchResult] = {}
        for source in sources:
            found = self._ask_the_web(source.site_query(query), share)
            kept = [result for result in found if source.covers(result.url)]
            if len(kept) != len(found):
                logger.info(
                    "Ignored %d result(s) from outside %s", len(found) - len(kept), source.domain
                )
            for result in kept:
                pooled.setdefault(result.url, result)
        return list(pooled.values())[:width]

    def _ask_the_web(self, query: str, limit: int) -> list[SearchResult]:
        """One search, on this run's region and this run's clock (ADR-0053)."""
        return search_web(query, max_results=limit, region=self.config.region, wait=sleep)

    def _empty_search_note(self) -> str:
        """What narrowed this search, for the one line that says it found nothing."""
        return f"{self._region_note() or '.'}{self._sources_note()}"

    def _sources_note(self) -> str:
        """The named sources, when they are what the search was confined to (ADR-0027)."""
        sources = self.config.sources
        if not sources:
            return ""
        named = _and_list([source.spec for source in sources])
        was = "was" if len(sources) == 1 else "were"
        return (
            f" Only {named} {was} searched, and there is no falling back to the rest "
            f"of the web: a source that does not cover this leaves nothing to report."
        )

    def _region_note(self) -> str:
        """The region, when it is one worth suspecting of an empty search (ADR-0031)."""
        region = self.config.region
        if region == DEFAULT_REGION:
            return ""
        return (
            f" in region {region}. A region a search engine does not know returns "
            f"nothing rather than failing -- the codes are a country and then a "
            f"language, like {DEFAULT_REGION} or pl-pl."
        )

    def _refine_query(self, request: str) -> str:
        """Ask the LLM for a better search query, falling back to the raw request."""
        try:
            refined = self._invoke(self.query_chain, {"request": request})
        except ModelUnavailableError:
            raise
        # A bad query is recoverable -- searching the raw request still works, so what
        # went wrong is narrower than the catch and the catch is deliberate.
        # pylint: disable-next=broad-exception-caught
        except Exception:
            logger.warning("Query refinement failed; using the raw request", exc_info=True)
            return request

        query = refined.query.strip()
        if not query:
            return request
        logger.info("Refined search query: %s", query)
        return query

    def _extract_products(
        self, request: str, results: Sequence[SearchResult]
    ) -> list[Product]:
        """Read products out of the results, then keep only what the sources back."""
        logger.info("Extracting up to %d products from the results", self.config.num_products)
        payload = {
            "request": request,
            "results": format_results(results),
            "limit": self.config.num_products,
        }
        try:
            extracted = self._invoke(self.extraction_chain, payload)
        except UnreadableAnswerError as exc:
            # Caught here rather than in ``_invoke``, which the recoverable step goes
            # through too: a fumbled query falls back to the raw request, an unreadable
            # extraction has nothing to.
            logger.debug("The model's answer could not be read", exc_info=True)
            server = self.config.model_server
            raise ModelUnavailableError(server.hint(self.config, exc)) from exc

        products = [item.to_product() for item in extracted.products]
        logger.info("Extracted %d candidate(s)", len(products))
        grounded = ground(clean_products(products), results)
        return deduplicate(grounded, self.config.num_products)

    def _invoke(self, chain: Chain[Any], payload: dict[str, Any]) -> Any:
        """Invoke a chain, turning transport errors into an actionable message (ADR-0009)."""
        server = self.config.model_server
        try:
            return chain.invoke(payload)
        except server.transport_errors as exc:
            raise ModelUnavailableError(server.hint(self.config, exc)) from exc
