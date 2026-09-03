"""Two answers written by hand, so the benchmark can be run with no model at all.

A benchmark whose only reference point is a live model tells you a number and
nothing about whether the number is right. These two do the other half: they are
fixed :class:`~buy_agent.models.ProductList` answers put through the *real*
pipeline, so ``python -m benchmark --scripted perfect`` scores 1.000 and
``--scripted sloppy`` scores exactly what ``tests/test_benchmark.py`` says it
scores, on any machine, with nothing installed but the runtime dependencies.

That is what makes the scorer testable. Every metric has a scripted run that
moves it and a scripted run that does not, which is the only way to tell a
scorer that measures something from one that returns 1.0.

:data:`SLOPPY` is wrong in the seven ways a small model is wrong. Four of them
the pipeline catches and the scorecard therefore never sees; three it cannot,
and those are the ones a benchmark exists for:

* a listicle headline reported as a product -- ``clean_products`` drops it;
* a link to a page that was never searched -- ``attribute_sources`` replaces it;
* a verdict nobody wrote -- ``verify_opinions`` drops it;
* a paraphrase with a word changed near the end -- ``verify_opinions`` *keeps*
  it, tolerating a word at either end by design (ADR-0025), and the scorecard
  marks it under ``faithful``;
* the publisher's own name reported as a product -- it is not a headline, so
  ``clean_products`` keeps it, and every word of it is in the sources, so
  grounding keeps it too. ``genuine``;
* one product listed twice, once without its brand -- ``deduplicate`` merges
  names differing by *descriptive* words and "Anker" is not one, so both stand.
  ``genuine`` again, and the reason the scorecard counts ``invented`` and
  ``repeated`` apart;
* the Bose's price reported for the Sony -- both figures are in the corpus, and
  ``verify_numbers`` grounds against the pooled pages, so nothing in the
  pipeline can see it. ``attribution``.

The last three are all invisible to ``integration/test_live_pipeline.py``: every
invariant it asserts holds on this answer. That gap is the argument for the
benchmark (ADR-0036).
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableLambda

from buy_agent.models import ExtractedProduct, ProductList, SearchQuery

#: What a scripted run refines :data:`benchmark.corpus.REQUEST` into.
REFINED_QUERY = "noise cancelling headphones under $350 price review comfort"


class ScriptedLLM:
    """Stands in for a chat model, answering from a fixed script.

    ``with_structured_output`` is the entire surface both chains use, and the
    schema it is asked for is what says which of the two is calling -- the same
    stand-in ``demo/server.py`` uses, without the pauses it adds for the camera.
    """

    def __init__(self, answer: ProductList, query: str = REFINED_QUERY) -> None:
        self.answer = answer
        self.query = query

    def with_structured_output(self, schema: type, **_: Any) -> RunnableLambda:
        def respond(_value: Any) -> Any:
            if schema is SearchQuery:
                return SearchQuery(query=self.query)
            return self.answer

        return RunnableLambda(respond)


#: The first five products of the answer key, copied exactly as the pages print
#: them. Scores 1.000, and is the assertion that the key is *reachable*: a figure
#: the fetch layer condenses away or grounding refuses would show up here as a
#: reference run that cannot reach full marks, rather than as a silent ceiling
#: under every score the nightly ever reports.
#:
#: The AirPods Max quote comes off AudioDeal, a page that lists it beside the
#: Sony it is mostly about. That is deliberate: "a page that mentions the
#: product" is the bar ``verify_opinions`` sets (ADR-0025), and a benchmark whose
#: perfect answer the pipeline would reject is measuring a different pipeline.
PERFECT = ProductList(
    products=[
        ExtractedProduct(
            name="Sony WH-1000XM5",
            price=328.0,
            currency="USD",
            rating=4.7,
            review_count=12_480,
            url="https://audiosite.example/sony-wh-1000xm5-review",
            opinions=[
                "In our tests the noise cancelling was still the best of anything at this price.",
                "Testers found the earcups roomy enough for an eight-hour flight.",
            ],
        ),
        ExtractedProduct(
            name="Bose QuietComfort Ultra",
            price=349.0,
            currency="USD",
            rating=4.3,
            review_count=5_600,
            url="https://cansreview.example/bose-quietcomfort-ultra",
            opinions=["In our tests the isolation was outstanding on a noisy train."],
        ),
        ExtractedProduct(
            name="Sennheiser Accentum",
            price=179.0,
            currency="USD",
            rating=4.2,
            review_count=3_400,
            url="https://gearroundup.example/best-noise-cancelling",
            opinions=[
                "Comfort is excellent on a long flight, and reviewers praised the clamping force."
            ],
        ),
        ExtractedProduct(
            name="Apple AirPods Max",
            price=479.0,
            currency="USD",
            rating=4.6,
            review_count=9_100,
            url="https://audiosite.example/sony-wh-1000xm5-review",
            opinions=["Reviewers found the fit comfortable over a full working day."],
        ),
        ExtractedProduct(
            name="Anker Soundcore Space Q45",
            price=99.0,
            currency="USD",
            rating=4.4,
            review_count=31_200,
            url="https://headphonebarn.example/anker-space-q45",
            opinions=[
                "Owners report battery life of nearly two full working weeks.",
                "The value for money here is very hard to argue with at this price.",
            ],
        ),
    ]
)

#: The same run, wrong in the seven ways listed in this module's docstring.
#:
#: The order matters as much as the contents: seven entries against a
#: ``num_products`` of five means the two slots spent on a shop and on a repeat
#: cost the run the Sennheiser, which is the shape of what these mistakes
#: actually do to a report.
SLOPPY = ProductList(
    products=[
        ExtractedProduct(
            name="Sony WH-1000XM5",
            # The Bose's price. In the corpus, on another product's line.
            price=349.0,
            currency="USD",
            rating=4.7,
            review_count=12_480,
            url="https://sony.example/wh-1000xm5",  # never searched
            opinions=["The battery lasts forever and the fit is superb."],  # nobody wrote it
        ),
        ExtractedProduct(
            name="9 Best Noise Cancelling Headphones Under $400",
            price=328.0,
            currency="USD",
        ),
        ExtractedProduct(
            name="AudioSite",
            url="https://audiosite.example/sony-wh-1000xm5-review",
        ),
        ExtractedProduct(
            name="Bose QuietComfort Ultra",
            price=349.0,
            currency="EUR",  # the corpus prints 349 and it prints EUR, never together
            rating=4.3,
            review_count=5_600,
            url="https://cansreview.example/bose-quietcomfort-ultra",
            opinions=["In our tests the isolation was outstanding on a noisy train."],
        ),
        ExtractedProduct(
            name="Anker Soundcore Space Q45",
            price=99.0,
            currency="USD",
            rating=4.4,
            review_count=31_200,
            url="https://headphonebarn.example/anker-space-q45",
            opinions=[
                "The value for money here is very hard to argue with at this price.",
                # "weeks" on the page. One word, at the end, where the quote check
                # is deliberately tolerant.
                "Owners report battery life of nearly two full working months.",
            ],
        ),
        ExtractedProduct(
            name="Soundcore Space Q45",  # the Anker again, without its brand
            price=99.0,
            url="https://soundcheck.example/anker-vs-sennheiser",
        ),
        ExtractedProduct(
            name="Sennheiser Accentum",  # never reported: the cap ran out
            price=179.0,
            currency="USD",
            url="https://gearroundup.example/best-noise-cancelling",
        ),
    ]
)

#: The scripts ``python -m benchmark --scripted`` offers, by name.
SCRIPTS: dict[str, ProductList] = {"perfect": PERFECT, "sloppy": SLOPPY}

__all__ = ["PERFECT", "REFINED_QUERY", "SCRIPTS", "SLOPPY", "ScriptedLLM"]
