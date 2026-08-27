"""The fabricated web the WWII books demo searches, and what the fake model reads.

Shaped the way ``integration/conftest.py`` shapes its own fixtures: a title, the
line under it carrying a figure, a few lines of verdict, and the navigation and
legal boilerplate that make up most of a real page. Written to be *condensed*
rather than read -- every line meant to survive
:func:`buy_agent.fetch.condense` is one ``quotes_a_figure`` or
``reads_like_an_opinion`` accepts, and the rest is there to be thrown away.

The order of :data:`PAGES` is load-bearing for the recording: each book's own
page comes before the two round-ups that merely cross-reference it, because
``verification.attribute_sources`` links a product to the *first* searched page
that mentions it. Cross-references are therefore kept to those two pages, where
they still give the pooled haystack something to agree with.

:data:`EXTRACTED` is what the fake model claims it read off them, and it is
deliberately imperfect in the six ways a small model is imperfect, so the
progress log in the recording shows the pipeline doing its job rather than
agreeing with itself:

* a listicle headline reported as a product, for ``clean_products``;
* a book no page mentions, for ``drop_ungrounded``;
* a price no page printed, for ``verify_numbers``;
* a link to a page that was never searched, for ``attribute_sources``;
* a verdict nobody wrote, for ``verify_opinions``;
* one book listed twice, in two currencies, for ``deduplicate``.

The book titles and authors are real. The shops, the prices, the ratings, the
review counts and the quoted verdicts are invented, on ``*.example`` hosts that
cannot resolve; none of it is a claim about a real seller or a real reviewer.
"""

from __future__ import annotations

from buy_agent.models import ExtractedProduct, ProductList
from buy_agent.search import SearchResult

#: What the shopper types into the form. Vague enough that refining it into a
#: search query is a step with something to do.
REQUEST = "wwii books about war in Europe 1944-45"

#: What the fake model refines :data:`REQUEST` into.
REFINED_QUERY = "best WWII history books Western Front Europe 1944 1945 buy"

