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
        currency: What :attr:`price` is counted in, which the ideal ordering is
            ranked in (ADR-0043).
        verdicts: Every line a page about it prints passing judgement on it, as the
            condensed page shows it: what a quote of it may be copied from (ADR-0073).
            A line judging two products is listed under both; a page's line about
            another product it names is not this one's, however near it sits.
    """

    name: str
    price: float
    rating: float
    review_count: int
    prices: frozenset[tuple[float, str]]
    ratings: frozenset[tuple[float, int]]
    pages: frozenset[str]
    currency: str = "USD"
    verdicts: frozenset[str] = frozenset()

    def as_product(self) -> Product:
        """This entry as the :class:`~buy_agent.models.Product` a perfect run
        reports, for :func:`buy_agent.ranking.rank_products` to order."""
        return Product(
            name=self.name,
            price=self.price,
            currency=self.currency,
            rating=self.rating,
            review_count=self.review_count,
        )


def entry(
    name: str,
    price: float,
    rating: float,
    reviews: int,
    *,
    prices: set[tuple[float, str]],
    pages: set[str],
    ratings: tuple[tuple[float, int], ...] = (),
    currency: str = "USD",
    verdicts: tuple[str, ...] = (),
) -> Expected:
    """One row of a key: the canonical figures, every other pair the pages print for it
    besides, and the verdicts they pass on it."""
    return Expected(
        name=name,
        price=price,
        rating=rating,
        review_count=reviews,
        prices=frozenset(prices),
        ratings=frozenset({(rating, reviews), *ratings}),
        pages=frozenset(pages),
        currency=currency,
        verdicts=frozenset(verdicts),
    )


#: The seven products these ten pages are about, in the order they first appear.
ANSWER_KEY: tuple[Expected, ...] = (
    entry(
        "Sony WH-1000XM5", 328.0, 4.7, 12_480,
        # $328 on six pages; the refurbished $269 and the sale $299 on two more,
        # with the "was" price, the lowest ever, and EuroTech's euro listing.
        prices={(328.0, "USD"), (269.0, "USD"), (299.0, "USD"),
                (348.0, "USD"), (279.0, "USD"), (329.0, "EUR")},
        pages={AUDIOSITE, ROUNDUP, EUROTECH, SOUNDCHECK, AUDIODEAL,
               CANSREVIEW, FLIGHTGEAR, DEALTRACKER},
        verdicts=(
            "In our tests the noise cancelling was still the best of anything at this price.",
            "Testers found the earcups roomy enough for an eight-hour flight.",
            "The downside is that the case no longer folds flat, which is a real annoyance.",
            "1. Sony WH-1000XM5 - best overall",
            "The Sony WH-1000XM5 remains our overall pick at $328.",
            "Buyers noticed the carrying case is smaller than the previous generation.",
            "Owners recommend the carrying pouch sold separately at 29 EUR.",
            "Owners recommend buying while the sale lasts.",
            # AudioDeal's page is the Sony's, though it lists the AirPods Max below.
            "Reviewers found the fit comfortable over a full working day.",
            "Comfort is excellent on the Sony WH-1000XM5 even after ten hours.",
            "The Sony is worth the money if you fly monthly.",
        ),
    ),
    entry(
        "Bose QuietComfort Ultra", 349.0, 4.3, 5_600,
        prices={(349.0, "USD"), (329.0, "USD"), (359.0, "EUR")},
        pages={AUDIOSITE, ROUNDUP, EUROTECH, AUDIODEAL, CANSREVIEW, DEALTRACKER},
        verdicts=(
            "3. Bose QuietComfort Ultra - best for calls",
            "Testers found the immersive audio mode gimmicky but the isolation outstanding.",
            "In our tests the isolation was outstanding on a noisy train.",
            "Testers found the immersive mode gimmicky and switched it off within a day.",
            "The drawback is a battery that is merely mediocre next to the Sony.",
            "Owners praised the folding hinge and the case.",
        ),
    ),
    entry(
        "Sennheiser Accentum", 179.0, 4.2, 3_400,
        prices={(179.0, "USD"), (149.0, "USD"), (169.0, "EUR")},
        pages={AUDIOSITE, BARN, ROUNDUP, EUROTECH, SOUNDCHECK,
               CANSREVIEW, FLIGHTGEAR, DEALTRACKER},
        verdicts=(
            "2. Sennheiser Accentum - best value",
            "Comfort is excellent on a long flight, and reviewers praised the clamping force.",
            "Call quality is merely acceptable, which is the one complaint we heard often.",
            "The Accentum is the one we recommend for a commuter on a budget.",
            "Both are sturdy enough to live in a bag for a year.",
            "Users liked the Accentum's controls and disliked its cramped earcups.",
            "Reviewers found the Sennheiser Accentum cramped on a long sector.",
        ),
    ),
    entry(
        "Apple AirPods Max", 479.0, 4.6, 9_100,
        prices={(479.0, "USD")},
        pages={AUDIOSITE, AUDIODEAL},
        # Priced on two pages and judged on none.
    ),
    entry(
        "Anker Soundcore Space Q45", 99.0, 4.4, 31_200,
        prices={(99.0, "USD")},
        pages={BARN, SOUNDCHECK, FLIGHTGEAR, BUDGETAUDIO},
        verdicts=(
            "Owners report battery life of nearly two full working weeks.",
            "The value for money here is very hard to argue with at this price.",
            "The app is cluttered and the treble is dull, which several buyers complained of.",
            "Reviewers felt the headband padding was flimsy for the money.",
            "Reviewers found the Anker's noise cancelling merely mediocre next to the Sony.",
            "Both are sturdy enough to live in a bag for a year.",
            "Owners report the Anker Soundcore Space Q45 lasts three flights on a charge.",
        ),
    ),
    entry(
        "Soundcore Life Q30", 59.0, 4.5, 74_000,
        prices={(59.0, "USD"), (49.0, "USD")},
        pages={BARN, ROUNDUP, DEALTRACKER, BUDGETAUDIO},
        verdicts=(
            "4. Soundcore Life Q30 - the cheapest pick here",
            "Buyers loved the price and complained about the muddy bass in equal measure.",
            # BudgetAudio's "it" is the line above: the Life Q30.
            "Buyers recommend it as the best value in the category by some distance.",
        ),
    ),
    entry(
        # BudgetAudio prints its rating and refuses to print a price it trusts:
        # the one product whose figures are incomplete on the page most about it.
        "JLab JBuds Lux ANC", 79.0, 4.1, 8_900,
        prices={(79.0, "USD"), (69.0, "USD")},
        pages={BARN, ROUNDUP, DEALTRACKER, BUDGETAUDIO},
        verdicts=(
            "5. JLab JBuds Lux ANC - best under $100",
            "In our tests the JLab was underwhelming above 1kHz but excellent on a plane.",
            "Users wished the app remembered its EQ settings between sessions.",
            "Owners found the fit uncomfortable for anyone wearing glasses.",
        ),
    ),
)

#: The judgements these pages pass on no product the key names: a sale, a crew's advice,
#: a headline.
ABOUT_NOBODY: frozenset[str] = frozenset(
    {
        "Cabin crew we asked recommend anything with a wired fallback.",
        "Buyers found the January sales the best value of the year.",
        "Cheap noise cancelling: what is actually worth it",
    }
)

__all__ = ["ABOUT_NOBODY", "ANSWER_KEY", "Expected", "entry"]
