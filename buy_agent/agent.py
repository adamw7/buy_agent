"""The agent itself: request -> search query -> web search -> products -> ranked top N."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from time import perf_counter, sleep
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
from buy_agent.logging_setup import log_top_products, seconds_label
from buy_agent.models import and_list, comparable_price, nothing_recorded
from buy_agent.ranking import rank_products
from buy_agent.search import search_web
from buy_agent.verification import ground

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from buy_agent.chat import Chain, ChatModel
    from buy_agent.models import Product, RankedProduct, Recorder
    from buy_agent.ranking import SortBy
    from buy_agent.search import SearchResult

logger = logging.getLogger(__name__)

#: Called with the name of each step about to start (ADR-0034).
Checkpoint: TypeAlias = "Callable[[str], None]"


def every_step_passes(_step: str) -> None:
    """The default checkpoint."""


class ModelUnavailableError(RuntimeError):
    """No server, no model, or no answer (ADR-0028, ADR-0009)."""


def _asks_the_same_question(config: AgentConfig) -> dict[str, object]:
    """Everything besides the prompt that decides the answer (ADR-0044)."""
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
    """The journal of this exact search, keyed on what was *asked* and never on the
    model, which is no new search (ADR-0060). Built before the run."""
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


def _first_line(exc: Exception) -> str:
    """A failure's first line, or its type where it says nothing."""
    said = str(exc).strip()
    return said.splitlines()[0] if said else type(exc).__name__


class _Stopwatch:
    """Where one run's time went: the whole of it, and each step timed, in the order
    the steps ran (ADR-0084)."""

    def __init__(self) -> None:
        self.started = perf_counter()
        self.took: dict[str, float] = {}

    @contextmanager
    def timing(self, step: str) -> Iterator[None]:
        started = perf_counter()
        try:
            yield
        finally:
            # A refinement that failed took its time too, and the run goes on.
            self.took[step] = perf_counter() - started

    def label(self, step: str) -> str:
        return seconds_label(self.took[step])

    def summary(self) -> str:
        """The whole so far, then each step. Not ``__str__``: a handler formats its
        record later, and would read the clock then."""
        steps = ", ".join(f"{step} {self.label(step)}" for step in self.took)
        return f"{seconds_label(perf_counter() - self.started)}: {steps}"