#: What each page in :data:`PAGES` says, before :func:`buy_agent.fetch.condense`
#: gets to it. Read by ``demo/server.py`` in place of a fetch, and by
#: ``demo/record.mjs`` to answer for a shop a ``--follow-link`` take clicks
#: through to -- those hosts cannot resolve.
PAGE_TEXT: dict[str, str] = {
    "https://warhistorydesk.example/guns-at-last-light": """\
War History Desk
Home  Reviews  Reading lists  About
The Guns at Last Light: The War in Western Europe, 1944-1945 by Rick Atkinson
Paperback, 877 pages
The Guns at Last Light is $18.99 in paperback.
Rated 4.8 out of 5 from 3,240 reviews.
Verdict
Critics praised the reporting on the last winter as the finest in the trilogy.
The narrative is outstanding on the campaign from Normandy to the Elbe.
Owners report the third volume stands perfectly well on its own.
The drawback is the length, which several buyers complained of.
Specifications
Publisher: demo listing
ISBN: 000-0000000000
Shipping and returns
Copyright 2026 War History Desk. All rights reserved.
Terms of use  Privacy  Cookie settings
""",
    "https://frontlinebooks.example/armageddon-hastings": """\
Frontline Books
Your basket is empty
Armageddon: The Battle for Germany, 1944-1945 by Max Hastings
Add to basket
Armageddon: The Battle for Germany, 1944-1945 is $16.50.
Rated 4.6 out of 5 from 2,110 reviews.
What readers say
Buyers found the chapters on the eastern approaches the strongest here.
The bottom line is a hard, unsentimental account of the last nine months.
Critics complained that the generals get rougher treatment than the evidence bears.
Delivery information
Returns accepted within 30 days
Track your order
Contact customer services
""",
    "https://normandyreads.example/d-day-beevor": """\
Normandy Reads
D-Day: The Battle for Normandy by Antony Beevor - review and price
D-Day: The Battle for Normandy is $14.99.
Rated 4.7 out of 5 from 9,480 reviews.
Pros and cons
Owners recommend it as the first book to read on the Normandy landings.
Readers found the maps sparse for a campaign this complicated.
The chapters on Caen are superb and the ones on the bocage are punchy.
About the author
Comments are closed
Newsletter signup
""",
    "https://battlefieldpress.example/ardennes-1944": """\
Battlefield Press
Ardennes 1944: The Battle of the Bulge by Antony Beevor
Ardennes 1944: The Battle of the Bulge is $15.75.
Rated 4.5 out of 5 from 4,020 reviews.
Bottom line
Reviewers found the account of the Malmedy killings sober and well sourced.
Buyers praised the pacing once the German offensive stalls.
The downside is that the American command squabbles run long.
How we choose our stock
Affiliate disclosure
Sign up to the newsletter
""",
    "https://classicwarbooks.example/a-bridge-too-far": """\
Classic War Books
A Bridge Too Far by Cornelius Ryan
A Bridge Too Far is $12.99.
Rated 4.7 out of 5 from 6,300 reviews.
What owners say
Owners report it reads like a novel and still holds up after fifty years.
Critics found the interviews with Dutch civilians the best value in the book.
Users wished the paperback had kept the original photographs.
Store locator
Gift wrapping available
Shipping and returns
""",
    "https://readersfront.example/band-of-brothers": """\
Readers Front
Band of Brothers by Stephen E. Ambrose
Band of Brothers is $11.49.
Rated 4.8 out of 5 from 21,600 reviews.
Verdict
Readers found it the easiest way into the campaign from Normandy to Bavaria.
Critics complained that the sourcing is thinner than the reputation suggests.
The bottom line is that it is worth the money as a first book and little more.
Follow us
Subscribe to Readers Front
""",
    "https://kershawreview.example/the-end-hitlers-germany": """\
Kershaw Review
The End: Hitler's Germany, 1944-45 by Ian Kershaw
Rated 4.4 out of 5 from 1,180 reviews.
We could not confirm a price we trusted, so treat the listings with care.
Pros and cons
Critics praised the answer it gives to why the regime never surrendered.
Reviewers found the middle third mediocre next to the opening.
Owners report it is the one to read after Armageddon.
Scoring
Argument 9, readability 7, sourcing 9
Comments are closed
Advertise with us
""",
    "https://eurobookshop.example/d-day-beevor-eur": """\
Euro Book Shop
Home  History  Second World War  Accessories
In stock, ships today
D-Day: The Battle for Normandy, new edition - Antony Beevor
Price: 16 EUR
Free shipping within the EU
Buyers noticed this printing is on thinner paper than the hardback.
Owners recommend the hardback if it is going on a shelf for good.
Delivery in 2-4 working days
VAT included
Payment methods
Customer services
""",
    "https://historyroundup.example/best-books-1944-45": """\
History Roundup
The 12 Best WWII Books About Europe 1944-45
Updated August 2026
1. The Guns at Last Light - best overall
The Guns at Last Light is $18.99 and rated 4.8 out of 5 from 3,240 reviews.
2. D-Day: The Battle for Normandy - best on the landings
D-Day: The Battle for Normandy is $14.99 and rated 4.7 out of 5 from 9,480 reviews.
Reviewers found it the most readable single volume on the campaign.
3. Armageddon: The Battle for Germany, 1944-1945 - best on the last winter
Armageddon: The Battle for Germany, 1944-1945 is $16.50 and rated 4.6 out of 5 from 2,110 reviews.
4. A Bridge Too Far - best on Market Garden
A Bridge Too Far is $12.99 and rated 4.7 out of 5 from 6,300 reviews.
5. Band of Brothers - best for a first reader
Band of Brothers is $11.49 and rated 4.8 out of 5 from 21,600 reviews.
6. Ardennes 1944: The Battle of the Bulge
Ardennes 1944: The Battle of the Bulge is $15.75 and rated 4.5 out of 5 from 4,020 reviews.
7. Berlin: The Downfall 1945
Berlin: The Downfall 1945 is $13.99 and rated 4.6 out of 5 from 7,150 reviews.
More from History Roundup
How we test
Affiliate disclosure
""",
    "https://bookpricewatch.example/wwii-1944-45-prices": """\
Book Price Watch
WWII 1944-45 price history
Prices tracked across 40 sellers
The Guns at Last Light
Current best price $18.99, lowest ever $12.40
D-Day: The Battle for Normandy
Current best price $14.99, lowest ever $10.60
Armageddon: The Battle for Germany, 1944-1945
Current best price $16.50, lowest ever $11.20
A Bridge Too Far
Current best price $12.99, lowest ever $8.49
Band of Brothers
Current best price $11.49, lowest ever $8.15
Berlin: The Downfall 1945
Current best price $13.99, lowest ever $9.25
Buyers found the January sales the best value of the year.
Set a price alert
How our tracking works
Seller list
""",
}

