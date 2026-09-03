"""The answer key: what :mod:`benchmark.corpus` actually says, product by product.

This is the half the live tests in ``integration/`` deliberately do not have.
They assert the *invariants* -- every name in the sources, every figure in the
sources, nothing listed twice -- which hold however badly the model read the
pages, and which is the right bar for a nightly job on a 0.6B model. What they
cannot say is whether the run got *better* or *worse*, because nothing in them
knows what the right answer was.

So this file writes the right answer down. It is not a claim about which
headphones anyone should buy: it is a transcription of what these ten fabricated
pages print, which is the only thing the pipeline is ever asked to reproduce.

Two things make it a *deterministic* key rather than a wish:

* Every value here is read off the pages **after**
  :func:`buy_agent.fetch.condense` has been over them, so nothing in it is
  unreachable -- a line the fetch layer throws away is a figure the model never
  sees, and a key demanding one would put a ceiling under 1.0 on the score.
  ``tests/test_benchmark.py::test_every_answer_is_printed_in_the_corpus`` reads
  that back off the corpus rather than trusting this sentence.
* Each figure is recorded as the **set of values a page printed for that
  product**, not as one right answer. ``$328``, the ``$269`` refurbished price
  and the ``329 EUR`` listing are all things the sources say the Sony costs, and
  a model reporting any of them has copied rather than invented. The canonical
  value beside the set is for one purpose only: building the ranking this run
  *should* have produced.

That distinction is the reason the key is worth having at all.
:func:`buy_agent.verification.verify_numbers` grounds a figure against the
*pooled* haystack, so the ``$349`` printed for the Bose vouches for a ``$349``
reported for the Sony: pooling is what makes grounding cheap, and cross-product
contamination is precisely what it cannot see. Per-product sets can, which is
what :func:`benchmark.scoring.score_run` calls ``attribution``.

A currency travels with its price and a review count with its rating, as pairs,
for ADR-0022's reason one stage further on: "329 USD" is two figures the sources
both printed and a pairing neither of them did.
"""

from __future__ import annotations

from dataclasses import dataclass

from buy_agent.models import Product

_AUDIOSITE = "https://audiosite.example/sony-wh-1000xm5-review"
_HEADPHONEBARN = "https://headphonebarn.example/anker-space-q45"
_GEARROUNDUP = "https://gearroundup.example/best-noise-cancelling"
_EUROTECH = "https://eurotech.example/sony-wh-1000xm5-preis"
_SOUNDCHECK = "https://soundcheck.example/anker-vs-sennheiser"
_AUDIODEAL = "https://audiodeal.example/sony-wh-1000xm5"
_CANSREVIEW = "https://cansreview.example/bose-quietcomfort-ultra"
_FLIGHTGEAR = "https://flightgear.example/headphones-for-long-haul"
_DEALTRACKER = "https://dealtracker.example/anc-price-history"
_BUDGETAUDIO = "https://budgetaudio.example/cheap-anc"


@dataclass(frozen=True, slots=True)
class Expected:
    """One product the corpus really is about, and everything it says about it.

    Attributes:
        name: The fullest spelling the pages give it. A reported name is matched
            against this by words rather than by string equality -- "Sony
            WH-1000XM5 Wireless" and "WH-1000XM5" are this product, "Sony" is not
            enough to be (see :func:`benchmark.scoring.identifies`).
        price: The price to rank by -- the one most pages print, which is what a
            shopper would call *the* price. Used to build the ideal ordering and
            for nothing else; a run is never marked wrong for reporting one of
            the others in :attr:`prices`.
        rating: The rating to rank by, on the same footing.
        review_count: The review count to rank by, likewise.
        prices: Every ``(price, currency)`` a page prints for this product --
            sale prices, refurbished prices, the lowest ever, and the euro
            listing. A pair outside this set was either copied off another
            product or invented.
        ratings: Every ``(rating, review_count)`` a page prints for it, paired
            for ADR-0022's reason.
        pages: The URLs of the pages that say something about it. What a link may
            point at, and the only pages a quote about it may come from
            (ADR-0025).
    """

    name: str
    price: float
    rating: float
    review_count: int
    prices: frozenset[tuple[float, str]]
    ratings: frozenset[tuple[float, int]]
    pages: frozenset[str]

    def as_product(self) -> Product:
        """This entry as the :class:`~buy_agent.models.Product` a perfect run
        would have reported, for :func:`buy_agent.ranking.rank_products` to order."""
        return Product(
            name=self.name,
            price=self.price,
            currency="USD",
            rating=self.rating,
            review_count=self.review_count,
        )


