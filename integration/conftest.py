"""Fixtures for the live tests: a real Ollama, a fabricated web.

One session-scoped run does the expensive part. A CPU-only model answers in
seconds rather than milliseconds, so a fixture per test would be a fixture per
inference, and the nightly job has five minutes for the lot -- pull included.
:func:`live_run` therefore runs the pipeline once and every test asserts
something different about the same answer.

The model is the only thing not faked, and the fake stops at the *transport*.
``search_web`` hands back :data:`PAGES` instead of calling DuckDuckGo, and
``enrich`` reads :data:`_PAGE_TEXT` instead of fetching a URL -- but what it
does with that text is :func:`buy_agent.fetch.condense`, the real one, on the
real ``page_chars`` and ``opinion_chars`` budgets. So the prompt the model sees
here is shaped the way a production prompt is shaped: newline-separated lines
that quote a figure or pass judgement, and nothing else.

That matters more than it looks. Handing the model tidy prose the fetch layer
would never have produced tests it on input it will never meet, and -- since
``build_haystack`` runs over the same condensed text -- it also lets a quote be
"verified" against a sentence :mod:`buy_agent.fetch` would have thrown away.
Both halves of ADR-0025 are about text this project produces, so the live run
produces it.
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from typing import TYPE_CHECKING, NoReturn

import pytest
from langchain_core.runnables import RunnableLambda

from buy_agent import agent as agent_module
from buy_agent.agent import BuyAgent
from buy_agent.providers import list_models
from buy_agent.config import DEFAULT_BASE_URL, AgentConfig
from buy_agent.fetch import condense
from buy_agent.search import SearchResult
from integration import MODEL_ENV_VAR, REQUIRE_ENV_VAR, TINY_MODEL

if TYPE_CHECKING:
    from collections.abc import Iterator

    from buy_agent.models import ProductList, RankedProduct

#: The pages behind the results, as :func:`buy_agent.fetch.fetch_page` would
#: have found them: a product name, the line under it carrying the figure, a few
#: lines of verdict, and the navigation, specifications and legal boilerplate
#: that make up most of a real page. Fabricated, because an assertion about a
#: real shop's listing would be a nightly failure about the shop.
#:
#: Written to be *condensed*, not read. Every line meant to survive is a line
#: :func:`buy_agent.fetch.quotes_a_figure` or
#: :func:`buy_agent.fetch.reads_like_an_opinion` accepts, and the rest is there
#: to be discarded -- which is the half a fixture of tidy prose cannot test. A
#: verdict worded outside ``fetch._OPINION``'s vocabulary never reaches the
#: model in production, so one worded that way here would be testing nothing.
#:
#: Ten pages, which is what ``search_results`` ships as, and each of them dense,
#: because the width of the prompt is itself under test: ADR-0019 says
#: ``num_ctx=8192`` and ``reasoning=False`` are what stop a thinking model
#: reasoning until the context is gone, and a prompt that fits Ollama's 4096
#: default with room to spare cannot show that either way. Three tidy pages put
#: the extraction prompt at ~675 tokens, where the question cannot arise.
_PAGE_TEXT: dict[str, str] = {
    "https://audiosite.example/sony-wh-1000xm5-review": """\
AudioSite
Home  Reviews  Buying guides  About us
Sony WH-1000XM5 review: still the one to beat
By the AudioSite audio desk
The Sony WH-1000XM5 costs $328 at most shops.
Rated 4.7 out of 5 from 12,480 reviews.
Verdict
In our tests the noise cancelling was still the best of anything at this price.
Testers found the earcups roomy enough for an eight-hour flight.
The downside is that the case no longer folds flat, which is a real annoyance.
How it compares
Bose QuietComfort Ultra
The Bose QuietComfort Ultra is $349 and rated 4.3 out of 5 from 5,600 reviews.
Sennheiser Accentum
The Sennheiser Accentum is $179 and rated 4.2 out of 5 from 3,400 reviews.
Apple AirPods Max
The Apple AirPods Max is $479 and rated 4.6 out of 5 from 9,100 reviews.
Where to buy
Listed at $328 by three of the four shops we track this month.
Refurbished units start at $269 with the same one-year warranty.
Specifications
Driver size: 30mm
Weight: 250g
Bluetooth: 5.2
Charging: USB-C
Sign up for the AudioSite newsletter
Copyright 2026 AudioSite Media. All rights reserved.
Terms of use  Privacy  Cookie settings
""",
    "https://headphonebarn.example/anker-space-q45": """\
