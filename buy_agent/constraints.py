"""What the shopper will accept, applied to the products before they are ranked.

The request carries the shopper's terms in prose -- "wireless headphones under
$200" -- and the model keeps them when it rewrites the query, which is as far as a
search query can take them: a page is returned for matching the words, not for
obeying them. So the report could be topped by a $900 pair and read as the right
answer, ``ranking`` scoring price *relative to the candidate set* and the cheapest
of nine expensive things still scoring 1.0.

This is the other half: bounds said as numbers, checked in Python after the pages
have been read (ADR-0039). Nothing here is the model's judgement.

The one rule worth knowing is what happens to a product whose figure is *unknown*:
it is kept. A blank is the extractor having missed something or the page never
having printed it, and grounding blanks anything the sources did not back -- so
dropping blanks would reject products for the model's misses, which is what scores
missing data ``NEUTRAL`` rather than zero (ADR-0007).
"""

from __future__ import annotations

import logging
import operator
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeAlias

from buy_agent.models import comparable_price, dominant_currency

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from buy_agent.config import AgentConfig
    from buy_agent.models import Product

logger = logging.getLogger(__name__)

#: How a bound gets at the figure it judges: off the product, and off the currency
#: the run's prices are counted in, which only the price cares about.
Reader: TypeAlias = "Callable[[Product, str | None], float | None]"

#: One row per bound: the field holding it -- named the same here and on
#: :class:`~buy_agent.config.AgentConfig`, which lets ``from_config`` be a
#: comprehension -- how the figure is read off a product, what "outside" means,
#: and how it reads in the line a run logs. Everything below reads this table, so
#: a fourth bound is a row here and nothing else (ADR-0039). The price is read by
#: :func:`~buy_agent.models.comparable_price`, so a price in another currency
#: reads as unknown and passes (ADR-0043); the other two ignore the currency.
_BOUNDS: tuple[tuple[str, Reader, Callable[[float, float], bool], str], ...] = (
    ("max_price", comparable_price, operator.gt, "at most {:,.2f}"),
    ("min_rating", lambda p, _: p.rating, operator.lt, "rated at least {:g}"),
    ("min_reviews", lambda p, _: p.review_count, operator.lt, "from at least {:,} reviews"),
)


