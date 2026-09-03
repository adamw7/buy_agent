"""Check extracted products against the text they were supposedly read from.

Small models fill gaps -- a figure carried over from the prompt's own example, or
the example's electric kettle reported as a product -- so nothing reaches the
ranking unsupported. A name absent from the sources drops the product; an absent
price, rating or review count is blanked (a blank scores neutral, an invented
price wins the top spot); a link is worked out from the sources rather than read
off the model.

Quotes get the strictest bar, being the one field asked for in words: a quote is
supported only where *one page that mentions the product* has it as running text
-- not as words scattered across ten pages, and not as a real sentence about
whatever else those pages were selling.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from buy_agent.extraction import GENERIC_WORDS, NAME_TOKENS
from buy_agent.models import QUALIFIERS

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from buy_agent.models import Product
    from buy_agent.search import SearchResult

logger = logging.getLogger(__name__)

#: The two things a comma between digits can mean, told apart by how many digits
#: follow it: three and it groups thousands ("1,299" is 1299), one or two and it
#: is a decimal point ("129,99" on a euro-language page is 129.99). Stripping the
#: second the way the first is stripped made it 12999 -- a hundred times the
#: price -- and cost every figure on such a page its grounding.
_THOUSANDS_SEPARATOR = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
_DECIMAL_COMMA = re.compile(r"(?<=\d),(?=\d{1,2}(?!\d))")

#: Fraction of a name's distinctive words that must appear in the sources.
_NAME_COVERAGE = 0.6

#: How a quote is compared with the sources: as overlapping runs of this many
#: consecutive words, of which :data:`_QUOTE_COVERAGE` must be found. A
#: word-by-word check would pass any sentence assembled out of vocabulary the
#: pages share -- "great sound, very comfortable" is five words every headphone
#: page contains and none need have printed in that order.
#:
#: Not all the runs, so a model topping or tailing a real quote with a word of its
#: own is still quoting: that damages only the runs at one end. A word changed in
#: the *middle* breaks every run spanning it and fails, which is the point.
_QUOTE_WINDOW = 5
_QUOTE_COVERAGE = 0.6

#: A rating is a small number that occurs in text for a hundred other reasons, so
#: it counts only where it is written like one: "4.3/5", "4.3 out of 5", "4.3
#: stars", "rated 4.3". The 0-5 scale only -- ``Product.rating`` is always out of
#: 5, so accepting "4.5 out of 10" would vouch for a claimed 4.5/5 with a score
#: meaning 2.25/5; :data:`_RATING_OUT_OF_TEN` tells the lead-in form ("scored it
#: 4.5" reads like a rating whatever the scale) that the figure is out of ten.
#:
#: :data:`_COUNTING` words make the figure beside them a count of products rather
#: than a score -- "we rated the 5 best headphones" -- and are ruled out on both
#: sides, either being able to carry the tell.
_RATING_AFTER = r"\s*(?:/\s*5\b|(?:out\s+of|of)\s+5\b|stars?\b)"
_COUNTING = r"(?:best|top|cheapest|worst|greatest)"
#: The gap stays generous -- "rated a solid 4.6" is how pages write it.
_RATING_BEFORE = rf"(?:rated|rating|score[ds]?)\b(?![^\d]{{0,12}}{_COUNTING}\b)[^\d]{{0,12}}"
_RATING_OUT_OF_TEN = r"\s*(?:/\s*10\b|(?:out\s+of|of)\s+10\b)"

#: A review count is a small whole number, which is what a year, a model number
#: and a price all are too -- so checked as a bare figure it grounds on any of
#: them: "720" out of "WH-CH720N", "2023" out of a release date, "148" out of the
#: price beside it. That is the mistake :data:`_RATING_AFTER` exists to refuse,
#: on the other figure that feeds the score (the popularity half of it), so it is
#: refused the same way: the number counts only where it is written as a count of
#: somebody. The nouns are who does the reviewing, not what a page is about --
#: "headphones" or "products" would take every figure on it.
_COUNTED = (
    r"(?:reviews?|ratings?|reviewers?|shoppers?|customers?|buyers?|owners?|users?|votes?)"
)
#: Two words of room, which is what a page puts between: "3,200 global ratings",
#: "1,024 verified customer reviews".
_COUNT_AFTER = rf"\s+(?:\w+\s+){{0,2}}{_COUNTED}\b"
#: The same gap :data:`_RATING_BEFORE` leaves, for "Reviews (3,200)".
_COUNT_BEFORE = rf"{_COUNTED}\b[^\d]{{0,12}}"


def normalise_numbers(text: str) -> str:
    """Write every number one way, so the same figure compares equal either side.

    A *dot* is left alone, being already the decimal point on these pages. Both
    sides come through here (:func:`build_haystack`, :func:`running_words`):
    normalising only the pages left a quoted "1,299" unable to match a haystack in
    which it had already become "1299".
    """
    return _DECIMAL_COMMA.sub(".", _THOUSANDS_SEPARATOR.sub("", text))


def build_haystack(results: Sequence[SearchResult]) -> str:
    """All the text the model was shown, with its numbers normalised."""
    return normalise_numbers(
        " ".join(f"{result.title} {result.snippet} {result.content}" for result in results)
    )


def mentions_number(haystack: str, value: float) -> bool:
    """Whether ``value`` appears in ``haystack`` as a standalone number.

    ``129`` matches "$129" and "129.99" but not "1129": digits inside a longer
    number vouch for nothing. A decimal may pick up trailing zeros -- 10000.5 is
    written "$10,000.50".
    """
    literal = _as_literal(value)
    padding = "0*" if "." in literal else ""
    pattern = rf"(?<![\d.]){re.escape(literal)}{padding}(?!\d)"
    return re.search(pattern, haystack) is not None


def _as_literal(value: float) -> str:
    """Render a number the way a page would write it: 129.0 -> "129".

    ``.10g`` rather than ``g``: six significant digits turn a real 12999.95 into
    "13000", matching nothing and costing the product its price.
    """
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.10g}"


def _same_figure(literal: str) -> str:
    """What may follow ``literal`` and still be the same figure: trailing zeros.

    A page prints a whole 4 as "4.0" where :func:`_as_literal` renders it "4",
    which is also the "4" in a printed 4.3. Consuming the zeros and *then* refusing
    a further digit tells them apart; without it a rating the page stated exactly
    failed grounding, taking its review count with it.
    """
    zeros = r"0*" if "." in literal else r"(?:\.0+)?"
    return rf"{zeros}(?!\.?\d)"


def mentions_rating(haystack: str, value: float) -> bool:
    """Whether ``value`` appears in ``haystack`` written as a rating."""
    literal = _as_literal(value)
    figure = rf"{re.escape(literal)}{_same_figure(literal)}"
    after = rf"(?<![\d.]){figure}{_RATING_AFTER}"
    before = rf"{_RATING_BEFORE}{figure}(?!{_RATING_OUT_OF_TEN})(?!\s*{_COUNTING}\b)"
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


def mentions_name(haystack: str, name: str) -> bool:
    """Whether the distinctive words of ``name`` appear in ``haystack``.

    Generic words are ignored -- every headphone page says "wireless" -- so what is
    checked is the brand and model number. Most, not all, since a page may write
    "WH-CH720N" where the model wrote "Sony WH-CH720N Wireless".

    Both sides split by the same rule, and a word counts only as a word of its own:
    a substring test would let a page quoting "$1700" vouch for an invented "Bose
    700".
    """
    tokens = [
        token for token in NAME_TOKENS.findall(name.lower()) if token not in GENERIC_WORDS
    ]
    if not tokens:
        return False
    words = frozenset(NAME_TOKENS.findall(haystack.lower()))
    return sum(token in words for token in tokens) / len(tokens) >= _NAME_COVERAGE


def drop_ungrounded(products: Sequence[Product], haystack: str) -> list[Product]:
    """Remove products ``haystack`` never mentions.

    A name absent from every result cannot have been read from one.
    """
    kept = [product for product in products if mentions_name(haystack, product.name)]
    dropped = len(products) - len(kept)
    if dropped:
        logger.info("Dropped %d product(s) absent from the search results", dropped)
    return kept


def ground(
    products: Sequence[Product], results: Sequence[SearchResult]
) -> list[Product]:
    """Keep only what the sources support: real products, figures, quotes and links."""
    haystack = build_haystack(results)
    kept = verify_numbers(drop_ungrounded(products, haystack), haystack)
    return attribute_sources(verify_opinions(kept, results), results)


def source_urls(results: Sequence[SearchResult]) -> set[str]:
    """Every page the model was actually shown, by URL."""
    return {result.url for result in results if result.url}


def attribute_sources(
    products: Sequence[Product], results: Sequence[SearchResult]
) -> list[Product]:
    """Point each product at the searched page that mentions it.

    A wrong link is worse than a blank -- a blanked price is *shown* as unknown,
    while a link is what the shopper clicks -- and it is the field the model is
    worst at. So the model's link is kept only where it names a page that was
    searched (ADR-0017); otherwise the link is the first result whose text mentions
    the product, and one no page mentions keeps none rather than borrowing one.
    """
    known = source_urls(results)
    pages = [(result.url, build_haystack([result])) for result in results if result.url]

    attributed: list[Product] = []
    invented = 0
    for product in products:
        url = product.url if product.url in known else None
        if url is None:
            invented += bool(product.url)
            url = next(
                (page for page, text in pages if mentions_name(text, product.name)), None
            )
        attributed.append(
            product if url == product.url else product.model_copy(update={"url": url})
        )

    if invented:
        logger.info("Dropped %d link(s) to pages that were never searched", invented)
    return attributed


#: Each figure that has to be found in the sources, and how it is written when it
#: is -- a price as a number, a rating and a review count as themselves, both of
#: those being small figures a page prints for a hundred other reasons. A rejected figure takes its :data:`~buy_agent.models.QUALIFIERS` down with
#: it -- ADR-0022's grouping, one stage earlier than the merge it was written for.
#: ``rating`` comes before ``review_count`` so a rejected rating blanks the count
#: before the count is judged alone; blanking only adds, so nothing brings it back.
_GROUNDED_FIGURES: tuple[tuple[str, Callable[[str, float], bool]], ...] = (
    ("price", mentions_number),
    ("rating", mentions_rating),
    ("review_count", mentions_review_count),
)


def verify_numbers(products: Sequence[Product], haystack: str) -> list[Product]:
    """Blank out any price, rating or review count ``haystack`` does not contain.

    A blanked figure takes whatever only qualified it with it -- see
    :data:`_GROUNDED_FIGURES`.
    """
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
            product = product.model_copy(update=updates)
        verified.append(product)

    if dropped:
        logger.info("Dropped unsupported figures on %d product(s)", dropped)
    return verified


def running_words(text: str) -> str:
    """``text`` as its words alone, lowercased, normalised and single-spaced.

    Comparing quotes needs the words in order and nothing between them: a page
    prints "great sound, but heavy" where the model reports "great sound but
    heavy". Numbers go through :func:`normalise_numbers` for the same reason.
    """
    return " ".join(NAME_TOKENS.findall(normalise_numbers(text).lower()))


def quotes_sources(haystack_words: str, quote: str) -> bool:
    """Whether ``quote`` reads as running text out of ``haystack_words``.

    Cut into every run of :data:`_QUOTE_WINDOW` consecutive words, each looked
    for as a phrase; most have to be there, and a quote shorter than one window
    is its own run and must appear whole. ``haystack_words`` is one page's
    :func:`running_words`, computed by the caller rather than per quote.
    """
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
    """Drop every quoted opinion no page about this product actually printed.

    Page by page rather than pooled (ADR-0024, ADR-0025) -- the difference between
    "somebody wrote this" and "somebody wrote this about *this*", a verdict on the
    electric kettle three results down being no evidence about these headphones. A
    product may be quoted only from pages that mention it, by the rule
    :func:`attribute_sources` picks its link by. Per quote rather than per product:
    a model that read one verdict and invented a second has still read one.
    """
    pages = [
        (text, running_words(text))
        for text in (build_haystack([result]) for result in results)
    ]
    verified: list[Product] = []
    dropped = 0

    for product in products:
        mine = [words for text, words in pages if mentions_name(text, product.name)]
        kept = [
            opinion
            for opinion in product.opinions
            if any(quotes_sources(words, opinion) for words in mine)
        ]
        if len(kept) != len(product.opinions):
            dropped += len(product.opinions) - len(kept)
            logger.debug("Unsupported opinion(s) for %r", product.name)
            product = product.model_copy(update={"opinions": kept})
        verified.append(product)

    if dropped:
        logger.info("Dropped %d opinion(s) the sources never printed", dropped)
    return verified