class BuyAgent:
    """Finds, ranks and logs products for a shopper (ADR-0002, ADR-0028)."""

    def __init__(self, config: AgentConfig | None = None, *, llm: ChatModel | None = None) -> None:
        self.config = config or AgentConfig()
        # Here, not in ``providers``: caching is no server's concern.
        self.llm = llm or remember_answers(
            self.config.model_server.chat_model(self.config),
            fingerprint=_asks_the_same_question(self.config),
            ttl=self.config.cache_ttl,
            deterministic=self.config.temperature == 0,
        )
        #: Only a model this agent opened, never one handed in.
        self._opened = None if llm else self.llm
        self.query_chain = build_query_chain(self.llm)
        self.extraction_chain = build_extraction_chain(self.llm)

    def close(self) -> None:
        release(self._opened)

    def run(
        self,
        request: str,
        *,
        sort_by: SortBy = "score",
        checkpoint: Checkpoint = every_step_passes,
        record: Recorder = nothing_recorded,
    ) -> list[RankedProduct]:
        """Search, rank and log the top products; ``record`` receives each candidate
        the run removed (ADR-0034, ADR-0055).

        Raises:
            ValueError: if the request is empty.
            ModelUnavailableError: if the model server or the model is missing.
            SearchError: if the web search backend could not be reached.
        """
        request = request.strip()
        if not request:
            raise ValueError("Nothing to shop for: the request is empty.")

        logger.info("Shopping for: %s", request)
        if self._opened is not None:
            # A model handed in is its caller's to name: the benchmark's scripted one
            # runs under a config naming a model nobody asks.
            config = self.config
            logger.info(
                "Asking %s on %s at %s", config.model, config.model_server.label, config.base_url
            )
        watch = _Stopwatch()
        ranked = self._shop(request, watch, sort_by=sort_by, checkpoint=checkpoint, record=record)
        logger.info("Finished in %s", watch.summary())
        return ranked

    def _shop(
        self,
        request: str,
        watch: _Stopwatch,
        *,
        sort_by: SortBy,
        checkpoint: Checkpoint,
        record: Recorder,
    ) -> list[RankedProduct]:
        """The run itself, each step it waits on timed by ``watch``."""
        query = self._refine_query(request, watch)
        checkpoint("search")
        with watch.timing("search"):
            results = self._search(query)
        if not results:
            logger.warning("Search returned nothing for %r%s", query, self._empty_search_note())
            return []

        if self.config.fetch_pages:
            checkpoint("fetch")
            with watch.timing("fetch"):
                results = enrich(
                    results, max_chars=self.config.page_chars,
                    opinion_chars=self.config.opinion_chars, timeout=self.config.fetch_timeout,
                    cache_ttl=self.config.cache_ttl, wait=sleep,
                )

        checkpoint("extract")
        products = self._extract_products(request, results, watch, record)
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
            products, weights=self.config.weights, sort_by=sort_by,
            currency=self.config.currency or None,
        )
        log_top_products(ranked, self.config.top_n, weights=self.config.weights, sort_by=sort_by)
        return ranked

    def _warn_if_nothing_is_on_the_named_scale(self, products: Sequence[Product]) -> None:
        """Warn when the named currency prices nothing (ADR-0056)."""
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
            "Searching %d named source(s): %s", len(sources),
            ", ".join(source.spec for source in sources),
        )
        share = -(-width // len(sources))  # ceiling: every source gets at least one
        pooled: dict[str, SearchResult] = {}
        for source in sources:
            found = self._ask_the_web(source.site_query(query), share)
            covered: dict[bool, list[SearchResult]] = {True: [], False: []}
            for result in found:
                covered[source.covers(result.url)].append(result)
            kept, outside = covered[True], covered[False]
            if outside:
                # Names at DEBUG: with no fallback to the wider web (ADR-0027), an
                # over-strict ``covers`` needs them to diagnose.
                # A WARNING when nothing was left: that source then reports nothing.
                logger.log(
                    logging.INFO if kept else logging.WARNING,
                    "Ignored %d result(s) from outside %s%s",
                    len(outside),
                    source.domain,
                    "" if kept else ", which leaves nothing from it",
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
        """One search, each address once: a repeat would be fetched twice at once,
        too soon for the page cache to catch it."""
        found = search_web(
            query, max_results=limit, region=self.config.region, wait=sleep,
            backend=self.config.search_backend,
        )
        seen: set[str] = set()
        kept: list[SearchResult] = []
        for result in found:
            # A hit with no address is nobody's repeat.
            if result.url and result.url in seen:
                continue
            seen.add(result.url)
            kept.append(result)
        return kept

    def _empty_search_note(self) -> str:
        """What narrowed this search, for the empty-search warning."""
        return f"{self._region_note() or '.'}{self._sources_note()}"

    def _sources_note(self) -> str:
        sources = self.config.sources
        if not sources:
            return ""
        named = and_list([source.spec for source in sources])
        was = "was" if len(sources) == 1 else "were"
        return (
            f" Only {named} {was} searched, and there is no falling back to the rest "
            f"of the web: a source that does not cover this leaves nothing to report."
        )

    def _region_note(self) -> str:
        region = self.config.region
        if region == DEFAULT_REGION:
            return ""
        return (
            f" in region {region}. A region a search engine does not know returns "
            f"nothing rather than failing -- the codes are a country and then a "
            f"language, like {DEFAULT_REGION} or pl-pl."
        )

    def _refine_query(self, request: str, watch: _Stopwatch) -> str:
        """Ask the LLM for a better search query, falling back to the raw request."""
        try:
            with watch.timing("query"):
                refined = self._invoke(self.query_chain, {"request": request})
        except ModelUnavailableError:
            raise
        # Any failure here is recoverable: the raw request still searches.
        # pylint: disable-next=broad-exception-caught
        except Exception as exc:
            # One line both doors show; the traceback is for -v.
            logger.warning(
                "Query refinement failed (%s); using the raw request", _first_line(exc)
            )
            logger.debug("Why query refinement failed", exc_info=True)
            return request

        query = refined.query.strip()
        if not query:
            return request
        logger.info("Refined search query in %s: %s", watch.label("query"), query)
        return query

    def _extract_products(
        self,
        request: str,
        results: Sequence[SearchResult],
        watch: _Stopwatch,
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
            with watch.timing("extract"):
                extracted = self._invoke(self.extraction_chain, payload)
        except UnreadableAnswerError as exc:
            # Here, not in ``_invoke``: the query step recovers, this one cannot.
            logger.debug("The model's answer could not be read", exc_info=True)
            server = self.config.model_server
            raise ModelUnavailableError(server.hint(self.config, exc)) from exc

        products = [item.to_product() for item in extracted.products]
        logger.info("Extracted %d candidate(s) in %s", len(products), watch.label("extract"))
        grounded = ground(clean_products(products, record=record), results, record=record)
        return deduplicate(grounded, self.config.num_products, record=record)

    def _invoke(self, chain: Chain[Any], payload: dict[str, Any]) -> Any:
        """Invoke a chain; transport errors become the row's hint (ADR-0009)."""
        server = self.config.model_server
        try:
            return chain.invoke(payload)
        except server.transport_errors as exc:
            raise ModelUnavailableError(server.hint(self.config, exc)) from exc
