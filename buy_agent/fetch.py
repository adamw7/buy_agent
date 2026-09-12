"""Fetch the pages behind the search results and keep the parts worth reading.

DuckDuckGo snippets almost never quote a per-product price -- a search for
headphones under $200 returns ten snippets whose only number is the "$200" from
the query -- so extracting from them alone yields products with no comparable
data, and a model asked to fill that gap invents figures.

So each page is fetched and condensed to two kinds of line: the ones carrying a
price or a rating, and the ones carrying an *opinion*, a shopper's question rarely
being only "how much".
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

from buy_agent.cache import PAGES, DiskCache, open_cache

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from buy_agent.search import SearchResult

logger = logging.getLogger(__name__)

#: A browser-ish agent; many shops answer python-httpx with a 403.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

#: The signs a price may be written with, one character each. Every one of them
#: is a currency :func:`buy_agent.models._currency` can place, except ``¥``, which
#: is the yen's and the yuan's alike and is deliberately left unplaceable
#: (ADR-0043) -- the line is still kept, the figure still grounds, and the price
#: is one this run cannot compare. ``tests/test_conventions.py`` holds the two
#: tables to that, a sign read here and unplaceable there being a price taken off
#: a page and then scored on nothing.
#:
#: A sign of more than one character cannot go in a character class, and the ones
#: that matter need no row: ``R$``, ``C$`` and ``US$`` all carry the ``$`` this
#: already matches.
_CURRENCY_SIGNS = "$€£¥₹₩₪₺"  # dollar, euro, pound, yen, rupee, won, shekel, lira
#: The same currencies as their ISO codes, a page being as likely to print
#: "129 EUR" as "€129". Every sign above has its code here, or a price would be
#: read off one page and not off the next for no reason a reader could work out.
#:
#: ``TRY`` is the one sign above whose code is left out: it is an English word,
#: and matched case-insensitively beside a digit it takes "Try 3 of these" as a
#: price line. The sign carries that currency; a lost "129 TRY" costs one line.
_CURRENCY_CODES = (
    r"USD|EUR|GBP|JPY|PLN|CHF|SEK|CAD|AUD"
    r"|INR|KRW|ILS|BRL|CZK|HUF|MXN|NZD|SGD|DKK|NOK|CNY|ZAR"
)

#: The rule above the other way round: a sign of more than one character is one
#: :data:`_CURRENCY_SIGNS` cannot carry. A shop searched with ``--region pl-pl``
#: prints "599 zł" and almost never "599 PLN", so without this every price line on
#: it was dropped here, invisibly. Unambiguous, which is why "kr" is not here, and
#: :data:`~buy_agent.models._CURRENCY_ALIASES` folds it onto ``PLN`` (ADR-0043).
#: ``Kč`` is the Czech shop's spelling of the same thing, and "Ft" is left out for
#: the reason ``TRY`` is: lower-cased it is a unit of length.
_CURRENCY_WORDS = _CURRENCY_CODES + r"|zł|Kč"

_PRICE = re.compile(
    r"[" + re.escape(_CURRENCY_SIGNS) + r"]\s?\d"
    r"|\b(?:" + _CURRENCY_WORDS + r")\b\s*\d"
    r"|\d\s*(?:" + _CURRENCY_WORDS + r")\b",
    re.IGNORECASE,
)
#: A hyphen counts where a space does: "a 4.5-star average" is how a roundup
#: writes what a shop writes "4.5 stars", and keeping one form only let a page's
#: punctuation decide whether its rating reached the model. Only the ``stars``
#: branch takes it, "4.5/5" never having one.
_RATING = re.compile(
    r"\d(?:\.\d)?(?:\s*(?:/\s*(?:5|10)\b|out of\s*(?:5|10)\b)|[\s-]*stars?\b)"
    r"|\b(?:rated|rating)\b[^\d]{0,12}\d",
    re.IGNORECASE,
)

#: Lines that judge a product rather than price it. Deliberately a vocabulary of
#: *judgement* -- who is speaking, what they concluded, the words only an opinion
#: uses -- and never of subject matter, which every line on the page shares.
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

#: The XML declaration an XHTML page opens with. Taken off before parsing because
#: ``lxml`` refuses a *str* carrying an encoding declaration, and by here it names
#: an encoding nothing is in any more. Left in, every such page raised
#: ``ValueError`` and was reported as one that quoted nothing.
_XML_DECLARATION = re.compile(r"^\s*<\?xml[^>]*\?>")

#: A bare "$129" line is short but is what shop pages contain, so the floor only
#: excludes stray characters; the ceiling excludes walls of boilerplate.
_MIN_SEGMENT = 4
_MAX_SEGMENT = 300

#: An opinion is a sentence, not a figure, so it gets a floor of its own -- which
#: is what keeps a bare "Pros" heading, whose content is the lines below it, out
#: of the prompt.
_MIN_OPINION = 25

#: How much of one page is read before the rest is dropped. Neither other bound
#: is a ceiling: ``timeout`` is the wait between chunks rather than for the
#: transfer, and ``condense`` runs on text already in memory, eight pages at once.
_MAX_PAGE_BYTES = 4 * 1024 * 1024

#: The two statuses that mean "ask again later" rather than "no". Everything else
#: a shop answers is an answer: a 403 is a refusal, a 404 is a page that is not
#: there, and asking either twice is two identical failures and twice the wait
#: (ADR-0053).
_RETRY_STATUSES = frozenset({429, 503})

#: How long to wait before asking again where the answer did not say. A rate limit
#: is usually sent without a ``Retry-After``, and a second is the difference
#: between a page lost and a page read.
_RETRY_WAIT = 1.0

#: The longest a ``Retry-After`` is honoured. A shop asking for an hour is a page
#: to go without: eight fetches share one run, and the shopper is waiting on all
#: of them.
_MAX_RETRY_WAIT = 5.0


#: How a page's failure is named in the tally :func:`enrich` logs. Past tense
#: throughout and never a copula, so one phrase reads the same after "1" as after
#: "7".
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

    ``problem`` is a phrase rather than the exception, because :func:`enrich` *counts*
    it: ten pages answering 403 are worth one line and not ten, the exception each
    carried being the DEBUG line beside it. Set exactly when ``text`` is empty.

    ``cached`` says the text came off disk rather than the web (ADR-0040), counted the
    same way: a run that read nine of its ten pages off disk took seconds, and the
    line saying so is what tells that from a web that suddenly got fast.
    """

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
    # itertext, not text_content: one line per element, so a price stays separable
    # from the product name sitting in the element above it.
    return "\n".join(document.itertext())