HeadphoneBarn
Your basket is empty
Anker Soundcore Space Q45 - price and review
Add to basket
The Anker Soundcore Space Q45 is $99.
Rated 4.4 out of 5 from 31,200 reviews.
What owners say
Owners report battery life of nearly two full working weeks.
The value for money here is very hard to argue with at this price.
Cons
The app is cluttered and the treble is dull, which several buyers complained of.
Reviewers felt the headband padding was flimsy for the money.
Customers also viewed
Soundcore Life Q30
The Soundcore Life Q30 is $59 and rated 4.5 out of 5 from 74,000 reviews.
Sennheiser Accentum
The Sennheiser Accentum is $179 and rated 4.2 out of 5 from 3,400 reviews.
JLab JBuds Lux ANC
The JLab JBuds Lux ANC is $79 and rated 4.1 out of 5 from 8,900 reviews.
Frequently bought together
Carry case, $19
Replacement earpads, $24
Delivery information
Returns accepted within 30 days
Track your order
Contact customer services
""",
    "https://gearroundup.example/best-noise-cancelling": """\
GearRoundup
9 Best Noise Cancelling Headphones Under $400
Updated August 2026
1. Sony WH-1000XM5 - best overall
The Sony WH-1000XM5 remains our overall pick at $328.
Rated 4.7 out of 5 from 12,480 reviews across the shops we track.
2. Sennheiser Accentum - best value
The Sennheiser Accentum is $179 and rated 4.2 out of 5 from 3,400 reviews.
Comfort is excellent on a long flight, and reviewers praised the clamping force.
Call quality is merely acceptable, which is the one complaint we heard often.
3. Bose QuietComfort Ultra - best for calls
The Bose QuietComfort Ultra is $349 and rated 4.3 out of 5 from 5,600 reviews.
Testers found the immersive audio mode gimmicky but the isolation outstanding.
4. Soundcore Life Q30 - the cheapest pick here
The Soundcore Life Q30 is $59 and rated 4.5 out of 5 from 74,000 reviews.
Buyers loved the price and complained about the muddy bass in equal measure.
5. JLab JBuds Lux ANC - best under $100
The JLab JBuds Lux ANC is $79 and rated 4.1 out of 5 from 8,900 reviews.
More from GearRoundup
How we test
Affiliate disclosure
Sign up to the newsletter
""",
    "https://eurotech.example/sony-wh-1000xm5-preis": """\
EuroTech Shop
Home  Audio  Headphones  Accessories
In stock, ships today
Sony WH-1000XM5 Wireless Noise Cancelling Headphones
Price: 329 EUR
Free shipping within the EU
Buyers noticed the carrying case is smaller than the previous generation.
Owners recommend the carrying pouch sold separately at 29 EUR.
Also in this range
Sennheiser Accentum Wireless
Price: 169 EUR
Bose QuietComfort Ultra
Price: 359 EUR
Delivery in 2-4 working days
VAT included
Payment methods
Customer services
""",
    "https://soundcheck.example/anker-vs-sennheiser": """\
SoundCheck
Anker Space Q45 vs Sennheiser Accentum: which should you buy
The Anker Soundcore Space Q45 sells for $99 and the Sennheiser Accentum $179.
Bottom line
The Accentum is the one we recommend for a commuter on a budget.
Reviewers found the Anker's noise cancelling merely mediocre next to the Sony.
Both are sturdy enough to live in a bag for a year.
Users liked the Accentum's controls and disliked its cramped earcups.
Scores
We rated the Sennheiser Accentum 4.2 out of 5 from 3,400 reviews.
We rated the Anker Soundcore Space Q45 4.4 out of 5 from 31,200 reviews.
The Sony WH-1000XM5 is $328 if your budget stretches that far.
Related articles
Subscribe to SoundCheck
Follow us
""",
    "https://audiodeal.example/sony-wh-1000xm5": """\
AudioDeal
Sony WH-1000XM5 Wireless Headphones - Black
Sale price $299
Was $348, you save $49
Limited stock
Owners recommend buying while the sale lasts.
Reviewers found the fit comfortable over a full working day.
You may also like
Apple AirPods Max
The Apple AirPods Max is $479 and rated 4.6 out of 5 from 9,100 reviews.
Bose QuietComfort Ultra
The Bose QuietComfort Ultra is $349 and rated 4.3 out of 5 from 5,600 reviews.
Shipping and returns
Gift wrapping available
Store locator
""",
    "https://cansreview.example/bose-quietcomfort-ultra": """\