#: The web this demo searches, as ``search_web`` returns it: title, URL and
#: snippet, with ``content`` still empty because nothing has been fetched yet.
PAGES: tuple[SearchResult, ...] = (
    SearchResult(
        title="The Guns at Last Light: The War in Western Europe, 1944-1945 | War History Desk",
        url="https://warhistorydesk.example/guns-at-last-light",
        snippet="Rick Atkinson's last volume, $18.99, rated 4.8 out of 5.",
    ),
    SearchResult(
        title="D-Day: The Battle for Normandy - review and price | Normandy Reads",
        url="https://normandyreads.example/d-day-beevor",
        snippet="Antony Beevor on the Normandy campaign, $14.99, rated 4.7 out of 5.",
    ),
    SearchResult(
        title="Ardennes 1944: The Battle of the Bulge | Battlefield Press",
        url="https://battlefieldpress.example/ardennes-1944",
        snippet="Antony Beevor on the German offensive of December 1944, $15.75.",
    ),
    SearchResult(
        title="Armageddon: The Battle for Germany, 1944-1945 | Frontline Books",
        url="https://frontlinebooks.example/armageddon-hastings",
        snippet="Max Hastings on the last nine months, $16.50, rated 4.6 out of 5.",
    ),
    SearchResult(
        title="A Bridge Too Far | Classic War Books",
        url="https://classicwarbooks.example/a-bridge-too-far",
        snippet="Cornelius Ryan on Market Garden, $12.99, rated 4.7 out of 5.",
    ),
    SearchResult(
        title="Band of Brothers | Readers Front",
        url="https://readersfront.example/band-of-brothers",
        snippet="Stephen E. Ambrose on Easy Company, $11.49, rated 4.8 out of 5.",
    ),
    SearchResult(
        title="The End: Hitler's Germany, 1944-45 | Kershaw Review",
        url="https://kershawreview.example/the-end-hitlers-germany",
        snippet="Ian Kershaw on why the regime never surrendered. Rated 4.4 out of 5.",
    ),
    SearchResult(
        title="D-Day: The Battle for Normandy, new edition | Euro Book Shop",
        url="https://eurobookshop.example/d-day-beevor-eur",
        snippet="D-Day: The Battle for Normandy, 16 EUR, in stock and shipping today.",
    ),
    SearchResult(
        title="The 12 Best WWII Books About Europe 1944-45 | History Roundup",
        url="https://historyroundup.example/best-books-1944-45",
        snippet="Our picks, from The Guns at Last Light at $18.99 down to Band of Brothers at $11.49.",
    ),
    SearchResult(
        title="WWII 1944-45 price history | Book Price Watch",
        url="https://bookpricewatch.example/wwii-1944-45-prices",
        snippet="Current and lowest-ever prices across 40 sellers.",
    ),
)

