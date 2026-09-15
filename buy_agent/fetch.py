"""Fetch the pages behind the search results and keep the parts worth reading."""

from __future__ import annotations

import logging
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextvars import Context, copy_context
from functools import partial
from typing import TYPE_CHECKING, NamedTuple

import httpx
from lxml import html as lxml_html

from buy_agent.cache import PAGES, DiskCache, open_cache
from buy_agent.money import SIGNS, WORDS

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from buy_agent.search import SearchResult

logger = logging.getLogger(__name__)

#: A browser-ish agent; many shops answer python-httpx with a 403.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

#: Which spellings make a line worth keeping is :mod:`buy_agent.money`'s to say, not
#: this module's: a currency it can place and this one cannot see is every price on a
#: shop dropped before the model ever sees it, which is what ``--region pl-pl`` was
#: until ``zł`` was added to one table and not the other (ADR-0043, ADR-0054).
_CURRENCY_WORDS = "|".join(WORDS)

_PRICE = re.compile(
    r"[" + re.escape(SIGNS) + r"]\s?\d"
    r"|\b(?:" + _CURRENCY_WORDS + r")\b\s*\d"
    r"|\d\s*(?:" + _CURRENCY_WORDS + r")\b",
    re.IGNORECASE,
)
#: A hyphen counts where a space does: "a 4.5-star average" is how a roundup writes what
#: a shop writes "4.5 stars", and keeping one form only let a page's punctuation decide
#: whether its rating reached the model.
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
_SEGMENT_BREAK = re.compile(r"[\n\r]+")
_WHITESPACE = re.compile(r"\s+")

#: The XML declaration an XHTML page opens with.
_XML_DECLARATION = re.compile(r"^\s*<\?xml[^>]*\?>")

#: A bare "$129" line is short but is what shop pages contain, so the floor only
#: excludes stray characters; the ceiling excludes walls of boilerplate.
_MIN_SEGMENT = 4
_MAX_SEGMENT = 300

#: An opinion is a sentence, not a figure, so it gets a floor of its own -- which is
#: what keeps a bare "Pros" heading, whose content is the lines below it, out of the
#: prompt.
_MIN_OPINION = 25

#: How much of one page is read before the rest is dropped.
_MAX_PAGE_BYTES = 4 * 1024 * 1024

#: The two statuses that mean "ask again later" rather than "no" (ADR-0053).
_RETRY_STATUSES = frozenset({429, 503})

#: How long to wait before asking again where the answer did not say.
_RETRY_WAIT = 1.0

#: The longest a ``Retry-After`` is honoured.
_MAX_RETRY_WAIT = 5.0


#: How a page's failure is named in the tally :func:`enrich` logs.
_TIMED_OUT = "timed out"
_LOOPED = "redirected in a loop"
_UNFETCHABLE = "had an address that cannot be fetched"
_UNREACHABLE = "could not be reached"
_TRANSFER_FAILED = "failed mid-transfer"
_NOT_HTML = "did not answer with HTML"
_NOTHING_KEPT = "quoted no prices and no verdicts"

#: Which phrase each kind of transport failure gets, in the order asked -- see
#: :func:`describe_failure`.
_FAILURE_PHRASES: tuple[tuple[tuple[type[Exception], ...], str], ...] = (
    ((httpx.TimeoutException,), _TIMED_OUT),
    ((httpx.TooManyRedirects,), _LOOPED),
    ((httpx.InvalidURL, httpx.UnsupportedProtocol), _UNFETCHABLE),
    ((httpx.NetworkError, httpx.ProxyError), _UNREACHABLE),
)

#: What a status code means, where the number alone would not say it.
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
    """Strip a page down to its visible text."""
    try:
        document = lxml_html.fromstring(_XML_DECLARATION.sub("", markup, count=1))
    except (ValueError, lxml_html.etree.ParserError):
        return ""
    for element in document.xpath("//script|//style|//noscript|//svg"):
        element.drop_tree()
    # itertext, not text_content: one line per element, so a price stays separable from
    # the product name sitting in the element above it.
    return "\n".join(document.itertext())


def condense(text: str, *, max_chars: int, opinion_chars: int = 400) -> str:
    """Keep the lines that quote a figure or pass judgement, and nothing else."""
    segments = [_WHITESPACE.sub(" ", raw).strip() for raw in _SEGMENT_BREAK.split(text)]
    segments = [segment for segment in segments if segment]

    taken: set[int] = set()
    seen: set[str] = set()

    def sweep(matches: Callable[[str], bool], *, floor: int, budget: int) -> None:
        """Take every line ``matches`` accepts, until this sweep's budget runs out."""
        spent = 0

        def take(index: int) -> bool:
            """Add a segment; False once the budget is spent."""
            nonlocal spent
            segment = segments[index]
            # Already taken by the other sweep: kept, and paid for over there.
            if segment in seen or len(segment) > _MAX_SEGMENT:
                return True
            if spent + len(segment) > budget:
                return False
            seen.add(segment)
            taken.add(index)
            spent += len(segment) + 1
            return True

        for index, segment in enumerate(segments):
            if not (floor <= len(segment) <= _MAX_SEGMENT) or not matches(segment):
                continue
            # The matching line first, and a match that will not fit still ends the
            # sweep: the prompt is the page read top down, so skipping an expensive
            # listing for a cheap one below reorders its argument.
            if not take(index):
                break
            # Then the line above it -- usually the product this is about, shop pages
            # putting the price under the name.
            if index:
                take(index - 1)

    sweep(quotes_a_figure, floor=_MIN_SEGMENT, budget=max_chars)
    sweep(reads_like_an_opinion, floor=_MIN_OPINION, budget=opinion_chars)
    return "\n".join(segments[index] for index in sorted(taken))


