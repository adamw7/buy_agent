"""Web search over DuckDuckGo (no API key needed)."""

from __future__ import annotations

import logging
from typing import Any

from ddgs import DDGS
from ddgs.exceptions import DDGSException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

#: What ddgs says when every engine answered and none had anything. Its search
#: path ends in ``raise DDGSException(err or "No results found.")``, so a query
#: that matched nothing arrives as an exception like any other -- and calling that
#: a :class:`SearchError` would report "the backend could not be reached" (a 502
#: in the browser) for a search that worked. Every other ``DDGSException`` carries
#: the failing engine's own text, so the message is the discriminator ddgs itself
#: uses. Pinned to ``ddgs==9.15.0``: a later rewording shows up as the old 502
#: rather than a wrong answer, and ``tests/test_search.py`` says what to look at.
_NO_RESULTS = "No results found."


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


def search_web(query: str, *, max_results: int = 10, region: str = "us-en") -> list[SearchResult]:
    """Run a DuckDuckGo text search and return the results.

    A search that reached the backend and matched nothing returns ``[]`` -- an
    answer and not a failure, however ddgs spells it (:data:`_NO_RESULTS`).

    Raises:
        SearchError: if DuckDuckGo is unreachable or rate-limits the request.
    """
    logger.info("Searching the web for %r (max %d results)", query, max_results)
    try:
        raw: list[dict[str, Any]] = DDGS().text(
            query, max_results=max_results, region=region
        )
    except DDGSException as exc:  # rate limits and backend failures both land here
        if str(exc) == _NO_RESULTS:
            logger.info("Search matched nothing for %r", query)
            return []
        raise SearchError(f"Web search failed for {query!r}: {exc}") from exc

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
