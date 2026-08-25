"""The agent itself: request -> search query -> web search -> products -> ranked top N."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx
from langchain_ollama import ChatOllama
from ollama import RequestError, ResponseError

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


class OllamaUnavailableError(RuntimeError):
    """Raised when the Ollama server is unreachable or the model is not pulled."""


class BuyAgent:
    """Finds products for a shopper, ranks them, and logs the best few.

    The control flow is fixed rather than left to the model: the LLM refines the
    query and reads products out of the results, while searching, ranking and
    reporting are ordinary code. That keeps the agent usable with the small local
    models Ollama is typically run with, which are unreliable at driving a tool loop.
    """

    def __init__(
        self, config: AgentConfig | None = None, *, llm: BaseChatModel | None = None
    ) -> None:
        """Build an agent.

        Args:
            config: Model, search and ranking settings. The defaults are sensible.
            llm: Chat model to use instead of building a ChatOllama from ``config`` --
                the seam the tests inject a fake model through.
        """
        self.config = config or AgentConfig()
        self.llm = llm or ChatOllama(
            model=self.config.model,
            base_url=self.config.base_url,
            temperature=self.config.temperature,
            num_ctx=self.config.num_ctx,
            reasoning=self.config.reasoning,
        )
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
            OllamaUnavailableError: if the Ollama server or the model is missing.
            SearchError: if the web search backend could not be reached.
        """
        request = request.strip()
        if not request:
            msg = "Nothing to shop for: the request is empty."
            raise ValueError(msg)

        logger.info("Shopping for: %s", request)
        query = self._refine_query(request)
        results = search_web(
            query, max_results=self.config.search_results, region=self.config.region
        )
        if not results:
            logger.warning("Search returned nothing for %r", query)
            return []

        if self.config.fetch_pages:
            results = enrich(
                results,
                max_chars=self.config.page_chars,
                timeout=self.config.fetch_timeout,
            )

        products = self._extract_products(request, results)
        if not products:
            logger.warning("No products could be extracted from the search results.")
            return []

        ranked = rank_products(products, weights=self.config.weights, sort_by=sort_by)
        log_top_products(ranked, self.config.top_n)
        return ranked

    def _refine_query(self, request: str) -> str:
        """Ask the LLM for a better search query, falling back to the raw request."""
        try:
            refined = self._invoke(self.query_chain, {"request": request})
        except OllamaUnavailableError:
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
        """Invoke a chain, turning Ollama transport errors into an actionable message.

        ``httpx.HTTPError`` is in the list because it is what actually reaches us
        when Ollama is not running. The ollama client only converts a refused
        connection into a builtin ``ConnectionError`` on its *non*-streaming path,
        and ``ChatOllama`` always chats over the streaming one -- so a stopped
        server, a model too slow to answer and a killed stream all arrive here as
        raw ``httpx`` errors, none of which is an ``OSError``. Note that the
        ``RequestError`` above is ollama's own, a different class from httpx's.
        """
        try:
            return chain.invoke(payload)
        except (
            ResponseError,
            RequestError,
            ConnectionError,
            OSError,
            httpx.HTTPError,
        ) as exc:
            raise OllamaUnavailableError(self._ollama_hint(exc)) from exc

    def _ollama_hint(self, exc: Exception) -> str:
        """Turn an Ollama failure into something the user can act on."""
        detail = str(exc)
        if isinstance(exc, httpx.TimeoutException):
            return (
                f"Ollama at {self.config.base_url} did not answer in time "
                f"({detail or type(exc).__name__}). "
                f"The model {self.config.model!r} may be too slow for this prompt -- "
                "try a smaller model, or a smaller --num-ctx."
            )
        if "not found" in detail.lower():
            return (
                f"Ollama has no model named {self.config.model!r}. "
                f"Pull it with:  ollama pull {self.config.model}  "
                f"(installed: {self._installed_models()})"
            )
        return (
            f"Could not reach Ollama at {self.config.base_url} ({detail}). "
            "Start it with:  ollama serve"
        )

    def _installed_models(self) -> str:
        try:
            from ollama import Client

            models = [model.model for model in Client(self.config.base_url).list().models]
        except Exception:
            return "unknown"
        return ", ".join(models) or "none"