def quotes_a_figure(segment: str) -> bool:
    """Whether a line names a price or a rating -- what the ranking is made of."""
    return bool(_PRICE.search(segment) or _RATING.search(segment))


def reads_like_an_opinion(segment: str) -> bool:
    """Whether a line reports a judgement about a product rather than a fact."""
    return bool(_OPINION.search(segment))


def fetch_page(
    client: httpx.Client,
    url: str,
    *,
    max_chars: int,
    opinion_chars: int = 400,
    cache: DiskCache | None = None,
    wait: Callable[[float], None] | None = None,
) -> PageText:
    """Read one URL -- off the cache or off the web -- and condense it (ADR-0040,
    ADR-0053).
    """
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
    """One page's visible text, or the phrase saying why there is none (ADR-0040, ADR-0009,
    ADR-0053).
    """
    try:
        fetched = _markup(client, url)
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        fetched = _asked_again(client, url, exc, wait)
    if fetched.problem:
        return fetched

    text = html_to_text(fetched.text)
    if not text:
        # Markup nothing could parse, named rather than left as empty text: it keeps
        # ``PageText``'s rule that text is empty exactly when a problem says why, and
        # nothing empty reaches the cache.
        logger.debug("Nothing could be read out of %s", url)
        return PageText("", _NOTHING_KEPT)
    return PageText(text)


def _markup(client: httpx.Client, url: str) -> PageText:
    """One request: the page's markup, or the phrase saying it was not HTML."""
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
    # Asked in this order and in one condition, so there is no third state to cover: no
    # clock to wait by, or nothing worth waiting for, and the delay is only read where
    # the first of those passed.
    if wait is None or (delay := _come_back_in(exc)) is None:
        # The URL and the exception stay at DEBUG: one line per result is ten lines of
        # narration, and the tally carries the shape of the trouble.
        logger.debug("Could not fetch %s: %s", url, exc)
        return PageText("", describe_failure(exc))

    # INFO, unlike the line above it, and the one per-page line here that is: this one
    # is time the shopper is spending rather than a page they are not getting, and a run
    # that took ten seconds longer should say which pages asked for them.
    logger.info("%s asked to be tried again; waiting %.1fs", url, delay)
    wait(delay)
    try:
        return _markup(client, url)
    except (httpx.HTTPError, httpx.InvalidURL) as again:
        logger.debug("Could not fetch %s after waiting: %s", url, again)
        return PageText("", describe_failure(again))


def _come_back_in(exc: Exception) -> float | None:
    """How long this failure says to wait before asking again, or None for "do not"
    (ADR-0053).
    """
    if not isinstance(exc, httpx.HTTPStatusError):
        return None
    if exc.response.status_code not in _RETRY_STATUSES:
        return None
    try:
        asked = float(exc.response.headers.get("retry-after", ""))
    except ValueError:
        return _RETRY_WAIT
    return min(max(asked, 0.0), _MAX_RETRY_WAIT)


def _read_capped(response: httpx.Response, url: str) -> str:
    """The page's markup, up to :data:`_MAX_PAGE_BYTES` of it."""
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
    return markup.decode(response.encoding or "utf-8", errors="replace")


def describe_failure(exc: Exception) -> str:
    """Why one page could not be read, in the words the tally counts."""
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        word = _STATUS_WORDS.get(code) or ("failed" if code >= 500 else "rejected")
        return f"{word} ({code})"
    # In order: a ConnectTimeout is not a ConnectError, but the two read as one thing
    # and are counted as one, so the timeout is asked first.
    for kinds, phrase in _FAILURE_PHRASES:
        if isinstance(exc, kinds):
            return phrase
    return _TRANSFER_FAILED


def summarise_failures(problems: Iterable[str]) -> str:
    """The kinds of failure and how many of each, commonest first."""
    return ", ".join(
        f"{count} {problem}" for problem, count in Counter(problems).most_common()
    )


def _as_the_caller(context: Context) -> None:
    """Start a pool worker in the context its caller is running in.

    This is the one step that fans out into threads, and a thread starts in a
    context of its own: what a worker logged reached no stream, so the one line a
    rate-limited page writes at INFO -- the time the shopper is spending -- was
    missing from the browser's progress panel and from nowhere else (ADR-0011).
    Read rather than entered, the same context being handed to every worker.
    """
    for variable, value in context.items():
        variable.set(value)


def enrich(
    results: Sequence[SearchResult],
    *,
    max_chars: int = 1200,
    opinion_chars: int = 400,
    timeout: float = 8.0,
    workers: int = 8,
    cache_ttl: float = 0.0,
    wait: Callable[[float], None] | None = None,
) -> list[SearchResult]:
    """Attach condensed page content to each result, in parallel (ADR-0040, ADR-0053).
    """
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
            fetch_page,
            client,
            max_chars=max_chars,
            opinion_chars=opinion_chars,
            cache=cache,
            wait=wait,
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
        # Nothing read at all is what the shopper most needs told: every figure ahead is
        # about to be blanked by grounding.
        logging.WARNING if urls and not with_content else logging.INFO,
        "Got usable page text from %d of %d result(s)%s%s",
        with_content,
        len(urls),
        f", {cached} from cache" if cached else "",
        f": {failures}" if failures else "",
    )
    return enriched
