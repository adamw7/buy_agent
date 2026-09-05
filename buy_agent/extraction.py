"""The two LLM steps -- rewrite the request as a search query, then read products
out of the search results -- plus the deterministic clean-up that follows.

Both chains are answered under a JSON schema, constraining decoding to it, so a
small local model cannot answer with prose or a half-closed object -- how that
schema is declared is the provider's to say (ADR-0004, ADR-0038). What the model
still gets wrong is judgement, not syntax: it will
happily report "12 Best Headphones Under $200" as a product, which is what
``clean_products`` and ``deduplicate`` are for.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from buy_agent.chat import Chain, Prompt
from buy_agent.models import (
    MAX_OPINIONS,
    QUALIFIERS,
    ProductList,
    SearchQuery,
    distinct_quotes,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from buy_agent.chat import ChatModel
    from buy_agent.models import Opinion, Product
    from buy_agent.search import SearchResult

logger = logging.getLogger(__name__)

QUERY_PROMPT = Prompt(
    system=(
        "You turn a shopper's request into one web search query that will surface "
        "actual products for sale with prices and reviews.\n"
        "Keep the shopper's constraints (budget, brand, size, use case). "
        "Add words like 'price' or 'review' when useful. "
        "Do not add constraints the shopper never mentioned. "
        "Answer with the query only, no explanation."
    ),
    human="Shopper's request: {request}",
)

EXTRACTION_PROMPT = Prompt(
    system=(
        "You extract concrete, buyable products from web search results.\n"
        "Rules:\n"
        "- Extract at most {limit} distinct products that match the shopper's request.\n"
        "- Only use facts present in the results. Never invent a price or a rating.\n"
        "- Unknown price or rating is -1; unknown review count is 0; unknown text "
        "is empty; no opinions is an empty list.\n"
        "- Ratings go on a 0-5 scale. Convert a 0-10 or percentage score first.\n"
        "- A name is a specific model, such as 'Sony WH-1000XM5'. Never an article "
        "headline, a shop name, or a category.\n"
        f"- opinions are up to {MAX_OPINIONS} short quotes saying what the product "
        "is like to own: praise, a complaint, a verdict. Copy them word for word "
        "from the results. Never write your own, and never give a product an "
        "opinion the results gave to a different one.\n"
        "\n"
        "Example of the naming rule, on an unrelated product:\n"
        "  TITLE: 9 Best Electric Kettles of 2026 | KitchenSite\n"
        "  SNIPPET: The Fellow Stagg EKG is $165 (4.6/5 from 3,200 ratings). "
        "The Bonavita Gooseneck is $80.\n"
        "  PAGE: We loved the Stagg's precise temperature control, but the "
        "handle gets warm.\n"
        "Correct: 'Fellow Stagg EKG' (price 165, rating 4.6, review_count 3200, "
        "opinions [\"We loved the Stagg's precise temperature control\", "
        "\"the handle gets warm\"]) and 'Bonavita Gooseneck' (price 80, "
        "rating -1, opinions []).\n"
        "Wrong: '9 Best Electric Kettles of 2026' or 'KitchenSite' -- those are "
        "the article and the website, not products.\n"
        "The example shows the format only. Every number and every quote you "
        "report must appear in the search results below."
    ),
    human="Shopper's request: {request}\n\nSearch results:\n\n{results}",
)

#: A name opening on a superlative: "12 Best ...", "The 5 Best ...", "Top ...".
#: Named on its own as the one tell in :data:`_NOT_A_PRODUCT` a real product also
#: trips, so :func:`looks_like_a_product` asks a second question of its matches.
_SUPERLATIVE = re.compile(
    r"^\s*(the\s+)?(\d+\s+)?(best|top|cheapest|worst|greatest)\b", re.IGNORECASE
)

#: Article headlines the model mistakes for products. A real listing is named after
#: a model ("Sony WH-1000XM5"), never after the page it was found on.
_NOT_A_PRODUCT = re.compile(
    rf"""
      {_SUPERLATIVE.pattern}
    | \bbuy(ing)?\s+guide\b
    | ^\s*(how|why|what|which|where)\b                            # "How to choose ..."
    | \b(deals|coupons?)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

#: Publisher credit at the end of a headline: "Sony WH-1000XM5 | AudioSite".
_SITE_SUFFIX = re.compile(r"\s+\|\s+")

#: Words models tack onto a name when copying it off a review page.
_TRAILING_NOISE = re.compile(
    r"\s*[-|:,]?\s*\b(reviews?|price|deal|on sale|tested|hands[- ]on)\b\s*$",
    re.IGNORECASE,
)