@dataclass(frozen=True, slots=True)
class Constraints:
    """The bounds a product has to be inside to be reported.

    ``None`` is "no bound", which is what all three default to.

    Attributes:
        max_price: The most the shopper will pay, read in the currency the run's
            prices are counted in (ADR-0043). Nothing is converted -- a rate table is
            not this project's to ship, and a stale rate is a wrong answer wearing a
            right one's clothes -- so a price in another currency is not held against
            this at all: it is unplaceable, and passes the way an unknown one does.
        min_rating: The lowest average review score worth reporting, on the 0-5 scale
            ``Product.rating`` is in.
        min_reviews: How many reviews a rating has to be averaged over. A 5.0 from two
            people is not a rating, and the ranking already discounts it -- this
            refuses it outright.
    """

    max_price: float | None = None
    min_rating: float | None = None
    min_reviews: int | None = None

    @classmethod
    def from_config(cls, config: AgentConfig) -> Constraints:
        """The three bounds a run was configured with, off the config that holds them.

        They live on :class:`~buy_agent.config.AgentConfig` as three plain fields rather
        than as one of these, because that is what :data:`~buy_agent.config.LIMITS` bounds
        and what both front doors fill in -- one field, one flag, one form box.
        """
        return cls(**{name: getattr(config, name) for name, *_ in _BOUNDS})

    @property
    def given(self) -> bool:
        """Whether the shopper set any of them.

        The difference between "nothing was asked for" and "everything passed" is worth
        keeping: only the second is worth a line in the report.
        """
        # Every row ``_set`` yields is a non-empty tuple, so the rows themselves
        # are the truthy thing to ask about.
        return any(self._set())

    def admits(self, product: Product, currency: str | None = None) -> bool:
        """Whether this product is inside every bound that was set.

        A figure the run does not know passes: see the module docstring. So the test is
        "known *and* outside", never "not inside". ``currency`` is what the run's prices
        are counted in, and a price in another one is a figure this run does not know
        (ADR-0043).
        """
        return not any(
            (figure := read(product, currency)) is not None and outside(figure, bound)
            for read, bound, outside, _ in self._set()
        )

    def describe(self, currency: str | None = None) -> str:
        """The bounds as one phrase, for the line the run logs about them.

        Only the ones that were set, in the order :data:`_BOUNDS` declares them. A budget
        is named with the currency it was read in, that being the part nobody typed: the
        number came from the shopper and the currency from whatever the pages printed.
        """
        # The budget is the one bound whose figure carries a unit, so its reader
        # being ``comparable_price`` is what identifies it -- no extra column.
        unit = f" {currency}" if currency else ""
        return ", ".join(
            phrase.format(bound) + (unit if read is comparable_price else "")
            for read, bound, _, phrase in self._set()
        )

    def apply(self, products: Sequence[Product]) -> list[Product]:
        """The products inside the bounds, and a line saying how many were not.

        Silence is the failure mode this guards against: a run that quietly reports two
        products because seven were over budget looks exactly like a run that only found
        two, and the second is a reason to search differently. So the count goes out
        whenever bounds were set, even where everything passed -- "10 of 10" says the
        bound did nothing. Given no bounds this is the products, unexamined and
        unremarked.
        """
        if not self.given:
            return list(products)

        inside, currency = self._settled(products)
        held = frozenset(inside)
        kept = [products[index] for index in inside]
        excluded = [
            product.name
            for index, product in enumerate(products)
            if index not in held
        ]

        if excluded:
            # The names at DEBUG under the count, as everywhere a product is
            # removed: "why is the one I had in mind not in there?" is what a
            # bound provokes.
            logger.debug(
                "Outside the limits: %s", ", ".join(repr(name) for name in excluded)
            )
        logger.log(
            # Nothing left is worth interrupting for: the run found products and
            # is about to report none of them, which an empty web looks like too.
            logging.WARNING if not kept else logging.INFO,
            "%d of %d product(s) are within the limits (%s)",
            len(kept),
            len(products),
            self.describe(currency),
        )
        return kept

    def _settled(self, products: Sequence[Product]) -> tuple[list[int], str | None]:
        """Which products are inside the bounds, by index, and in which currency.

        The currency is a fact about the set (ADR-0043) and this is a function that
        *changes* the set, which is the whole of why it is asked more than once. Removing
        every product of the commonest currency leaves the survivors counted in another
        one, and that one is what the report, the ranking and the cart are then all in --
        so a budget read once, before the filtering, would be a budget applied in a
        currency nothing that survived it was ever held to: "at most 92.00 USD" logged
        over a report of euros, one of them at 95.

        So the bound is re-read against the set it is leaving behind until the two agree.
        Each pass keeps a subset of the pass before it, and a pass that removes nothing
        is the fixed point -- which is also the first pass for the runs that have one
        currency, this costing them a second comparison and nothing else.
        """
        inside = list(range(len(products)))
        while True:
            currency = dominant_currency(products[index] for index in inside)
            kept = [index for index in inside if self.admits(products[index], currency)]
            # Nothing left settles nothing -- an empty set is counted in no currency
            # at all -- so the answer is the currency that emptied it, which is the
            # one the line the shopper reads has to name: "0 of 2 within the limits
            # (at most 1.00)" leaves out the half of the bound nobody typed.
            if not kept or len(kept) == len(inside):
                return kept, currency
            inside = kept

    def _set(self) -> Iterator[tuple[Reader, float, Callable[[float, float], bool], str]]:
        """The rows of :data:`_BOUNDS` the shopper actually gave a number for."""
        for name, read, outside, phrase in _BOUNDS:
            bound = getattr(self, name)
            if bound is not None:
                yield read, bound, outside, phrase
