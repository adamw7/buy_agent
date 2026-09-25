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
from buy_agent.journal import Journal, open_journal
from buy_agent.logging_setup import log_top_products
from buy_agent.models import comparable_price, nothing_recorded
from buy_agent.ranking import rank_products
from buy_agent.search import search_web
from buy_agent.verification import ground

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from buy_agent.chat import Chain, ChatModel
    from buy_agent.models import Product, RankedProduct, Recorder
    from buy_agent.ranking import SortBy
    from buy_agent.search import SearchResult

logger = logging.getLogger(__name__)

#: Called with the name of each step about to start (ADR-0034).
Checkpoint: TypeAlias = "Callable[[str], None]"

#: How the web-facing steps wait before a retry (ADR-0053).
Wait: TypeAlias = "Callable[[float], None]"


def every_step_passes(_step: str) -> None:
    """The default checkpoint: every boundary passes."""


class ModelUnavailableError(RuntimeError):
    """No server, no model, or no answer (ADR-0028, ADR-0009)."""


def _asks_the_same_question(config: AgentConfig) -> dict[str, object]:
    """Everything besides the prompt that decides the answer (ADR-0044, ADR-0051)."""
    fingerprint: dict[str, object] = {
        "provider": config.provider,
        "model": config.model,
        "base_url": config.base_url,
        "reasoning": config.reasoning,
    }
    if config.model_server.takes_num_ctx:
        fingerprint["num_ctx"] = config.num_ctx
    return fingerprint


def journal_for(request: str, config: AgentConfig) -> Journal:
    """The journal of this exact search (ADR-0060).

    Keyed on what was *asked*, never on the model: a model change is not a new search.
    The cache keys the other way (:func:`_asks_the_same_question`) because it hands an
    answer back. Built before the run, so it holds the previous one.
    """
    return open_journal(
        request,
        asked={
            "region": config.region,
            "currency": config.currency,
            "backend": config.backend,
            "sources": [source.spec for source in config.sources],
            "max_price": config.max_price,
            "min_rating": config.min_rating,
            "min_reviews": config.min_reviews,
            "num_products": config.num_products,
        },
        keeping=config.journal,
    )


def _and_list(items: list[str]) -> str:
    """``a``, ``a and b``, ``a, b and c``."""
    if len(items) < 2:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


class BuyAgent:
    """Finds, ranks and logs products for a shopper (ADR-0002, ADR-0028)."""

    def __init__(
        self, config: AgentConfig | None = None, *, llm: ChatModel | None = None
    ) -> None:
        """Build an agent."""
        self.config = config or AgentConfig()
        # Here, not in ``providers``: caching is no server's concern.
        self.llm = llm or remember_answers(
            self.config.model_server.chat_model(self.config),
            fingerprint=_asks_the_same_question(self.config),
            ttl=self.config.cache_ttl,
            deterministic=self.config.temperature == 0,
        )
        #: What :meth:`close` releases: only a model this agent opened, never one handed in.
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
        record: Recorder = nothing_recorded,
    ) -> list[RankedProduct]:
        """Search, rank and log the top products (ADR-0009, ADR-0034, ADR-0055).

        ``record`` receives each candidate the run removed.

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
        products = self._extract_products(request, results, record)
        if not products:
            logger.warning("No products could be extracted from the search results.")
            return []

        # After merging, which may supply the price a bound judges (ADR-0039).
        products = Constraints.from_config(self.config).apply(products, record=record)
        if not products:
            return []

        self._warn_if_nothing_is_on_the_named_scale(products)

        # Cheap, but it writes the report, which a stopped run should not.
        checkpoint("rank")
        ranked = rank_products(
            products,
            weights=self.config.weights,
            sort_by=sort_by,
            currency=self.config.currency or None,
        )
        log_top_products(
            ranked, self.config.top_n, weights=self.config.weights, sort_by=sort_by
        )
        return ranked

    def _warn_if_nothing_is_on_the_named_scale(self, products: Sequence[Product]) -> None:
        """Warn when the named currency prices nothing, so every price scores neutral
        (ADR-0056)."""
        named = self.config.currency
        if not named or any(
            comparable_price(product, named) is not None for product in products
        ):
            return
        logger.warning(
            "Nothing found is priced in %s, so every price is a figure this run cannot "
            "place: the price criterion is assumed for all %d of them and the order is "
            "the rating and the reviews alone. Leave the currency unset to count in "
            "whatever the pages quote.",
            named,
            len(products),
        )

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
            # ``covers`` asked once per result; both sides are reported below.
            covered: dict[bool, list[SearchResult]] = {True: [], False: []}
            for result in found:
                covered[source.covers(result.url)].append(result)
            kept, outside = covered[True], covered[False]
            if outside:
                # Count at INFO, names at DEBUG: with no fallback to the wider web
                # (ADR-0027), an over-strict ``covers`` needs the names to diagnose.
                logger.info(
                    "Ignored %d result(s) from outside %s", len(outside), source.domain
                )
                logger.debug(
                    "From outside %s: %s",
                    source.domain,
                    ", ".join(result.url for result in outside),
                )
            for result in kept:
                pooled.setdefault(result.url, result)
        return list(pooled.values())[:width]

    def _ask_the_web(self, query: str, limit: int) -> list[SearchResult]:
        """One search through this run's backend and region (ADR-0053, ADR-0057)."""
        return search_web(
            query,
            max_results=limit,
            region=self.config.region,
            wait=sleep,
            backend=self.config.search_backend,
        )

    def _empty_search_note(self) -> str:
        """What narrowed this search, for the empty-search warning."""
        return f"{self._region_note() or '.'}{self._sources_note()}"

    def _sources_note(self) -> str:
        """The named sources, if any (ADR-0027)."""
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
        """The region, unless it is the default (ADR-0031)."""
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
        # Any failure here is recoverable: the raw request still searches.
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
        self,
        request: str,
        results: Sequence[SearchResult],
        record: Recorder = nothing_recorded,
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
            # Here, not in ``_invoke``: the query step recovers, this one cannot.
            logger.debug("The model's answer could not be read", exc_info=True)
            server = self.config.model_server
            raise ModelUnavailableError(server.hint(self.config, exc)) from exc

        products = [item.to_product() for item in extracted.products]
        logger.info("Extracted %d candidate(s)", len(products))
        grounded = ground(clean_products(products, record=record), results, record=record)
        return deduplicate(grounded, self.config.num_products, record=record)

    def _invoke(self, chain: Chain[Any], payload: dict[str, Any]) -> Any:
        """Invoke a chain; transport errors become an actionable message (ADR-0009)."""

        server = self.config.model_server
        try:
            return chain.invoke(payload)
        except server.transport_errors as exc:
            raise ModelUnavailableError(server.hint(self.config, exc)) from exc
