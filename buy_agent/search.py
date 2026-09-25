"""The search backends, one row each (ADR-0021, ADR-0053, ADR-0057)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx
from ddgs import DDGS
from ddgs.exceptions import DDGSException
from pydantic import BaseModel

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

#: ddgs's message for "matched nothing", which it raises.
_NO_RESULTS = "No results found."

#: The wait before a retry (ADR-0053).
_RETRY_WAIT = 2.0

#: Timeout for an HTTP backend.
_TIMEOUT = 10.0

#: Brave's maximum ``count``; it refuses a larger one rather than answering fewer.
_BRAVE_MAX_COUNT = 20

#: The default region (not imported: :mod:`buy_agent.config` imports this module).
_DEFAULT_REGION = "us-en"


class SearchError(RuntimeError):
    """Raised when the search backend could not be reached."""


class SearchResult(BaseModel):
    """One raw web result, before the LLM makes sense of it."""

    title: str = ""
    url: str = ""
    snippet: str = ""
    content: str = ""

    def as_prompt_block(self) -> str:
        block = f"TITLE: {self.title}\nURL: {self.url}\nSNIPPET: {self.snippet}"
        if self.content:
            block += f"\nPAGE:\n{self.content}"
        return block


@dataclass(frozen=True, slots=True)
class Query:
    """One question for a backend; splits the region for rows that want a half
    (ADR-0031)."""

    text: str
    max_results: int
    region: str = _DEFAULT_REGION

    @property
    def country(self) -> str:
        """The region's country half, as a search API asks for it."""
        return self.region.partition("-")[0].upper()

    @property
    def language(self) -> str:
        """The region's language half."""
        return self.region.partition("-")[2] or self.region


@dataclass(frozen=True, slots=True)
class Backend:
    """One way of searching the web: where it is, and how it is asked.

    ``find`` and ``hint`` receive the row itself, which carries the address and key.
    """

    name: str
    label: str
    #: Where it listens (its own env var); empty for a library-backed row.
    endpoint: str
    #: Its key, from its env var only: secrets have no flag or form field.
    api_key: str
    needs_key: bool
    find: Callable[[Backend, Query], list[SearchResult]]
    #: What "the backend is not there" raises through this row's client; typed
    #: ``Exception`` so ``hint`` can accept what the ``except`` binds.
    transport_errors: tuple[type[Exception], ...]
    hint: Callable[[Backend, Exception], str]

    @property
    def configured(self) -> bool:
        """Whether the backend has the key it needs; the form's picker shows this."""
        return not self.needs_key or bool(self.api_key)


def _ddg_find(backend: Backend, query: Query) -> list[SearchResult]:
    """DuckDuckGo through ``ddgs``, which raises only when every engine failed."""
    del backend
    try:
        raw: list[dict[str, Any]] = DDGS().text(
            query.text, max_results=query.max_results, region=query.region
        )
    except DDGSException as exc:
        # The one backend that raises for "nothing matched".
        if str(exc) == _NO_RESULTS:
            logger.info("Search matched nothing for %r", query.text)
            return []
        raise
    return _results(raw, url_key="href", text_key="body", limit=query.max_results)


def _ddg_hint(backend: Backend, exc: Exception) -> str:
    """DuckDuckGo's usual failure: rate-limiting."""
    return (
        f"{backend.label} could not be asked ({exc}). It rate-limits heavy use and "
        f"there is no key to raise that with, so the answers are to wait, or to point "
        f"the search backend at one you run yourself or hold a key for."
    )


