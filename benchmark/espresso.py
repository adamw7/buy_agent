"""The espresso case: a shopper counting in euros, shops writing a decimal comma and
dotted thousands, an American review in dollars, and the cashback and accessories a price
can be misread off."""

from __future__ import annotations

from buy_agent.models import ExtractedProduct, ProductList
from buy_agent.search import SearchResult
from benchmark.answers import Expected, entry
from benchmark.query import QueryKey

#: What the shopper typed.
REQUEST = "espresso machine for a small kitchen, under 400 euros"

#: What a refined query owes :data:`REQUEST`. The currency is a constraint here, as it
#: is not for a dollar budget searched in the default region: a query that drops it
#: searches dollar prices.
QUERY = QueryKey(
    keeps=(
        ("espresso",),
        ("machine", "machines", "maker"),
        ("400",),
        ("euro", "euros", "eur", "€"),
        ("small", "compact", "narrow", "slim"),
    ),
    brands=(
        "de longhi", "delonghi", "dedica", "sage", "breville", "bambino", "gaggia",
        "lelit", "krups", "philips", "lattego", "melitta",
    ),
)

#: What the scripted model refines :data:`REQUEST` into.
REFINED_QUERY = "compact espresso machine under 400 euros price review"

KAFFEEHAUS = "https://kaffeehaus.example/delonghi-dedica-arte-ec885"
ESPRESSOREVIEW = "https://espressoreview.example/sage-bambino-plus"
ROUNDUP = "https://kitchenroundup.example/best-espresso-machines-under-400"
BREWLAB = "https://brewlab.example/delonghi-dedica-arte-review"
BARISTASHOP = "https://baristashop.example/gaggia-classic-evo-pro"
SMALLKITCHEN = "https://smallkitchen.example/compact-espresso-machines"
PRICETRACKER = "https://pricetracker.example/espresso-machine-prices"
BEANTOCUP = "https://beantocup.example/philips-3200-lattego"

