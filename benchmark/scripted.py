"""Two answers written by hand, so the benchmark can run with no model at all.

A benchmark whose only reference point is a live model tells you a number and
nothing about whether the number is right. These do the other half: fixed
:class:`~buy_agent.models.ProductList` answers put through the *real* pipeline,
so ``--scripted perfect`` scores 1.000 and ``--scripted sloppy`` scores exactly
what ``tests/test_benchmark.py`` says, on any machine, with nothing installed but
the runtime dependencies. That is what makes the scorer testable: every metric
has a scripted run that moves it and one that does not, which is the only way to
tell a scorer that measures something from one that returns 1.0.

:data:`SLOPPY` is wrong in the eight ways a small model is wrong. Four the
pipeline catches, so the scorecard never sees them; four it cannot, and those
are what a benchmark exists for (ADR-0036):

* a listicle headline reported as a product -- ``clean_products`` drops it;
* a link to a page that was never searched -- ``attribute_sources`` replaces it;
* a verdict nobody wrote -- ``verify_opinions`` drops it;
* a paraphrase with a word changed near the end -- ``verify_opinions`` *keeps*
  it, tolerating a word at either end by design (ADR-0025), and the scorecard
  marks it under ``faithful``;
* the publisher's own name reported as a product -- not a headline, so
  ``clean_products`` keeps it, and every word of it is in the sources, so
  grounding does too. ``genuine``;
* one product listed twice, once without its brand -- ``deduplicate`` merges
  names differing by *descriptive* words and "Anker" is not one. ``genuine``
  again, and why the scorecard counts ``invented`` and ``repeated`` apart;
* the Bose's price reported for the Sony -- both figures are in the corpus and
  ``verify_numbers`` grounds against the pooled pages, so nothing in the
  pipeline can see it. ``attribution``;
* the Bose's own price paired with the euro sign off another listing, which is
  the pairing ADR-0022 is about: 349 is printed and EUR is printed, never
  together. ``figures``, and then ``order`` as well, a price in a currency the
  set is not counted in scoring ``NEUTRAL`` rather than last (ADR-0043).

Every invariant ``integration/test_live_pipeline.py`` asserts holds on this
answer, which is the argument for the benchmark in one fixture.
"""

from __future__ import annotations

from typing import Any

from buy_agent.models import ExtractedProduct, ProductList, SearchQuery
from benchmark.answers import AUDIOSITE, BARN, CANSREVIEW, ROUNDUP, SOUNDCHECK

#: What a scripted run refines :data:`benchmark.corpus.REQUEST` into.
REFINED_QUERY = "noise cancelling headphones under $350 price review comfort"


class ScriptedLLM:
    """Stands in for a chat model, answering from a fixed script.

    ``answer`` is the entire surface both chains use, and the schema it is asked
    for is what says which of the two is calling -- the same stand-in
    ``demo/server.py`` uses, without the pauses it adds for the camera.
    """

    def __init__(self, answer: ProductList, query: str = REFINED_QUERY) -> None:
        self.script = answer
        self.query = query

    def answer(self, _messages: Any, schema: type) -> Any:
        if schema is SearchQuery:
            return SearchQuery(query=self.query)
        return self.script


#: The first five products of the answer key, copied exactly as the pages print
#: them. Scores 1.000, which is the assertion that the key is *reachable*: a
#: figure the fetch layer condenses away or grounding refuses would show up here
#: as a reference run that cannot reach full marks, rather than as a silent
#: ceiling under every score the nightly ever reports.
#:
#: The AirPods Max quote comes off AudioDeal, which lists it beside the Sony it
#: is mostly about. Deliberate: "a page that mentions the product" is the bar
#: ``verify_opinions`` sets (ADR-0025), and a benchmark whose perfect answer the
#: pipeline would reject is measuring a different pipeline.
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
#:
#: The order matters as much as the contents: seven entries against a
#: ``num_products`` of five means the two slots spent on a shop and on a repeat
#: cost the run the Sennheiser, which is the shape of what these mistakes do to
#: a report.
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
                # "weeks" on the page. One word, at the end, where the quote
                # check is deliberately tolerant.
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
