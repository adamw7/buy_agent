"""The two LLM steps (query, then products) and the clean-up after them (ADR-0004,
ADR-0038)."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from buy_agent.chat import Chain, Prompt
from buy_agent.models import (
    MAX_OPINIONS,
    QUALIFIERS,
    Offer,
    ProductList,
    Removal,
    SearchQuery,
    distinct_quotes,
    nothing_recorded,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from buy_agent.chat import ChatModel
    from buy_agent.models import Opinion, Product, Recorder
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

#: The words a roundup ranks with (shared with :mod:`buy_agent.verification`).
SUPERLATIVES = r"(?:best|top|cheapest|worst|greatest)"

#: A name opening on a superlative: "12 Best ...", "The 5 Best ...", "Top ...".
_SUPERLATIVE = re.compile(rf"^\s*(the\s+)?(\d+\s+)?{SUPERLATIVES}\b", re.IGNORECASE)

#: Article headlines the model mistakes for products.
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
    r"\s*[-|:,]?\s*\b(reviews?|prices?|deals?|on sale|tested|hands[- ]on)\b\s*$",
    re.IGNORECASE,
)

#: A token carrying both letters and digits, as a model number does: "WH-1000XM5" has
#: "1000xm5".
_MODEL_NUMBER = re.compile(r"\b(?=[a-z0-9]*[a-z])(?=[a-z0-9]*\d)[a-z0-9]+\b", re.IGNORECASE)

#: Longer than any real model name. Article titles run long.
_MAX_NAME_LENGTH = 80

#: Words that describe a product without identifying it.
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

#: How a name splits into words; shared so merging and grounding agree.
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
    """Strip the page furniture models copy along with a product name."""
    name = _SITE_SUFFIX.split(name.strip(), maxsplit=1)[0]
    name = _TRAILING_NOISE.sub("", name)
    return name.strip(" -|:,").strip()


def looks_like_a_product(name: str) -> bool:
    """Whether ``name`` reads like a product rather than the page it came from."""
    name = name.strip()
    if not name or len(name) > _MAX_NAME_LENGTH or "?" in name:
        return False
    if not _NOT_A_PRODUCT.search(name):
        return True
    if not _SUPERLATIVE.match(name):
        return False
    # Past the superlative and the next word (a category, or a brand).
    tail = _SUPERLATIVE.sub("", name, count=1).split(maxsplit=1)
    return len(tail) > 1 and bool(_MODEL_NUMBER.search(tail[1]))


def clean_products(
    products: Sequence[Product], *, record: Recorder = nothing_recorded
) -> list[Product]:
    """Tidy up names and drop entries that are articles or shops, not products
    (ADR-0055)."""
    kept: list[Product] = []
    discarded: list[str] = []
    for product in products:
        name = clean_name(product.name)
        if looks_like_a_product(name):
            kept.append(product.model_copy(update={"name": name}))
        else:
            discarded.append(name or product.name)
            reason = "Reads as an article or a shop, not a product."
            record(Removal(name=discarded[-1], step="clean", reason=reason))
    if discarded:
        # Count at INFO, names at DEBUG.
        logger.info("Discarded %d result(s) that were pages, not products", len(discarded))
        logger.debug(
            "Discarded as pages, not products: %s", ", ".join(repr(n) for n in discarded)
        )
    return kept


def deduplicate(
    products: Sequence[Product], limit: int, *, record: Recorder = nothing_recorded
) -> list[Product]:
    """Drop repeats of the same product, keeping the most complete entry (ADR-0055,
    ADR-0058)."""
    # ``dedup_key`` is costly, so it is read once per product.
    keyed: dict[bool, list[Product]] = {True: [], False: []}
    for product in products:
        keyed[bool(product.dedup_key)].append(product)
    named = [_as_a_listing(product) for product in keyed[True]]
    nameless = keyed[False]
    for product in nameless:
        reason = "The name identifies nothing."
        record(Removal(name=product.name, step="deduplicate", reason=reason))
    if nameless:
        # Count at INFO, names at DEBUG.
        logger.info("Dropped %d result(s) whose name identifies nothing", len(nameless))
        logger.debug(
            "Nothing to identify them by: %s",
            ", ".join(repr(product.name) for product in nameless),
        )
    deduped = merge_variants(named, record=record)
    merged = len(named) - len(deduped)
    if merged:
        logger.info("Merged %d duplicate listing(s)", merged)
    return deduped[:limit]


def _as_a_listing(product: Product) -> Product:
    """One grounded listing, carrying its price as its one offer (ADR-0058).

    Seeded here: after ``ground``, and before merging folds listings together.
    """
    if product.price is None:
        return product
    return product.model_copy(
        update={
            "offers": [
                Offer(
                    price=product.price,
                    currency=product.currency,
                    seller=product.seller,
                    url=product.url,
                )
            ]
        }
    )


def merge_variants(
    products: Sequence[Product], *, record: Recorder = nothing_recorded
) -> list[Product]:
    """Fold together names that identify the same thing (ADR-0055)."""
    merged: list[Product] = []
    for product in products:
        for index, existing in enumerate(merged):
            if _same_product(existing.name, product.name):
                # The shorter name survives; the other leaves the report.
                logger.debug("Folded %r together with %r", existing.name, product.name)
                merged[index] = _combine(existing, product)
                # After the merge: ``_combine`` picks the survivor.
                kept = merged[index].name
                gone = product.name if kept != product.name else existing.name
                if gone != kept:
                    reason = f"Folded into {kept}, which names the same thing."
                    record(Removal(name=gone, step="merge", reason=reason))
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


#: Fields a weaker listing may fill in (ADR-0022). ``opinions`` and ``offers`` never
#: conflict, so both are kept whole by merges of their own (ADR-0042, ADR-0058).
_MERGEABLE_FIELDS = ("price", "rating", "seller", "url", "notes")


def _combine(first: Product, second: Product) -> Product:
    """Merge two listings for one product."""
    winner, loser = (
        (first, second) if _completeness(first) >= _completeness(second) else (second, first)
    )
    updates = _fill_gaps(winner, loser)
    updates["name"] = min(first.name, second.name, key=len)
    updates["opinions"] = _merge_opinions(winner, loser)
    updates["offers"] = _merge_offers(winner, loser)
    return winner.model_copy(update=updates)


def _merge_opinions(winner: Product, loser: Product) -> list[Opinion]:
    """Both listings' opinions, the winner's first, without repeats (ADR-0042)."""
    return distinct_quotes([*winner.opinions, *loser.opinions])


def _merge_offers(winner: Product, loser: Product) -> list[Offer]:
    """Both listings' offers, the winner's first, without repeats (ADR-0058). The
    headline price stays the winner's."""
    seen: dict[tuple[float, str | None, str | None, str | None], Offer] = {}
    for offer in (*winner.offers, *loser.offers):
        seen.setdefault((offer.price, offer.currency, offer.seller, offer.url), offer)
    return list(seen.values())


def _fill_gaps(winner: Product, loser: Product) -> dict[str, object]:
    """The fields ``loser`` can contribute because ``winner`` left them blank."""
    updates: dict[str, object] = {}
    for figure in _MERGEABLE_FIELDS:
        qualifiers = QUALIFIERS.get(figure, ())
        ours, theirs = getattr(winner, figure), getattr(loser, figure)
        if ours is None and theirs is not None:
            # The loser's figure moves with its qualifiers (ADR-0022).
            updates[figure] = theirs
            updates.update({name: getattr(loser, name) for name in qualifiers})
        elif ours is not None and ours == theirs:
            # Same figure on both, so the loser's qualifiers describe it too.
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
