"""The fabricated web the laptops demo searches, and what the fake model reads.

The second script, shaped exactly like :mod:`demo.books` and read through the
same five names: a request, what refining turns it into, ten pages, what those
pages say, and the answer the scripted model gives when it is asked to read
products off them.

The shopper asks for three things at once here -- a budget, a weight and a noise
level -- which is what the pages are written to answer: every laptop page
carries a price line, a rating line and a verdict about the fans or the kilos,
because those are the two kinds of line :func:`buy_agent.fetch.condense` keeps
and everything else on the page is there to be thrown away. Nothing in the
pipeline reads "not too heavy or loud": the ranking is price, rating and review
count, and the weights and the fan noise reach the shopper as the quotes on the
cards. That is the division of labour this project is built on -- the model
copies what the pages said, and Python decides the order.

The order of :data:`PAGES` is load-bearing for the recording: each laptop's own
page comes before the two round-ups that merely cross-reference it, because
``verification.attribute_sources`` links a product to the *first* searched page
that mentions it. Cross-references are kept to those two pages for the same
reason -- a name is grounded by its distinctive words, and a page naming a
second brand in passing can clear that bar for a laptop it is not about.

:data:`EXTRACTED` is what the fake model claims it read off them, and it is
deliberately imperfect in the six ways a small model is imperfect, so the
progress log in the recording shows the pipeline doing its job rather than
agreeing with itself:

* a listicle headline reported as a product, for ``clean_products``;
* a laptop no page mentions, for ``drop_ungrounded``;
* a price no page printed, for ``verify_numbers``;
* a link to a page that was never searched, for ``attribute_sources``;
* a verdict nobody wrote, for ``verify_opinions``;
* one laptop listed twice, in two currencies, for ``deduplicate``.

The last two matter to the recording's ending, which follows the top product's
link: the laptop that comes out first is the one the model gave an invented
link, so what the click opens is the page grounding put there instead.

The laptop model names are real. The shops, the prices, the ratings, the review
counts, the weights and the quoted verdicts are invented, on ``*.example`` hosts
that cannot resolve; none of it is a claim about a real seller, a real reviewer
or a real machine.
"""

from __future__ import annotations

from buy_agent.models import ExtractedProduct, ProductList
from buy_agent.search import SearchResult

#: What the shopper types into the form. Three constraints in one sentence, so
#: refining it into a search query is a step with something to do.
REQUEST = "new laptop below 1000 USD, not too heavy or loud. windows 11 installed"

#: What the fake model refines :data:`REQUEST` into.
REFINED_QUERY = "lightweight quiet Windows 11 laptop under $1000 price review"

