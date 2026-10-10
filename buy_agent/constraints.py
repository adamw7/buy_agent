"""The shopper's bounds, applied before ranking (ADR-0039, ADR-0007)."""

from __future__ import annotations

import logging
import operator
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeAlias

from buy_agent.models import Removal, comparable_price, dominant_currency, nothing_recorded

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from buy_agent.config import AgentConfig
    from buy_agent.models import Product, Recorder

logger = logging.getLogger(__name__)

#: A bound's figure off a product, given the run's currency (price uses it).
Reader: TypeAlias = "Callable[[Product, str | None], float | None]"

#: Per bound: its config field, reader, what "outside" means, and its phrase (ADR-0039).
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
    #: The budget's currency when the shopper named one (ADR-0056); not a bound.
    currency: str | None = None

    @classmethod
    def from_config(cls, config: AgentConfig) -> Constraints:
        return cls(
            **{name: getattr(config, name) for name, *_ in _BOUNDS},
            currency=config.currency or None,
        )

    @property
    def given(self) -> bool:
        return any(self._set())

    def admits(self, product: Product, currency: str | None = None) -> bool:
        """Whether this product is inside every bound set; an unknown figure is."""
        return not any(
            (figure := read(product, currency)) is not None and outside(figure, bound)
            for read, bound, outside, _ in self._set()
        )

    def describe(self, currency: str | None = None) -> str:
        """The bounds as one phrase; the budget, read by ``comparable_price``, has a unit."""
        unit = f" {currency}" if currency else ""
        return ", ".join(
            phrase.format(bound) + (unit if read is comparable_price else "")
            for read, bound, _, phrase in self._set()
        )

    def apply(
        self, products: Sequence[Product], *, record: Recorder = nothing_recorded
    ) -> list[Product]:
        """The products inside the bounds, saying how many were not (ADR-0055)."""
        if not self.given:
            return list(products)

        inside, currency = self._settled(products)
        held = frozenset(inside)
        kept = [products[index] for index in inside]
        excluded = [item.name for index, item in enumerate(products) if index not in held]

        # The log line's phrase, so panel and progress agree.
        reason = f"Outside the limits you set ({self.describe(currency)})."
        for name in excluded:
            record(Removal(name=name, step="limits", reason=reason))

        if excluded:
            logger.debug("Outside the limits: %s", ", ".join(repr(name) for name in excluded))
        logger.log(
            # An empty result would otherwise look like an empty web.
            logging.WARNING if not kept else logging.INFO,
            "%d of %d product(s) are within the limits (%s)",
            len(kept),
            len(products),
            self.describe(currency),
        )
        return kept

    def _settled(self, products: Sequence[Product]) -> tuple[list[int], str | None]:
        """Which products are inside the bounds, by index, and in which currency: judged
        again until the currency of what is left stops moving (ADR-0043)."""
        inside = list(range(len(products)))
        judged: str | None = None
        while True:
            # What is left may vote for none, and keeps the one the rest were judged in.
            currency = (
                dominant_currency((products[index] for index in inside), self.currency)
                or judged
            )
            kept = [index for index in inside if self.admits(products[index], currency)]
            # An empty set reports the currency that emptied it, for the log to name.
            if not kept or len(kept) == len(inside):
                return kept, currency
            inside, judged = kept, currency

    def _set(self) -> Iterator[tuple[Reader, float, Callable[[float, float], bool], str]]:
        """The rows of :data:`_BOUNDS` the shopper gave a number for."""
        for name, read, outside, phrase in _BOUNDS:
            bound = getattr(self, name)
            if bound is not None:
                yield read, bound, outside, phrase