#: A token carrying both letters and digits, which is what a model number looks
#: like: "WH-1000XM5" has "1000xm5". A year or a price is digits alone and does
#: not count, which keeps "Best Headphones 2026" a headline.
_MODEL_NUMBER = re.compile(r"\b(?=[a-z0-9]*[a-z])(?=[a-z0-9]*\d)[a-z0-9]+\b", re.IGNORECASE)

#: Longer than any real model name. Article titles run long.
_MAX_NAME_LENGTH = 80

#: Words that describe a product without identifying it. Two names differing only
#: by these are one product ("Sony WH-CH720N" / "Sony WH-CH720N Wireless
#: Headphones"); any other difference is not ("AirPods" / "AirPods Pro").
GENERIC_WORDS = frozenset(
    """
    a an and the with for
    wireless wired bluetooth
    headphone headphones headset earbud earbuds earphones buds
    noise cancelling canceling cancellation anc
    over on in ear ears overear onear inear
    black white silver blue red grey gray
    new latest edition version model
    """.split()
)

#: How a name is broken into words, shared with :mod:`buy_agent.verification` --
#: merging and grounding must agree on what a name's words are.
NAME_TOKENS = re.compile(r"[a-z0-9]+")


def build_query_chain(llm: ChatModel) -> Chain[SearchQuery]:
    """Chain: ``{"request": str}`` -> :class:`SearchQuery`."""
    return Chain(QUERY_PROMPT, llm, SearchQuery)


def build_extraction_chain(llm: ChatModel) -> Chain[ProductList]:
    """Chain: ``{"request", "results", "limit"}`` -> :class:`ProductList`."""
    return Chain(EXTRACTION_PROMPT, llm, ProductList)


def format_results(results: Sequence[SearchResult]) -> str:
    """Render search results as numbered blocks for the extraction prompt."""
    return "\n\n".join(
        f"[{index}]\n{result.as_prompt_block()}"
        for index, result in enumerate(results, start=1)
    )


def clean_name(name: str) -> str:
    """Strip the page furniture models copy along with a product name.

    ``"Sennheiser HD 450BT Review | AudioSite"`` is a real product wearing a
    headline, so the suffix comes off rather than the product being dropped.
    """
    name = _SITE_SUFFIX.split(name.strip(), maxsplit=1)[0]
    name = _TRAILING_NOISE.sub("", name)
    return name.strip(" -|:,").strip()


def looks_like_a_product(name: str) -> bool:
    """Whether ``name`` reads like a product rather than the page it came from.

    A leading superlative is the strongest tell of a headline and the one a real
    product also trips ("Best Buy Essentials BE-HAPB02"). What tells them apart is
    *where* the model number sits: a headline puts a category qualifier after the
    superlative and names no single model ("Best PS5 Headsets"), a product puts its
    brand there and its model number later. So a superlative name is kept only
    where a model number follows something else -- at the cost of "Top Gun
    Sunglasses", which is why :func:`clean_products` logs what it took.
    """
    name = name.strip()
    if not name or len(name) > _MAX_NAME_LENGTH or "?" in name:
        return False
    if not _NOT_A_PRODUCT.search(name):
        return True
    if not _SUPERLATIVE.match(name):
        return False
    # Past the superlative and past the word after it -- the category qualifier a
    # headline puts there, the brand a product does.
    tail = _SUPERLATIVE.sub("", name, count=1).split(maxsplit=1)
    return len(tail) > 1 and bool(_MODEL_NUMBER.search(tail[1]))


def clean_products(products: Sequence[Product]) -> list[Product]:
    """Tidy up names and drop entries that are articles or shops, not products."""
    kept: list[Product] = []
    discarded: list[str] = []
    for product in products:
        name = clean_name(product.name)
        if looks_like_a_product(name):
            kept.append(product.model_copy(update={"name": name}))
        else:
            discarded.append(name or product.name)
    if discarded:
        # The count at INFO, the names at DEBUG: a heuristic that drops a real
        # product should be diagnosable, and the names say which just happened.
        logger.info("Discarded %d result(s) that were pages, not products", len(discarded))
        logger.debug(
            "Discarded as pages, not products: %s", ", ".join(repr(n) for n in discarded)
        )
    return kept


