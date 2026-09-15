"""Two answers written by hand, so the benchmark can run with no model at all."""

from __future__ import annotations

from typing import Any

from buy_agent.models import ExtractedProduct, ProductList, SearchQuery
from benchmark.answers import AUDIOSITE, BARN, CANSREVIEW, ROUNDUP, SOUNDCHECK

#: What a scripted run refines :data:`benchmark.corpus.REQUEST` into.
REFINED_QUERY = "noise cancelling headphones under $350 price review comfort"


class ScriptedLLM:
    """Stands in for a chat model, answering from a fixed script."""

    def __init__(self, answer: ProductList, query: str = REFINED_QUERY) -> None:
        self.script = answer
        self.query = query

    def answer(self, _messages: Any, schema: type) -> Any:
        if schema is SearchQuery:
            return SearchQuery(query=self.query)
        return self.script


#: The first five products of the answer key, copied exactly as the pages print them.
PERFECT = ProductList(
    products=[
        ExtractedProduct(
            name="Sony WH-1000XM5", price=328.0, currency="USD",
            rating=4.7, review_count=12_480, url=AUDIOSITE,
            opinions=[
                "In our tests the noise cancelling was still the best of anything at this price.",
                "Testers found the earcups roomy enough for an eight-hour flight.",
            ],
        ),
        ExtractedProduct(
            name="Bose QuietComfort Ultra", price=349.0, currency="USD",
            rating=4.3, review_count=5_600, url=CANSREVIEW,
            opinions=["In our tests the isolation was outstanding on a noisy train."],
        ),
        ExtractedProduct(
            name="Sennheiser Accentum", price=179.0, currency="USD",
            rating=4.2, review_count=3_400, url=ROUNDUP,
            opinions=[
                "Comfort is excellent on a long flight, and reviewers praised the clamping force."
            ],
        ),
        ExtractedProduct(
            name="Apple AirPods Max", price=479.0, currency="USD",
            rating=4.6, review_count=9_100, url=AUDIOSITE,
            opinions=["Reviewers found the fit comfortable over a full working day."],
        ),
        ExtractedProduct(
            name="Anker Soundcore Space Q45", price=99.0, currency="USD",
            rating=4.4, review_count=31_200, url=BARN,
            opinions=[
                "Owners report battery life of nearly two full working weeks.",
                "The value for money here is very hard to argue with at this price.",
            ],
        ),
    ]
)

#: The same run, wrong in the eight ways this module's docstring lists.
SLOPPY = ProductList(
    products=[
        ExtractedProduct(
            # 349 is the Bose's price. In the corpus, on another product's line.
            name="Sony WH-1000XM5", price=349.0, currency="USD",
            rating=4.7, review_count=12_480,
            url="https://sony.example/wh-1000xm5",  # never searched
            opinions=["The battery lasts forever and the fit is superb."],  # nobody wrote it
        ),
        ExtractedProduct(
            name="9 Best Noise Cancelling Headphones Under $400", price=328.0, currency="USD"
        ),
        ExtractedProduct(name="AudioSite", url=AUDIOSITE),
        ExtractedProduct(
            # The corpus prints 349 and it prints EUR, never together -- and
            # the euro price it does print is the Sony's, on EuroTech.
            name="Bose QuietComfort Ultra", price=349.0, currency="EUR",
            rating=4.3, review_count=5_600, url=CANSREVIEW,
            opinions=["In our tests the isolation was outstanding on a noisy train."],
        ),
        ExtractedProduct(
            name="Anker Soundcore Space Q45", price=99.0, currency="USD",
            rating=4.4, review_count=31_200, url=BARN,
            opinions=[
                "The value for money here is very hard to argue with at this price.",
                # "weeks" on the page.
                "Owners report battery life of nearly two full working months.",
            ],
        ),
        # The Anker again, without its brand.
        ExtractedProduct(name="Soundcore Space Q45", price=99.0, url=SOUNDCHECK),
        # Never reported: the cap ran out.
        ExtractedProduct(
            name="Sennheiser Accentum", price=179.0, currency="USD", url=ROUNDUP
        ),
    ]
)

#: The scripts ``python -m benchmark --scripted`` offers, by name.
SCRIPTS: dict[str, ProductList] = {"perfect": PERFECT, "sloppy": SLOPPY}

__all__ = ["PERFECT", "REFINED_QUERY", "SCRIPTS", "SLOPPY", "ScriptedLLM"]
