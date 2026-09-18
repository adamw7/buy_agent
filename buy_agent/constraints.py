"""What the shopper will accept, applied to the products before they are ranked (ADR-0039,
ADR-0007)."""

from __future__ import annotations

import logging
import operator
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeAlias

from buy_agent.models import (
    Removal,
    comparable_price,
    dominant_currency,
    nothing_recorded,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from buy_agent.config import AgentConfig
    from buy_agent.models import Product, Recorder

logger = logging.getLogger(__name__)

#: How a bound gets at the figure it judges: off the product, and off the currency the
#: run's prices are counted in, which only the price cares about.
Reader: TypeAlias = "Callable[[Product, str | None], float | None]"

#: One row per bound: the field holding it -- named the same here and on
#: :class:`~buy_agent.config.AgentConfig`, which lets ``from_config`` be a comprehension
#: -- how the figure is read off a product, what "outside" means, and how it reads in
#: the line a run logs (ADR-0039, ADR-0043).
_BOUNDS: tuple[tuple[str, Reader, Callable[[float, float], bool], str], ...] = (
    ("max_price", comparable_price, operator.gt, "at most {:,.2f}"),
    ("min_rating", lambda p, _: p.rating, operator.lt, "rated at least {:g}"),
    ("min_reviews", lambda p, _: p.review_count, operator.lt, "from at least {:,} reviews"),
)


@dataclass(frozen=True, slots=True)
class Constraints:
    """The bounds a product has to be inside to be reported (ADR-0043)."""

    max_price: float | None = None
    min_rating: float | None = None
    min_reviews: int | None = None

    @classmethod
    def from_config(cls, config: AgentConfig) -> Constraints:
        """The three bounds a run was configured with, off the config that holds them."""
        return cls(**{name: getattr(config, name) for name, *_ in _BOUNDS})

    @property
    def given(self) -> bool:
        """Whether the shopper set any of them."""
        # Every row ``_set`` yields is a non-empty tuple, so the rows themselves are the
        # truthy thing to ask about.
        return any(self._set())

    def admits(self, product: Product, currency: str | None = None) -> bool:
        """Whether this product is inside every bound that was set (ADR-0043)."""
        return not any(
            (figure := read(product, currency)) is not None and outside(figure, bound)
            for read, bound, outside, _ in self._set()
        )

    def describe(self, currency: str | None = None) -> str:
        """The bounds as one phrase, for the line the run logs about them."""
        # The budget is the one bound whose figure carries a unit, so its reader being
        # ``comparable_price`` is what identifies it -- no extra column.
        unit = f" {currency}" if currency else ""
        return ", ".join(
            phrase.format(bound) + (unit if read is comparable_price else "")
            for read, bound, _, phrase in self._set()
        )

    def apply(
        self, products: Sequence[Product], *, record: Recorder = nothing_recorded
    ) -> list[Product]:
        """The products inside the bounds, and a line saying how many were not (ADR-0055).
        """
        if not self.given:
            return list(products)

        inside, currency = self._settled(products)
        held = frozenset(inside)
        kept = [products[index] for index in inside]
        excluded = [item.name for index, item in enumerate(products) if index not in held]

        for name in excluded:
            # The bounds as the shopper set them, in the currency they were settled in:
            # the same phrase the logged line carries, so the panel and the progress
            # cannot say two different things about one number (ADR-0043).
            record(
                Removal(
                    name=name,
                    step="limits",
                    reason=f"Outside the limits you set ({self.describe(currency)}).",
                )
            )

        if excluded:
            # The names at DEBUG under the count, as everywhere a product is removed:
            # "why is the one I had in mind not in there?" is what a bound provokes.
            logger.debug(
                "Outside the limits: %s", ", ".join(repr(name) for name in excluded)
            )
        logger.log(
            # Nothing left is worth interrupting for: the run found products and is
            # about to report none of them, which an empty web looks like too.
            logging.WARNING if not kept else logging.INFO,
            "%d of %d product(s) are within the limits (%s)",
            len(kept),
            len(products),
            self.describe(currency),
        )
        return kept

    def _settled(self, products: Sequence[Product]) -> tuple[list[int], str | None]:
        """Which products are inside the bounds, by index, and in which currency
        (ADR-0043)."""
        inside = list(range(len(products)))
        while True:
            currency = dominant_currency(products[index] for index in inside)
            kept = [index for index in inside if self.admits(products[index], currency)]
            # Nothing left settles nothing -- an empty set is counted in no currency at
            # all -- so the answer is the currency that emptied it, which is the one the
            # line the shopper reads has to name: "0 of 2 within the limits (at most
            # 1.00)" leaves out the half of the bound nobody typed.
            if not kept or len(kept) == len(inside):
                return kept, currency
            inside = kept

    def _set(self) -> Iterator[tuple[Reader, float, Callable[[float, float], bool], str]]:
        """The rows of :data:`_BOUNDS` the shopper actually gave a number for."""
        for name, read, outside, phrase in _BOUNDS:
            bound = getattr(self, name)
            if bound is not None:
                yield read, bound, outside, phrase