CansReview
Bose QuietComfort Ultra review
The Bose QuietComfort Ultra is $349.
Rated 4.3 out of 5 from 5,600 reviews.
Pros and cons
In our tests the isolation was outstanding on a noisy train.
Testers found the immersive mode gimmicky and switched it off within a day.
The drawback is a battery that is merely mediocre next to the Sony.
Owners praised the folding hinge and the case.
How it compares
The Sony WH-1000XM5 is $328 and rated 4.7 out of 5 from 12,480 reviews.
The Sennheiser Accentum is $179 and rated 4.2 out of 5 from 3,400 reviews.
Scoring
Comfort 9, sound 8, noise cancelling 9
About the author
Comments are closed
""",
    "https://flightgear.example/headphones-for-long-haul": """\
FlightGear
Headphones for long-haul flights
What we look for on a plane
Comfort is excellent on the Sony WH-1000XM5 even after ten hours.
The Sony WH-1000XM5 is $328 and rated 4.7 out of 5 from 12,480 reviews.
Reviewers found the Sennheiser Accentum cramped on a long sector.
The Sennheiser Accentum is $179.
Owners report the Anker Soundcore Space Q45 lasts three flights on a charge.
The Anker Soundcore Space Q45 is $99 and rated 4.4 out of 5 from 31,200 reviews.
Bottom line
The Sony is worth the money if you fly monthly.
Cabin crew we asked recommend anything with a wired fallback.
Next article
Travel newsletter
""",
    "https://dealtracker.example/anc-price-history": """\
DealTracker
Noise cancelling price history
Prices tracked across 40 retailers
Sony WH-1000XM5
Current best price $328, lowest ever $279
Bose QuietComfort Ultra
Current best price $349, lowest ever $329
Sennheiser Accentum
Current best price $179, lowest ever $149
Soundcore Life Q30
Current best price $59, lowest ever $49
JLab JBuds Lux ANC
Current best price $79, lowest ever $69
Buyers found the January sales the best value of the year.
Set a price alert
How our tracking works
Retailer list
""",
    "https://budgetaudio.example/cheap-anc": """\
