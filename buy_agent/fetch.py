"""Fetch the pages behind the search results and keep the parts worth reading.

DuckDuckGo snippets almost never quote a per-product price -- a search for
headphones under $200 returns ten snippets whose only number is the "$200" from
the query -- so extracting from them alone yields products with no comparable
data, and a model asked to fill that gap invents figures.

So each page is fetched and condensed to two kinds of line: the ones carrying a
price or a rating, and the ones carrying an *opinion* -- what a reviewer or an
owner thought of the thing, since a shopper's question is rarely only "how much".
Both together keep the prompt small enough for a small local model while giving
it something real to read.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
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
#: subject matter, which every line on a headphone page shares with every other.
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


#: How a page's failure is named in the tally :func:`enrich` logs. Past tense
#: throughout and never a copula, so one phrase reads the same after "1" as after
#: "7" -- the counts are what a reader is here for, and "1 were not HTML" would
#: cost a second line to fix what a word choice fixes for free.
_TIMED_OUT = "timed out"
_LOOPED = "redirected in a loop"
_UNFETCHABLE = "had an address that cannot be fetched"
_UNREACHABLE = "could not be reached"
_TRANSFER_FAILED = "failed mid-transfer"
_NOT_HTML = "did not answer with HTML"
_NOTHING_KEPT = "quoted no prices and no verdicts"

#: What a status code means, where the number alone would not say it. Anything
#: else is "rejected" under 500 and "failed" at or above it, and the code is
#: quoted beside the word either way -- so a 402 is still legible as itself.
_STATUS_WORDS = {
    401: "refused",
    403: "refused",
    404: "not found",
    410: "gone",
    429: "rate-limited",
}


class PageText(NamedTuple):
    """What one fetch yielded, and -- when it yielded nothing -- why not.

    ``problem`` is a phrase rather than the exception, because what :func:`enrich`
    does with it is *count* it: ten pages that all answered 403 are worth one line
    ("10 refused (403)") and not ten, and the exception each carried is already
    the DEBUG line beside it. It is set exactly when ``text`` is empty, so a page
    that was read and a page that was not are never both "".
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

    A product page is mostly navigation and legal text; what matters is the
    handful of lines naming a price or a rating and the handful saying what the
    thing is like to own. The two kinds are swept for in turn, each on a budget of
    its own (``max_chars`` and ``opinion_chars``), so a page listing forty prices
    cannot crowd the verdicts out and a page of prose cannot crowd out the price.
    ``opinion_chars=0`` leaves the opinions unread.

    Lines come back in the page's own order whichever sweep took them, so the
    prompt reads as an excerpt rather than as two lists.
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
    genuinely quoted no figures are three different diagnoses, and without them
    the run reports the same "0 of 10" for all three -- which reads as the model
    having been bad rather than as nothing having been read.

    ``InvalidURL`` sits beside ``HTTPError`` because it is not one: httpx raises it
    out of parsing rather than the transport, so it inherits from ``Exception``
    directly and the obvious ``except httpx.HTTPError`` misses it -- leaving a
    result whose href has a bad port or an unbracketed IPv6 literal to escape the
    pool and make ``BuyAgent.run`` raise a fourth thing (ADR-0009). A link that
    cannot name a page is a page that could not be fetched.
    """
    try:
        response = client.get(url)
        response.raise_for_status()
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        # The URL and the exception stay at DEBUG, where they were: one line per
        # result is ten lines of a run's narration, and the shape of the trouble
        # -- which is what the tally carries -- is the same in one word.
        logger.debug("Could not fetch %s: %s", url, exc)
        return PageText("", describe_failure(exc))

    content_type = response.headers.get("content-type", "html")
    if "html" not in content_type:
        logger.debug("Skipped %s: served as %r", url, content_type)
        return PageText("", _NOT_HTML)

    text = condense(
        html_to_text(response.text), max_chars=max_chars, opinion_chars=opinion_chars
    )
    if not text:
        logger.debug("Nothing worth keeping on %s", url)
        return PageText("", _NOTHING_KEPT)
    return PageText(text)


def describe_failure(exc: Exception) -> str:
    """Why one page could not be read, in the words the tally counts.

    Grouped by what a shopper could do about it rather than by httpx's class
    tree: a connect timeout and a read timeout are one wait as far as
    ``--fetch-timeout`` is concerned, and every kind of network failure is the
    same "it is not answering". The status codes worth a word of their own get
    one, and the rest are the number, which says enough beside "rejected".
    """
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        word = _STATUS_WORDS.get(code) or ("failed" if code >= 500 else "rejected")
        return f"{word} ({code})"
    # Before the network check: a ConnectTimeout is a timeout and not a
    # ConnectError, but the two read as one thing and are counted as one.
    if isinstance(exc, httpx.TimeoutException):
        return _TIMED_OUT
    if isinstance(exc, httpx.TooManyRedirects):
        return _LOOPED
    if isinstance(exc, (httpx.InvalidURL, httpx.UnsupportedProtocol)):
        return _UNFETCHABLE
    if isinstance(exc, (httpx.NetworkError, httpx.ProxyError)):
        return _UNREACHABLE
    return _TRANSFER_FAILED


def summarise_failures(problems: Iterable[str]) -> str:
    """The kinds of failure and how many of each, commonest first.

    Empty when nothing went wrong, so the caller appends it or does not. Ties
    keep the order the results were in, which is fixed, so two identical runs
    narrate identically.
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

    A result whose page could not be fetched keeps its snippet and is still used:
    a slow shop should cost the run a few seconds, not the product.

    The tally at the end says how the pages that yielded nothing failed, and how
    many failed each way -- "7 refused (403), 2 timed out" -- because grounding
    blanks every figure the pages did not back, so a run whose fetches all failed
    produces a report of "price unknown" that is indistinguishable from a bad
    model unless something says so. One line, so it is as readable in the
    browser's progress panel as on the CLI, and it names no URLs: those are the
    DEBUG lines the failures already write.
    """
    urls = [result.url for result in results]
    logger.info("Fetching %d result page(s)", len(urls))

    with httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
    ) as client:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            pages = list(
                pool.map(
                    lambda url: fetch_page(
                        client, url, max_chars=max_chars, opinion_chars=opinion_chars
                    ),
                    urls,
                )
            )

    enriched = [
        result.model_copy(update={"content": page.text})
        for result, page in zip(results, pages, strict=True)
    ]
    with_content = sum(1 for page in pages if page.text)
    failures = summarise_failures(page.problem for page in pages if page.problem)
    logger.log(
        # Nothing read at all is the case the shopper most needs told: every
        # figure in the report ahead is about to be blanked by grounding, and a
        # warning is what separates that from the narration it sits in.
        logging.WARNING if urls and not with_content else logging.INFO,
        "Got usable page text from %d of %d result(s)%s",
        with_content,
        len(urls),
        f": {failures}" if failures else "",
    )
    return enriched
