"""Fetch the pages behind the search results and keep the parts worth reading."""

from __future__ import annotations

import logging
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextvars import Context, copy_context
from functools import partial
from math import isnan
from typing import TYPE_CHECKING, NamedTuple

import httpx
from lxml import html as lxml_html

from buy_agent.cache import PAGES, DiskCache, open_cache
from buy_agent.models import STANDING_PHRASES
from buy_agent.money import SCANNED_CODES, SIGNS, WORDS
from buy_agent.structured import declared

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from buy_agent.search import SearchResult

logger = logging.getLogger(__name__)

#: Many shops answer python-httpx with a 403.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

#: Every placeable currency (ADR-0054); words case-folded, codes not ("Try" is no lira).
_CURRENCY = "(?i:" + "|".join(WORDS) + ")|" + "|".join(SCANNED_CODES)

#: A currency on either side of the figure: "€129" and "129,99 €" (ADR-0054).
_PRICE = re.compile(
    r"[" + re.escape(SIGNS) + r"]\s?\d"
    r"|\d\s?[" + re.escape(SIGNS) + r"]"
    r"|\b(?:" + _CURRENCY + r")\b\s*\d"
    r"|\d\s*(?:" + _CURRENCY + r")\b",
)
#: A hyphen counts as a space: "4.5-star" and "4.5 stars".
_RATING = re.compile(
    r"\d(?:\.\d)?(?:\s*(?:/\s*(?:5|10)\b|out of\s*(?:5|10)\b)|[\s-]*stars?\b)"
    r"|\b(?:rated|rating)\b[^\d]{0,12}\d",
    re.IGNORECASE,
)

#: Lines that judge a product rather than price it.
_OPINION = re.compile(
    r"""
      # Plural only: a singular "pro" is half the products on the page
      # ("AirPods Pro"), while "pros and cons" is nobody's model name.
      \b(?:pros|cons|downsides?|drawbacks?|upsides?|complaints?|verdict)\b
    | \b(?:we|i|reviewers?|testers?|owners?|users?|buyers?|critics?)\s+
      (?:\w+\s+){0,2}?
      (?:like[ds]?|love[ds]?|hate[ds]?|found|felt|prefer(?:red)?|praise[ds]?
        |complain(?:ed|ing)?|noticed|report(?:ed)?|recommend(?:ed)?|wish(?:ed)?)\b
    | \b(?:in\s+(?:our|my)\s+tests?|hands[-\s]on|bottom\s+line|tested\s+by)\b
    | \b(?:comfortable|uncomfortable|impressive|disappointing|excellent|superb
        |mediocre|flimsy|sturdy|durable|underwhelming|outstanding|punchy|muddy
        |boomy|tinny|harsh|roomy|cramped)\b
    | \bbest\s+(?:for|value|overall)\b
    | \bworth\s+(?:it|the)\b
    | \bvalue\s+for\s+money\b
    | \b(?:highly\s+)?recommend(?:ed|s)?\b
    """,
    re.IGNORECASE | re.VERBOSE,
)
#: Lines saying whether a listing is in stock or what state it comes in (ADR-0079).
_STANDING = re.compile("|".join(STANDING_PHRASES.values()), re.IGNORECASE)

#: Where a page declares its products to search engines (ADR-0078).
_DECLARATIONS = "//script[contains(translate(@type, 'LDJSON', 'ldjson'), 'ld+json')]"

_SEGMENT_BREAK = re.compile(r"[\n\r]+")
_WHITESPACE = re.compile(r"\s+")

_XML_DECLARATION = re.compile(r"^\s*<\?xml[^>]*\?>")

#: The floor lets a bare "$129" line in; the ceiling keeps walls of boilerplate out.
_MIN_SEGMENT = 4
_MAX_SEGMENT = 300
#: Keeps a bare "Pros" heading out.
_MIN_OPINION = 25

_PAGE_BUDGET = 1200
_OPINION_BUDGET = 400
_MAX_PAGE_BYTES = 4 * 1024 * 1024

#: "Ask again later" rather than "no" (ADR-0053); ``Retry-After`` is capped.
_RETRY_STATUSES = frozenset({429, 503})
_RETRY_WAIT = 1.0
_MAX_RETRY_WAIT = 5.0

#: How a page's failure is named in the tally :func:`enrich` logs.
_TIMED_OUT = "timed out"
_LOOPED = "redirected in a loop"
_UNFETCHABLE = "had an address that cannot be fetched"
_UNREACHABLE = "could not be reached"
_TRANSFER_FAILED = "failed mid-transfer"
_NOT_HTML = "did not answer with HTML"
_NOTHING_KEPT = "quoted no prices and no verdicts"

#: Two are outside httpx's root: ``InvalidURL``, and the ``UnicodeError`` the socket
#: raises for a host it cannot IDNA-encode (``shop..example``).
_CANNOT_FETCH = (httpx.HTTPError, httpx.InvalidURL, UnicodeError)