#: The seven products these ten pages are about, in the order they first appear.
#:
#: Seven against a ``num_products`` of five (:data:`benchmark.corpus.NUM_PRODUCTS`)
#: is deliberate: the run cannot report them all, so a benchmark that measured
#: recall against all seven would put its own ceiling at 5/7 and never be
#: legible. Recall is measured against the cap instead -- of the five slots the
#: run has, how many hold a product that is really there.
ANSWER_KEY: tuple[Expected, ...] = (
    Expected(
        name="Sony WH-1000XM5",
        price=328.0,
        rating=4.7,
        review_count=12_480,
        # $328 on six pages, the refurbished $269 and the sale $299 on two more,
        # the "was" price, the lowest ever, and EuroTech's euro listing.
        prices=frozenset(
            {
                (328.0, "USD"),
                (269.0, "USD"),
                (299.0, "USD"),
                (348.0, "USD"),
                (279.0, "USD"),
                (329.0, "EUR"),
            }
        ),
        ratings=frozenset({(4.7, 12_480)}),
        pages=frozenset(
            {
                _AUDIOSITE,
                _GEARROUNDUP,
                _EUROTECH,
                _SOUNDCHECK,
                _AUDIODEAL,
                _CANSREVIEW,
                _FLIGHTGEAR,
                _DEALTRACKER,
            }
        ),
    ),
    Expected(
        name="Bose QuietComfort Ultra",
        price=349.0,
        rating=4.3,
        review_count=5_600,
        prices=frozenset({(349.0, "USD"), (329.0, "USD"), (359.0, "EUR")}),
        ratings=frozenset({(4.3, 5_600)}),
        pages=frozenset(
            {_AUDIOSITE, _GEARROUNDUP, _EUROTECH, _AUDIODEAL, _CANSREVIEW, _DEALTRACKER}
        ),
    ),
    Expected(
        name="Sennheiser Accentum",
        price=179.0,
        rating=4.2,
        review_count=3_400,
        prices=frozenset({(179.0, "USD"), (149.0, "USD"), (169.0, "EUR")}),
        ratings=frozenset({(4.2, 3_400)}),
        pages=frozenset(
            {
                _AUDIOSITE,
                _HEADPHONEBARN,
                _GEARROUNDUP,
                _EUROTECH,
                _SOUNDCHECK,
                _CANSREVIEW,
                _FLIGHTGEAR,
                _DEALTRACKER,
            }
        ),
    ),
    Expected(
        name="Apple AirPods Max",
        price=479.0,
        rating=4.6,
        review_count=9_100,
        prices=frozenset({(479.0, "USD")}),
        ratings=frozenset({(4.6, 9_100)}),
        pages=frozenset({_AUDIOSITE, _AUDIODEAL}),
    ),
    Expected(
        name="Anker Soundcore Space Q45",
        price=99.0,
        rating=4.4,
        review_count=31_200,
        prices=frozenset({(99.0, "USD")}),
        ratings=frozenset({(4.4, 31_200)}),
        pages=frozenset({_HEADPHONEBARN, _SOUNDCHECK, _FLIGHTGEAR, _BUDGETAUDIO}),
    ),
    Expected(
        name="Soundcore Life Q30",
        price=59.0,
        rating=4.5,
        review_count=74_000,
        prices=frozenset({(59.0, "USD"), (49.0, "USD")}),
        ratings=frozenset({(4.5, 74_000)}),
        pages=frozenset({_HEADPHONEBARN, _GEARROUNDUP, _DEALTRACKER, _BUDGETAUDIO}),
    ),
    Expected(
        # BudgetAudio prints its rating and refuses to print a price it trusts,
        # which is the one product whose figures are deliberately incomplete on
        # the page that is most about it.
        name="JLab JBuds Lux ANC",
        price=79.0,
        rating=4.1,
        review_count=8_900,
        prices=frozenset({(79.0, "USD"), (69.0, "USD")}),
        ratings=frozenset({(4.1, 8_900)}),
        pages=frozenset({_HEADPHONEBARN, _GEARROUNDUP, _DEALTRACKER, _BUDGETAUDIO}),
    ),
)

__all__ = ["ANSWER_KEY", "Expected"]