def _searxng_find(backend: Backend, query: Query) -> list[SearchResult]:
    """A SearXNG of the shopper's own, asked for JSON (ADR-0057)."""
    response = httpx.get(
        f"{backend.endpoint.rstrip('/')}/search",
        params={
            "q": query.text,
            "format": "json",
            "language": query.language,
            "safesearch": 0,
        },
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    return _results(
        _answer(backend, response).get("results"),
        url_key="url",
        text_key="content",
        limit=query.max_results,
    )


def _searxng_hint(backend: Backend, exc: Exception) -> str:
    """Nothing answered: the instance is what is missing."""
    return _unreachable_hint(
        backend,
        exc,
        "Check the address in $SEARXNG_HOST, and that the instance serves the JSON "
        "format -- a stock configuration answers HTML only",
    )


def _brave_find(backend: Backend, query: Query) -> list[SearchResult]:
    """Brave's search API, on a key read off the environment (ADR-0057)."""
    if not backend.api_key:
        # Not a transport failure, so never retried.
        raise SearchError(
            f"{backend.label} needs a key and $BRAVE_API_KEY is not set. It is read "
            f"off the environment and has no flag and no form field, the way every "
            f"other key here is -- or search through {DDG.label}, which needs none."
        )
    response = httpx.get(
        backend.endpoint,
        params={
            "q": query.text,
            "count": min(query.max_results, _BRAVE_MAX_COUNT),
            "country": query.country,
            "search_lang": query.language,
        },
        headers={"Accept": "application/json", "X-Subscription-Token": backend.api_key},
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    web = _answer(backend, response).get("web")
    return _results(
        web.get("results") if isinstance(web, dict) else None,
        url_key="url",
        text_key="description",
        limit=query.max_results,
    )


def _brave_hint(backend: Backend, exc: Exception) -> str:
    """A key that was refused, or nothing answering at all."""
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (401, 403):
        return (
            f"{backend.label} refused the key in $BRAVE_API_KEY ({exc}). Check it, or "
            f"search through {DDG.label}, which needs none."
        )
    return _unreachable_hint(backend, exc, "Check the address in $BRAVE_HOST")


def _unreachable_hint(backend: Backend, exc: Exception, remedy: str) -> str:
    """The unreachable-address sentence both HTTP backends share."""
    return (
        f"Could not reach {backend.label} at {backend.endpoint} ({exc}). {remedy}. "
        f"Or search through {DDG.label}, which needs no server of your own."
    )


def _answer(backend: Backend, response: httpx.Response) -> dict[str, Any]:
    """A backend's answer as a JSON object, or a (non-retried) :class:`SearchError`."""
    try:
        payload = response.json()
    except ValueError as exc:
        raise SearchError(
            f"{backend.label} at {backend.endpoint} answered with something that is "
            f"not JSON ({exc})."
        ) from exc
    if not isinstance(payload, dict):
        raise SearchError(
            f"{backend.label} at {backend.endpoint} answered with "
            f"{type(payload).__name__}, not an object."
        )
    return payload


def _results(
    entries: Any, *, url_key: str, text_key: str, limit: int
) -> list[SearchResult]:
    """A backend's hits as :class:`SearchResult`; only the key names differ per row."""
    rows = entries if isinstance(entries, list) else []
    return [
        SearchResult(
            # ``or ""``: a JSON ``null`` would otherwise become "None".
            title=str(entry.get("title") or ""),
            url=str(entry.get(url_key) or ""),
            snippet=str(entry.get(text_key) or ""),
        )
        for entry in rows[:limit]
        if isinstance(entry, dict)
    ]


DDG = Backend(
    name="ddg",
    label="DuckDuckGo",
    # Reached through ``ddgs``, not an address.
    endpoint="",
    api_key="",
    needs_key=False,
    find=_ddg_find,
    # The library's root: rate limits and outages alike.
    transport_errors=(DDGSException, OSError),
    hint=_ddg_hint,
)

SEARXNG = Backend(
    name="searxng",
    label="SearXNG",
    endpoint=os.getenv("SEARXNG_HOST", "http://localhost:8080"),
    api_key="",
    # No key and no account, like the local model (ADR-0003, ADR-0057).
    needs_key=False,
    find=_searxng_find,
    # httpx's root: refusals, timeouts and ``raise_for_status``.
    transport_errors=(httpx.HTTPError, OSError),
    hint=_searxng_hint,
)

BRAVE = Backend(
    name="brave",
    label="Brave Search",
    endpoint=os.getenv("BRAVE_HOST", "https://api.search.brave.com/res/v1/web/search"),
    api_key=os.getenv("BRAVE_API_KEY", ""),
    needs_key=True,
    find=_brave_find,
    transport_errors=(httpx.HTTPError, OSError),
    hint=_brave_hint,
)

#: Every backend, by the name the CLI, the API and ``$BUY_AGENT_BACKEND`` use (ADR-0057).
BACKENDS: dict[str, Backend] = {
    backend.name: backend for backend in (DDG, SEARXNG, BRAVE)
}


def backend_for(name: str) -> Backend:
    """The search backend called ``name``."""
    try:
        return BACKENDS[name]
    except KeyError:
        raise ValueError(
            f"Unknown search backend {name!r}; expected one of {', '.join(BACKENDS)}."
        ) from None


def backend_options() -> list[dict[str, object]]:
    """Every backend, as the form's picker needs it."""
    return [
        {
            "name": backend.name,
            "label": backend.label,
            "endpoint": backend.endpoint,
            "needs_key": backend.needs_key,
            "configured": backend.configured,
        }
        for backend in BACKENDS.values()
    ]


def search_web(
    query: str,
    *,
    max_results: int = 10,
    region: str = _DEFAULT_REGION,
    wait: Callable[[float], None] | None = None,
    backend: Backend = DDG,
) -> list[SearchResult]:
    """Run one text search through ``backend`` and return the results (ADR-0053)."""
    logger.info(
        "Searching %s for %r (max %d results)", backend.label, query, max_results
    )
    asked = Query(text=query, max_results=max_results, region=region)
    attempts_left = 2
    while True:
        try:
            results = backend.find(backend, asked)
        # pylint infers this tuple through the default row and cannot confirm its
        # members are exceptions; they are typed ``type[Exception]``.
        # pylint: disable-next=catching-non-exception
        except backend.transport_errors as exc:  # rate limits and outages both land here
            attempts_left -= 1
            if wait is None or not attempts_left:
                raise SearchError(
                    f"Web search failed for {query!r}: {backend.hint(backend, exc)}"
                ) from exc
            # WARNING: this is the failure the run nearly ended on.

            logger.warning(
                "Web search failed for %r (%s); asking again in %.0fs",
                query,
                exc,
                _RETRY_WAIT,
            )
            wait(_RETRY_WAIT)
        else:
            logger.info("Search returned %d results", len(results))
            return results