#: What each page in :data:`PAGES` says, before :func:`buy_agent.fetch.condense`
#: gets to it. Read by ``demo/server.py`` in place of a fetch, and by
#: ``demo/record.mjs`` to answer for a shop a ``--follow-link`` take clicks
#: through to -- those hosts cannot resolve.
PAGE_TEXT: dict[str, str] = {
    "https://laptopbench.example/msi-modern-14": """\
Laptop Bench
Home  Reviews  Benchmarks  About
MSI Modern 14, Windows 11 Home, 1.4 kg
The MSI Modern 14 is $599.
Rated 4.3 out of 5 from 1,150 reviews.
Verdict
Reviewers found the fans stay quiet until a long export starts.
Owners report the 1.4 kg chassis disappears into a rucksack.
The downside is a dim screen for the money.
Specifications
Core i5, 16 GB memory, 512 GB storage, Windows 11 Home preinstalled
Ports and connectivity
Copyright 2026 Laptop Bench. All rights reserved.
Terms of use  Privacy  Cookie settings
""",
    "https://notebookdesk.example/dell-inspiron-14-plus": """\
Notebook Desk
Home  Laptops  Offers  Contact
Dell Inspiron 14 Plus, Windows 11 Home, 1.55 kg
The Dell Inspiron 14 Plus is $699.
Rated 4.4 out of 5 from 4,310 reviews.
What reviewers say
Buyers praised the keyboard and the tall 2.2K screen.
Testers found the fan audible but never shrill under load.
Critics complained that 1.55 kg is heavy for this size of machine.
Delivery and returns
Windows 11 Home preinstalled
Track your order
Contact customer services
""",
    "https://portablepc.example/lenovo-ideapad-slim-5": """\
Portable PC
Lenovo IdeaPad Slim 5 14, Windows 11 Home, 1.46 kg
The Lenovo IdeaPad Slim 5 14 is $749.
Rated 4.6 out of 5 from 3,140 reviews.
Verdict
Owners report the fans are inaudible outside of a game.
Reviewers found the aluminium lid sturdy for the price.
The bottom line is the calmest machine we have measured this year.
About us
Windows 11 Home preinstalled
Newsletter signup
""",
    "https://ultralightreview.example/hp-pavilion-aero-13": """\
Ultralight Review
HP Pavilion Aero 13, Windows 11 Home, 0.97 kg
The HP Pavilion Aero 13 is $799.
Rated 4.6 out of 5 from 1,920 reviews.
Pros and cons
Owners report it is the lightest thing they have carried at 0.97 kg.
Reviewers found the fan spins up early but stays soft.
Users wished the webcam matched the rest of the machine.
How we test
Windows 11 Home preinstalled
Advertise with us
""",
    "https://screenandkeys.example/asus-zenbook-14-oled": """\
Screen and Keys
ASUS Zenbook 14 OLED, Windows 11 Home, 1.28 kg
The ASUS Zenbook 14 OLED is $899.
Rated 4.7 out of 5 from 5,480 reviews.
Bottom line
Critics praised the panel as the best anywhere near this money.
Testers found the fans louder than most rivals under sustained load.
Owners report the 1.28 kg body is easy to carry all day.
Windows 11 Home preinstalled
Comments are closed
""",
    "https://carryonlaptops.example/acer-swift-go-14": """\
Carry On Laptops
Acer Swift Go 14, Windows 11 Home, 1.32 kg
The Acer Swift Go 14 is $829.
Rated 4.5 out of 5 from 2,260 reviews.
What owners say
Owners report the fans are loud whenever the machine is charging.
Buyers found the 1.32 kg weight fine for a daily commute.
Critics found the trackpad mediocre next to the screen.
Store locator
Windows 11 Home preinstalled
Shipping and returns
""",
    "https://quietmachines.example/samsung-galaxy-book4": """\
Quiet Machines
Samsung Galaxy Book4, Windows 11 Home, 1.55 kg
Rated 4.4 out of 5 from 980 reviews.
We could not confirm a price we trusted, so treat the listings with care.
Pros and cons
Reviewers found it near silent on anything short of a render.
Owners report the plastic lid flexes more than they expected.
Critics complained the screen is dim for the class.
Scoring
Portability 8, noise 9, value 7
Windows 11 Home preinstalled
Advertise with us
""",
    "https://eurotechstore.example/lenovo-ideapad-slim-5-eur": """\
Euro Tech Store
Home  Laptops  Accessories  Support
In stock, ships today
Lenovo IdeaPad Slim 5 14, new edition - Windows 11 Home
Price: 689 EUR
Free shipping within the EU
Buyers noticed this batch ships with the smaller battery.
Owners recommend the 16 GB configuration if it is going to last.
Delivery in 2-4 working days
VAT included
Payment methods
Customer services
""",
    "https://laptoproundup.example/best-windows-11-laptops-under-1000": """\
Laptop Roundup
The 9 Best Windows 11 Laptops Under $1000
Updated August 2026
1. MSI Modern 14 - best overall
The MSI Modern 14 is $599 and rated 4.3 out of 5 from 1,150 reviews.
2. Dell Inspiron 14 Plus - best screen
The Dell Inspiron 14 Plus is $699 and rated 4.4 out of 5 from 4,310 reviews.
3. Lenovo IdeaPad Slim 5 14 - quietest
The Lenovo IdeaPad Slim 5 14 is $749 and rated 4.6 out of 5 from 3,140 reviews.
4. HP Pavilion Aero 13 - lightest
The HP Pavilion Aero 13 is $799 and rated 4.6 out of 5 from 1,920 reviews.
5. Acer Swift Go 14 - best ports
The Acer Swift Go 14 is $829 and rated 4.5 out of 5 from 2,260 reviews.
6. ASUS Zenbook 14 OLED - best panel
The ASUS Zenbook 14 OLED is $899 and rated 4.7 out of 5 from 5,480 reviews.
7. Samsung Galaxy Book4 - the quiet one
Reviewers found it the calmest machine on the whole list.
More from Laptop Roundup
How we test
Affiliate disclosure
""",
    "https://pricewatchpc.example/windows-laptops-price-history": """\
Price Watch PC
Windows 11 laptop price history
Prices tracked across 40 sellers
MSI Modern 14
Current best price $599, lowest ever $529
Dell Inspiron 14 Plus
Current best price $699, lowest ever $619
Lenovo IdeaPad Slim 5 14
Current best price $749, lowest ever $679
HP Pavilion Aero 13
Current best price $799, lowest ever $729
Acer Swift Go 14
Current best price $829, lowest ever $769
ASUS Zenbook 14 OLED
Current best price $899, lowest ever $819
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
        title="MSI Modern 14 | Laptop Bench",
        url="https://laptopbench.example/msi-modern-14",
        snippet="1.4 kg, Windows 11 Home, $599, rated 4.3 out of 5.",
    ),
    SearchResult(
        title="Dell Inspiron 14 Plus | Notebook Desk",
        url="https://notebookdesk.example/dell-inspiron-14-plus",
        snippet="1.55 kg, Windows 11 Home, $699, rated 4.4 out of 5.",
    ),
    SearchResult(
        title="Lenovo IdeaPad Slim 5 14 | Portable PC",
        url="https://portablepc.example/lenovo-ideapad-slim-5",
        snippet="1.46 kg, Windows 11 Home, $749, rated 4.6 out of 5.",
    ),
    SearchResult(
        title="HP Pavilion Aero 13 | Ultralight Review",
        url="https://ultralightreview.example/hp-pavilion-aero-13",
        snippet="0.97 kg, Windows 11 Home, $799, rated 4.6 out of 5.",
    ),
    SearchResult(
        title="ASUS Zenbook 14 OLED | Screen and Keys",
        url="https://screenandkeys.example/asus-zenbook-14-oled",
        snippet="1.28 kg, Windows 11 Home, $899, rated 4.7 out of 5.",
    ),
    SearchResult(
        title="Acer Swift Go 14 | Carry On Laptops",
        url="https://carryonlaptops.example/acer-swift-go-14",
        snippet="1.32 kg, Windows 11 Home, $829, rated 4.5 out of 5.",
    ),
    SearchResult(
        title="Samsung Galaxy Book4 | Quiet Machines",
        url="https://quietmachines.example/samsung-galaxy-book4",
        snippet="1.55 kg, Windows 11 Home, rated 4.4 out of 5. No price we trust.",
    ),
    SearchResult(
        title="Lenovo IdeaPad Slim 5 14, new edition | Euro Tech Store",
        url="https://eurotechstore.example/lenovo-ideapad-slim-5-eur",
        snippet="Lenovo IdeaPad Slim 5 14, 689 EUR, in stock and shipping today.",
    ),
    SearchResult(
        title="The 9 Best Windows 11 Laptops Under $1000 | Laptop Roundup",
        url="https://laptoproundup.example/best-windows-11-laptops-under-1000",
        snippet="Our picks, from the MSI Modern 14 at $599 up to the ASUS Zenbook 14 OLED at $899.",
    ),
    SearchResult(
        title="Windows 11 laptop price history | Price Watch PC",
        url="https://pricewatchpc.example/windows-laptops-price-history",
        snippet="Current and lowest-ever prices across 40 sellers.",
    ),
)

#: What the fake model says it read off :data:`PAGES`. See the module docstring
#: for what each of the deliberate mistakes is there to exercise.
EXTRACTED = ProductList(
    products=[
        ExtractedProduct(
            name="MSI Modern 14",
            price=599.0,
            currency="usd",
            rating=4.3,
            review_count=1150,
            seller="Laptop Bench",
            # A page that was never searched: attribute_sources drops the link
            # and puts the searched page that mentions it in its place -- which
            # is the link the recording ends by clicking.
            url="https://msi-deals.example/modern-14-buy-now",
            notes="Core i5, 16 GB, 512 GB. 1.4 kg.",
            opinions=[
                "Reviewers found the fans stay quiet until a long export starts.",
                "Owners report the 1.4 kg chassis disappears into a rucksack.",
            ],
        ),
        ExtractedProduct(
            name="Dell Inspiron 14 Plus",
            price=699.0,
            currency="usd",
            rating=4.4,
            review_count=4310,
            seller="Notebook Desk",
            notes="2.2K screen, 1.55 kg.",
            opinions=[
                "Testers found the fan audible but never shrill under load.",
                "Critics complained that 1.55 kg is heavy for this size of machine.",
                # Nobody wrote this: verify_opinions drops it.
                "Reviewers called it the quietest laptop of the year by some way.",
            ],
        ),
        ExtractedProduct(
            name="Lenovo IdeaPad Slim 5 14",
            price=749.0,
            currency="usd",
            rating=4.6,
            review_count=3140,
            seller="Portable PC",
            notes="Aluminium lid, 1.46 kg.",
            opinions=[
                "Owners report the fans are inaudible outside of a game.",
                "Reviewers found the aluminium lid sturdy for the price.",
            ],
        ),
        # The same laptop, priced again in another currency and without a
        # rating: a real conflict for _fill_gaps to get right rather than two
        # copies of one listing agreeing with itself (ADR-0022). The names
        # differ only by words in GENERIC_WORDS, which is what merge_variants
        # folds together.
        ExtractedProduct(
            name="Lenovo IdeaPad Slim 5 14, new edition",
            price=689.0,
            currency="EUR",
            seller="Euro Tech Store",
            opinions=["Owners recommend the 16 GB configuration if it is going to last."],
        ),
        ExtractedProduct(
            name="HP Pavilion Aero 13",
            price=799.0,
            currency="usd",
            rating=4.6,
            review_count=1920,
            seller="Ultralight Review",
            notes="Magnesium chassis, 0.97 kg.",
            opinions=[
                "Owners report it is the lightest thing they have carried at 0.97 kg.",
                "Reviewers found the fan spins up early but stays soft.",
            ],
        ),
        ExtractedProduct(
            name="ASUS Zenbook 14 OLED",
            price=899.0,
            currency="usd",
            rating=4.7,
            review_count=5480,
            seller="Screen and Keys",
            notes="OLED panel, 1.28 kg.",
            opinions=[
                "Critics praised the panel as the best anywhere near this money.",
                "Testers found the fans louder than most rivals under sustained load.",
            ],
        ),
        ExtractedProduct(
            name="Acer Swift Go 14",
            price=829.0,
            currency="usd",
            rating=4.5,
            review_count=2260,
            seller="Carry On Laptops",
            notes="Full port selection, 1.32 kg.",
            opinions=[
                "Owners report the fans are loud whenever the machine is charging.",
                "Critics found the trackpad mediocre next to the screen.",
            ],
        ),
        ExtractedProduct(
            name="Samsung Galaxy Book4",
            # No page printed this price -- that listing says outright that it
            # could not confirm one -- so verify_numbers blanks it, and the
            # currency goes down with it (ADR-0022).
            price=649.0,
            currency="usd",
            rating=4.4,
            review_count=980,
            seller="Quiet Machines",
            notes="Near silent under everyday load, 1.55 kg.",
            opinions=["Reviewers found it near silent on anything short of a render."],
        ),
        # An article headline, not a product: clean_products drops it.
        ExtractedProduct(
            name="The 9 Best Windows 11 Laptops Under $1000",
            price=599.0,
            currency="usd",
        ),
        # An invented model no page mentions: drop_ungrounded removes it.
        ExtractedProduct(
            name="Nordvale Zephyr 14",
            price=989.0,
            currency="usd",
            rating=4.9,
            review_count=15000,
        ),
    ]
)
