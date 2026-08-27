"""Check extracted products against the text they were supposedly read from.

Small models fill gaps. They carry a figure over from the prompt's own example,
or report a product that appears nowhere in the results at all -- a search for
headphones returning the electric kettle used to illustrate the schema.

So nothing reaches the ranking unsupported: a product whose name is absent from
the sources is dropped, and a price, rating or review count that is absent gets
blanked. A blank scores neutral, whereas an invented price wins the top spot.

Links are grounded the same way, and worked out here rather than read off the
model: a product is pointed at the searched page that actually mentions it.

Quoted opinions are held to the strictest bar of the three, because a quote is
the one field where the model is asked for words rather than for a figure, and
where being *nearly* right is being wrong: a paraphrase attributed to a reviewer
is something nobody said. So a quote counts as supported only where *one page
that mentions the product* contains it as running text -- not as a bag of words
occurring somewhere across ten pages, and not as a real sentence about whatever
else those ten pages were selling.
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

#: A comma inside a number where it groups three digits, which is the only shape
#: a thousands separator has: "90,000" is 90000 and "1,299" is 1299.
_THOUSANDS_SEPARATOR = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")

#: The same comma with one or two digits behind it, which is the other thing it
#: can mean: "129,99" on a euro-language page is 129.99. Stripping this one the
#: way a thousands separator is stripped made it 12999 -- a hundred times the
#: price -- and cost every figure on such a page its grounding, since a reported
#: 129.99 is not in a haystack that reads 12999. ``--region pl-pl`` and the
#: currency codes in :mod:`buy_agent.fetch` are what put those pages in reach.
_DECIMAL_COMMA = re.compile(r"(?<=\d),(?=\d{1,2}(?!\d))")

#: Fraction of a name's distinctive words that must appear in the sources.
_NAME_COVERAGE = 0.6

#: How a quote is compared with the sources: as overlapping runs of this many
#: consecutive words. A word-by-word check would pass any sentence assembled out
#: of vocabulary the pages happen to share -- "great sound, very comfortable" is
#: five words every headphone page contains and no page need have printed in that
#: order -- which is the substring problem :func:`mentions_name` avoids, one
#: level up. Five words is long enough that matching one is quoting.
_QUOTE_WINDOW = 5

#: Fraction of a quote's runs that must be found. Not all of them, so that a
#: model which tops or tails a real quote with a word of its own -- a leading
#: "The", a trailing "too" -- is still quoting the page: that damages only the
#: runs at one end. A word changed in the *middle* breaks every run spanning it
#: and fails, which is the point. The middle is where a paraphrase happens.
_QUOTE_COVERAGE = 0.6

#: A rating is a small number that occurs in text for a hundred other reasons, so
#: it counts as supported only where it is written like a rating: "4.3/5",
#: "4.3 out of 5", "4.3 stars", "rated 4.3".
#:
#: Only the 0-5 scale, deliberately. ``Product.rating`` is always out of 5, so
#: accepting "4.5 out of 10" here would vouch for a claimed 4.5/5 using a score
#: that actually means 2.25/5 -- it can only ever confirm the unconverted figure
#: the extraction prompt asks the model not to report.
_RATING_AFTER = r"\s*(?:/\s*5\b|(?:out\s+of|of)\s+5\b|stars?\b)"
#: Words that make the figure beside them a count of products rather than a
#: score. "We rated the 5 best headphones" is an article's own headline: the 5
#: belongs to the noun after it, and the lead-in form used to read it as a
#: claimed 5/5. Ruled out on both sides of the figure, because either side can
#: carry the tell -- "rated the 5 best" puts it after, "rated the top 3
#: headphones" before.
_COUNTING = r"(?:best|top|cheapest|worst|greatest)"

#: The gap stays generous -- "rated a solid 4.6" is how pages write it -- but may
#: not span a counting word.
_RATING_BEFORE = rf"(?:rated|rating|score[ds]?)\b(?![^\d]{{0,12}}{_COUNTING}\b)[^\d]{{0,12}}"

#: "scored it 4.5" reads like a rating whatever the scale, so the lead-in form
#: has to be told when the figure it found is out of ten rather than five.
_RATING_OUT_OF_TEN = r"\s*(?:/\s*10\b|(?:out\s+of|of)\s+10\b)"


def normalise_numbers(text: str) -> str:
    """Write every number one way, so the same figure compares equal either side.

    How many digits follow the comma is what tells its two meanings apart,
    because it is the only thing that can: three and it separates thousands, one
    or two and it is a decimal point. A *dot* is left alone whichever it is --
    it is already the decimal point on the pages this searches, and reading it
    as a German thousands separator would break far more than it fixed.

    Both sides of every comparison come through here: the pages, in
    :func:`build_haystack`, and the model's own words, in :func:`running_words`.
    Normalising only the pages is what left a quoted "1,299" unable to match a
    haystack in which it had already become "1299".
    """
    return _DECIMAL_COMMA.sub(".", _THOUSANDS_SEPARATOR.sub("", text))


def build_haystack(results: Sequence[SearchResult]) -> str:
    """All the text the model was shown, with its numbers normalised."""
    text = " ".join(
        f"{result.title} {result.snippet} {result.content}" for result in results
    )
    return normalise_numbers(text)


def mentions_number(haystack: str, value: float) -> bool:
    """Whether ``value`` appears in ``haystack`` as a standalone number.

    ``129`` matches "$129" and "129.99" but not "1129" or "3129", so a price is
    not accepted merely because its digits occur inside a longer number. A
    decimal may pick up trailing zeros -- 10000.5 is written "$10,000.50" -- so
    those are allowed too.
    """
    literal = _as_literal(value)
    padding = "0*" if "." in literal else ""
    pattern = rf"(?<![\d.]){re.escape(literal)}{padding}(?!\d)"
    return re.search(pattern, haystack) is not None


def _as_literal(value: float) -> str:
    """Render a number the way a page would write it: 129.0 -> "129".

    ``.10g`` rather than ``g``: the default six significant digits turn a real
    12999.95 into "13000", which then matches nothing and costs the product its
    price -- and every price over 9999.99 has more than six digits.
    """
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.10g}"


def mentions_rating(haystack: str, value: float) -> bool:
    """Whether ``value`` appears in ``haystack`` written as a rating."""
    literal = re.escape(_as_literal(value))
    # (?!\.?\d) so a claimed 4.0 is not "verified" by the 4 in a printed 4.3.
    after = rf"(?<![\d.]){literal}(?!\.?\d){_RATING_AFTER}"
    before = (
        rf"{_RATING_BEFORE}{literal}(?!\.?\d)"
        rf"(?!{_RATING_OUT_OF_TEN})(?!\s*{_COUNTING}\b)"
    )
    return bool(
        re.search(after, haystack, re.IGNORECASE)
        or re.search(before, haystack, re.IGNORECASE)
    )


def mentions_name(haystack: str, name: str) -> bool:
    """Whether the distinctive words of ``name`` appear in ``haystack``.

    Generic words are ignored -- every headphone page says "wireless" -- so what
    is really checked is the brand and model number. Most of them must be there,
    not all, because a page may write "WH-CH720N" where the model wrote
    "Sony WH-CH720N Wireless".

    Both sides are broken into words by the same rule, and a word counts only
    where the page has it as a word of its own. A plain substring test would let
    the digits of an invented model number be "found" inside an unrelated one --
    a page quoting "$1700" would vouch for a "Bose 700" and a "Bose 170" alike,
    which is exactly the invention this module exists to catch. Splitting the
    haystack still keeps the case the loose bar is here for: "WH-CH720N" is the
    two words "wh" and "ch720n" on both sides of the comparison.
    """
    tokens = [
        token
        for token in NAME_TOKENS.findall(name.lower())
        if token not in GENERIC_WORDS
    ]
    if not tokens:
        return False
    words = frozenset(NAME_TOKENS.findall(haystack.lower()))
    found = sum(1 for token in tokens if token in words)
    return found / len(tokens) >= _NAME_COVERAGE


def drop_ungrounded(products: Sequence[Product], haystack: str) -> list[Product]:
    """Remove products ``haystack`` never mentions.

    A small model will carry a product straight out of the prompt's own example
    -- a search for headphones returning an electric kettle -- and a name absent
    from every result cannot have been read from one.
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
    kept = verify_opinions(kept, results)
    return attribute_sources(kept, results)