def condense(text: str, *, max_chars: int, opinion_chars: int = 400) -> str:
    """Keep the lines that quote a figure or pass judgement, and nothing else.

    A product page is mostly navigation and legal text; what matters is the handful of
    lines naming a price or a rating and the handful saying what the thing is like to
    own. The two kinds are swept for in turn, each on a budget of its own
    (``max_chars`` and ``opinion_chars``), so neither crowds the other out;
    ``opinion_chars=0`` leaves the opinions unread. Lines come back in the page's own
    order whichever sweep took them.
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
            # The matching line first, and a match that will not fit still ends
            # the sweep: the prompt is the page read top down, so skipping an
            # expensive listing for a cheap one below reorders its argument.
            if not take(index):
                break
            # Then the line above it -- usually the product this is about, shop
            # pages putting the price under the name. Context is the one thing
            # here that is not a figure, so it is the one worth going without:
            # taken first, a long line of it ended the sweep and took every price
            # below it down, with most of the budget unspent.
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
    """Read one URL -- off the cache or off the web -- and condense it.

    Every way of yielding nothing is named rather than collapsed into "": a shop that
    answered 403, a proxy that swallowed the connection and a page that genuinely
    quoted no figures are three diagnoses, and without them the run reports the same
    "0 of 10" for all three.

    The condensing happens on both paths and the cache only ever holds the text that
    went into it, so a page read off disk yields what the web would at these budgets
    (ADR-0040). Only a page that was read is stored: a 403 stays live, so a shop that
    has stopped refusing is noticed on the next run.

    ``wait`` is how this may pause before asking a second time, and ``None`` -- the
    default -- is a fetch that asks once. A step of the pipeline holds no clock of its
    own, so the waiting is handed in by whoever is orchestrating the run (ADR-0053).
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
    """One page's visible text, or the phrase saying why there is none.

    Split from the condensing above it because this half is what a cache can stand in
    for: the budgets deciding which lines survive are per-run settings, and an excerpt
    stored under one is wrong under the next (ADR-0040).

    ``InvalidURL`` sits beside ``HTTPError`` because it is not one: httpx raises it
    out of parsing rather than the transport, so ``except httpx.HTTPError`` misses it
    and a bad port or an unbracketed IPv6 literal would make ``BuyAgent.run`` raise a
    fourth thing (ADR-0009).

    Streamed rather than fetched whole, so the body is bounded by
    :data:`_MAX_PAGE_BYTES` and the content type is read before any of it.

    Given a ``wait``, a server that said to come back later is asked once more and
    once only: that is what turns a rate-limited shop into a slow page rather than a
    blank one (ADR-0053). Without one, every answer is final.
    """
    try:
        fetched = _markup(client, url)
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        fetched = _asked_again(client, url, exc, wait)
    if fetched.problem:
        return fetched

    text = html_to_text(fetched.text)
    if not text:
        # Markup nothing could parse, named rather than left as empty text: it
        # keeps ``PageText``'s rule that text is empty exactly when a problem says
        # why, and nothing empty reaches the cache.
        logger.debug("Nothing could be read out of %s", url)
        return PageText("", _NOTHING_KEPT)
    return PageText(text)


