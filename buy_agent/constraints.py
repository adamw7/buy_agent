"""What the shopper will accept, applied to the products before they are ranked.

The request carries the shopper's terms in prose -- "wireless headphones under
$200" -- and the model is asked to keep them when it rewrites the query
(:mod:`buy_agent.extraction`), which is as far as a search query can take them: a
page is returned for matching the words, not for obeying them. So the report
could be topped by a $900 pair, and it read as the right answer, because
``ranking`` scores price *relative to the candidate set* and the cheapest of nine
expensive things still scores 1.0.

This is the other half: bounds said as numbers rather than as prose, checked in
Python after the pages have been read, so what is reported is what was asked for
(ADR-0039). Nothing here is the model's judgement -- these are three comparisons
over figures grounding has already backed.

The one rule worth knowing is what happens to a product whose figure is
*unknown*: it is kept. A blank is not a violation, it is the extractor having
missed something or the page never having printed it, and grounding blanks
anything the sources did not back -- so dropping blanks would reject products for
the model's misses, which is the same reasoning that scores missing data
``NEUTRAL`` rather than zero (ADR-0007).
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

#: One row per bound: the field holding it -- named the same on this class and on
#: :class:`~buy_agent.config.AgentConfig`, which is what lets ``from_config`` be a
#: comprehension -- how the figure it judges is read off a product, what "outside"
#: means for it, and how it reads in the line a run logs. A fourth bound is a row
#: here and nothing else: everything below reads the table rather than the fields,
#: so a bound that is added cannot be one that is applied and never mentioned, or
#: mentioned and never applied (ADR-0039).
#:
#: The price is read by :func:`~buy_agent.models.comparable_price` rather than off
#: the product, which is the whole of ADR-0043 here: a budget is a number in one
#: currency, and a price in another is not a figure it can be held against. Such a
#: price reads as unknown, and an unknown figure passes every bound. The other two
#: ignore the currency they are handed: a rating is out of five wherever it was
#: printed.
_BOUNDS: tuple[tuple[str, Reader, Callable[[float, float], bool], str], ...] = (
    ("max_price", comparable_price, operator.gt, "at most {:,.2f}"),
    ("min_rating", lambda p, _: p.rating, operator.lt, "rated at least {:g}"),
    ("min_reviews", lambda p, _: p.review_count, operator.lt, "from at least {:,} reviews"),
)


@dataclass(frozen=True, slots=True)
class Constraints:
    """The bounds a product has to be inside to be reported.

    ``None`` is "no bound", which is what all three default to -- a run nobody
    gave terms to reports everything it found, as it always did.

    Attributes:
        max_price: The most the shopper will pay, read in the currency the run's
            prices are counted in (ADR-0043). Nothing is converted -- a rate table
            is not this project's to ship, and a stale rate is a wrong answer
            wearing a right one's clothes -- so a price in some other currency is
            not held against this at all: it is a figure the run cannot place, and
            an unplaceable figure passes, the way an unknown one does.
        min_rating: The lowest average review score worth reporting, on the 0-5
            scale ``Product.rating`` is in.
        min_reviews: How many reviews a rating has to be averaged over. A 5.0
            from two people is not a rating, and the ranking already discounts it
            -- this refuses it outright.
    """

    max_price: float | None = None
    min_rating: float | None = None
    min_reviews: int | None = None

    @classmethod
    def from_config(cls, config: AgentConfig) -> Constraints:
        """The three bounds a run was configured with, off the config that holds them.

        They live on :class:`~buy_agent.config.AgentConfig` as three plain fields
        rather than as one of these, because that is what
        :data:`~buy_agent.config.LIMITS` bounds and what both front doors fill in
        -- one field, one flag, one form box. This is where they become the thing
        that does the work.
        """
        return cls(**{name: getattr(config, name) for name, *_ in _BOUNDS})

    @property
    def given(self) -> bool:
        """Whether the shopper set any of them.

        None set is the default, and the difference between "nothing was asked
        for" and "everything passed" is worth keeping: only the second is worth a
        line in the report.
        """
        return any(True for _ in self._set())

    def admits(self, product: Product, currency: str | None = None) -> bool:
        """Whether this product is inside every bound that was set.

        A figure the run does not know passes: see the module docstring. So the
        test is "known *and* outside", never "not inside". ``currency`` is what
        the run's prices are counted in, and a price in another one is a figure
        this run does not know (ADR-0043). It defaults to "nothing says these are
        different currencies", which is what one product on its own says.
        """
        return not any(
            (figure := read(product, currency)) is not None and outside(figure, bound)
            for read, bound, outside, _ in self._set()
        )

    def describe(self, currency: str | None = None) -> str:
        """The bounds as one phrase, for the line the run logs about them.

        Only the ones that were set, in the order :data:`_BOUNDS` declares them,
        so a run narrowed on price alone does not report two bounds it never had.

        A budget is named with the currency it was read in where the run found
        one, because that is the part of it nobody typed: the number came from the
        shopper and the currency from whatever the pages were printing.
        """
        # The budget is the one bound whose figure carries a unit, which is why it
        # is the one read by ``comparable_price``: said here rather than as a
        # column repeating what the reader already is.
        unit = f" {currency}" if currency else ""
        return ", ".join(
            phrase.format(bound) + (unit if read is comparable_price else "")
            for read, bound, _, phrase in self._set()
        )

    def apply(self, products: Sequence[Product]) -> list[Product]:
        """The products inside the bounds, and a line saying how many were not.

        Silence is the failure mode this guards against: a run that quietly
        reports two products because seven were over budget looks exactly like a
        run that only found two, and the second is a reason to search differently
        while the first is not. So the count goes out whenever bounds were set,
        even where everything passed -- "10 of 10" is the answer that says the
        bound did nothing.

        Given no bounds at all this is the products, unexamined and unremarked.
        """
        if not self.given:
            return list(products)

        # The run's own currency, worked out here for the reason ``rank_products``
        # works it out: it is a fact about the set, and this is the one place that
        # has the set (ADR-0043).
        currency = dominant_currency(products)
        kept = [product for product in products if self.admits(product, currency)]
        logger.log(
            # Nothing left is the one case worth interrupting for: the run found
            # products and is about to report none of them, and without this line
            # the only difference from an empty web is a warning that never came.
            logging.WARNING if not kept else logging.INFO,
            "%d of %d product(s) are within the limits (%s)",
            len(kept),
            len(products),
            self.describe(currency),
        )
        return kept

    def _set(self) -> Iterator[tuple[Reader, float, Callable[[float, float], bool], str]]:
        """The rows of :data:`_BOUNDS` the shopper actually gave a number for."""
        for name, read, outside, phrase in _BOUNDS:
            bound = getattr(self, name)
            if bound is not None:
                yield read, bound, outside, phrase
