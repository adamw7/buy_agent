"""The answer key: what :mod:`benchmark.corpus` actually says, product by product.

This is the half the live tests in ``integration/`` deliberately do not have.
They assert the *invariants*, which hold however badly the model read the pages
-- the right bar for a nightly job on a 0.6B model, and the reason nothing there
can say whether a run got better or worse. So this file writes the right answer
down. It is not a claim about which headphones anyone should buy: it is a
transcription of what ten fabricated pages print, which is the only thing the
pipeline is ever asked to reproduce.

Two things make it a *deterministic* key rather than a wish (ADR-0036):

* Every value is read off the pages **after** :func:`buy_agent.fetch.condense`
  has been over them, so nothing in it is unreachable -- a line the fetch layer
  throws away is a figure no run can ever be credited for, and a key demanding
  one would put a silent ceiling under 1.0. ``tests/test_benchmark.py`` reads
  that back off the corpus rather than trusting this sentence.
* Each figure is the **set of values a page printed for that product**, not one
  right answer. ``$328``, the refurbished ``$269`` and the ``329 EUR`` listing
  are all things the sources say the Sony costs, and a model reporting any of
  them has copied rather than invented. The canonical value beside the set has
  one purpose: building the ranking this run *should* have produced.

That second point is why the key is worth having.
:func:`buy_agent.verification.verify_numbers` grounds a figure against the
*pooled* haystack -- which is what makes grounding one cheap pass -- so the
``$349`` printed for the Bose vouches for a ``$349`` reported for the Sony.
Cross-product contamination is precisely what pooling cannot see, and per-product
sets can: :mod:`benchmark.scoring` calls it ``attribution``. Prices and ratings
are recorded as *pairs* with their qualifiers for ADR-0022's reason one stage on:
"329 USD" is two figures the corpus prints and a pairing neither of them did.
"""

from __future__ import annotations

from dataclasses import dataclass

from buy_agent.models import Product

AUDIOSITE = "https://audiosite.example/sony-wh-1000xm5-review"
BARN = "https://headphonebarn.example/anker-space-q45"
ROUNDUP = "https://gearroundup.example/best-noise-cancelling"
EUROTECH = "https://eurotech.example/sony-wh-1000xm5-preis"
SOUNDCHECK = "https://soundcheck.example/anker-vs-sennheiser"
AUDIODEAL = "https://audiodeal.example/sony-wh-1000xm5"
CANSREVIEW = "https://cansreview.example/bose-quietcomfort-ultra"
FLIGHTGEAR = "https://flightgear.example/headphones-for-long-haul"
DEALTRACKER = "https://dealtracker.example/anc-price-history"
BUDGETAUDIO = "https://budgetaudio.example/cheap-anc"


@dataclass(frozen=True, slots=True)
class Expected:
    """One product the corpus really is about, and everything it says about it.

    Attributes:
        name: The fullest spelling the pages give it. A reported name is matched
            by words rather than by equality -- "Sony WH-1000XM5 Wireless" and
            "WH-1000XM5" are this product, "Sony" is not enough to be (see
            :func:`benchmark.scoring.identifies`).
        price: The price to rank by -- the one most pages print. Used to build
            the ideal ordering and nothing else; a run is never marked wrong for
            reporting one of the others in :attr:`prices`.
        rating: The rating to rank by, on the same footing.
        review_count: The review count to rank by, likewise.
        prices: Every ``(price, currency)`` a page prints for it -- sale,
            refurbished, lowest-ever and the euro listing. A pair outside this
            set was copied off another product or invented.
        ratings: Every ``(rating, review_count)`` a page prints for it, paired
            for ADR-0022's reason.
        pages: The URLs that say something about it. What a link may point at,
            and the only pages a quote about it may come from (ADR-0025).
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
        reports, for :func:`buy_agent.ranking.rank_products` to order."""
        return Product(
            name=self.name,
            price=self.price,
            currency="USD",
            rating=self.rating,
            review_count=self.review_count,
        )


def _entry(
    name: str,
    price: float,
    rating: float,
    reviews: int,
    *,
    prices: set[tuple[float, str]],
    pages: set[str],
) -> Expected:
    """One row of the key. The rating pair is the canonical one: no product in
    this corpus is rated twice, so only ``prices`` is ever more than one pair."""
    return Expected(
        name=name,
        price=price,
        rating=rating,
        review_count=reviews,
        prices=frozenset(prices),
        ratings=frozenset({(rating, reviews)}),
        pages=frozenset(pages),
    )


#: The seven products these ten pages are about, in the order they first appear.
#:
#: Seven against a ``num_products`` of five (:data:`benchmark.corpus.NUM_PRODUCTS`)
#: is deliberate: the run cannot report them all, so a benchmark measuring recall
#: against all seven would put its own ceiling at 5/7 and never be legible. Recall
#: is measured against the cap instead -- of the five slots the run has, how many
#: hold a product that is really there.
ANSWER_KEY: tuple[Expected, ...] = (
    _entry(
        "Sony WH-1000XM5", 328.0, 4.7, 12_480,
        # $328 on six pages; the refurbished $269 and the sale $299 on two more,
        # with the "was" price, the lowest ever, and EuroTech's euro listing.
        prices={(328.0, "USD"), (269.0, "USD"), (299.0, "USD"),
                (348.0, "USD"), (279.0, "USD"), (329.0, "EUR")},
        pages={AUDIOSITE, ROUNDUP, EUROTECH, SOUNDCHECK, AUDIODEAL,
               CANSREVIEW, FLIGHTGEAR, DEALTRACKER},
    ),
    _entry(
        "Bose QuietComfort Ultra", 349.0, 4.3, 5_600,
        prices={(349.0, "USD"), (329.0, "USD"), (359.0, "EUR")},
        pages={AUDIOSITE, ROUNDUP, EUROTECH, AUDIODEAL, CANSREVIEW, DEALTRACKER},
    ),
    _entry(
        "Sennheiser Accentum", 179.0, 4.2, 3_400,
        prices={(179.0, "USD"), (149.0, "USD"), (169.0, "EUR")},
        pages={AUDIOSITE, BARN, ROUNDUP, EUROTECH, SOUNDCHECK,
               CANSREVIEW, FLIGHTGEAR, DEALTRACKER},
    ),
    _entry(
        "Apple AirPods Max", 479.0, 4.6, 9_100,
        prices={(479.0, "USD")},
        pages={AUDIOSITE, AUDIODEAL},
    ),
    _entry(
        "Anker Soundcore Space Q45", 99.0, 4.4, 31_200,
        prices={(99.0, "USD")},
        pages={BARN, SOUNDCHECK, FLIGHTGEAR, BUDGETAUDIO},
    ),
    _entry(
        "Soundcore Life Q30", 59.0, 4.5, 74_000,
        prices={(59.0, "USD"), (49.0, "USD")},
        pages={BARN, ROUNDUP, DEALTRACKER, BUDGETAUDIO},
    ),
    _entry(
        # BudgetAudio prints its rating and refuses to print a price it trusts:
        # the one product whose figures are incomplete on the page most about it.
        "JLab JBuds Lux ANC", 79.0, 4.1, 8_900,
        prices={(79.0, "USD"), (69.0, "USD")},
        pages={BARN, ROUNDUP, DEALTRACKER, BUDGETAUDIO},
    ),
)

__all__ = ["ANSWER_KEY", "Expected"]
