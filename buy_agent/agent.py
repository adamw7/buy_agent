"""The agent itself: request -> search query -> web search -> products -> ranked top N."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from buy_agent.config import AgentConfig
from buy_agent.extraction import (
    build_extraction_chain,
    build_query_chain,
    clean_products,
    deduplicate,
    format_results,
)
from buy_agent.fetch import enrich
from buy_agent.logging_setup import log_top_products
from buy_agent.providers import build_chat_model, transport_errors, unavailable_hint
from buy_agent.ranking import rank_products
from buy_agent.search import search_web
from buy_agent.verification import ground

if TYPE_CHECKING:
    from collections.abc import Sequence

    from langchain_core.language_models import BaseChatModel
    from langchain_core.runnables import Runnable

    from buy_agent.models import Product, RankedProduct
    from buy_agent.ranking import SortBy
    from buy_agent.search import SearchResult

logger = logging.getLogger(__name__)


class ModelUnavailableError(RuntimeError):
    """Raised when the model server is unreachable, or is not serving the model.

    One exception for both providers, because it is one thing to the shopper:
    the model could not be used. What differs is only the sentence it carries --
    ``ollama pull`` or ``vllm serve`` -- which is
    :func:`buy_agent.providers.unavailable_hint`'s to write (ADR-0028).
    """


class BuyAgent:
    """Finds products for a shopper, ranks them, and logs the best few.

    The control flow is fixed rather than left to the model: the LLM refines the
    query and reads products out of the results, while searching, ranking and
    reporting are ordinary code. That keeps the agent usable with the small local
    models these servers are typically run with, which are unreliable at driving
    a tool loop.

    Which server that is -- Ollama, or a vLLM behind its OpenAI-compatible API --
    is ``config.provider``'s to say, and nothing in this class asks: everything
    that differs between them is in :mod:`buy_agent.providers` (ADR-0028).
    """

    def __init__(
        self, config: AgentConfig | None = None, *, llm: BaseChatModel | None = None
    ) -> None:
        """Build an agent.

        Args:
            config: Model, search and ranking settings. The defaults are sensible.
            llm: Chat model to use instead of building the provider's own from
                ``config`` -- the seam the tests inject a fake model through.
        """
        self.config = config or AgentConfig()
        self.llm = llm or build_chat_model(self.config)
        self.query_chain = build_query_chain(self.llm)
        self.extraction_chain = build_extraction_chain(self.llm)

    def run(self, request: str, *, sort_by: SortBy = "score") -> list[RankedProduct]:
        """Search for what the shopper asked for and log the top products.

        Args:
            request: What the user wants to buy, in their own words.
            sort_by: ``"score"`` (default), ``"price"`` or ``"rating"``.

        Returns:
            Every product found, best first -- not only the ones logged.

        Raises:
            ValueError: if the request is empty.
            ModelUnavailableError: if the model server or the model is missing.
            SearchError: if the web search backend could not be reached.
        """
        request = request.strip()
        if not request:
            msg = "Nothing to shop for: the request is empty."
            raise ValueError(msg)

        logger.info("Shopping for: %s", request)
        query = self._refine_query(request)
        results = self._search(query)
        if not results:
            logger.warning("Search returned nothing for %r", query)
            return []

        if self.config.fetch_pages:
            results = enrich(
                results,
                max_chars=self.config.page_chars,
                opinion_chars=self.config.opinion_chars,
                timeout=self.config.fetch_timeout,
            )

        products = self._extract_products(request, results)
        if not products:
            logger.warning("No products could be extracted from the search results.")
            return []

        ranked = rank_products(products, weights=self.config.weights, sort_by=sort_by)
        log_top_products(ranked, self.config.top_n)
        return ranked

    def _search(self, query: str) -> list[SearchResult]:
        """Search the web, or only the sources the shopper named.

        Named none, this is one search and nothing else. Named some, it is one
        search per source -- ``site:`` narrows a query to a single domain, so
        several domains take several searches -- pooled back into one list in
        the order the sources were given.

        Two things are load-bearing about the pooling. Every result is put
        through :meth:`~buy_agent.sources.Source.covers` before it is kept, so a
        backend that ignored the operator cannot smuggle a page from elsewhere
        into a run that asked for these sites only; and the same page found
        under two sources is kept once, since it is one page and the second copy
        would only crowd out a different one.

        The width is shared out rather than multiplied: five sources at ten
        results each would fetch fifty pages for a report of three. Each source
        is asked for its share of ``search_results``, rounded up so the last one
        is not left with nothing, and the pool is cut back to the width the run
        was configured for.
        """
        sources = self.config.sources
        width = self.config.search_results
        if not sources:
            return search_web(query, max_results=width, region=self.config.region)

        logger.info(
            "Searching %d named source(s): %s",
            len(sources),
            ", ".join(source.spec for source in sources),
        )
        share = -(-width // len(sources))  # ceiling: every source gets at least one
        pooled: dict[str, SearchResult] = {}
        for source in sources:
            found = search_web(
                source.site_query(query), max_results=share, region=self.config.region
            )
            kept = [result for result in found if source.covers(result.url)]
            if len(kept) != len(found):
                logger.info(
                    "Ignored %d result(s) from outside %s", len(found) - len(kept), source.domain
                )
            for result in kept:
                pooled.setdefault(result.url, result)
        return list(pooled.values())[:width]

    def _refine_query(self, request: str) -> str:
        """Ask the LLM for a better search query, falling back to the raw request."""
        try:
            refined = self._invoke(self.query_chain, {"request": request})
        except ModelUnavailableError:
            raise
        except Exception:
            # A bad query is recoverable -- searching the raw request still works.
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
        extracted = self._invoke(
            self.extraction_chain,
            {
                "request": request,
                "results": format_results(results),
                "limit": self.config.num_products,
            },
        )
        products = [item.to_product() for item in extracted.products]
        logger.info("Extracted %d candidate(s)", len(products))
        grounded = ground(clean_products(products), results)
        return deduplicate(grounded, self.config.num_products)

    def _invoke(self, chain: Runnable, payload: dict[str, Any]) -> Any:
        """Invoke a chain, turning transport errors into an actionable message.

        Which errors those are, and what the message says, is the provider's to
        answer: a stopped Ollama and a stopped vLLM raise different classes and
        are restarted with different commands. Both arrive here as the same
        ``ModelUnavailableError``, because to everything above this line they are
        one failure -- the model could not be used (ADR-0009).
        """
        try:
            return chain.invoke(payload)
        except transport_errors(self.config) as exc:
            raise ModelUnavailableError(unavailable_hint(self.config, exc)) from exc