def source_urls(results: Sequence[SearchResult]) -> set[str]:
    """Every page the model was actually shown, by URL."""
    return {result.url for result in results if result.url}


def attribute_sources(
    products: Sequence[Product], results: Sequence[SearchResult]
) -> list[Product]:
    """Point each product at the searched page that mentions it.

    A link is the one field where a wrong value is worse than a blank. A blanked
    price scores neutral (ADR-0007) and an unknown one is *shown* as unknown,
    whereas a link is the part of the report the shopper actually acts on: a
    made-up one sends them to a page nobody vouched for.

    It is also the field the model is worst at. Asked for a link it usually
    leaves the field empty -- the extraction prompt is about names and figures,
    and every product in a run comes back with no link at all -- and when it does
    fill one in there is nothing tying it to the page the figures came from.

    Nothing here needs the model, though, because the answer is already in the
    prompt: each result block carries its own ``URL:``. So the model's link is
    accepted only when it names one of the pages that were searched, and
    otherwise the link is worked out from the sources -- the first result whose
    own text mentions the product, by the same coverage rule that decided the
    product was real in the first place. A product no single page mentions keeps
    no link rather than borrowing one.
    """
    known = source_urls(results)
    pages = [(result.url, build_haystack([result])) for result in results if result.url]

    attributed: list[Product] = []
    invented = 0
    for product in products:
        url = product.url if product.url in known else None
        if url is None:
            if product.url:
                invented += 1
            url = next(
                (page for page, text in pages if mentions_name(text, product.name)),
                None,
            )
        attributed.append(
            product if url == product.url else product.model_copy(update={"url": url})
        )

    if invented:
        logger.info("Dropped %d link(s) to pages that were never searched", invented)
    return attributed


