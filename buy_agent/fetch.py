"""Fetch the pages behind the search results and keep the parts worth reading.

DuckDuckGo snippets almost never quote a per-product price -- a search for
headphones under $200 returns ten snippets whose only number is the "$200" from
the query -- so extracting from them alone yields products with no comparable
data, and a model asked to fill that gap invents figures.

So each page is fetched and condensed to two kinds of line: the ones carrying a
price or a rating, and the ones carrying an *opinion*, a shopper's question rarely
being only "how much". Together they keep the prompt small enough for a small
local model while giving it something real to read.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import TYPE_CHECKING, NamedTuple

import httpx
from lxml import html as lxml_html

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from buy_agent.search import SearchResult

logger = logging.getLogger(__name__)

#: A browser-ish agent; many shops answer python-httpx with a 403.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

_CURRENCY_SIGNS = "$€£¥"  # dollar, euro, pound, yen
_CURRENCY_CODES = r"USD|EUR|GBP|PLN|CHF|SEK|CAD|AUD"

_PRICE = re.compile(
    r"[" + re.escape(_CURRENCY_SIGNS) + r"]\s?\d"
    r"|\b(?:" + _CURRENCY_CODES + r")\b\s*\d"
    r"|\d\s*(?:" + _CURRENCY_CODES + r")\b",
    re.IGNORECASE,
)
_RATING = re.compile(
    r"\d(?:\.\d)?\s*(?:/\s*(?:5|10)\b|out of\s*(?:5|10)\b|stars?\b)"
    r"|\b(?:rated|rating)\b[^\d]{0,12}\d",
    re.IGNORECASE,
)

#: Lines that judge a product rather than price it. Deliberately a vocabulary of
#: *judgement* -- who is speaking ("reviewers found"), what they concluded ("the
#: downside is"), the words only an opinion uses ("disappointing") -- and never of
#: subject matter, which every line on a headphone page shares.
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

#: A bare "$129" line is short but is what shop pages contain, so the floor only
#: excludes stray characters; the ceiling excludes walls of boilerplate.
_MIN_SEGMENT = 4
_MAX_SEGMENT = 300

#: An opinion is a sentence, not a figure, so it gets a floor of its own -- which
#: is what keeps a bare "Pros" heading, whose content is the lines below it, out
#: of the prompt.
_MIN_OPINION = 25

#: How much of one page is read before the rest is dropped on the floor. A
#: ceiling is needed because neither of the other two bounds is one: ``timeout``
#: is the wait between chunks rather than for the transfer, so a large, steady
#: response never trips it, and ``condense`` runs on text that is already in
#: memory -- with eight of these in flight at once. Far past any page worth
#: reading: ``page_chars`` keeps 1200 characters of what arrives.
_MAX_PAGE_BYTES = 4 * 1024 * 1024


#: How a page's failure is named in the tally :func:`enrich` logs. Past tense
#: throughout and never a copula, so one phrase reads the same after "1" as after
#: "7" -- "1 were not HTML" would cost a plural rule to fix what wording fixes free.
_TIMED_OUT = "timed out"
_LOOPED = "redirected in a loop"
_UNFETCHABLE = "had an address that cannot be fetched"
_UNREACHABLE = "could not be reached"
_TRANSFER_FAILED = "failed mid-transfer"
_NOT_HTML = "did not answer with HTML"
_NOTHING_KEPT = "quoted no prices and no verdicts"

#: Which phrase each kind of transport failure gets, in the order asked -- see
#: :func:`describe_failure`. A status code is answered before any of them.
_FAILURE_PHRASES: tuple[tuple[tuple[type[Exception], ...], str], ...] = (
    ((httpx.TimeoutException,), _TIMED_OUT),
    ((httpx.TooManyRedirects,), _LOOPED),
    ((httpx.InvalidURL, httpx.UnsupportedProtocol), _UNFETCHABLE),
    ((httpx.NetworkError, httpx.ProxyError), _UNREACHABLE),
)

#: What a status code means, where the number alone would not say it. Anything
#: else is "rejected" under 500 and "failed" at or above, the code quoted beside
#: the word either way -- so a 402 is still legible as itself.
_STATUS_WORDS = {
    401: "refused",
    403: "refused",
    404: "not found",
    410: "gone",
    429: "rate-limited",
}


class PageText(NamedTuple):
    """What one fetch yielded, and -- when it yielded nothing -- why not.

    ``problem`` is a phrase rather than the exception, because :func:`enrich`
    *counts* it: ten pages answering 403 are worth one line ("10 refused (403)")
    and not ten, the exception each carried being the DEBUG line beside it. Set
    exactly when ``text`` is empty, so a page read and a page not are never both "".
    """

    text: str
    problem: str | None = None


def html_to_text(markup: str) -> str:
    """Strip a page down to its visible text."""
    try:
        document = lxml_html.fromstring(markup)
    except (ValueError, lxml_html.etree.ParserError):
        return ""
    for element in document.xpath("//script|//style|//noscript|//svg"):
        element.drop_tree()
    # itertext, not text_content: one line per element, so a price stays separable
    # from the product name sitting in the element above it.
    return "\n".join(document.itertext())


def condense(text: str, *, max_chars: int, opinion_chars: int = 400) -> str:
    """Keep the lines that quote a figure or pass judgement, and nothing else.

    A product page is mostly navigation and legal text; what matters is the handful
    of lines naming a price or a rating and the handful saying what the thing is
    like to own. The two kinds are swept for in turn, each on a budget of its own
    (``max_chars`` and ``opinion_chars``), so neither crowds the other out;
    ``opinion_chars=0`` leaves the opinions unread. Lines come back in the page's
    own order whichever sweep took them, so the prompt reads as an excerpt rather
    than as two lists.
    """
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
            # The line above is usually the product this is about: shop pages put
            # the price under the name, review pages the verdict under a heading.
            if index and not take(index - 1):
                break
            if not take(index):
                break

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
    client: httpx.Client, url: str, *, max_chars: int, opinion_chars: int = 400
) -> PageText:
    """Fetch one URL and condense it, saying why when nothing comes back.

    Every way of yielding nothing is named rather than collapsed into "": a shop
    that answered 403, a proxy that swallowed the connection and a page that
    genuinely quoted no figures are three diagnoses, and without them the run
    reports the same "0 of 10" for all three -- which reads as a bad model rather
    than as nothing having been read.

    ``InvalidURL`` sits beside ``HTTPError`` because it is not one: httpx raises it
    out of parsing rather than the transport, so it inherits from ``Exception``
    directly and the obvious ``except httpx.HTTPError`` misses it -- letting a bad
    port or an unbracketed IPv6 literal escape the pool and make ``BuyAgent.run``
    raise a fourth thing (ADR-0009).

    Streamed rather than fetched whole, so the body is bounded by
    :data:`_MAX_PAGE_BYTES` and the content type is read before any of it: a shop
    answering with a video keeps its headers and none of its gigabytes.
    """
    try:
        with client.stream("GET", url) as response:
            response.raise_for_status()

            content_type = response.headers.get("content-type", "html")
            if "html" not in content_type:
                logger.debug("Skipped %s: served as %r", url, content_type)
                return PageText("", _NOT_HTML)

            markup = _read_capped(response, url)
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        # The URL and the exception stay at DEBUG: one line per result is ten lines
        # of narration, and the tally already carries the shape of the trouble.
        logger.debug("Could not fetch %s: %s", url, exc)
        return PageText("", describe_failure(exc))

    text = condense(html_to_text(markup), max_chars=max_chars, opinion_chars=opinion_chars)
    if not text:
        logger.debug("Nothing worth keeping on %s", url)
        return PageText("", _NOTHING_KEPT)
    return PageText(text)


def _read_capped(response: httpx.Response, url: str) -> str:
    """The page's markup, up to :data:`_MAX_PAGE_BYTES` of it.

    Truncated rather than refused: a page cut mid-tag still parses -- ``lxml``
    takes broken markup, which is most of the web -- and the lines above the cut
    are the ones a shop puts its prices on. The decoding is httpx's own
    ``encoding``, read off the header a streamed response has already delivered.
    """
    chunks: list[bytes] = []
    read = 0
    for chunk in response.iter_bytes():
        chunks.append(chunk)
        read += len(chunk)
        if read >= _MAX_PAGE_BYTES:
            logger.debug("Read the first %d bytes of %s and stopped", read, url)
            break
    return b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")


def describe_failure(exc: Exception) -> str:
    """Why one page could not be read, in the words the tally counts.

    Grouped by what a shopper could do about it rather than by httpx's class tree:
    a connect timeout and a read timeout are one wait as far as ``--fetch-timeout``
    is concerned, and every network failure is the same "it is not answering".
    """
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        word = _STATUS_WORDS.get(code) or ("failed" if code >= 500 else "rejected")
        return f"{word} ({code})"
    # In order: a ConnectTimeout is a timeout and not a ConnectError, but the two
    # read as one thing and are counted as one, so the timeout is asked first.
    for kinds, phrase in _FAILURE_PHRASES:
        if isinstance(exc, kinds):
            return phrase
    return _TRANSFER_FAILED


def summarise_failures(problems: Iterable[str]) -> str:
    """The kinds of failure and how many of each, commonest first.

    Empty when nothing went wrong, so the caller appends it or does not. Ties keep
    the results' own order, which is fixed, so two identical runs narrate alike.
    """
    return ", ".join(
        f"{count} {problem}" for problem, count in Counter(problems).most_common()
    )


def enrich(
    results: Sequence[SearchResult],
    *,
    max_chars: int = 1200,
    opinion_chars: int = 400,
    timeout: float = 8.0,
    workers: int = 8,
) -> list[SearchResult]:
    """Attach condensed page content to each result, in parallel.

    A result whose page could not be fetched keeps its snippet and is still used: a
    slow shop should cost the run a few seconds, not the product.

    The tally at the end says how the pages that yielded nothing failed, and how
    many each way -- "7 refused (403), 2 timed out". Grounding blanks every figure
    the pages did not back, so a run whose fetches all failed reports "price
    unknown" throughout, which is indistinguishable from a bad model unless
    something says so. One line, and no URLs: those are the DEBUG lines already
    written beside each failure.
    """
    urls = [result.url for result in results]
    logger.info("Fetching %d result page(s)", len(urls))

    with httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
    ) as client, ThreadPoolExecutor(max_workers=workers) as pool:
        read = partial(
            fetch_page, client, max_chars=max_chars, opinion_chars=opinion_chars
        )
        pages = list(pool.map(read, urls))

    enriched = [
        result.model_copy(update={"content": page.text})
        for result, page in zip(results, pages, strict=True)
    ]
    with_content = sum(1 for page in pages if page.text)
    failures = summarise_failures(page.problem for page in pages if page.problem)
    logger.log(
        # Nothing read at all is what the shopper most needs told: every figure in
        # the report ahead is about to be blanked by grounding.
        logging.WARNING if urls and not with_content else logging.INFO,
        "Got usable page text from %d of %d result(s)%s",
        with_content,
        len(urls),
        f": {failures}" if failures else "",
    )
    return enriched
