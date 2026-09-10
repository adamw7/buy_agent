"""Scoring and sorting. Deliberately plain Python — no LLM involved.

The model is good at reading prices off a page and bad at arithmetic, so the
ordering is decided here where it is deterministic and testable.
"""

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

#: Products with no data on a criterion score mid-field rather than last, so a
#: listing that simply did not publish a rating is not buried by one that did.
NEUTRAL = 0.5

#: The criteria a score is blended from, in the order they are weighted -- the
#: field names of both :class:`RankingWeights` and
#: :class:`~buy_agent.models.ScoreParts`, so a weight is looked up beside its
#: share.
CRITERIA: tuple[str, ...] = ("rating", "popularity", "price")


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
        """Each criterion's share of the blend, by name, adding up to one.

        The weights as a reader needs them rather than as they were written: 0.5 out of
        1.0 and 5 out of 10 weigh the same. Without them a breakdown cannot be read at all
        -- "rating 0.94, price 1.00" beside a score of 0.96 invites adding three numbers
        that were never meant to be added (ADR-0041). Zero throughout for weights totalling
        nothing, which is the run ``score_product`` scores 0.0 for.
        """
        total = self.total
        return {
            name: (getattr(self, name) / total if total else 0.0)
            for name in CRITERIA
        }


def _price_share(
    placed: float | None, cheapest: float | None, priciest: float | None
) -> float | None:
    """Where ``placed`` sits between the cheapest and the priciest of the set.

    ``None`` means *nothing was read*, which is what puts a criterion in
    :attr:`~buy_agent.models.ScoreParts.neutral` and prints it "assumed": a price
    nobody published, or one in a currency this run cannot place (ADR-0043).

    :data:`NEUTRAL` is the other answer, and deliberately not the same thing. A set
    with one distinct price has a price that *was* read and simply does not separate
    anything, so it is returned as the 0.5 it scores rather than as an assumption: a
    one-product run said "price assumed" over a figure grounding had backed, which is
    the one thing ``neutral`` exists to tell apart (ADR-0041).

    ``cheapest`` and ``priciest`` are ``None`` only where no product has a placeable
    price, and then ``placed`` is ``None`` too -- so they are asked after it.
    """
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
    """Score one product in ``[0, 1]`` relative to the rest of the candidate set.

    Price is relative rather than absolute: the cheapest in the set gets 1.0, the most
    expensive 0.0, and with one distinct price everything ties at ``NEUTRAL``.

    ``currency`` is the one the set's prices are compared in; a product priced in
    another is not on that scale, so its price scores ``NEUTRAL`` and says so rather
    than being read as the number it happens to be (ADR-0043). ``None`` is "nothing
    says these are different currencies".

    The three shares come back beside the blend rather than being added up and
    forgotten (ADR-0041). Without them a report can say what a product scored but not
    what it scored *on*: ``NEUTRAL`` is what a product with no rating gets and also
    what a thoroughly average one gets, and ``neutral`` is what names the difference.
    """
    # ``None`` is "nothing was read", turned into ``NEUTRAL`` once below rather
    # than by testing a share against 0.5: a product priced mid-way through the
    # set scores that on the evidence -- see :func:`_price_share`.
    placed = comparable_price(product, currency)
    read = {
        "rating": None if product.rating is None else product.rating / 5,
        # log10 so the 10th review counts for far more than the 10_000th;
        # saturates at 1_000. Asked whether the count is *positive* and not merely
        # truthy: a re-sort scores whatever products a request carried (ADR-0035),
        # and ``-5`` reviews took ``log10`` outside its domain.
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
    """One product on its way through :func:`rank_products`, mid-sort.

    A name per part rather than a bare triple: the three sort keys below reach into it
    three different ways. ``price`` is :func:`~buy_agent.models.comparable_price`'s
    answer and not the product's own -- ``None`` for a price this run cannot place as
    well as for one nobody published -- which is what a price sort sinks.
    """

    product: Product
    price: float | None
    parts: ScoreParts


def rank_products(
    products: Sequence[Product],
    *,
    weights: RankingWeights | None = None,
    sort_by: SortBy = "score",
) -> list[RankedProduct]:
    """Sort products best-first and attach the score and 1-based rank.

    ``sort_by="price"`` sorts cheapest first and ``"rating"`` highest first; either way
    products missing that field sink to the bottom rather than being dropped -- and a
    price in a currency this set is not counted in sinks with them (ADR-0043).
    """
    weights = weights or RankingWeights()
    # The scale is the run's own currency and the prices on it: one price in yen
    # would otherwise put every dollar price at the cheap end of a range five
    # orders of magnitude wide (ADR-0043). A fact about the set, so worked out
    # here and passed down.
    currency = dominant_currency(products)
    prices = [comparable_price(product, currency) for product in products]
    on_the_scale = [price for price in prices if price is not None]
    cheapest = min(on_the_scale) if on_the_scale else None
    priciest = max(on_the_scale) if on_the_scale else None

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