#: What the fake model says it read off :data:`PAGES`. See the module docstring
#: for what each of the deliberate mistakes is there to exercise.
EXTRACTED = ProductList(
    products=[
        ExtractedProduct(
            name="The Guns at Last Light: The War in Western Europe, 1944-1945",
            price=18.99,
            currency="usd",
            rating=4.8,
            review_count=3240,
            seller="War History Desk",
            notes="Rick Atkinson. Third volume of the Liberation Trilogy.",
            opinions=[
                "Critics praised the reporting on the last winter as the finest in the trilogy.",
                "The narrative is outstanding on the campaign from Normandy to the Elbe.",
                # Nobody wrote this: verify_opinions drops it.
                "Reviewers called it the definitive account of the entire war.",
            ],
        ),
        ExtractedProduct(
            name="D-Day: The Battle for Normandy",
            price=14.99,
            currency="usd",
            rating=4.7,
            review_count=9480,
            seller="Normandy Reads",
            notes="Antony Beevor on the Normandy campaign.",
            opinions=[
                "Owners recommend it as the first book to read on the Normandy landings.",
                "Readers found the maps sparse for a campaign this complicated.",
            ],
        ),
        # The same book, priced again in another currency and without a rating:
        # a real conflict for _fill_gaps to get right rather than two copies of
        # one listing agreeing with itself (ADR-0022). The names differ only by
        # words in GENERIC_WORDS, which is what merge_variants folds together.
        ExtractedProduct(
            name="D-Day: The Battle for Normandy, new edition",
            price=16.0,
            currency="EUR",
            seller="Euro Book Shop",
            opinions=["Owners recommend the hardback if it is going on a shelf for good."],
        ),
        ExtractedProduct(
            name="A Bridge Too Far",
            price=12.99,
            currency="usd",
            rating=4.7,
            review_count=6300,
            seller="Classic War Books",
            notes="Cornelius Ryan on Operation Market Garden.",
            opinions=[
                "Owners report it reads like a novel and still holds up after fifty years.",
                "Critics found the interviews with Dutch civilians the best value in the book.",
            ],
        ),
        ExtractedProduct(
            name="Band of Brothers",
            price=11.49,
            currency="usd",
            rating=4.8,
            review_count=21600,
            seller="Readers Front",
            notes="Stephen E. Ambrose on Easy Company, Normandy to Bavaria.",
            opinions=[
                "Readers found it the easiest way into the campaign from Normandy to Bavaria.",
                "Critics complained that the sourcing is thinner than the reputation suggests.",
            ],
        ),
        ExtractedProduct(
            name="Armageddon: The Battle for Germany, 1944-1945",
            price=16.50,
            currency="usd",
            rating=4.6,
            review_count=2110,
            seller="Frontline Books",
            notes="Max Hastings on the last nine months in the west and the east.",
            opinions=[
                "Buyers found the chapters on the eastern approaches the strongest here.",
                "The bottom line is a hard, unsentimental account of the last nine months.",
            ],
        ),
        ExtractedProduct(
            name="Ardennes 1944: The Battle of the Bulge",
            price=15.75,
            currency="usd",
            rating=4.5,
            review_count=4020,
            seller="Battlefield Press",
            # A page that was never searched: attribute_sources drops the link
            # and puts the searched page that mentions the book in its place.
            url="https://ardennes-books.example/buy-now",
            notes="Antony Beevor on the German offensive of December 1944.",
            opinions=[
                "Reviewers found the account of the Malmedy killings sober and well sourced.",
            ],
        ),
        ExtractedProduct(
            name="The End: Hitler's Germany, 1944-45",
            # No page printed this price -- that listing says outright that it
            # could not confirm one -- so verify_numbers blanks it, and the
            # currency goes down with it (ADR-0022).
            price=7.25,
            currency="usd",
            rating=4.4,
            review_count=1180,
            seller="Kershaw Review",
            notes="Ian Kershaw on why the regime never surrendered.",
            opinions=["Critics praised the answer it gives to why the regime never surrendered."],
        ),
        # An article headline, not a product: clean_products drops it.
        ExtractedProduct(
            name="The 12 Best WWII Books About Europe 1944-45",
            price=18.99,
            currency="usd",
        ),
        # An invented title no page mentions: drop_ungrounded removes it.
        ExtractedProduct(
            name="Blitzkrieg Twilight: A Wehrmacht Chronicle",
            price=21.00,
            currency="usd",
            rating=4.9,
            review_count=15000,
        ),
    ]
)
