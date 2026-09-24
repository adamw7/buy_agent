"""Check extracted products against the text they were supposedly read from (ADR-0006,
ADR-0017, ADR-0024, ADR-0025)."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from buy_agent.extraction import GENERIC_WORDS, NAME_TOKENS, SUPERLATIVES
from buy_agent.models import QUALIFIERS, Removal, nothing_recorded

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from buy_agent.models import Opinion, Product, Recorder
    from buy_agent.search import SearchResult

logger = logging.getLogger(__name__)

#: The two things a comma between digits can mean, told apart by how many digits follow:
#: three groups thousands ("1,299" is 1299), one or two is a decimal point ("129,99" is
#: 129.99).
_THOUSANDS_SEPARATOR = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
_DECIMAL_COMMA = re.compile(r"(?<=\d),(?=\d{1,2}(?!\d))")

#: Fraction of a name's distinctive words that must appear in the sources.
NAME_COVERAGE = 0.6

#: How a quote is compared with the sources: as overlapping runs of this many
#: consecutive words, of which :data:`_QUOTE_COVERAGE` must be found.
_QUOTE_WINDOW = 5
_QUOTE_COVERAGE = 0.6

#: A rating is a small number that occurs in text for a hundred other reasons, so it
#: counts only written like one: "4.3/5", "4.3 out of 5", "4.3 stars", "rated 4.3".
_RATING_AFTER = r"(?:\s*(?:/\s*5\b|(?:out\s+of|of)\s+5\b)|[\s-]*stars?\b)"
#: The gap stays generous -- "rated a solid 4.6" is how pages write it.
_RATING_BEFORE = rf"(?:rated|rating|score[ds]?)\b(?![^\d]{{0,12}}{SUPERLATIVES}\b)[^\d]{{0,12}}"
_RATING_OUT_OF_TEN = r"\s*(?:/\s*10\b|(?:out\s+of|of)\s+10\b)"

#: A review count is a small whole number, which is what a year, a model number and a
#: price all are -- so checked bare it grounds on any of them: "720" out of "WH-CH720N",
#: "2023" out of a release date.
_COUNTED = (
    r"(?:reviews?|ratings?|reviewers?|shoppers?|customers?|buyers?|owners?|users?|votes?)"
)
#: Two words of room, which is what a page puts between: "3,200 global ratings", "1,024
#: verified customer reviews".
_COUNT_AFTER = rf"\s+(?:\w+\s+){{0,2}}{_COUNTED}\b"
#: The same gap :data:`_RATING_BEFORE` leaves, for "Reviews (3,200)".
_COUNT_BEFORE = rf"{_COUNTED}\b[^\d]{{0,12}}"


def normalise_numbers(text: str) -> str:
    """Write every number one way, so the same figure compares equal either side."""
    return _DECIMAL_COMMA.sub(".", _THOUSANDS_SEPARATOR.sub("", text))


def build_haystack(results: Sequence[SearchResult]) -> str:
    """All the text the model was shown, with its numbers normalised."""
    return normalise_numbers(
        " ".join(f"{result.title} {result.snippet} {result.content}" for result in results)
    )


def mentions_number(haystack: str, value: float) -> bool:
    """Whether ``value`` appears in ``haystack`` as a standalone number."""
    literal = _as_literal(value)
    padding = "0*" if "." in literal else ""
    pattern = rf"(?<![\d.]){re.escape(literal)}{padding}(?!\d)"
    return re.search(pattern, haystack) is not None


def _as_literal(value: float) -> str:
    """Render a number the way a page would write it: 129.0 -> "129"."""
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.10g}"


def _same_figure(literal: str) -> str:
    """What may follow ``literal`` and still be the same figure: trailing zeros."""
    zeros = r"0*" if "." in literal else r"(?:\.0+)?"
    return rf"{zeros}(?!\.?\d)"


def mentions_rating(haystack: str, value: float) -> bool:
    """Whether ``value`` appears in ``haystack`` written as a rating."""
    literal = _as_literal(value)
    figure = rf"{re.escape(literal)}{_same_figure(literal)}"
    after = rf"(?<![\d.]){figure}{_RATING_AFTER}"
    before = rf"{_RATING_BEFORE}{figure}(?!{_RATING_OUT_OF_TEN})(?!\s*{SUPERLATIVES}\b)"
    return bool(
        re.search(after, haystack, re.IGNORECASE)
        or re.search(before, haystack, re.IGNORECASE)
    )


def mentions_review_count(haystack: str, value: float) -> bool:
    """Whether ``value`` appears in ``haystack`` written as a count of reviews."""
    literal = _as_literal(value)
    figure = rf"(?<![\d.]){re.escape(literal)}(?!\d)"
    return bool(
        re.search(rf"{figure}{_COUNT_AFTER}", haystack, re.IGNORECASE)
        or re.search(rf"{_COUNT_BEFORE}{figure}", haystack, re.IGNORECASE)
    )


def distinctive_words(name: str) -> list[str]:
    """The words of ``name`` that identify something rather than describe it."""
    return [
        token for token in NAME_TOKENS.findall(name.lower()) if token not in GENERIC_WORDS
    ]


def word_coverage(tokens: Sequence[str], text: str) -> float:
    """Share of ``tokens`` appearing in ``text`` as words of their own."""
    words = frozenset(NAME_TOKENS.findall(text.lower()))
    return sum(token in words for token in tokens) / len(tokens) if tokens else 0.0


def mentions_name(haystack: str, name: str) -> bool:
    """Whether the distinctive words of ``name`` appear in ``haystack``."""
    return word_coverage(distinctive_words(name), haystack) >= NAME_COVERAGE


def drop_ungrounded(
    products: Sequence[Product], haystack: str, *, record: Recorder = nothing_recorded
) -> list[Product]:
    """Remove products ``haystack`` never mentions: a name absent from every result cannot
    have been read from one (ADR-0055)."""
    kept: list[Product] = []
    dropped: list[str] = []
    for product in products:
        if mentions_name(haystack, product.name):
            kept.append(product)
        else:
            dropped.append(product.name)
            reason = "No page that was searched mentions it."
            record(Removal(name=product.name, step="ground", reason=reason))

    if dropped:
        # The count at INFO and the names at DEBUG, as everywhere a product is removed
        # -- most worth naming here, ``mentions_name`` deciding whether a product is
        # real at all.
        logger.info("Dropped %d product(s) absent from the search results", len(dropped))
        logger.debug(
            "Absent from the search results: %s", ", ".join(repr(name) for name in dropped)
        )
    return kept


def ground(
    products: Sequence[Product],
    results: Sequence[SearchResult],
    *,
    record: Recorder = nothing_recorded,
) -> list[Product]:
    """Keep only what the sources support: real products, figures, quotes and links.

    Only the first of the four removes a whole product, so it is the only one handed the
    recorder: a blanked figure, quote or link leaves the product in the report, saying so
    on its own card (ADR-0055).
    """
    haystack = build_haystack(results)
    kept = verify_numbers(drop_ungrounded(products, haystack, record=record), haystack)
    return attribute_sources(verify_opinions(kept, results), results)


def source_urls(results: Sequence[SearchResult]) -> set[str]:
    """Every page the model was actually shown, by URL."""
    return {result.url for result in results if result.url}


def _page_haystacks(results: Sequence[SearchResult]) -> list[tuple[str | None, str]]:
    """Each result on its own, as its URL and the text that page printed (ADR-0042)."""
    return [(result.url or None, build_haystack([result])) for result in results]


def attribute_sources(
    products: Sequence[Product], results: Sequence[SearchResult]
) -> list[Product]:
    """Point each product at the searched page that mentions it (ADR-0017)."""
    known = source_urls(results)
    pages = [(url, text) for url, text in _page_haystacks(results) if url]

    attributed: list[Product] = []
    invented = 0
    for product in products:
        url = product.url if product.url in known else None
        if url is None:
            if product.url:
                invented += 1
                # A link is the field the model is worst at and the one the shopper
                # clicks, so which page it invented is worth having (ADR-0017).
                logger.debug("Never searched: %r for %r", product.url, product.name)
            url = next(
                (page for page, text in pages if mentions_name(text, product.name)), None
            )
        attributed.append(
            product if url == product.url else product.model_copy(update={"url": url})
        )

    if invented:
        logger.info("Dropped %d link(s) to pages that were never searched", invented)
    return attributed


#: Each figure that has to be found in the sources, and how it is written when it is --
#: a price as a number, a rating and a review count as themselves (ADR-0022).
_GROUNDED_FIGURES: tuple[tuple[str, Callable[[str, float], bool]], ...] = (
    ("price", mentions_number),
    ("rating", mentions_rating),
    ("review_count", mentions_review_count),
)


def verify_numbers(products: Sequence[Product], haystack: str) -> list[Product]:
    """Blank out any price, rating or review count ``haystack`` does not contain."""
    verified: list[Product] = []
    dropped = 0

    for product in products:
        updates: dict[str, None] = {}
        for figure, supported in _GROUNDED_FIGURES:
            value = getattr(product, figure)
            if value is not None and not supported(haystack, value):
                updates[figure] = None
                updates.update(dict.fromkeys(QUALIFIERS.get(figure, ())))

        if updates:
            dropped += 1
            logger.debug("Unsupported %s for %r", "/".join(sorted(updates)), product.name)
        verified.append(product.model_copy(update=updates) if updates else product)

    if dropped:
        logger.info("Dropped unsupported figures on %d product(s)", dropped)
    return verified


def running_words(text: str) -> str:
    """``text`` as its words alone, lowercased, normalised and single-spaced."""
    return " ".join(NAME_TOKENS.findall(normalise_numbers(text).lower()))


def quotes_sources(haystack_words: str, quote: str) -> bool:
    """Whether ``quote`` reads as running text out of ``haystack_words``."""
    words = running_words(quote).split()
    if not words:
        return False
    padded = f" {haystack_words} "
    runs = [
        " ".join(words[start : start + _QUOTE_WINDOW])
        for start in range(max(1, len(words) - _QUOTE_WINDOW + 1))
    ]
    return sum(f" {run} " in padded for run in runs) / len(runs) >= _QUOTE_COVERAGE


def verify_opinions(
    products: Sequence[Product], results: Sequence[SearchResult]
) -> list[Product]:
    """Keep the quotes a page about this product printed, and say which page (ADR-0024,
    ADR-0025, ADR-0042)."""
    pages = [(url, text, running_words(text)) for url, text in _page_haystacks(results)]
    verified: list[Product] = []
    dropped = 0

    for product in products:
        mine = [
            (url, words) for url, text, words in pages if mentions_name(text, product.name)
        ]
        kept: list[Opinion] = []
        for opinion in product.opinions:
            # A loop rather than a comprehension: it answers two things at once, whether
            # any page printed the quote and which was first -- and ``None`` is taken, a
            # page that printed it and has no URL.
            for url, words in mine:
                if quotes_sources(words, opinion.text):
                    kept.append(opinion.model_copy(update={"url": url}))
                    break

        if len(kept) != len(product.opinions):
            dropped += len(product.opinions) - len(kept)
            logger.debug("Unsupported opinion(s) for %r", product.name)
        verified.append(product.model_copy(update={"opinions": kept}))

    if dropped:
        logger.info("Dropped %d opinion(s) the sources never printed", dropped)
    return verified
