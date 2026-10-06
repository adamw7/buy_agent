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

#: ddgs raises "matched nothing" with this message.
_NO_RESULTS = "No results found."
_RETRY_WAIT = 2.0
_TIMEOUT = 10.0
#: Brave refuses a larger ``count`` rather than answering fewer.
_BRAVE_MAX_COUNT = 20
#: Not imported: :mod:`buy_agent.config` imports this module.
_DEFAULT_REGION = "us-en"


class SearchError(RuntimeError):
    """The search backend could not be reached."""


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
    """One question for a backend; splits the region for rows that want a half."""

    text: str
    max_results: int
    region: str = _DEFAULT_REGION

    @property
    def country(self) -> str:
        return self.region.partition("-")[0].upper()

    @property
    def language(self) -> str:
        return self.region.partition("-")[2] or self.region


@dataclass(frozen=True, slots=True)
class Backend:
    """One way of searching the web; ``find`` and ``hint`` are handed the row itself."""

    name: str
    label: str
    #: Empty for a library-backed row.
    endpoint: str
    #: From its env var only: secrets have no flag or form field.
    api_key: str
    needs_key: bool
    find: Callable[[Backend, Query], list[SearchResult]]
    transport_errors: tuple[type[Exception], ...]
    hint: Callable[[Backend, Exception], str]

    @property
    def configured(self) -> bool:
        """Whether the backend has the key it needs."""
        return not self.needs_key or bool(self.api_key)


def _ddg_find(backend: Backend, query: Query) -> list[SearchResult]:
    del backend
    try:
        raw: list[dict[str, Any]] = DDGS().text(
            query.text, max_results=query.max_results, region=query.region
        )
    except DDGSException as exc:
        if str(exc) == _NO_RESULTS:
            logger.info("Search matched nothing for %r", query.text)
            return []
        raise
    return _results(raw, url_key="href", text_key="body", limit=query.max_results)


def _ddg_hint(backend: Backend, exc: Exception) -> str:
    return (
        f"{backend.label} could not be asked ({exc}). It rate-limits heavy use and "
        f"there is no key to raise that with, so the answers are to wait, or to point "
        f"the search backend at one you run yourself or hold a key for."
    )


def _searxng_find(backend: Backend, query: Query) -> list[SearchResult]:
    response = httpx.get(
        f"{backend.endpoint.rstrip('/')}/search",
        params={"q": query.text, "format": "json", "language": query.language, "safesearch": 0},
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    return _results(
        _answer(backend, response).get("results"), url_key="url", text_key="content",
        limit=query.max_results,
    )


def _searxng_hint(backend: Backend, exc: Exception) -> str:
    return _unreachable_hint(
        backend,
        exc,
        "Check the address in $SEARXNG_HOST, and that the instance serves the JSON "
        "format -- a stock configuration answers HTML only",
    )


def _brave_find(backend: Backend, query: Query) -> list[SearchResult]:
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
        web.get("results") if isinstance(web, dict) else None, url_key="url",
        text_key="description", limit=query.max_results,
    )


def _brave_hint(backend: Backend, exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (401, 403):
        return (
            f"{backend.label} refused the key in $BRAVE_API_KEY ({exc}). Check it, or "
            f"search through {DDG.label}, which needs none."
        )
    return _unreachable_hint(backend, exc, "Check the address in $BRAVE_HOST")


def _unreachable_hint(backend: Backend, exc: Exception, remedy: str) -> str:
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


def _results(entries: Any, *, url_key: str, text_key: str, limit: int) -> list[SearchResult]:
    """A backend's hits; only the key names differ per row."""
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
    needs_key=False,
    find=_searxng_find,
    # Outside httpx's root: ``InvalidURL``, and ``UnicodeError`` for ``localhost..``.
    transport_errors=(httpx.HTTPError, httpx.InvalidURL, OSError, UnicodeError),
    hint=_searxng_hint,
)

BRAVE = Backend(
    name="brave",
    label="Brave Search",
    endpoint=os.getenv("BRAVE_HOST", "https://api.search.brave.com/res/v1/web/search"),
    api_key=os.getenv("BRAVE_API_KEY", ""),
    needs_key=True,
    find=_brave_find,
    transport_errors=(httpx.HTTPError, httpx.InvalidURL, OSError, UnicodeError),
    hint=_brave_hint,
)

#: By the name the CLI, the API and ``$BUY_AGENT_BACKEND`` use (ADR-0057).
BACKENDS: dict[str, Backend] = {backend.name: backend for backend in (DDG, SEARXNG, BRAVE)}


def backend_for(name: str) -> Backend:
    try:
        return BACKENDS[name]
    except KeyError:
        raise ValueError(
            f"Unknown search backend {name!r}; expected one of {', '.join(BACKENDS)}."
        ) from None


#: What the form's picker is told about each backend: never its key.
_OFFERED = ("name", "label", "endpoint", "needs_key", "configured")


def backend_options() -> list[dict[str, object]]:
    return [{key: getattr(backend, key) for key in _OFFERED} for backend in BACKENDS.values()]


def search_web(
    query: str,
    *,
    max_results: int = 10,
    region: str = _DEFAULT_REGION,
    wait: Callable[[float], None] | None = None,
    backend: Backend = DDG,
) -> list[SearchResult]:
    """One text search through ``backend``, asked again once on failure (ADR-0053)."""
    logger.info("Searching %s for %r (max %d results)", backend.label, query, max_results)
    asked = Query(text=query, max_results=max_results, region=region)
    attempts_left = 2
    while True:
        try:
            results = backend.find(backend, asked)
        # pylint cannot confirm the row's tuple holds exceptions; it is typed so.
        # pylint: disable-next=catching-non-exception
        except backend.transport_errors as exc:
            attempts_left -= 1
            if wait is None or not attempts_left:
                raise SearchError(
                    f"Web search failed for {query!r}: {backend.hint(backend, exc)}"
                ) from exc
            logger.warning(
                "Web search failed for %r (%s); asking again in %.0fs", query, exc, _RETRY_WAIT
            )
            wait(_RETRY_WAIT)
        else:
            logger.info("Search returned %d results", len(results))
            return results
