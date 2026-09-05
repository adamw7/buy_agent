"""The fixed web this benchmark searches, and the run settings it searches with.

Moved here from ``integration/conftest.py``, which now reads it back: a corpus
and its answer key have to be one thing that cannot drift apart, and it keeps the
nightly job to *one* model call, :mod:`integration.test_benchmark` scoring the
same run the invariant tests read.

The product names are real; the sites are ``*.example`` hosts that cannot
resolve, and every price, rating, review count and verdict is invented.
"""

from __future__ import annotations

from buy_agent.config import AgentConfig
from buy_agent.search import SearchResult

#: How many products the run keeps, and how many it reports. Below the seven
#: distinct products :data:`PAGE_TEXT` names, so ``deduplicate``'s limit is a cap
#: that has to bite rather than a ceiling nothing reaches -- which is also why
#: :func:`benchmark.scoring.score_run` measures recall against the *cap* and not
#: against the whole answer key.
NUM_PRODUCTS = 5
TOP_N = 3


def settings(**overrides: object) -> AgentConfig:
    """The config a benchmark run uses: the shipped defaults, on this corpus.

    Only the widths move. ``temperature``, ``num_ctx`` and ``reasoning`` stay as
    the agent ships them (ADR-0019) -- whether those defaults still make a small
    model answer with JSON rather than think until the context runs out is one of
    the things a live run is here to find out, which needs a prompt wide enough
    for the question to arise. Ten condensed pages put the extraction prompt at
    ~9.5k characters, near enough 2.4k tokens.

    Args:
        **overrides: Fields to set instead -- the model and the server, which
            belong to whoever is being scored rather than to the corpus, and
            ``num_products`` where a run is given more room.
    """
    fields: dict[str, object] = {
        "search_results": len(PAGES),
        "num_products": NUM_PRODUCTS,
        "top_n": TOP_N,
        # Nothing here is remembered between runs (ADR-0044). The pages are the
        # corpus and never fetched, so the page half would do nothing -- but the
        # *answers* are the thing being measured, and a scored run that replayed
        # yesterday's would report yesterday's model. A benchmark asks the model.
        "cache_ttl": 0,
    }
    return AgentConfig(**(fields | overrides))  # type: ignore[arg-type]


#: The pages behind the results, as :func:`buy_agent.fetch.fetch_page` would have
#: found them: a product name, the line carrying the figure, a few lines of
#: verdict, and the navigation, specifications and legal boilerplate that make up
#: most of a real page. Fabricated, an assertion about a real shop's listing being
#: a nightly failure about the shop.
#:
#: Written to be *condensed*, not read. Every line meant to survive is one
#: :func:`buy_agent.fetch.quotes_a_figure` or
#: :func:`buy_agent.fetch.reads_like_an_opinion` accepts, and the rest is there to
#: be discarded -- the half a fixture of tidy prose cannot test. A verdict worded
#: outside ``fetch._OPINION``'s vocabulary never reaches the model in production,
#: so one worded that way here would be testing nothing.
#:
#: Ten of them, which is what ``search_results`` ships as, and each dense, because
#: the *width* of the prompt is under test too: ADR-0019's ``num_ctx=8192`` and
#: ``reasoning=False`` are about a prompt that fills the window, and three tidy
#: pages came to ~675 tokens, where the question cannot arise.
PAGE_TEXT: dict[str, str] = {
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

#: The web a benchmark run searches, as ``search_web`` returns it: title, URL and
#: snippet, ``content`` still empty because nothing has been fetched yet --
#: :func:`benchmark.runner.serving_the_corpus` fills it in as
#: :mod:`buy_agent.fetch` would.
#:
#: The third is a listicle, the mistake this pipeline exists to catch: a small
#: model reports its headline as a product, and ``clean_products`` is what stops
#: it reaching the top 3. The fourth prices the Sony a second time, in another
#: currency and without a rating, so ``extraction._fill_gaps`` has a real conflict
#: to get right rather than two copies of one listing (ADR-0022).
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
#: to do. Nothing downstream reads "comfortable" or "for flights" -- the ranking
#: is price, rating and review count, and comfort reaches the shopper as the
#: quoted verdicts -- so the benchmark scores neither.
REQUEST = "comfortable noise cancelling headphones for flights, under $350"

