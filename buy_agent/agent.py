"""The agent itself: request -> search query -> web search -> products -> ranked top N."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, TypeAlias

from buy_agent.cache import remember_answers
from buy_agent.chat import UnreadableAnswerError
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

#: Called at each of a run's step boundaries with the name of the step about to
#: start. Raising from it ends the run there, which is how a caller whose client
#: has gone stops one (ADR-0034).
Checkpoint: TypeAlias = "Callable[[str], None]"


def every_step_passes(_step: str) -> None:
    """The default checkpoint: nobody is watching, so every boundary passes."""


class ModelUnavailableError(RuntimeError):
    """Raised when the model could not be used: no server, no model, or no answer.

    One exception for both providers: to the shopper it is one thing, the model
    could not be used. Only the sentence differs -- ``ollama pull`` or ``vllm
    serve`` -- which is the provider's own to write (ADR-0028).

    A server that answered with something other than the JSON it was asked for is
    the third of those and not a fourth failure mode (ADR-0009): nothing about the
    request was wrong, and the remedy is the model's -- more room, or a smaller
    prompt.
    """


def _asks_the_same_question(config: AgentConfig) -> dict[str, object]:
    """Everything besides the prompt that decides what a model answers (ADR-0044).

    The prompt and the schema go into the key where it is built; this is the rest.
    Two settings are deliberately absent. ``api_key`` is a secret and the key is
    written to a file. ``temperature`` is a constant here -- only a run at zero is
    remembered at all -- and a constant in a key is noise. ``num_ctx`` goes in
    only where the provider sends it: vLLM fixes its window at startup, so
    including it would miss on a setting that server never saw.
    """
    fingerprint: dict[str, object] = {
        "provider": config.provider,
        "model": config.model,
        "base_url": config.base_url,
        "reasoning": config.reasoning,
    }
    if config.model_server.takes_num_ctx:
        fingerprint["num_ctx"] = config.num_ctx
    return fingerprint


class BuyAgent:
    """Finds products for a shopper, ranks them, and logs the best few.

    The control flow is fixed rather than left to the model: the LLM refines the
    query and reads products out of the results, while searching, ranking and
    reporting are ordinary code -- which keeps the agent usable with the small
    local models these servers are typically run with. Which server is answering,
    ``config.provider`` says and nothing here asks (ADR-0028).
    """

    def __init__(
        self, config: AgentConfig | None = None, *, llm: ChatModel | None = None
    ) -> None:
        """Build an agent.

        Args:
            config: Model, search and ranking settings; the defaults are sensible.
            llm: Chat model to use instead of the provider's own -- the seam the
                tests inject a fake model through. Given one, nothing is wrapped
                around it: a stand-in answers whatever it was told to, and a
                remembered answer over the top of that would be this module
                deciding what a test meant.
        """
        self.config = config or AgentConfig()
        # The remembering goes here rather than in ``providers``: it has nothing
        # to do with which server is answering, so neither row declares it.
        self.llm = llm or remember_answers(
            self.config.model_server.chat_model(self.config),
            fingerprint=_asks_the_same_question(self.config),
            ttl=self.config.cache_ttl,
            deterministic=self.config.temperature == 0,
        )
        self.query_chain = build_query_chain(self.llm)
        self.extraction_chain = build_extraction_chain(self.llm)

    def run(
        self,
        request: str,
        *,
        sort_by: SortBy = "score",
        checkpoint: Checkpoint = every_step_passes,
    ) -> list[RankedProduct]:
        """Search for what the shopper asked for and log the top products.

        Three failures come out of here and no more (ADR-0009). What
        ``checkpoint`` raises comes out too, but that is the caller's own exception
        travelling back rather than a fourth thing this pipeline fails with, which
        is why it is not in ``Raises`` below.

        Args:
            request: What the user wants to buy, in their own words.
            sort_by: ``"score"`` (default), ``"price"`` or ``"rating"``.
            checkpoint: Called with the name of each step as it is about to start
                -- ``"search"``, ``"fetch"``, ``"extract"``, ``"rank"`` -- so a
                caller can end a run it no longer wants. Nothing here catches what
                it raises, which is how it ends one (ADR-0034). A step boundary is
                as fine as it gets: a model call already in flight finishes first,
                nothing being able to cancel one.

        Returns:
            Every product found that is inside the bounds the config carries,
            best first -- not only the ones logged.

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
            logger.warning("Search returned nothing for %r%s", query, self._region_note())
            return []

        if self.config.fetch_pages:
            checkpoint("fetch")
            results = enrich(
                results,
                max_chars=self.config.page_chars,
                opinion_chars=self.config.opinion_chars,
                timeout=self.config.fetch_timeout,
                cache_ttl=self.config.cache_ttl,
            )

        checkpoint("extract")
        products = self._extract_products(request, results)
        if not products:
            logger.warning("No products could be extracted from the search results.")
            return []

        # After the merging rather than before it: ``deduplicate`` fills a
        # listing's gaps from another listing of the same product, so a product
        # whose price is only known once the two are merged would be judged here
        # on a blank and kept for the wrong reason (ADR-0039).
        products = Constraints.from_config(self.config).apply(products)
        if not products:
            return []

        # Before ranking rather than only before the slow steps: ranking is cheap,
        # but it ends in ``log_top_products``, and a report is worth not writing
        # for a run nobody is reading any more.
        checkpoint("rank")
        ranked = rank_products(products, weights=self.config.weights, sort_by=sort_by)
        log_top_products(ranked, self.config.top_n)
        return ranked

    def _search(self, query: str) -> list[SearchResult]:
        """Search the web, or only the sources the shopper named.

        Named none, this is one search. Named some, it is one search per source --
        ``site:`` narrows to a single domain -- pooled in the order given
        (ADR-0027). Two things about the pooling are load-bearing: every result
        goes through :meth:`~buy_agent.sources.Source.covers` first, so a backend
        ignoring the operator cannot smuggle in a page from elsewhere, and a page
        found twice is kept once rather than crowding out another.

        The width is shared out rather than multiplied -- five sources at ten
        results each would fetch fifty pages for a report of three.
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

    def _region_note(self) -> str:
        """The region, when it is one worth suspecting of an empty search.

        A region is checked for shape at both front doors, but the shapes outnumber
        the codes: ``en-us`` is the right shape the wrong way round, and a search
        engine given it answers with nothing rather than complaining (ADR-0031).
        The default is left unnamed on purpose -- it is the one value known to work,
        and pointing at it would send someone to correct a correct setting.
        """
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
        payload = {
            "request": request,
            "results": format_results(results),
            "limit": self.config.num_products,
        }
        try:
            extracted = self._invoke(self.extraction_chain, payload)
        except UnreadableAnswerError as exc:
            # Caught here rather than in ``_invoke``, which the recoverable step
            # goes through too: a query the model fumbles falls back to the raw
            # request, while an extraction that comes back unreadable has nothing
            # to fall back to. It is a ``ValueError`` -- which the API answers 400
            # to and the CLI reports as the run's own fault -- so it is turned
            # into the failure it actually is, carrying what to do about it.
            logger.debug("The model's answer could not be read", exc_info=True)
            server = self.config.model_server
            raise ModelUnavailableError(server.hint(self.config, exc)) from exc

        products = [item.to_product() for item in extracted.products]
        logger.info("Extracted %d candidate(s)", len(products))
        grounded = ground(clean_products(products), results)
        return deduplicate(grounded, self.config.num_products)

    def _invoke(self, chain: Chain[Any], payload: dict[str, Any]) -> Any:
        """Invoke a chain, turning transport errors into an actionable message.

        Which errors those are, and what the message says, is the provider's to
        answer: a stopped Ollama and a stopped vLLM raise different classes and are
        restarted with different commands. Both arrive as one
        ``ModelUnavailableError`` -- above this line they are one failure (ADR-0009).
        """
        server = self.config.model_server
        try:
            return chain.invoke(payload)
        except server.transport_errors as exc:
            raise ModelUnavailableError(server.hint(self.config, exc)) from exc
