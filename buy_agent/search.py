"""Web search over DuckDuckGo (no API key needed)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ddgs import DDGS
from ddgs.exceptions import DDGSException
from pydantic import BaseModel

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

#: What ddgs says when every engine answered and none had anything: a query that
#: matched nothing arrives as an exception like any other. Calling that a
#: :class:`SearchError` would report "the backend could not be reached" for a
#: search that worked, and the message is the only discriminator ddgs offers.
#: Pinned to ``ddgs==9.15.0``, so a rewording shows up as the old 502.
_NO_RESULTS = "No results found."

#: How long to wait before asking a second time, where the caller handed something
#: to wait with. ``ddgs`` asks several engines and raises only when every one of
#: them failed, which is what a rate limit looks like from here -- the one failure
#: that is about this minute rather than about this query (ADR-0053). Two seconds
#: because the alternative is a run that ends, and a shopper who runs it again by
#: hand has waited longer than that.
_RETRY_WAIT = 2.0


class SearchError(RuntimeError):
    """Raised when the search backend could not be reached."""


class SearchResult(BaseModel):
    """One raw web result, before the LLM makes sense of it.

    ``content`` is the condensed page text, filled in by :mod:`buy_agent.fetch`;
    it stays empty when fetching is turned off or the page could not be read.
    """

    title: str = ""
    url: str = ""
    snippet: str = ""
    content: str = ""

    def as_prompt_block(self) -> str:
        block = f"TITLE: {self.title}\nURL: {self.url}\nSNIPPET: {self.snippet}"
        if self.content:
            block += f"\nPAGE:\n{self.content}"
        return block


def search_web(
    query: str,
    *,
    max_results: int = 10,
    region: str = "us-en",
    wait: Callable[[float], None] | None = None,
) -> list[SearchResult]:
    """Run a DuckDuckGo text search and return the results.

    A search that reached the backend and matched nothing returns ``[]`` -- an
    answer and not a failure, however ddgs spells it (:data:`_NO_RESULTS`).

    Given a ``wait``, a failed search is asked once more after
    :data:`_RETRY_WAIT` (ADR-0053): every engine failing at once is the transient
    case, and one search is the whole of a run's input -- there is no partial
    answer to carry on with, the way a lost page leaves nine. ``None``, the
    default, asks once. A search that matched nothing is never asked again: it
    worked, and it would match nothing twice.

    Raises:
        SearchError: if DuckDuckGo is unreachable or rate-limits the request.
    """
    logger.info("Searching the web for %r (max %d results)", query, max_results)
    attempts_left = 2
    while True:
        try:
            raw: list[dict[str, Any]] = DDGS().text(
                query, max_results=max_results, region=region
            )
        except DDGSException as exc:  # rate limits and backend failures both land here
            if str(exc) == _NO_RESULTS:
                logger.info("Search matched nothing for %r", query)
                return []
            attempts_left -= 1
            if wait is None or not attempts_left:
                raise SearchError(f"Web search failed for {query!r}: {exc}") from exc
            # WARNING rather than INFO: this is the failure the run would have
            # ended on, and the line is what says a run that took two seconds
            # longer was one that nearly did not happen.
            logger.warning(
                "Web search failed for %r (%s); asking again in %.0fs",
                query,
                exc,
                _RETRY_WAIT,
            )
            wait(_RETRY_WAIT)
        else:
            return _read_results(raw)


def _read_results(raw: list[dict[str, Any]]) -> list[SearchResult]:
    """What ddgs answered, as the results the rest of the run passes around."""
    results = [
        SearchResult(
            title=item.get("title", ""),
            url=item.get("href", ""),
            snippet=item.get("body", ""),
        )
        for item in raw
    ]
    logger.info("Search returned %d results", len(results))
    return results