#: Each figure that has to be found in the sources, and how it is written when
#: it is. A rejected figure takes its :data:`~buy_agent.models.QUALIFIERS` down
#: with it -- ADR-0022's grouping, one stage earlier than the merge it was
#: written for.
#:
#: ``rating`` comes before ``review_count`` so that a rejected rating blanks the
#: count before the count is judged on its own; blanking only ever adds, so the
#: later check cannot bring it back.
_GROUNDED_FIGURES: tuple[tuple[str, Callable[[str, float], bool]], ...] = (
    ("price", mentions_number),
    ("rating", mentions_rating),
    ("review_count", mentions_number),
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
            logger.debug(
                "Unsupported %s for %r", "/".join(sorted(updates)), product.name
            )
            product = product.model_copy(update=updates)
        verified.append(product)

    if dropped:
        logger.info("Dropped unsupported figures on %d product(s)", dropped)
    return verified


def running_words(text: str) -> str:
    """``text`` as its words alone, lowercased, normalised and single-spaced.

    Comparing quotes needs the words in order and nothing between them: a page
    prints "great sound, but heavy" where the model reports "great sound but
    heavy", and the difference is punctuation the shopper never sees. The
    numbers go through :func:`normalise_numbers` for the same reason -- a page
    and a quote must not disagree over a figure only one of them grouped.
    """
    return " ".join(NAME_TOKENS.findall(normalise_numbers(text).lower()))


def quotes_sources(haystack_words: str, quote: str) -> bool:
    """Whether ``quote`` reads as running text out of ``haystack_words``.

    The quote is cut into every run of :data:`_QUOTE_WINDOW` consecutive words
    and each run looked for in the sources as a phrase; most of them have to be
    there. A quote shorter than one window is its own single run, so it has to
    appear whole.

    ``haystack_words`` is :func:`running_words` of the sources, computed once by
    the caller rather than per quote: it is every page the model was shown.
    """
    words = running_words(quote).split()
    if not words:
        return False
    padded = f" {haystack_words} "
    runs = [
        " ".join(words[start : start + _QUOTE_WINDOW])
        for start in range(max(1, len(words) - _QUOTE_WINDOW + 1))
    ]
    found = sum(1 for run in runs if f" {run} " in padded)
    return found / len(runs) >= _QUOTE_COVERAGE


def verify_opinions(
    products: Sequence[Product], results: Sequence[SearchResult]
) -> list[Product]:
    """Drop every quoted opinion no page about this product actually printed.

    Page by page rather than against all of them pooled, which is the difference
    between "somebody wrote this" and "somebody wrote this about *this*". A quote
    is the one field that says something about a product rather than reporting a
    figure of its own, so a real verdict on the electric kettle three results
    down is not evidence about these headphones -- and one pooled haystack made
    every quote on any of ten pages available to every product on all of them.
    ADR-0024 recorded that as a limitation; ADR-0025 closes it as far as a page
    boundary reaches.

    The pages a product may be quoted from are the ones that mention it, by the
    same coverage rule that decided the product was real and that
    :func:`attribute_sources` picks its link by. A product no single page
    mentions keeps no quotes, the same way it keeps no link.

    Per quote rather than per product: a model that read one verdict off the page
    and invented a second has still read one, and the real one is worth keeping.
    A product left with none simply has nothing said about it, which is how it
    started out.
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