def deduplicate(products: Sequence[Product], limit: int) -> list[Product]:
    """Drop repeats of the same product, keeping the most complete entry.

    Search results overlap heavily, so without this the top 3 can be one product
    listed three times. One pass of :func:`merge_variants` does all of it: an exact
    repeat is the easiest case of a name differing by descriptive words -- its
    difference being empty and vacuously generic -- and a pass of its own for exact
    names would be the same merge under a second rule about whose name survives.

    A name with nothing to identify it by is dropped: it can be neither merged nor
    reported.
    """
    named = [product for product in products if product.dedup_key]
    if len(named) != len(products):
        logger.info(
            "Dropped %d result(s) whose name identifies nothing", len(products) - len(named)
        )
    deduped = merge_variants(named)
    merged = len(named) - len(deduped)
    if merged:
        logger.info("Merged %d duplicate listing(s)", merged)
    return deduped[:limit]


def merge_variants(products: Sequence[Product]) -> list[Product]:
    """Fold together names that identify the same thing.

    Exact matching would miss the common case where one page says "Sony WH-CH720N"
    and the next "Sony WH-CH720N Noise Canceling Wireless Headphones", taking two of
    the three reported slots.
    """
    merged: list[Product] = []
    for product in products:
        for index, existing in enumerate(merged):
            if _same_product(existing.name, product.name):
                merged[index] = _combine(existing, product)
                break
        else:
            merged.append(product)
    return merged


def _same_product(left: str, right: str) -> bool:
    """Whether two names identify the same thing modulo descriptive words."""
    left_tokens = frozenset(NAME_TOKENS.findall(left.lower()))
    right_tokens = frozenset(NAME_TOKENS.findall(right.lower()))
    if not left_tokens or not right_tokens:
        return False
    if not (left_tokens <= right_tokens or right_tokens <= left_tokens):
        return False
    return (left_tokens ^ right_tokens) <= GENERIC_WORDS


#: Fields worth carrying over from a weaker listing, and the list to edit when one
#: is added to ``Product``. Each moves with whatever only qualifies it
#: (:data:`~buy_agent.models.QUALIFIERS`): grounding runs before the merge and
#: each half really is in the sources, so only an invented *pairing* is left to
#: catch. ``opinions`` is deliberately not here -- see :func:`_merge_opinions`.
_MERGEABLE_FIELDS = ("price", "rating", "seller", "url", "notes")


def _combine(first: Product, second: Product) -> Product:
    """Merge two listings for one product.

    Name and data are decided separately: the shorter name reads better, the
    figures come from whichever listing filled in more of them. Both ties go to
    ``first``, the listing that ranked higher in the search results.
    """
    winner, loser = (
        (first, second) if _completeness(first) >= _completeness(second) else (second, first)
    )
    updates = _fill_gaps(winner, loser)
    updates["name"] = min(first.name, second.name, key=len)
    updates["opinions"] = _merge_opinions(winner, loser)
    return winner.model_copy(update=updates)


def _merge_opinions(winner: Product, loser: Product) -> list[Opinion]:
    """Both listings' opinions, the winner's first, without repeats.

    The only field taken from both. Two listings quoting different prices are in
    conflict and one has to win; two reviewers are not. Nothing is invented: each
    quote was grounded on its own, and a quote says who it is about by being about
    the product rather than by sitting next to a figure -- which is why it needs
    none of the pairing care :data:`_MERGEABLE_FIELDS` takes.

    A quote travels with the page that printed it, which is the one qualifier here
    that needs no rule of its own (ADR-0042): the pair is one object, so neither
    half can be carried over without the other and neither listing's link can end
    up under the other listing's words.
    """
    return distinct_quotes([*winner.opinions, *loser.opinions])


def _fill_gaps(winner: Product, loser: Product) -> dict[str, object]:
    """The fields ``loser`` can contribute because ``winner`` left them blank.

    A figure travels with the words that qualify it: the loser's currency or
    review count is taken only where its price or rating is taken too, or where
    both quote the same one -- never grafted onto a figure it never printed.
    """
    updates: dict[str, object] = {}
    for figure in _MERGEABLE_FIELDS:
        qualifiers = QUALIFIERS.get(figure, ())
        ours, theirs = getattr(winner, figure), getattr(loser, figure)
        if ours is None and theirs is not None:
            # The loser's whole group moves across, and any qualifier the winner
            # was left holding goes with the blank it used to describe.
            updates[figure] = theirs
            updates.update({name: getattr(loser, name) for name in qualifiers})
        elif ours is not None and ours == theirs:
            # Both listings printed this figure, so the loser's qualifier describes
            # the very one being kept.
            updates.update(
                {
                    name: getattr(loser, name)
                    for name in qualifiers
                    if getattr(winner, name) is None and getattr(loser, name) is not None
                }
            )
    return updates


def _completeness(product: Product) -> int:
    """How many of the fields that matter this listing actually filled in."""
    return sum(
        value is not None
        for value in (product.price, product.rating, product.review_count, product.url)
    )