#: Asked in order, so a ConnectTimeout counts as a timeout.
_FAILURE_PHRASES: tuple[tuple[tuple[type[Exception], ...], str], ...] = (
    ((httpx.TimeoutException,), _TIMED_OUT),
    ((httpx.TooManyRedirects,), _LOOPED),
    ((httpx.InvalidURL, httpx.UnsupportedProtocol, UnicodeError), _UNFETCHABLE),
    ((httpx.NetworkError, httpx.ProxyError), _UNREACHABLE),
)

_STATUS_WORDS = {
    401: "refused",
    403: "refused",
    404: "not found",
    410: "gone",
    429: "rate-limited",
}


class PageText(NamedTuple):
    """What one fetch yielded, and -- when it yielded nothing -- why not (ADR-0040)."""

    text: str
    problem: str | None = None
    cached: bool = False


def html_to_text(markup: str) -> str:
    """A page's visible text, one line per element, after a line per offer and rating
    its JSON-LD declares (ADR-0078): first, so the budget reaches them first."""
    try:
        document = lxml_html.fromstring(_XML_DECLARATION.sub("", markup, count=1))
    except (ValueError, lxml_html.etree.ParserError):
        return ""
    stated = declared(script.text or "" for script in document.xpath(_DECLARATIONS))
    for element in document.xpath("//script|//style|//noscript|//svg"):
        element.drop_tree()
    return "\n".join([*stated, *document.itertext()])


def condense(text: str, *, max_chars: int, opinion_chars: int = _OPINION_BUDGET) -> str:
    """Keep the lines that quote a figure or pass judgement, and nothing else."""
    segments = [_WHITESPACE.sub(" ", raw).strip() for raw in _SEGMENT_BREAK.split(text)]
    segments = [segment for segment in segments if segment]

    taken: set[int] = set()
    # Keyed with the line above: two products at one price are not a repeat.
    seen: set[tuple[str, str]] = set()

    def sweep(matches: Callable[[str], bool], *, floor: int, budget: int) -> None:
        """Take every line ``matches`` accepts, until this sweep's budget runs out."""
        spent = 0

        def take(index: int) -> bool:
            """Add a segment; False once the budget is spent."""
            nonlocal spent
            segment = segments[index]
            if index in taken or len(segment) > _MAX_SEGMENT:
                return True
            if spent + len(segment) > budget:
                return False
            taken.add(index)
            spent += len(segment) + 1
            return True

        for index, segment in enumerate(segments):
            if not (floor <= len(segment) <= _MAX_SEGMENT) or not matches(segment):
                continue
            entry = (segments[index - 1] if index else "", segment)
            if entry in seen:
                continue
            # A match that does not fit ends the sweep: the page is kept in order.
            if not take(index):
                break
            seen.add(entry)
            # Then the line above, usually the product's name.
            if index:
                take(index - 1)

    sweep(quotes_a_figure, floor=_MIN_SEGMENT, budget=max_chars)
    sweep(reads_like_an_opinion, floor=_MIN_OPINION, budget=opinion_chars)
    return "\n".join(segments[index] for index in sorted(taken))


def quotes_a_figure(segment: str) -> bool:
    """Whether a line names a price or a rating, or says how a listing stands."""
    return bool(_PRICE.search(segment) or _RATING.search(segment) or _STANDING.search(segment))


def reads_like_an_opinion(segment: str) -> bool:
    return bool(_OPINION.search(segment))


def fetch_page(
    client: httpx.Client,
    url: str,
    *,
    max_chars: int,
    opinion_chars: int = _OPINION_BUDGET,
    cache: DiskCache | None = None,
    wait: Callable[[float], None] | None = None,
) -> PageText:
    """One URL, off the cache or the web, condensed (ADR-0040, ADR-0053)."""
    text = cache.get(url) if cache else None
    cached = text is not None
    if text is None:
        page = read_page(client, url, wait=wait)
        if page.problem:
            return page
        text = page.text
        if cache:
            cache.put(url, text)

    kept = condense(text, max_chars=max_chars, opinion_chars=opinion_chars)
    if not kept:
        logger.debug("Nothing worth keeping on %s", url)
        return PageText("", _NOTHING_KEPT, cached)
    return PageText(kept, None, cached)


def read_page(
    client: httpx.Client, url: str, *, wait: Callable[[float], None] | None = None
) -> PageText:
    """One page's visible text, or the phrase saying why there is none."""
    try:
        fetched = _markup(client, url)
    except _CANNOT_FETCH as exc:
        fetched = _asked_again(client, url, exc, wait)
    if fetched.problem:
        return fetched

    text = html_to_text(fetched.text)
    if not text:
        # Named, so empty text never reaches the cache.
        logger.debug("Nothing could be read out of %s", url)
        return PageText("", _NOTHING_KEPT)
    return PageText(text)


