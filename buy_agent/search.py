"""Web search, exposed as a LangChain tool over DuckDuckGo (no API key needed)."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from ddgs import DDGS
from ddgs.exceptions import DDGSException
from langchain_core.tools import tool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


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

    Raises:
        SearchError: if DuckDuckGo is unreachable or rate-limits the request.
    """
    logger.info("Searching the web for %r (max %d results)", query, max_results)
    try:
        raw: list[dict[str, Any]] = DDGS().text(
            query, max_results=max_results, region=region
        )
    except DDGSException as exc:  # rate limits and backend failures both land here
        msg = f"Web search failed for {query!r}: {exc}"
        raise SearchError(msg) from exc

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


@tool("search_products", parse_docstring=True)
def search_products_tool(
    query: Annotated[str, Field(description="A shopping-oriented web search query.")],
    max_results: int = 10,
) -> str:
    """Search the web for products matching a query.

    Args:
        query: A shopping-oriented web search query.
        max_results: How many results to return.

    Returns:
        The search results as newline-separated title/url/snippet blocks.
    """
    results = search_web(query, max_results=max_results)
    return "\n\n".join(result.as_prompt_block() for result in results)
