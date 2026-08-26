"""Check extracted products against the text they were supposedly read from.

Small models fill gaps. They carry a figure over from the prompt's own example,
or report a product that appears nowhere in the results at all -- a search for
headphones returning the electric kettle used to illustrate the schema.

So nothing reaches the ranking unsupported: a product whose name is absent from
the sources is dropped, and a price, rating or review count that is absent gets
blanked. A blank scores neutral, whereas an invented price wins the top spot.

Links are grounded the same way, and worked out here rather than read off the
model: a product is pointed at the searched page that actually mentions it.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from buy_agent.extraction import GENERIC_WORDS, NAME_TOKENS

if TYPE_CHECKING:
    from collections.abc import Sequence

    from buy_agent.models import Product
    from buy_agent.search import SearchResult

logger = logging.getLogger(__name__)

_THOUSANDS_SEPARATOR = re.compile(r"(?<=\d),(?=\d)")

#: Fraction of a name's distinctive words that must appear in the sources.
_NAME_COVERAGE = 0.6

#: A rating is a small number that occurs in text for a hundred other reasons, so
#: it counts as supported only where it is written like a rating: "4.3/5",
#: "4.3 out of 5", "4.3 stars", "rated 4.3".
#:
#: Only the 0-5 scale, deliberately. ``Product.rating`` is always out of 5, so
#: accepting "4.5 out of 10" here would vouch for a claimed 4.5/5 using a score
#: that actually means 2.25/5 -- it can only ever confirm the unconverted figure
#: the extraction prompt asks the model not to report.
_RATING_AFTER = r"\s*(?:/\s*5\b|(?:out\s+of|of)\s+5\b|stars?\b)"
_RATING_BEFORE = r"(?:rated|rating|score[ds]?)\b[^\d]{0,12}"

#: "scored it 4.5" reads like a rating whatever the scale, so the lead-in form
#: has to be told when the figure it found is out of ten rather than five.
_RATING_OUT_OF_TEN = r"\s*(?:/\s*10\b|(?:out\s+of|of)\s+10\b)"


def build_haystack(results: Sequence[SearchResult]) -> str:
    """All the text the model was shown, with 90,000 normalised to 90000."""
    text = " ".join(
        f"{result.title} {result.snippet} {result.content}" for result in results
    )
    return _THOUSANDS_SEPARATOR.sub("", text)


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
    before = rf"{_RATING_BEFORE}{literal}(?!\.?\d)(?!{_RATING_OUT_OF_TEN})"
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
    """Keep only what the sources support: real products, figures and links."""
    haystack = build_haystack(results)
    kept = verify_numbers(drop_ungrounded(products, haystack), haystack)
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


def verify_numbers(products: Sequence[Product], haystack: str) -> list[Product]:
    """Blank out any price, rating or review count ``haystack`` does not contain."""
    verified: list[Product] = []
    dropped = 0

    for product in products:
        updates: dict[str, None] = {}
        if product.price is not None and not mentions_number(haystack, product.price):
            updates["price"] = None
            updates["currency"] = None
        if product.rating is not None and not mentions_rating(haystack, product.rating):
            updates["rating"] = None
        if product.review_count is not None and not mentions_number(
            haystack, product.review_count
        ):
            updates["review_count"] = None

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