BudgetAudio
Cheap noise cancelling: what is actually worth it
The JLab JBuds Lux ANC is rated 4.1 out of 5 from 8,900 reviews.
We could not confirm a price we trusted, so treat the listings with care.
In our tests the JLab was underwhelming above 1kHz but excellent on a plane.
Users wished the app remembered its EQ settings between sessions.
Owners found the fit uncomfortable for anyone wearing glasses.
Cheaper still
The Soundcore Life Q30 is $59 and rated 4.5 out of 5 from 74,000 reviews.
Buyers recommend it as the best value in the category by some distance.
The Anker Soundcore Space Q45 is $99 and rated 4.4 out of 5 from 31,200 reviews.
Newsletter signup
About BudgetAudio
Advertise with us
""",
}

#: The web these tests search, as ``search_web`` returns it: title, URL and
#: snippet, with ``content`` still empty because nothing has been fetched yet.
#: :func:`fake_web` fills that in the way :mod:`buy_agent.fetch` would.
#:
#: The third is a listicle, which is the mistake this pipeline exists to catch:
#: a small model reports its headline as a product, and ``clean_products`` is
#: what stops "9 Best Noise Cancelling Headphones Under $400" reaching the top 3.
#: The fourth prices the Sony a second time, in another currency and without a
#: rating, so the merge in ``extraction._fill_gaps`` has a real conflict to get
#: right rather than two copies of one listing to agree with itself (ADR-0022).
PAGES: tuple[SearchResult, ...] = (
    SearchResult(
        title="Sony WH-1000XM5 review: still the one to beat | AudioSite",
        url="https://audiosite.example/sony-wh-1000xm5-review",
        snippet="The Sony WH-1000XM5 sells for $328 and is rated 4.7 out of 5.",
    ),
    SearchResult(
        title="Anker Soundcore Space Q45 - price and review",
        url="https://headphonebarn.example/anker-space-q45",
        snippet="Anker Soundcore Space Q45, $99, rated 4.4 out of 5.",
    ),
    SearchResult(
        title="9 Best Noise Cancelling Headphones Under $400 | GearRoundup",
        url="https://gearroundup.example/best-noise-cancelling",
        snippet="Our picks: the Sony WH-1000XM5 at $328, the Sennheiser Accentum at $179.",
    ),
    SearchResult(
        title="Sony WH-1000XM5 Wireless Noise Cancelling Headphones | EuroTech",
        url="https://eurotech.example/sony-wh-1000xm5-preis",
        snippet="Sony WH-1000XM5, 329 EUR, in stock and shipping today.",
    ),
    SearchResult(
        title="Anker Space Q45 vs Sennheiser Accentum | SoundCheck",
        url="https://soundcheck.example/anker-vs-sennheiser",
        snippet="Two of the best budget noise cancellers, compared.",
    ),
    SearchResult(
        title="Sony WH-1000XM5 Wireless Headphones - Black | AudioDeal",
        url="https://audiodeal.example/sony-wh-1000xm5",
        snippet="Sony WH-1000XM5 on sale at $299, was $348.",
    ),
    SearchResult(
        title="Bose QuietComfort Ultra review | CansReview",
        url="https://cansreview.example/bose-quietcomfort-ultra",
        snippet="The Bose QuietComfort Ultra is $349, rated 4.3 out of 5.",
    ),
    SearchResult(
        title="Headphones for long-haul flights | FlightGear",
        url="https://flightgear.example/headphones-for-long-haul",
        snippet="What actually works on a ten-hour sector, and what does not.",
    ),
    SearchResult(
        title="Noise cancelling price history | DealTracker",
        url="https://dealtracker.example/anc-price-history",
        snippet="Current and lowest-ever prices across 40 retailers.",
    ),
    SearchResult(
        title="Cheap noise cancelling: what is actually worth it | BudgetAudio",
        url="https://budgetaudio.example/cheap-anc",
        snippet="The JLab JBuds Lux ANC is rated 4.1 out of 5 from 8,900 reviews.",
    ),
)

#: What the shopper typed. Deliberately vague, so query refinement has something
#: to do: a request that already reads like a query proves nothing about it.
REQUEST = "comfortable noise cancelling headphones for flights, under $350"


@dataclass(frozen=True, slots=True)
class LiveRun:
    """One end-to-end run, plus what the model said before Python judged it.

    ``extracted`` is the extraction chain's own answer, kept so a test can tell
    the two failures apart: a model that read nothing off the pages, and a model
    that read plenty and had it all thrown out by grounding.

    ``pages`` is what the agent was actually given -- the results as ``enrich``
    left them, condensed. Not :data:`PAGES`, which still has empty ``content``:
    grounding runs over the condensed text, so a test that rebuilt its haystack
    out of the un-enriched results would be checking a different corpus from the
    one the pipeline checked against.
    """

    ranked: list[RankedProduct]
    extracted: ProductList
    pages: tuple[SearchResult, ...]


def _unreachable_base_url() -> str:
    """A loopback URL nothing is listening on.

    A port the kernel has just handed out and nothing has bound since, which is
    the closest thing to a reserved one available: closing it does not hold it,
    so this is a small race rather than a guarantee. What it buys over a
    hard-coded number is that no other test here, and no service on the runner,
    is already sitting on it.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}"


@pytest.fixture(scope="session")
def base_url() -> str:
    """Where Ollama is, honouring ``$OLLAMA_HOST`` as the rest of the project does."""
    return DEFAULT_BASE_URL


@pytest.fixture(scope="session")
def tiny_model(base_url: str) -> str:
    """The model tag to test against, once it is known to be pulled.

    Missing Ollama is a skip on a developer's machine and a failure on the
    nightly runner: locally these tests are opt-in, and a scheduled run that
    quietly skipped every one of them would be a green job reporting nothing.
    ``.github/workflows/integration.yml`` is what sets :data:`REQUIRE_ENV_VAR`.
    """
    tag = os.getenv(MODEL_ENV_VAR, TINY_MODEL)
    try:
        installed = list_models(AgentConfig(base_url=base_url))
    except Exception as exc:  # noqa: BLE001 -- any transport failure means "not there"
        _absent(f"No Ollama at {base_url} ({exc}). Start it with: ollama serve")
    if tag not in installed:
        _absent(f"Ollama at {base_url} has no {tag!r}. Pull it with: ollama pull {tag}")
    return tag


