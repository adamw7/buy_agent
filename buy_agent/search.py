"""Which search backend the agent asks: one row per backend, and nothing else (ADR-0021,
ADR-0053, ADR-0057)."""

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

#: What ddgs says when every engine answered and none had anything: a query that matched
#: nothing arrives as an exception like any other.
_NO_RESULTS = "No results found."

#: How long to wait before asking a second time, where the caller handed something to
#: wait with (ADR-0053).
_RETRY_WAIT = 2.0

#: How long to wait on a backend that is asked over HTTP. Shorter than a model call by
#: an order of magnitude: a search engine that has not answered in ten seconds is one
#: that is not going to.
_TIMEOUT = 10.0

#: Where the search looks when a caller names nothing. Written here rather than read off
#: :mod:`buy_agent.config`, which is the module that reads *this* one.
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
    """One question for a backend: what to look for, how much of it, and where.

    The region arrives as this project spells it -- a country and then a language
    (ADR-0031) -- and each backend wants its own half of that, so the splitting is done
    once here rather than on every row.
    """

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

    ``find`` and ``hint`` are handed the row itself, the way a provider's are handed the
    config they were resolved from: there is no config here to hand over -- a search
    takes a query and answers results -- and the address and the key a row was built
    with are the two things its own functions need.
    """

    name: str
    label: str
    #: Where it listens, from its own environment variable. Empty for a backend that is
    #: reached through a library rather than an address of its own.
    endpoint: str
    #: Its key, read off its own environment variable and nowhere else: a secret has no
    #: flag and no form field, exactly as ``$VLLM_API_KEY`` has neither.
    api_key: str
    needs_key: bool
    find: Callable[[Backend, Query], list[SearchResult]]
    transport_errors: tuple[type[BaseException], ...]
    hint: Callable[[Backend, Exception], str]

    @property
    def configured(self) -> bool:
        """Whether this backend has what it needs to be asked at all.

        The only thing a row can be missing is its key -- every address here has a
        default -- so this is that question, and it is what the form's picker marks a
        backend with rather than working the rule out in TypeScript.
        """
        return not self.needs_key or bool(self.api_key)


def _ddg_find(backend: Backend, query: Query) -> list[SearchResult]:
    """DuckDuckGo through ``ddgs``, which fronts several engines and raises only when
    every one of them failed."""
    del backend
    try:
        raw: list[dict[str, Any]] = DDGS().text(
            query.text, max_results=query.max_results, region=query.region
        )
    except DDGSException as exc:
        # The one backend that answers "nothing matched" by raising. Read here rather
        # than above the rows: what an empty answer looks like is the backend's own.
        if str(exc) == _NO_RESULTS:
            logger.info("Search matched nothing for %r", query.text)
            return []
        raise
    return _results(raw, url_key="href", text_key="body", limit=query.max_results)


def _ddg_hint(backend: Backend, exc: Exception) -> str:
    """DuckDuckGo's one failure worth a remedy: it is rate-limiting this machine."""
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
    """Nothing answered, so the instance itself is what is missing."""
    return _unreachable_hint(
        backend,
        exc,
        "Check the address in $SEARXNG_HOST, and that the instance serves the JSON "
        "format -- a stock configuration answers HTML only",
    )


def _brave_find(backend: Backend, query: Query) -> list[SearchResult]:
    """Brave's search API, on a key read off the environment (ADR-0057)."""
    if not backend.api_key:
        # Not a transport failure, so it is not asked twice: a second identical request
        # with no key is a second 401.
        raise SearchError(
            f"{backend.label} needs a key and $BRAVE_API_KEY is not set. It is read "
            f"off the environment and has no flag and no form field, the way every "
            f"other key here is -- or search through {DDG.label}, which needs none."
        )
    response = httpx.get(
        backend.endpoint,
        params={
            "q": query.text,
            "count": query.max_results,
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
    """The sentence both addressed backends write, said once above the rows."""
    return (
        f"Could not reach {backend.label} at {backend.endpoint} ({exc}). {remedy}. "
        f"Or search through {DDG.label}, which needs no server of your own."
    )


def _answer(backend: Backend, response: httpx.Response) -> dict[str, Any]:
    """What a backend sent back, as the object it was asked for.

    Readable and still not an answer is the same failure as unreadable, and neither is
    worth a second identical request: a backend answering HTML where JSON was asked for
    is one that is configured wrongly, not one that is busy.
    """
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
    """One backend's list of hits, as the results the rest of the run passes around.

    Each backend names the same three things differently and nothing else about them
    differs, so the two keys are the row's and the shaping is shared.
    """
    rows = entries if isinstance(entries, list) else []
    return [
        SearchResult(
            title=str(entry.get("title", "")),
            url=str(entry.get(url_key, "")),
            snippet=str(entry.get(text_key, "")),
        )
        for entry in rows[:limit]
        if isinstance(entry, dict)
    ]


DDG = Backend(
    name="ddg",
    label="DuckDuckGo",
    # Reached through ``ddgs`` rather than at an address, so there is none to name.
    endpoint="",
    api_key="",
    needs_key=False,
    find=_ddg_find,
    # ``DDGSException`` is the root of that library's hierarchy, and a rate limit and a
    # backend failure both land there.
    transport_errors=(DDGSException, OSError),
    hint=_ddg_hint,
)

SEARXNG = Backend(
    name="searxng",
    label="SearXNG",
    endpoint=os.getenv("SEARXNG_HOST", "http://localhost:8080"),
    api_key="",
    # No key and no account, which is the whole reason it is here: the model already
    # runs on the shopper's own machine (ADR-0003, ADR-0057).
    needs_key=False,
    find=_searxng_find,
    # ``HTTPError`` is httpx's root: a refused connection, a timeout, and the statuses
    # ``raise_for_status`` turns into one.
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

#: Every search backend, by the name the CLI, the API and ``$BUY_AGENT_BACKEND`` use
#: (ADR-0057).
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
    """Every backend a run can be pointed at, as the form's picker needs it."""
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
        # The row declares its failures as ``type[BaseException]``, which is what the
        # other two tables declare theirs as and what an ``except`` clause takes; read
        # off that annotation alone, pylint sees a class that need not be an
        # ``Exception``. Those two are reached through a property it cannot infer and
        # this one through a default it can, which is the whole of the difference.
        # pylint: disable-next=catching-non-exception
        except backend.transport_errors as exc:  # rate limits and outages both land here
            attempts_left -= 1
            if wait is None or not attempts_left:
                raise SearchError(
                    f"Web search failed for {query!r}: {backend.hint(backend, exc)}"
                ) from exc
            # WARNING rather than INFO: this is the failure the run would have ended on,
            # and the line is what says a run that took two seconds longer was one that
            # nearly did not happen.
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