def _markup(client: httpx.Client, url: str) -> PageText:
    with client.stream("GET", url) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "html")
        if "html" not in content_type:
            logger.debug("Skipped %s: served as %r", url, content_type)
            return PageText("", _NOT_HTML)
        return PageText(_read_capped(response, url))


def _asked_again(
    client: httpx.Client,
    url: str,
    exc: Exception,
    wait: Callable[[float], None] | None,
) -> PageText:
    """The page on a second attempt, where this failure was worth one."""
    if wait is None or (delay := _come_back_in(exc)) is None:
        # DEBUG: the tally at INFO summarises.
        logger.debug("Could not fetch %s: %s", url, exc)
        return PageText("", describe_failure(exc))

    logger.info("%s asked to be tried again; waiting %.1fs", url, delay)
    wait(delay)
    try:
        return _markup(client, url)
    except _CANNOT_FETCH as again:
        logger.debug("Could not fetch %s after waiting: %s", url, again)
        return PageText("", describe_failure(again))


def _come_back_in(exc: Exception) -> float | None:
    """How long to wait before a retry, or None for none (ADR-0053)."""
    if not isinstance(exc, httpx.HTTPStatusError):
        return None
    if exc.response.status_code not in _RETRY_STATUSES:
        return None
    try:
        asked = float(exc.response.headers.get("retry-after", ""))
    except ValueError:
        return _RETRY_WAIT
    # A NaN would pass both bounds and crash ``time.sleep``.
    if isnan(asked):
        return _RETRY_WAIT
    return min(max(asked, 0.0), _MAX_RETRY_WAIT)


def _read_capped(response: httpx.Response, url: str) -> str:
    chunks: list[bytes] = []
    read = 0
    for chunk in response.iter_bytes():
        chunks.append(chunk)
        read += len(chunk)
        if read >= _MAX_PAGE_BYTES:
            logger.debug("Read the first %d bytes of %s and stopped", read, url)
            break
    markup = b"".join(chunks)
    chunks.clear()
    encoding = response.encoding or "utf-8"
    try:
        return markup.decode(encoding, errors="replace")
    # ``charset=base64`` is no text encoding (``LookupError``) and ``charset=idna`` will
    # not replace a byte (``UnicodeError``); read those as UTF-8, as httpx reads unknowns.
    except (LookupError, UnicodeError):
        logger.debug("%s declared %r, which reads no text; read it as UTF-8", url, encoding)
        return markup.decode("utf-8", errors="replace")


def describe_failure(exc: Exception) -> str:
    """Why one page could not be read, in the tally's words."""
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        word = _STATUS_WORDS.get(code) or ("failed" if code >= 500 else "rejected")
        return f"{word} ({code})"
    for kinds, phrase in _FAILURE_PHRASES:
        if isinstance(exc, kinds):
            return phrase
    return _TRANSFER_FAILED


def summarise_failures(problems: Iterable[str]) -> str:
    """The kinds of failure and how many of each, commonest first."""
    return ", ".join(f"{count} {problem}" for problem, count in Counter(problems).most_common())


def _as_the_caller(context: Context) -> None:
    """Start a pool worker in its caller's context, so its log lines are relayed."""
    for variable, value in context.items():
        variable.set(value)


def enrich(
    results: Sequence[SearchResult],
    *,
    max_chars: int = _PAGE_BUDGET,
    opinion_chars: int = _OPINION_BUDGET,
    timeout: float = 8.0,
    workers: int = 8,
    cache_ttl: float = 0.0,
    wait: Callable[[float], None] | None = None,
) -> list[SearchResult]:
    """Attach condensed page content to each result, in parallel (ADR-0040, ADR-0053)."""
    urls = [result.url for result in results]
    logger.info("Fetching %d result page(s)", len(urls))
    cache = open_cache(PAGES, cache_ttl)

    with httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
    ) as client, ThreadPoolExecutor(
        max_workers=workers, initializer=_as_the_caller, initargs=(copy_context(),)
    ) as pool:
        read = partial(
            fetch_page, client, max_chars=max_chars, opinion_chars=opinion_chars,
            cache=cache, wait=wait,
        )
        pages = list(pool.map(read, urls))

    enriched = [
        result.model_copy(update={"content": page.text})
        for result, page in zip(results, pages, strict=True)
    ]
    with_content = sum(1 for page in pages if page.text)
    cached = sum(1 for page in pages if page.cached)
    failures = summarise_failures(page.problem for page in pages if page.problem)
    logger.log(
        # Nothing read means grounding will blank every figure.
        logging.WARNING if urls and not with_content else logging.INFO,
        "Got usable page text from %d of %d result(s)%s%s",
        with_content,
        len(urls),
        f", {cached} from cache" if cached else "",
        f": {failures}" if failures else "",
    )
    return enriched