#: The pages behind the results, as :func:`buy_agent.fetch.fetch_page` would have found
#: them.
PAGE_TEXT: dict[str, str] = {
    KAFFEEHAUS: """\
KaffeeHaus
Home  Espresso  Grinders  Accessories
Ships within the EU in 2-3 working days
De'Longhi Dedica Arte EC885 espresso machine
Price: 279,00 €
Rated 4,5 out of 5 from 3.412 reviews.
What customers say
Owners report it fits beside the kettle with room to spare at 15 cm wide.
Buyers found the steam wand weak for more than one milk drink at a time.
Also in this range
Krups Virtuoso XP442
Price: 199,99 €
Rated 4,0 out of 5 from 1.885 reviews.
VAT included
Payment methods
Customer services
""",
    ESPRESSOREVIEW: """\
EspressoReview
Sage Bambino Plus review: the small machine to beat
By the EspressoReview test kitchen
The Sage Bambino Plus costs €399.95 at most shops.
Rated 4.7 out of 5 from 2,180 reviews.
Verdict
In our tests it was ready to pull a shot in three seconds from cold.
Testers found the automatic milk texturing outstanding for a machine this size.
The drawback is a drip tray that fills after a handful of shots.
How it compares
De'Longhi Dedica Arte EC885
The De'Longhi Dedica Arte EC885 is €279.00 and rated 4.5 out of 5 from 3,412 reviews.
Gaggia Classic Evo Pro
The Gaggia Classic Evo Pro is €449.00 and rated 4.6 out of 5 from 1,050 reviews.
Specifications
Width: 19.5 cm
Water tank: 1.9 litres
Copyright 2026 EspressoReview. All rights reserved.
""",
    ROUNDUP: """\
KitchenRoundup
The 6 Best Espresso Machines Under €400
Updated September 2026
1. Sage Bambino Plus - best overall
The Sage Bambino Plus is our pick at €399.95, or €349.95 in the autumn sale.
Rated 4.7 out of 5 from 2,180 reviews.
2. De'Longhi Dedica Arte EC885 - best for small kitchens
The De'Longhi Dedica Arte EC885 is €279.00 and rated 4.5 out of 5 from 3,412 reviews.
3. Lelit Anna PL41TEM - best for enthusiasts
The Lelit Anna PL41TEM is €389.00 and rated 4.4 out of 5 from 312 reviews.
Reviewers praised its temperature control as the most precise under €400.
4. Krups Virtuoso XP442 - the cheapest pick here
The Krups Virtuoso XP442 is €199.99 and rated 4.0 out of 5 from 1,885 reviews.
Users complained the Krups pump is loud enough to wake a flatmate.
5. Melitta Solo E950 - best bean-to-cup on a budget
The Melitta Solo E950 is rated 4.2 out of 5 from 2,760 reviews.
How we test
Affiliate disclosure
""",
    BREWLAB: """\
BrewLab
Reviews  Gear  Recipes
De'Longhi Dedica Arte review
The De'Longhi Dedica Arte EC885 sells for $299.95 in the US.
Rated 4.4 out of 5 from 870 reviews on American shops.
Bottom line
We found the shots sour until we bought a better grinder.
It is worth the money if your counter is narrow.
Related reviews
Subscribe to BrewLab
""",
    BARISTASHOP: """\
BaristaShop
Your basket is empty
Gaggia Classic Evo Pro - Italian espresso machine
Price: 449,00 €
€50 cashback until 31 October
Rated 4,6 out of 5 from 1.050 reviews.
What owners say
Owners praised the commercial portafilter as built to last for decades.
Buyers felt it was too big for a small kitchen at 23 cm wide.
Frequently bought together
Milk jug 350 ml, 14,99 €
Tamper 58 mm, 24,90 €
Descaler, 9,99 €
Delivery information
""",
    SMALLKITCHEN: """\
SmallKitchen
Compact espresso machines for a small kitchen
Measured, not guessed
The De'Longhi Dedica Arte EC885 is 279,00 € and only 15 cm wide.
Reviewers found the Lelit Anna PL41TEM compact enough for a studio flat.
The Lelit Anna PL41TEM is 389,00 € and rated 4,4 out of 5 from 312 reviews.
Testers found the Krups Virtuoso XP442 disappointing on milk drinks.
The Krups Virtuoso XP442 is 199,99 € and rated 4,0 out of 5 from 1.885 reviews.
Bottom line
The Dedica is the one we recommend where every centimetre counts.
Next article
""",
    PRICETRACKER: """\
PriceTracker
Espresso machine prices in euros
Tracked across 25 European shops
Sage Bambino Plus
Current best price 399,95 €, lowest ever 329,95 €
De'Longhi Dedica Arte EC885
Current best price 279,00 €, lowest ever 249,00 €
Gaggia Classic Evo Pro
Current best price 449,00 €, lowest ever 419,00 €
Lelit Anna PL41TEM
Current best price 389,00 €, lowest ever 359,00 €
Melitta Solo E950
Current best price 329,00 €, lowest ever 299,00 €
Buyers found the January sales the best value of the year.
Set a price alert
""",
    BEANTOCUP: """\
BeanToCup
Philips 3200 LatteGo review
The Philips 3200 LatteGo is 429,00 €.
Rated 4,5 out of 5 from 9.400 reviews.
Pros and cons
Owners report the milk system rinses clean in under ten seconds.
Testers found the coffee weaker than anything from a portafilter.
The Melitta Solo E950 is rated 4,2 out of 5 from 2.760 reviews.
We could not confirm a Melitta price we trusted, so check the listings.
Newsletter signup
""",
}

