"""The two LLM steps -- rewrite the request as a search query, then read products
out of the search results -- plus the deterministic clean-up that follows.

Both chains use Ollama's structured-output API (``method="json_schema"``), which
constrains decoding to the schema. That is what makes this workable with small
local models: they cannot answer with prose or a half-closed JSON object.

What the model still gets wrong is judgement, not syntax -- it will happily
report "12 Best Headphones Under $200" as a product. That is what
``clean_products`` and ``deduplicate`` are for.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from langchain_core.prompts import ChatPromptTemplate

from buy_agent.models import (
    MAX_OPINIONS,
    QUALIFIERS,
    ProductList,
    SearchQuery,
    distinct_quotes,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from langchain_core.language_models import BaseChatModel
    from langchain_core.runnables import Runnable

    from buy_agent.models import Product
    from buy_agent.search import SearchResult

logger = logging.getLogger(__name__)

QUERY_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You turn a shopper's request into one web search query that will surface "
            "actual products for sale with prices and reviews.\n"
            "Keep the shopper's constraints (budget, brand, size, use case). "
            "Add words like 'price' or 'review' when useful. "
            "Do not add constraints the shopper never mentioned. "
            "Answer with the query only, no explanation.",
        ),
        ("human", "Shopper's request: {request}"),
    ]
)

EXTRACTION_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
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
            "report must appear in the search results below.",
        ),
        (
            "human",
            "Shopper's request: {request}\n\nSearch results:\n\n{results}",
        ),
    ]
)

#: Article headlines the model mistakes for products. A real listing is named after
#: a model ("Sony WH-1000XM5"), never after the page it was found on.
_NOT_A_PRODUCT = re.compile(
    r"""
      ^\s*(the\s+)?(\d+\s+)?(best|top|cheapest|worst|greatest)\b   # "12 Best ...", "Best ..."
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

#: Longer than any real model name. Article titles run long.
_MAX_NAME_LENGTH = 80

#: Words that describe a product without identifying it. Two names differing only
#: by these are one product ("Sony WH-CH720N" / "Sony WH-CH720N Wireless
#: Headphones"); a difference of anything else is not ("AirPods" / "AirPods Pro").
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


def build_query_chain(llm: BaseChatModel) -> Runnable:
    """Chain: ``{"request": str}`` -> :class:`SearchQuery`."""
    return QUERY_PROMPT | llm.with_structured_output(SearchQuery, method="json_schema")


def build_extraction_chain(llm: BaseChatModel) -> Runnable:
    """Chain: ``{"request", "results", "limit"}`` -> :class:`ProductList`."""
    return EXTRACTION_PROMPT | llm.with_structured_output(ProductList, method="json_schema")


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
    """Whether ``name`` reads like a product rather than the page it came from."""
    name = name.strip()
    if not name or len(name) > _MAX_NAME_LENGTH or "?" in name:
        return False
    return not _NOT_A_PRODUCT.search(name)


def clean_products(products: Sequence[Product]) -> list[Product]:
    """Tidy up names and drop entries that are articles or shops, not products."""
    kept: list[Product] = []
    for product in products:
        name = clean_name(product.name)
        if looks_like_a_product(name):
            kept.append(product.model_copy(update={"name": name}))
    dropped = len(products) - len(kept)
    if dropped:
        logger.info("Discarded %d result(s) that were pages, not products", dropped)
    return kept


def deduplicate(products: Sequence[Product], limit: int) -> list[Product]:
    """Drop repeats of the same product, keeping the most complete entry.

    Search results overlap heavily -- the same headphones show up on three sites --
    so without this the top 3 can be one product listed three times.

    One pass of :func:`merge_variants` does all of it. An exact repeat of a name
    is only the easiest case of a name differing by descriptive words: two
    spellings of one name have the same distinctive tokens, so their difference
    is empty and vacuously generic. Matching exact names first, in a pass of
    their own, would be the same merge decided by a second and subtly different
    rule about whose name survives it.

    A product whose name has nothing to identify it by -- punctuation, or
    nothing at all -- is dropped rather than kept as its own entry: it can be
    neither merged nor reported.
    """
    named = [product for product in products if product.dedup_key]
    deduped = merge_variants(named)
    dropped = len(products) - len(deduped)
    if dropped:
        logger.info("Merged %d duplicate listing(s)", dropped)
    return deduped[:limit]


def merge_variants(products: Sequence[Product]) -> list[Product]:
    """Fold together names that identify the same thing.

    Matching names exactly would miss the common case where one page calls it
    "Sony WH-CH720N" and the next "Sony WH-CH720N Noise Canceling Wireless
    Headphones", which would otherwise take two of the three reported slots.
    Names that differ by nothing at all are the same case with an empty
    difference, so this is the only pass :func:`deduplicate` needs.
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


#: Fields worth carrying over from a weaker listing, and the list to edit when
#: one is added to ``Product``. Each moves with whatever only qualifies it, by
#: :data:`~buy_agent.models.QUALIFIERS`. Grounding cannot catch an invented
#: pairing, because it runs before the merge and each half really is in the
#: sources; only the pairing is new.
#: ``opinions`` is deliberately not here: see :func:`_merge_opinions`.
_MERGEABLE_FIELDS = ("price", "rating", "seller", "url", "notes")


def _combine(first: Product, second: Product) -> Product:
    """Merge two listings for one product.

    The name and the data are decided separately: the shorter name reads better,
    while the figures come from whichever listing filled in more of them. Both
    ties go to ``first``, which is the listing that ranked higher in the search
    results -- so two spellings of one length keep the one seen first.
    """
    winner, loser = (
        (first, second) if _completeness(first) >= _completeness(second) else (second, first)
    )
    updates = _fill_gaps(winner, loser)
    updates["name"] = min(first.name, second.name, key=len)
    updates["opinions"] = _merge_opinions(winner, loser)
    return winner.model_copy(update=updates)


def _merge_opinions(winner: Product, loser: Product) -> list[str]:
    """Both listings' opinions, the winner's first, without repeats.

    The only field taken from both rather than from one. A price is a single
    fact, so two listings quoting different ones are in conflict and one has to
    win; two reviewers are not in conflict, and the shopper asking whether the
    thing is any good is better served by both of them. Nothing is invented by
    keeping both: each quote was grounded on its own before the merge, and a
    quote says who it is about by being about the product, not by sitting next
    to a figure -- which is why this needs none of the pairing care
    :data:`_MERGEABLE_FIELDS` takes.
    """
    return distinct_quotes([*winner.opinions, *loser.opinions])


def _fill_gaps(winner: Product, loser: Product) -> dict[str, object]:
    """The fields ``loser`` can contribute because ``winner`` left them blank.

    A figure travels with the words that qualify it. The loser's currency or
    review count is taken only where its price or rating is taken too, or where
    both listings quote the same one -- never grafted onto a figure the loser
    never printed it against.
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
            # Both listings printed this figure, so the loser's qualifier is
            # describing the very one being kept.
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
