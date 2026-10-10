"""Scoring and sorting. Deliberately plain Python — no LLM involved."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, NamedTuple

from buy_agent.models import RankedProduct, ScoreParts, comparable_price, dominant_currency

if TYPE_CHECKING:
    from collections.abc import Sequence

    from buy_agent.models import Product

SortBy = Literal["score", "price", "rating"]

#: What a missing figure scores: mid-field, not last (ADR-0007).
NEUTRAL = 0.5

#: Fields of both :class:`RankingWeights` and :class:`~buy_agent.models.ScoreParts`.
CRITERIA: tuple[str, ...] = ("rating", "popularity", "price")

ORDERINGS: dict[SortBy, str] = {
    "score": "best score first",
    "price": "cheapest first",
    "rating": "best rated first",
}


@dataclass(frozen=True, slots=True)
class RankingWeights:
    """How much each criterion contributes to the final score."""

    rating: float = 0.5
    popularity: float = 0.2
    price: float = 0.3

    @property
    def total(self) -> float:
        return self.rating + self.popularity + self.price

    @property
    def fractions(self) -> dict[str, float]:
        """Each criterion's share of the blend, adding up to one (ADR-0041)."""
        total = self.total
        return {name: (getattr(self, name) / total if total else 0.0) for name in CRITERIA}


def _price_share(
    placed: float | None, cheapest: float | None, priciest: float | None
) -> float | None:
    """1 for the cheapest of the set, 0 for the priciest."""
    if placed is None:
        return None
    if cheapest is None or priciest is None or priciest <= cheapest:
        return NEUTRAL
    return (priciest - placed) / (priciest - cheapest)


def score_product(
    product: Product,
    *,
    cheapest: float | None,
    priciest: float | None,
    weights: RankingWeights,
    currency: str | None = None,
) -> ScoreParts:
    """One product's score in ``[0, 1]`` against the set (ADR-0041, ADR-0043)."""
    # ``None`` means unread; a measured share may equal 0.5.
    placed = comparable_price(product, currency)
    read = {
        "rating": None if product.rating is None else product.rating / 5,
        # log10: the 10th review counts for far more than the 10_000th; saturates at 1_000.
        "popularity": (
            min(1.0, math.log10(product.review_count + 1) / 3)
            if product.review_count and product.review_count > 0
            else None
        ),
        "price": _price_share(placed, cheapest, priciest),
    }
    shares = {name: NEUTRAL if share is None else share for name, share in read.items()}
    weighted = sum(getattr(weights, name) * share for name, share in shares.items())
    return ScoreParts(
        **shares,
        total=weighted / weights.total if weights.total else 0.0,
        neutral=[name for name, share in read.items() if share is None],
    )


class _Scored(NamedTuple):
    """One product mid-sort."""

    product: Product
    price: float | None
    parts: ScoreParts


def rank_products(
    products: Sequence[Product],
    *,
    weights: RankingWeights | None = None,
    sort_by: SortBy = "score",
    currency: str | None = None,
) -> list[RankedProduct]:
    """Products best-first with score and 1-based rank; ``currency`` overrides the
    set's vote (ADR-0056)."""
    weights = weights or RankingWeights()
    currency = dominant_currency(products, currency)
    prices = [comparable_price(product, currency) for product in products]
    on_the_scale = [price for price in prices if price is not None]
    cheapest = min(on_the_scale, default=None)
    priciest = max(on_the_scale, default=None)

    scored = [
        _Scored(
            product,
            price,
            score_product(
                product,
                cheapest=cheapest,
                priciest=priciest,
                weights=weights,
                currency=currency,
            ),
        )
        for product, price in zip(products, prices, strict=True)
    ]

    if sort_by == "price":
        scored.sort(key=lambda item: (item.price is None, item.price or 0.0))
    elif sort_by == "rating":
        scored.sort(
            key=lambda item: (item.product.rating is None, -(item.product.rating or 0.0))
        )
    else:
        # Name breaks ties, so equal scores come out in a reproducible order.
        scored.sort(key=lambda item: (-item.parts.total, item.product.name.lower()))

    return [
        RankedProduct(product=item.product, breakdown=item.parts, rank=index, scale=currency)
        for index, item in enumerate(scored, start=1)
    ]


def scale_of(ranked: Sequence[RankedProduct]) -> str | None:
    """The currency a ranking was counted in, read back rather than voted again: a
    vote in rank order can break a tie the other way (ADR-0056)."""
    return ranked[0].scale if ranked else None