def _absent(reason: str) -> NoReturn:
    """Skip, unless the environment says these tests were meant to run.

    ``NoReturn`` because both branches raise: the caller reads ``installed``
    straight after, and it is bound only where this was not reached.
    """
    if os.getenv(REQUIRE_ENV_VAR):
        pytest.fail(reason)
    pytest.skip(reason)


@pytest.fixture(scope="session")
def live_config(tiny_model: str, base_url: str) -> AgentConfig:
    """The shipped defaults, on the tiny model.

    Only the model, the server and the widths move. ``temperature``, ``num_ctx``
    and ``reasoning`` are left exactly as the agent ships them (ADR-0019),
    because whether those defaults still make a small model answer with JSON
    instead of thinking until the context runs out is one of the things a live
    run is here to find out -- which needs a prompt wide enough for the question
    to arise. Ten condensed pages put the extraction prompt at ~9.5k characters,
    near enough 2.4k tokens: comfortable on the 8192 this asks for, and about
    1.7k left for thinking and JSON on Ollama's 4096 default, which is where a
    thinking model runs out. It is not the ~4.3k a ten-result run of real pages
    reaches -- fabricated pages are thinner than fetched ones -- but it is the
    same order, where three pages of prose were not.

    ``search_results`` is therefore the shipped default rather than a number
    chosen here. ``num_products`` is below the seven distinct products the pages
    name, so ``deduplicate``'s limit is a cap that has to bite rather than a
    ceiling nothing reaches.
    """
    return AgentConfig(
        model=tiny_model,
        base_url=base_url,
        search_results=len(PAGES),
        num_products=5,
        top_n=3,
    )


@pytest.fixture(scope="session", autouse=True)
def fake_web() -> Iterator[list[SearchResult]]:
    """Hand every agent in this session :data:`PAGES` instead of the web.

    ``autouse``, so that "nothing here reaches DuckDuckGo" is a property of the
    package rather than of each test remembering to ask for it. The tests that
    point an agent at a stopped Ollama call ``run()`` too, and today they are
    safe only because ``_refine_query`` re-raises ``ModelUnavailableError``
    before the search -- one change to that and an unrelated failure would start
    going out over the network.

    ``enrich`` is faked only as far as the fetch: the text comes from
    :data:`_PAGE_TEXT` rather than from a URL, and then goes through the real
    :func:`buy_agent.fetch.condense` on the config's own budgets. What it yields
    is the condensed results, which :func:`live_run` keeps -- the pipeline
    grounds against that text, so the tests have to read it and not the raw page.

    ``monkeypatch`` is function-scoped, so the patching is done by hand: these
    two names are replaced for the whole session and put back at the end of it.
    """
    served: list[SearchResult] = []

    def search(query: str, *, max_results: int = 10, region: str = "us-en") -> list:
        return [result.model_copy() for result in PAGES[:max_results]]

    def enrich(
        results: list, *, max_chars: int = 1200, opinion_chars: int = 400, **_: object
    ) -> list:
        served[:] = [
            result.model_copy(
                update={
                    "content": condense(
                        _PAGE_TEXT[result.url],
                        max_chars=max_chars,
                        opinion_chars=opinion_chars,
                    )
                }
            )
            for result in results
        ]
        return list(served)

    original = agent_module.search_web, agent_module.enrich
    agent_module.search_web, agent_module.enrich = search, enrich
    yield served
    agent_module.search_web, agent_module.enrich = original


@pytest.fixture(scope="session")
def live_run(live_config: AgentConfig, fake_web: list[SearchResult]) -> LiveRun:
    """One real run of the whole pipeline, shared by every test that reads it.

    The extraction chain is wrapped rather than replaced: the appended step
    records what came back and hands it straight on, so the run underneath is
    the one ``BuyAgent`` would have made on its own.
    """
    seen: list[ProductList] = []

    def record(products: ProductList) -> ProductList:
        seen.append(products)
        return products

    agent = BuyAgent(live_config)
    agent.extraction_chain = agent.extraction_chain | RunnableLambda(record)
    ranked = agent.run(REQUEST)

    assert seen, "the extraction chain was never invoked"
    assert fake_web, "the agent never fetched the pages it was given"
    return LiveRun(ranked=ranked, extracted=seen[-1], pages=tuple(fake_web))


@pytest.fixture(scope="session")
def unreachable_base_url() -> str:
    """A loopback address with nothing behind it, for the transport error paths."""
    return _unreachable_base_url()
