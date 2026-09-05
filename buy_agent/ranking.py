"""Scoring and sorting. Deliberately plain Python — no LLM involved.

The model is good at reading prices off a page and bad at arithmetic, so the
ordering is decided here where it is deterministic and testable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from buy_agent.models import RankedProduct, ScoreParts

if TYPE_CHECKING:
    from collections.abc import Sequence

    from buy_agent.models import Product

SortBy = Literal["score", "price", "rating"]

#: Products with no data on a criterion score mid-field rather than last, so a
#: listing that simply did not publish a rating is not buried by one that did.
NEUTRAL = 0.5


@dataclass(frozen=True, slots=True)
class RankingWeights:
    """How much each criterion contributes to the final score."""

    rating: float = 0.5
    popularity: float = 0.2
    price: float = 0.3

    @property
    def total(self) -> float:
        return self.rating + self.popularity + self.price


def score_product(
    product: Product,
    *,
    cheapest: float | None,
    priciest: float | None,
    weights: RankingWeights,
) -> ScoreParts:
    """Score one product in ``[0, 1]`` relative to the rest of the candidate set.

    Price is relative rather than absolute: the cheapest in the set gets 1.0, the
    most expensive 0.0, and with one distinct price everything ties at ``NEUTRAL``.

    The three shares come back beside the blend rather than being added up and
    forgotten (ADR-0041). They cost nothing -- they are what the blend was made of
    -- and without them a report can say what a product scored but not what it
    scored *on*, nor which criteria it scored on at all: ``NEUTRAL`` is what a
    product with no rating gets and also what a thoroughly average one gets, so
    the two are one number until something names the difference. ``neutral`` is
    that name, and the one thing here nothing else could work out afterwards.
    """
    # ``None`` is "nothing was read", which is what tells a criterion apart from
    # one that scored 0.5 on the evidence -- so it is carried as its own value and
    # turned into ``NEUTRAL`` once, below, rather than by testing a share against
    # 0.5 afterwards. A product priced exactly mid-way through the set really does
    # score 0.5 on price, and that is the case this whole field exists to tell
    # apart from a product nobody priced.
    read = {
        "rating": None if product.rating is None else product.rating / 5,
        # log10 so the 10th review counts for far more than the 10_000th;
        # saturates at 1_000 reviews, past which extra reviews say nothing new.
        "popularity": (
            min(1.0, math.log10(product.review_count + 1) / 3)
            if product.review_count
            else None
        ),
        # The last clause is a set with one distinct price in it, where nothing
        # separates any product from any other.
        "price": (
            None
            if product.price is None or cheapest is None or priciest is None
            or priciest <= cheapest
            else (priciest - product.price) / (priciest - cheapest)
        ),
    }
    shares = {name: NEUTRAL if share is None else share for name, share in read.items()}
    weighted = sum(getattr(weights, name) * share for name, share in shares.items())
    return ScoreParts(
        **shares,
        total=weighted / weights.total if weights.total else 0.0,
        neutral=[name for name, share in read.items() if share is None],
    )


def rank_products(
    products: Sequence[Product],
    *,
    weights: RankingWeights | None = None,
    sort_by: SortBy = "score",
) -> list[RankedProduct]:
    """Sort products best-first and attach the score and 1-based rank.

    ``sort_by="price"`` sorts cheapest first and ``"rating"`` highest first; either
    way products missing that field sink to the bottom rather than being dropped.
    """
    weights = weights or RankingWeights()
    prices = [p.price for p in products if p.price is not None]
    cheapest = min(prices) if prices else None
    priciest = max(prices) if prices else None

    scored = [
        (
            product,
            score_product(product, cheapest=cheapest, priciest=priciest, weights=weights),
        )
        for product in products
    ]

    if sort_by == "price":
        scored.sort(key=lambda item: (item[0].price is None, item[0].price or 0.0))
    elif sort_by == "rating":
        scored.sort(key=lambda item: (item[0].rating is None, -(item[0].rating or 0.0)))
    else:
        # Name breaks ties, so equal scores come out in a reproducible order.
        scored.sort(key=lambda item: (-item[1].total, item[0].name.lower()))

    return [
        RankedProduct(product=product, breakdown=parts, rank=index)
        for index, (product, parts) in enumerate(scored, start=1)
    ]