#: The results the search hands back, ``content`` empty until the corpus is served.
PAGES: tuple[SearchResult, ...] = (
    SearchResult(
        title="De'Longhi Dedica Arte EC885 | KaffeeHaus",
        url=KAFFEEHAUS,
        snippet="De'Longhi Dedica Arte EC885 espresso machine, 279,00 €.",
    ),
    SearchResult(
        title="Sage Bambino Plus review | EspressoReview",
        url=ESPRESSOREVIEW,
        snippet="The Sage Bambino Plus costs €399.95 and is rated 4.7 out of 5.",
    ),
    SearchResult(
        title="The 6 Best Espresso Machines Under €400 | KitchenRoundup",
        url=ROUNDUP,
        snippet="Our picks: the Sage Bambino Plus at €399.95, the Dedica Arte at €279.",
    ),
    SearchResult(
        title="De'Longhi Dedica Arte review | BrewLab",
        url=BREWLAB,
        snippet="The Dedica Arte sells for $299.95 in the US.",
    ),
    SearchResult(
        title="Gaggia Classic Evo Pro | BaristaShop",
        url=BARISTASHOP,
        snippet="Gaggia Classic Evo Pro, 449,00 €, with €50 cashback.",
    ),
    SearchResult(
        title="Compact espresso machines for a small kitchen | SmallKitchen",
        url=SMALLKITCHEN,
        snippet="Machines measured for a small kitchen, narrowest first.",
    ),
    SearchResult(
        title="Espresso machine prices in euros | PriceTracker",
        url=PRICETRACKER,
        snippet="Current and lowest-ever prices across 25 European shops.",
    ),
    SearchResult(
        title="Philips 3200 LatteGo review | BeanToCup",
        url=BEANTOCUP,
        snippet="The Philips 3200 LatteGo is 429,00 €, rated 4,5 out of 5.",
    ),
)

#: The seven machines these eight pages are about, in the order they first appear.
ANSWER_KEY: tuple[Expected, ...] = (
    entry(
        "De'Longhi Dedica Arte EC885", 279.0, 4.5, 3_412,
        # The lowest ever, and BrewLab's American price and American rating.
        prices={(279.0, "EUR"), (249.0, "EUR"), (299.95, "USD")},
        ratings=((4.4, 870),),
        pages={KAFFEEHAUS, ESPRESSOREVIEW, ROUNDUP, BREWLAB, SMALLKITCHEN, PRICETRACKER},
        currency="EUR",
        verdicts=(
            "Owners report it fits beside the kettle with room to spare at 15 cm wide.",
            "Buyers found the steam wand weak for more than one milk drink at a time.",
            "2. De'Longhi Dedica Arte EC885 - best for small kitchens",
            "We found the shots sour until we bought a better grinder.",
            "It is worth the money if your counter is narrow.",
            "The Dedica is the one we recommend where every centimetre counts.",
        ),
    ),
    entry(
        "Krups Virtuoso XP442", 199.99, 4.0, 1_885,
        prices={(199.99, "EUR")},
        pages={KAFFEEHAUS, ROUNDUP, SMALLKITCHEN},
        currency="EUR",
        verdicts=(
            "4. Krups Virtuoso XP442 - the cheapest pick here",
            "Users complained the Krups pump is loud enough to wake a flatmate.",
            "Testers found the Krups Virtuoso XP442 disappointing on milk drinks.",
        ),
    ),
    entry(
        # Five cents under the budget; the autumn sale and the lowest ever below it.
        "Sage Bambino Plus", 399.95, 4.7, 2_180,
        prices={(399.95, "EUR"), (349.95, "EUR"), (329.95, "EUR")},
        pages={ESPRESSOREVIEW, ROUNDUP, PRICETRACKER},
        currency="EUR",
        verdicts=(
            "In our tests it was ready to pull a shot in three seconds from cold.",
            "Testers found the automatic milk texturing outstanding for a machine this "
            "size.",
            "The drawback is a drip tray that fills after a handful of shots.",
            "1. Sage Bambino Plus - best overall",
            "The Sage Bambino Plus is our pick at €399.95, or €349.95 in the autumn sale.",
        ),
    ),
    entry(
        # €50 is cashback, and 350 ml, 58 mm and the accessories' prices are not it.
        "Gaggia Classic Evo Pro", 449.0, 4.6, 1_050,
        prices={(449.0, "EUR"), (419.0, "EUR")},
        pages={ESPRESSOREVIEW, BARISTASHOP, PRICETRACKER},
        currency="EUR",
        verdicts=(
            "Owners praised the commercial portafilter as built to last for decades.",
            "Buyers felt it was too big for a small kitchen at 23 cm wide.",
        ),
    ),
    entry(
        "Lelit Anna PL41TEM", 389.0, 4.4, 312,
        prices={(389.0, "EUR"), (359.0, "EUR")},
        pages={ROUNDUP, SMALLKITCHEN, PRICETRACKER},
        currency="EUR",
        verdicts=(
            "3. Lelit Anna PL41TEM - best for enthusiasts",
            "Reviewers praised its temperature control as the most precise under €400.",
            "Reviewers found the Lelit Anna PL41TEM compact enough for a studio flat.",
        ),
    ),
    entry(
        # Its rating where its price is not, and the other way about.
        "Melitta Solo E950", 329.0, 4.2, 2_760,
        prices={(329.0, "EUR"), (299.0, "EUR")},
        pages={ROUNDUP, PRICETRACKER, BEANTOCUP},
        currency="EUR",
        verdicts=("5. Melitta Solo E950 - best bean-to-cup on a budget",),
    ),
    entry(
        "Philips 3200 LatteGo", 429.0, 4.5, 9_400,
        prices={(429.0, "EUR")},
        pages={BEANTOCUP},
        currency="EUR",
        verdicts=(
            "Owners report the milk system rinses clean in under ten seconds.",
            "Testers found the coffee weaker than anything from a portafilter.",
        ),
    ),
)

