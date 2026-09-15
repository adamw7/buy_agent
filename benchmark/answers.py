"""The answer key: what :mod:`benchmark.corpus` actually says, product by product."""

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
    """One row of the key."""
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
