"""Scoring and sorting. Deliberately plain Python — no LLM involved."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, NamedTuple

from buy_agent.models import (
    RankedProduct,
    ScoreParts,
    comparable_price,
    dominant_currency,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from buy_agent.models import Product

SortBy = Literal["score", "price", "rating"]

#: Products with no data on a criterion score mid-field rather than last, so a listing
#: that simply did not publish a rating is not buried by one that did.
NEUTRAL = 0.5

#: The criteria a score is blended from, in the order they are weighted -- the field
#: names of both :class:`RankingWeights` and :class:`~buy_agent.models.ScoreParts`, so a
#: weight is looked up beside its share.
CRITERIA: tuple[str, ...] = ("rating", "popularity", "price")

#: What each :data:`SortBy` puts first, said as the ordering rather than as the field.
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
        """Each criterion's share of the blend, by name, adding up to one (ADR-0041)."""
        total = self.total
        return {
            name: (getattr(self, name) / total if total else 0.0)
            for name in CRITERIA
        }


def _price_share(
    placed: float | None, cheapest: float | None, priciest: float | None
) -> float | None:
    """Where ``placed`` sits between the cheapest and the priciest of the set (ADR-0043,
    ADR-0041)."""
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
    """Score one product in ``[0, 1]`` relative to the rest of the candidate set
    (ADR-0043, ADR-0041)."""
    # ``None`` is "nothing was read", turned into ``NEUTRAL`` once below rather than by
    # testing a share against 0.5: a product priced mid-way through the set scores that
    # on the evidence -- see :func:`_price_share`.
    placed = comparable_price(product, currency)
    read = {
        "rating": None if product.rating is None else product.rating / 5,
        # log10 so the 10th review counts for far more than the 10_000th; saturates at
        # 1_000 (ADR-0035).
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
    """One product on its way through :func:`rank_products`, mid-sort."""

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
    """Sort products best-first and attach the score and 1-based rank (ADR-0043).

    ``currency`` is the scale the shopper named, where they named one; left out, the set
    votes on its own as it always did (ADR-0056).
    """
    weights = weights or RankingWeights()
    # The scale is the run's own currency and the prices on it (ADR-0043).
    currency = dominant_currency(products, currency)
    prices = [comparable_price(product, currency) for product in products]
    on_the_scale = [price for price in prices if price is not None]
    # ``default`` rather than a guard apiece: a set with nothing placeable in it has no
    # ends, which is the ``None`` ``_price_share`` is asked after.
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
        RankedProduct(product=item.product, breakdown=item.parts, rank=index)
        for index, item in enumerate(scored, start=1)
    ]