def _markup(client: httpx.Client, url: str) -> PageText:
    """One request: the page's markup, or the phrase saying it was not HTML.

    :class:`PageText` one step early -- the markup rather than the text read out of it
    -- because this is the half that may be asked twice, and both of its answers have
    to come back the same shape whichever attempt produced them. Transport failures are
    left to raise: which of them is worth a second try is :func:`_come_back_in`'s to
    say, and it needs the exception to say it.
    """
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
    """The page on a second attempt, where this failure was worth one.

    Worth one exactly where a ``wait`` was given and the server said to come back:
    anything else is this failure, named for the tally. The second attempt is final
    whatever it answers -- a shop asking twice is one this run is not going to get.
    """
    # Asked in this order and in one condition, so there is no third state to
    # cover: no clock to wait by, or nothing worth waiting for, and the delay is
    # only read where the first of those passed.
    if wait is None or (delay := _come_back_in(exc)) is None:
        # The URL and the exception stay at DEBUG: one line per result is ten
        # lines of narration, and the tally carries the shape of the trouble.
        logger.debug("Could not fetch %s: %s", url, exc)
        return PageText("", describe_failure(exc))

    # INFO, unlike the line above it, and the one per-page line here that is: this
    # one is time the shopper is spending rather than a page they are not getting,
    # and a run that took ten seconds longer should say which pages asked for them.
    logger.info("%s asked to be tried again; waiting %.1fs", url, delay)
    wait(delay)
    try:
        return _markup(client, url)
    except (httpx.HTTPError, httpx.InvalidURL) as again:
        logger.debug("Could not fetch %s after waiting: %s", url, again)
        return PageText("", describe_failure(again))


def _come_back_in(exc: Exception) -> float | None:
    """How long this failure says to wait before asking again, or None for "do not".

    Only a response that said so: :data:`_RETRY_STATUSES` are the two statuses meaning
    "later", and ``Retry-After`` is how long. A missing or unreadable header still gets
    :data:`_RETRY_WAIT`, a rate limit being the case worth one more try however it was
    phrased -- and the ``HTTP-date`` form of that header counts as unreadable here,
    since reading it means subtracting a clock this module does not hold (ADR-0053).

    The answer is capped by :data:`_MAX_RETRY_WAIT` and floored at zero: a header is
    whatever a shop chose to send, including an hour and including a negative number.
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
    """The page's markup, up to :data:`_MAX_PAGE_BYTES` of it.

    Truncated rather than refused: a page cut mid-tag still parses -- ``lxml`` takes
    broken markup, which is most of the web -- and the lines above the cut are the ones
    a shop puts its prices on. The pieces are let go of between the joining and the
    decoding, the one moment this holds the page more than twice over, and
    :func:`enrich` reads eight pages at once.
    """
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
    """Why one page could not be read, in the words the tally counts.

    Grouped by what a shopper could do about it rather than by httpx's class tree: a
    connect timeout and a read timeout are one wait as far as ``--fetch-timeout`` is
    concerned, and every network failure is the same "it is not answering".
    """
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        word = _STATUS_WORDS.get(code) or ("failed" if code >= 500 else "rejected")
        return f"{word} ({code})"
    # In order: a ConnectTimeout is not a ConnectError, but the two read as one
    # thing and are counted as one, so the timeout is asked first.
    for kinds, phrase in _FAILURE_PHRASES:
        if isinstance(exc, kinds):
            return phrase
    return _TRANSFER_FAILED


def summarise_failures(problems: Iterable[str]) -> str:
    """The kinds of failure and how many of each, commonest first.

    Empty when nothing went wrong, so the caller appends it or does not. Ties keep the
    results' own order, so two identical runs narrate alike.
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
    cache_ttl: float = 0.0,
    wait: Callable[[float], None] | None = None,
) -> list[SearchResult]:
    """Attach condensed page content to each result, in parallel.

    A result whose page could not be fetched keeps its snippet and is still used: a
    slow shop should cost the run a few seconds, not the product.

    ``cache_ttl`` is how many seconds a stored page stays usable, 0 reading every page
    off the web. The cache is opened and pruned here rather than passed in, for the
    reason the HTTP client is: this is the function that knows when the fetching starts
    and when it is done (ADR-0040).

    ``wait`` is handed down to every page, so a shop that rate-limits this run is asked
    a second time rather than written off (ADR-0053). It is a parameter and not a
    ``time.sleep`` written here because a step of the pipeline is given what it needs
    and goes looking for nothing -- least of all a clock.

    The tally at the end says how many pages came off disk and how the rest failed --
    "7 refused (403), 2 timed out". Grounding blanks every figure the pages did not
    back, so a run whose fetches all failed reports "price unknown" throughout, which
    is indistinguishable from a bad model unless something says so.
    """
    urls = [result.url for result in results]
    logger.info("Fetching %d result page(s)", len(urls))
    cache = open_cache(PAGES, cache_ttl)

    with httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
    ) as client, ThreadPoolExecutor(max_workers=workers) as pool:
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
        # Nothing read at all is what the shopper most needs told: every figure
        # ahead is about to be blanked by grounding.
        logging.WARNING if urls and not with_content else logging.INFO,
        "Got usable page text from %d of %d result(s)%s%s",
        with_content,
        len(urls),
        f", {cached} from cache" if cached else "",
        f": {failures}" if failures else "",
    )
    return enriched