#: The judgement these pages pass on no machine: a sale.
ABOUT_NOBODY: frozenset[str] = frozenset(
    {"Buyers found the January sales the best value of the year."}
)

#: The first five products of the key, copied exactly as the pages print them.
PERFECT = ProductList(
    products=[
        ExtractedProduct(
            name="De'Longhi Dedica Arte EC885", price=279.0, currency="EUR",
            rating=4.5, review_count=3_412, url=KAFFEEHAUS,
            opinions=[
                "Owners report it fits beside the kettle with room to spare at 15 cm wide."
            ],
        ),
        ExtractedProduct(
            name="Krups Virtuoso XP442", price=199.99, currency="EUR",
            rating=4.0, review_count=1_885, url=KAFFEEHAUS,
            opinions=["Users complained the Krups pump is loud enough to wake a flatmate."],
        ),
        ExtractedProduct(
            name="Sage Bambino Plus", price=399.95, currency="EUR",
            rating=4.7, review_count=2_180, url=ESPRESSOREVIEW,
            opinions=[
                "In our tests it was ready to pull a shot in three seconds from cold.",
                "Testers found the automatic milk texturing outstanding for a machine "
                "this size.",
            ],
        ),
        ExtractedProduct(
            name="Gaggia Classic Evo Pro", price=449.0, currency="EUR",
            rating=4.6, review_count=1_050, url=BARISTASHOP,
            opinions=[
                "Owners praised the commercial portafilter as built to last for decades."
            ],
        ),
        ExtractedProduct(
            name="Lelit Anna PL41TEM", price=389.0, currency="EUR",
            rating=4.4, review_count=312, url=SMALLKITCHEN,
            opinions=[
                "Reviewers found the Lelit Anna PL41TEM compact enough for a studio flat."
            ],
        ),
    ]
)

#: The same run, wrong in the ways a model reading these pages tends to be.
SLOPPY = ProductList(
    products=[
        ExtractedProduct(
            # The euro sign read as a dollar, and "3.412 reviews" read as three.
            name="De'Longhi Dedica Arte EC885", price=279.0, currency="USD",
            rating=4.5, review_count=3, url=KAFFEEHAUS,
            opinions=[
                "Owners report it fits beside the kettle with room to spare at 15 cm wide."
            ],
        ),
        ExtractedProduct(name="The 6 Best Espresso Machines Under €400", price=400.0),
        # The shop.
        ExtractedProduct(name="KaffeeHaus", url=KAFFEEHAUS),
        ExtractedProduct(
            # The cashback.
            name="Gaggia Classic Evo Pro", price=50.0, currency="EUR",
            rating=4.6, review_count=1_050, url=BARISTASHOP,
        ),
        ExtractedProduct(
            name="Sage Bambino Plus", price=399.95, currency="EUR",
            rating=4.7, review_count=2_180, url=ESPRESSOREVIEW,
            # "a machine this size" on the page: close enough for the pipeline only.
            opinions=[
                "Testers found the automatic milk texturing outstanding for a machine "
                "of this size."
            ],
        ),
        # The Dedica again, under less of its name.
        ExtractedProduct(
            name="De'Longhi Dedica", price=279.0, currency="EUR", url=SMALLKITCHEN
        ),
        # Never reported: the cap ran out.
        ExtractedProduct(name="Lelit Anna PL41TEM", price=389.0, currency="EUR"),
    ]
)
