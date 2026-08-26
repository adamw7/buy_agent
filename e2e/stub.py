"""The agent these tests put behind the server, and the catalogues it answers with.

Everything below the agent is the real thing -- ``api.py``'s payloads,
``server.py``'s routing and event stream, the built Angular app -- so what a stub
here has to stand in for is exactly what a browser can never be shown otherwise:
a run that takes a minute, over a network and a model no test may touch.

A script is written the way a run reads: the log lines the panel will show, then
either the products the page will rank or the failure the banner will carry.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from buy_agent.config import AgentConfig
from buy_agent.models import Product, RankedProduct
from buy_agent.ranking import rank_products
from buy_agent.search import SearchError

#: Seconds between log lines. Small, but not zero: two of these tests are about
#: what the page does while a run is still going, and a run that finishes inside
#: one frame never shows them a stream. The whole suite spends about a second here.
STEP = 0.2

#: What a run says on its way through the pipeline, logger by logger, as the real
#: one does -- the panel trims these names, so they have to be the real ones.
PROGRESS: tuple[tuple[str, int, str], ...] = (
    ("buy_agent.agent", logging.INFO, "Refined search query: {request} price review"),
    ("buy_agent.search", logging.INFO, "Search returned 10 results"),
    ("buy_agent.fetch", logging.INFO, "Fetching 10 result page(s)"),
    ("buy_agent.fetch", logging.INFO, "Got usable page text from 10 of 10 result(s)"),
)

CATALOGUE = [
    Product(
        name="Bose QuietComfort Ultra",
        price=329.0,
        currency="USD",
        rating=4.7,
        review_count=5874,
        seller="Bose",
        url="https://example.com/reviews/bose-qc-ultra",
        opinions=[
            "the noise cancelling is uncanny for the money",
            "the case is too bulky for a coat pocket",
        ],
        notes="Best-in-class cancellation, priced like it.",
    ),
    Product(
        name="Sony WH-1000XM6",
        price=398.0,
        currency="USD",
        rating=4.6,
        review_count=12043,
        seller="Sony",
        url="https://example.com/reviews/sony-xm6",
        opinions=["battery life outlasts a transatlantic flight twice over"],
        notes="The all-rounder everyone compares the rest to.",
    ),
    # Every figure blanked, as grounding leaves a product the sources did not
    # back: the card has to say so in Python's words rather than show a gap.
    Product(
        name="Sennheiser Momentum 4",
        url="https://example.com/reviews/momentum-4",
        notes="Nothing the pages said about the price survived grounding.",
    ),
    Product(
        name="Anker Soundcore Space One",
        price=99.99,
        currency="USD",
        rating=4.2,
        review_count=8800,
        url="https://example.com/reviews/space-one",
        opinions=["astonishing for a hundred dollars, if you can forgive the app"],
        notes="The budget pick.",
    ),
]

#: One product carrying everything a page can hand a card that a card cannot
#: shorten: a title long enough to be a sentence, a seller and a host that never
#: break at a space, and a quote that is one unbroken word. On a 320px screen
#: these are what carries a card off the side of it.
AWKWARD = [
    Product(
        name=(
            "Sennheiser MOMENTUM 4 Wireless Adaptive Noise Cancelling Bluetooth "
            "Over-Ear Headphones"
        ),
        price=299.95,
        currency="USD",
        rating=4.4,
        review_count=1234567,
        seller="Marketplace-Seller-With-An-Extremely-Long-Unbroken-Name-Ltd",
        url="https://an-extremely-long-subdomain.review-aggregator-site.example.com/a/b/c",
        opinions=[
            "supercalifragilisticexpialidociousunbrokenwordnobrowsercanhyphenatesensibly",
            "the app is a chore but the sound is worth it",
        ],
        notes="A long note beside a long name, to see what the card does with both.",
    )
]


@dataclass
class Script:
    """What one server's agent does with a run.

    Args:
        products: What the run finds, ranked by ``sort_by`` before it is answered.
            Empty is the answer a real run gives when nothing survives grounding.
        fails: Raised instead of answering, if given. The page shows a failure
            banner and offers the log as a bug report.
    """

    products: list[Product] = field(default_factory=lambda: list(CATALOGUE))
    fails: Exception | None = None


#: The failure a browser is most likely to meet second, after Ollama: the search
#: backend saying no. ``SearchError`` is one of the agent's three failure modes.
RATE_LIMITED = SearchError("DuckDuckGo refused the query (rate limited)")


def agent_factory(script: Script):
    """An ``agent_factory=`` for ``create_server``, running *script* on every search."""

    class ScriptedAgent:
        def __init__(self, config: AgentConfig) -> None:
            self.config = config

        def run(self, request: str, *, sort_by: str = "score") -> list[RankedProduct]:
            for name, level, message in PROGRESS:
                logging.getLogger(name).log(level, message.format(request=request))
                time.sleep(STEP)

            if script.fails is not None:
                raise script.fails

            logging.getLogger("buy_agent.agent").info(
                "Extracted %d candidate(s)", len(script.products)
            )
            # A warning as well as the info lines: the panel colours this one, and
            # a run that only ever logs INFO would never show that it does.
            logging.getLogger("buy_agent.verification").warning(
                "Dropped unsupported figures on 1 product(s)"
            )
            # Every product found, best first -- not only the ones the page
            # highlights. Trimming here would leave "Top 3 of 3" for a run that
            # found nine, which is the number the heading is about.
            return rank_products(script.products, sort_by=sort_by)  # type: ignore[arg-type]

    return ScriptedAgent
