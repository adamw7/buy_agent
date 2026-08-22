"""Fetch the pages behind the search results and keep the parts that mention money.

DuckDuckGo snippets almost never quote a per-product price -- a search for
headphones under $200 returns ten snippets whose only number is "$200", from the
query itself. Extracting from snippets alone therefore yields products with no
comparable data, and a model asked to fill that gap invents figures instead.

So each result page is fetched and condensed down to the lines that actually
carry a price or a rating. That keeps the prompt small enough for a small local
model while giving it something real to read.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import httpx
from lxml import html as lxml_html

if TYPE_CHECKING:
    from collections.abc import Sequence

    from buy_agent.search import SearchResult

logger = logging.getLogger(__name__)

#: A browser-ish agent; many shops answer python-httpx with a 403.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

_CURRENCY_SIGNS = "$" + "€£¥"  # dollar, euro, pound, yen
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
_SEGMENT_BREAK = re.compile(r"[\n\r]+")
_WHITESPACE = re.compile(r"\s+")

#: A bare "$129" line is short but is exactly what shop pages contain, so the
#: floor only excludes stray characters. The ceiling excludes walls of boilerplate.
_MIN_SEGMENT = 4
_MAX_SEGMENT = 300


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


def condense(text: str, *, max_chars: int) -> str:
    """Keep only the lines that mention a price or a rating, up to ``max_chars``.

    A product page is mostly navigation and legal text. What matters for ranking
    is the handful of lines that name a figure, so everything else is discarded
    before the prompt is built.
    """
    segments = [_WHITESPACE.sub(" ", raw).strip() for raw in _SEGMENT_BREAK.split(text)]
    segments = [segment for segment in segments if segment]

    kept: list[str] = []
    seen: set[str] = set()
    size = 0

    def take(segment: str) -> bool:
        """Add a segment; False once the budget is spent."""
        nonlocal size
        if segment in seen or len(segment) > _MAX_SEGMENT:
            return True
        if size + len(segment) > max_chars:
            return False
        seen.add(segment)
        kept.append(segment)
        size += len(segment) + 1
        return True

    for index, segment in enumerate(segments):
        if not (_MIN_SEGMENT <= len(segment) <= _MAX_SEGMENT):
            continue
        if not (_PRICE.search(segment) or _RATING.search(segment)):
            continue
        # The line above a price is usually the product name it belongs to.
        if index and not take(segments[index - 1]):
            break
        if not take(segment):
            break

    return "\n".join(kept)


def fetch_page(client: httpx.Client, url: str, *, max_chars: int) -> str:
    """Fetch one URL and condense it. Returns "" for anything that goes wrong."""
    try:
        response = client.get(url)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.debug("Could not fetch %s: %s", url, exc)
        return ""

    if "html" not in response.headers.get("content-type", "html"):
        return ""
    return condense(html_to_text(response.text), max_chars=max_chars)


def enrich(
    results: Sequence[SearchResult],
    *,
    max_chars: int = 1200,
    timeout: float = 8.0,
    workers: int = 8,
) -> list[SearchResult]:
    """Attach condensed page content to each result, in parallel.

    Results whose page could not be fetched keep their snippet and are still
    used; a slow shop should cost the run a few seconds, not the product.
    """
    urls = [result.url for result in results]
    logger.info("Fetching %d result page(s)", len(urls))

    with httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
    ) as client:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            contents = list(
                pool.map(lambda url: fetch_page(client, url, max_chars=max_chars), urls)
            )

    enriched = [
        result.model_copy(update={"content": content})
        for result, content in zip(results, contents, strict=True)
    ]
    with_content = sum(1 for content in contents if content)
    logger.info("Got usable page text from %d of %d result(s)", with_content, len(urls))
    return enriched
