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
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from buy_agent.config import AgentConfig
    from buy_agent.models import Product

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Constraints:
    """The bounds a product has to be inside to be reported.

    ``None`` is "no bound", which is what all three default to -- a run nobody
    gave terms to reports everything it found, as it always did.

    Attributes:
        max_price: The most the shopper will pay, in whatever currency the page
            printed. Not converted: a bound and a price from two currencies are
            not comparable, and the one place that could be fixed is a rate table
            this project has no business shipping (ADR-0039).
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
        return cls(
            max_price=config.max_price,
            min_rating=config.min_rating,
            min_reviews=config.min_reviews,
        )

    @property
    def given(self) -> bool:
        """Whether the shopper set any of them.

        None set is the default, and the difference between "nothing was asked
        for" and "everything passed" is worth keeping: only the second is worth a
        line in the report.
        """
        return any(
            bound is not None
            for bound in (self.max_price, self.min_rating, self.min_reviews)
        )

    def admits(self, product: Product) -> bool:
        """Whether this product is inside every bound that was set.

        A figure the run does not know passes: see the module docstring. So each
        clause is "known *and* outside", never "not inside".
        """
        if self.max_price is not None and product.price is not None:
            if product.price > self.max_price:
                return False
        if self.min_rating is not None and product.rating is not None:
            if product.rating < self.min_rating:
                return False
        if self.min_reviews is not None and product.review_count is not None:
            if product.review_count < self.min_reviews:
                return False
        return True

    def describe(self) -> str:
        """The bounds as one phrase, for the line the run logs about them.

        Only the ones that were set, in the order they are declared, so a run
        narrowed on price alone does not report two bounds it never had.
        """
        said = []
        if self.max_price is not None:
            said.append(f"at most {self.max_price:,.2f}")
        if self.min_rating is not None:
            said.append(f"rated at least {self.min_rating:g}")
        if self.min_reviews is not None:
            said.append(f"from at least {self.min_reviews:,} reviews")
        return ", ".join(said)

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

        kept = [product for product in products if self.admits(product)]
        logger.log(
            # Nothing left is the one case worth interrupting for: the run found
            # products and is about to report none of them, and without this line
            # the only difference from an empty web is a warning that never came.
            logging.WARNING if not kept else logging.INFO,
            "%d of %d product(s) are within the limits (%s)",
            len(kept),
            len(products),
            self.describe(),
        )
        return kept
