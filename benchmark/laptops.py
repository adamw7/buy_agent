"""The gaming-laptop case: dollar prices in the thousands, a Canadian listing, and the
spec sheets, monthly payments and student discounts a price can be misread off."""

from __future__ import annotations

from buy_agent.models import ExtractedProduct, ProductList
from buy_agent.search import SearchResult
from benchmark.answers import Expected, entry
from benchmark.query import QueryKey

#: What the shopper typed.
REQUEST = "a gaming laptop light enough to carry to lectures, under $1,500"

#: What a refined query owes :data:`REQUEST`.
QUERY = QueryKey(
    keeps=(
        ("gaming",),
        ("laptop", "laptops", "notebook"),
        ("1500",),
        ("light", "lightweight", "portable", "thin", "carry", "travel"),
    ),
    brands=(
        "asus", "rog", "zephyrus", "lenovo", "legion", "hp", "omen", "acer", "nitro",
        "msi", "katana", "razer", "blade", "gigabyte",
    ),
)

#: What the scripted model refines :data:`REQUEST` into.
REFINED_QUERY = "lightweight gaming laptop under $1500 price review"

LAPTOPLAB = "https://laptoplab.example/asus-rog-zephyrus-g14-review"
LAPTOPLANE = "https://laptoplane.example/lenovo-legion-slim-5"
ROUNDUP = "https://gamingroundup.example/best-gaming-laptops-under-1500"
NORTHBYTE = "https://northbyte.example/asus-rog-zephyrus-g14"
STUDENTGEAR = "https://studentgear.example/gaming-laptops-for-college"
BLADEREVIEW = "https://bladereview.example/razer-blade-14"
DEALWATCH = "https://dealwatch.example/gaming-laptop-price-history"
BUDGETGAMER = "https://budgetgamer.example/cheap-gaming-laptops"

#: The pages behind the results, as :func:`buy_agent.fetch.fetch_page` would have found
#: them.
PAGE_TEXT: dict[str, str] = {
    LAPTOPLAB: """\
LaptopLab
Home  Reviews  Deals  Newsletter
ASUS ROG Zephyrus G14 review: the gaming laptop you can actually carry
By the LaptopLab hardware desk
The ASUS ROG Zephyrus G14 costs $1,399.99 with an RTX 4070.
Rated 4.6 out of 5 from 2,310 reviews.
Verdict
In our tests the fans stayed quiet through an hour of Cyberpunk.
Reviewers praised the 1.5 kg chassis as the easiest gaming machine to carry to class.
The downside is a glossy screen that reflects every light in a lecture hall.
How it compares
Lenovo Legion Slim 5
The Lenovo Legion Slim 5 is $1,249.00 and rated 4.5 out of 5 from 1,875 reviews.
HP Omen Transcend 14
The HP Omen Transcend 14 is $1,499.99 and rated 4.3 out of 5 from 640 reviews.
Razer Blade 14
The Razer Blade 14 is $2,199.99 and rated 4.4 out of 5 from 980 reviews.
Specifications
Graphics: RTX 4070, 8GB
Memory: 32GB
Display: 14-inch 3K OLED, 120Hz
Weight: 1.5 kg
Copyright 2026 LaptopLab Media. All rights reserved.
Terms of use  Privacy  Cookie settings
""",
    LAPTOPLANE: """\
LaptopLane
Your cart is empty
Lenovo Legion Slim 5 gaming laptop
Add to cart
14.5-inch screen, RTX 4060, 16GB memory, 1TB storage
The Lenovo Legion Slim 5 is $1,249.00.
Students pay $1,099.00 with a verified college email.
Or $52.04/month for 24 months.
Rated 4.5 out of 5 from 1,875 reviews.
What owners say
Owners report the keyboard is the best on any laptop at this price.
Buyers found the 1.6 kg weight easy to live with on a daily commute.
Cons
The webcam is mediocre, which several buyers complained of.
Frequently bought together
Cooling pad, $29.99
Gaming mouse, $49.99
Customers also viewed
MSI Katana 15
The MSI Katana 15 is $949.00 and rated 4.0 out of 5 from 3,108 reviews.
Acer Nitro V 15
The Acer Nitro V 15 is $799.99 and rated 4.2 out of 5 from 5,420 reviews.
Delivery information
Returns accepted within 30 days
Track your order
""",
    ROUNDUP: """\
GamingRoundup
The 7 Best Gaming Laptops Under $1,500 in 2026
Updated September 2026
1. ASUS ROG Zephyrus G14 - best overall
The ASUS ROG Zephyrus G14 remains our overall pick at $1,399.99.
Rated 4.6 out of 5 from 2,310 reviews across the shops we track.
2. Lenovo Legion Slim 5 - best value
The Lenovo Legion Slim 5 is $1,249.00 and rated 4.5 out of 5 from 1,875 reviews.
The Legion Slim 5 is $150 cheaper than the Zephyrus G14 and nearly as fast.
3. Acer Nitro V 15 - the cheapest pick here
The Acer Nitro V 15 is $799.99 and rated 4.2 out of 5 from 5,420 reviews.
Buyers found it heavy at 2.1 kg but excellent value for money.
4. HP Omen Transcend 14 - best screen
The HP Omen Transcend 14 is $1,499.99 and rated 4.3 out of 5 from 640 reviews.
Testers found the OLED screen outstanding but the speakers tinny.
5. Gigabyte G6X - best for upgrades
The Gigabyte G6X is rated 4.1 out of 5 from 720 reviews.
More from GamingRoundup
How we test
Affiliate disclosure
""",
    NORTHBYTE: """\
NorthByte Computers
Home  Laptops  Gaming  Accessories
In stock, ships from Toronto
ASUS ROG Zephyrus G14 gaming laptop
Price: C$1,899.99
Free shipping across Canada
Buyers noticed the charger is heavier than you would expect to carry.
Owners recommend the two-year care plan sold separately at C$149.99.
Also in this range
Lenovo Legion Slim 5
Price: C$1,679.99
Delivery in 2-4 business days
Taxes calculated at checkout
""",
    STUDENTGEAR: """\
StudentGear
Gaming laptops for college: what to carry
What matters when it lives in a backpack
Reviewers found the ASUS ROG Zephyrus G14 light enough to forget it is in your bag.
The ASUS ROG Zephyrus G14 is $1,399.99 and rated 4.6 out of 5 from 2,310 reviews.
The Acer Nitro V 15 is $799.99, but owners report it is too heavy to carry every day.
Users complained the MSI Katana 15 battery barely lasts two hours away from a socket.
The MSI Katana 15 is $949.00 and rated 4.0 out of 5 from 3,108 reviews.
Bottom line
The Zephyrus is worth the money if it goes to every lecture.
Next article
Student newsletter
""",
    BLADEREVIEW: """\
BladeReview
Razer Blade 14 review
The Razer Blade 14 is $2,199.99.
Rated 4.4 out of 5 from 980 reviews.
Pros and cons
Critics praised the aluminium build as the best in any gaming laptop.
Testers found it not worth the money on a student budget.
How it compares
The ASUS ROG Zephyrus G14 is $1,399.99 and rated 4.6 out of 5 from 2,310 reviews.
About the author
Comments are closed
""",
    DEALWATCH: """\
DealWatch
Gaming laptop price history
Prices tracked across 30 retailers
ASUS ROG Zephyrus G14
Current best price $1,399.99, lowest ever $1,299.99
Lenovo Legion Slim 5
Current best price $1,249.00, lowest ever $1,149.00
HP Omen Transcend 14
Current best price $1,499.99, lowest ever $1,399.99
Acer Nitro V 15
Current best price $799.99, lowest ever $749.99
Gigabyte G6X
Current best price $1,099.00, lowest ever $999.00
Buyers found the back-to-school sales the best value of the year.
Set a price alert
How our tracking works
""",
    BUDGETGAMER: """\
BudgetGamer
Cheap gaming laptops that are actually worth it
The Gigabyte G6X is rated 4.1 out of 5 from 720 reviews.
We could not confirm a price we trusted, so check the listings with care.
In our tests the G6X was loud under load but excellent for the money.
Owners found the trackpad flimsy next to the rest of the machine.
Cheaper still
The Acer Nitro V 15 is $799.99 and rated 4.2 out of 5 from 5,420 reviews.
Buyers recommend it as the best value in the category by some distance.
The MSI Katana 15 is $949.00 and rated 4.0 out of 5 from 3,108 reviews.
Newsletter signup
About BudgetGamer
""",
}

#: The results the search hands back, ``content`` empty until the corpus is served.
PAGES: tuple[SearchResult, ...] = (
    SearchResult(
        title="ASUS ROG Zephyrus G14 review | LaptopLab",
        url=LAPTOPLAB,
        snippet="The ASUS ROG Zephyrus G14 costs $1,399.99 and is rated 4.6 out of 5.",
    ),
    SearchResult(
        title="Lenovo Legion Slim 5 gaming laptop | LaptopLane",
        url=LAPTOPLANE,
        snippet="Lenovo Legion Slim 5, $1,249.00, or $52.04/month.",
    ),
    SearchResult(
        title="The 7 Best Gaming Laptops Under $1,500 in 2026 | GamingRoundup",
        url=ROUNDUP,
        snippet="Our picks: the ASUS ROG Zephyrus G14 at $1,399.99, the Legion Slim 5 at $1,249.",
    ),
    SearchResult(
        title="ASUS ROG Zephyrus G14 gaming laptop | NorthByte Computers",
        url=NORTHBYTE,
        snippet="ASUS ROG Zephyrus G14, C$1,899.99, in stock and shipping from Toronto.",
    ),
    SearchResult(
        title="Gaming laptops for college: what to carry | StudentGear",
        url=STUDENTGEAR,
        snippet="Which gaming laptops survive a semester in a backpack.",
    ),
    SearchResult(
        title="Razer Blade 14 review | BladeReview",
        url=BLADEREVIEW,
        snippet="The Razer Blade 14 is $2,199.99, rated 4.4 out of 5.",
    ),
    SearchResult(
        title="Gaming laptop price history | DealWatch",
        url=DEALWATCH,
        snippet="Current and lowest-ever prices across 30 retailers.",
    ),
    SearchResult(
        title="Cheap gaming laptops that are actually worth it | BudgetGamer",
        url=BUDGETGAMER,
        snippet="The Gigabyte G6X is rated 4.1 out of 5 from 720 reviews.",
    ),
)

#: The seven laptops these eight pages are about, in the order they first appear.
ANSWER_KEY: tuple[Expected, ...] = (
    entry(
        "ASUS ROG Zephyrus G14", 1399.99, 4.6, 2_310,
        # The lowest-ever price, and NorthByte's in Canadian dollars. C$149.99 on the
        # same page is a care plan, not the laptop.
        prices={(1399.99, "USD"), (1299.99, "USD"), (1899.99, "CAD")},
        pages={LAPTOPLAB, ROUNDUP, NORTHBYTE, STUDENTGEAR, BLADEREVIEW, DEALWATCH},
    ),
    entry(
        "Lenovo Legion Slim 5", 1249.0, 4.5, 1_875,
        # The student price and the lowest ever. $52.04 is a month of 24, and $150
        # is the gap to the Zephyrus.
        prices={(1249.0, "USD"), (1099.0, "USD"), (1149.0, "USD"), (1679.99, "CAD")},
        pages={LAPTOPLAB, LAPTOPLANE, ROUNDUP, NORTHBYTE, DEALWATCH},
    ),
    entry(
        # Its lowest ever is the Zephyrus's current price.
        "HP Omen Transcend 14", 1499.99, 4.3, 640,
        prices={(1499.99, "USD"), (1399.99, "USD")},
        pages={LAPTOPLAB, ROUNDUP, DEALWATCH},
    ),
    entry(
        # Over the budget, and on the pages all the same.
        "Razer Blade 14", 2199.99, 4.4, 980,
        prices={(2199.99, "USD")},
        pages={LAPTOPLAB, BLADEREVIEW},
    ),
    entry(
        "MSI Katana 15", 949.0, 4.0, 3_108,
        prices={(949.0, "USD")},
        pages={LAPTOPLANE, STUDENTGEAR, BUDGETGAMER},
    ),
    entry(
        "Acer Nitro V 15", 799.99, 4.2, 5_420,
        prices={(799.99, "USD"), (749.99, "USD")},
        pages={LAPTOPLANE, ROUNDUP, STUDENTGEAR, DEALWATCH, BUDGETGAMER},
    ),
    entry(
        # BudgetGamer prints its rating and will not print a price; DealWatch does.
        "Gigabyte G6X", 1099.0, 4.1, 720,
        prices={(1099.0, "USD"), (999.0, "USD")},
        pages={ROUNDUP, DEALWATCH, BUDGETGAMER},
    ),
)

#: The first five products of the key, copied exactly as the pages print them.
PERFECT = ProductList(
    products=[
        ExtractedProduct(
            name="ASUS ROG Zephyrus G14", price=1399.99, currency="USD",
            rating=4.6, review_count=2_310, url=LAPTOPLAB,
            opinions=[
                "In our tests the fans stayed quiet through an hour of Cyberpunk.",
                "Reviewers praised the 1.5 kg chassis as the easiest gaming machine to "
                "carry to class.",
            ],
        ),
        ExtractedProduct(
            name="Lenovo Legion Slim 5", price=1249.0, currency="USD",
            rating=4.5, review_count=1_875, url=LAPTOPLANE,
            opinions=["Owners report the keyboard is the best on any laptop at this price."],
        ),
        ExtractedProduct(
            name="HP Omen Transcend 14", price=1499.99, currency="USD",
            rating=4.3, review_count=640, url=ROUNDUP,
            opinions=["Testers found the OLED screen outstanding but the speakers tinny."],
        ),
        ExtractedProduct(
            name="Razer Blade 14", price=2199.99, currency="USD",
            rating=4.4, review_count=980, url=BLADEREVIEW,
            opinions=["Critics praised the aluminium build as the best in any gaming laptop."],
        ),
        ExtractedProduct(
            name="MSI Katana 15", price=949.0, currency="USD",
            rating=4.0, review_count=3_108, url=STUDENTGEAR,
            opinions=[
                "Users complained the MSI Katana 15 battery barely lasts two hours away "
                "from a socket."
            ],
        ),
    ]
)

#: The same run, wrong in the ways a model reading these pages tends to be.
SLOPPY = ProductList(
    products=[
        ExtractedProduct(
            # $1,249.00 is the Legion's, printed on the Zephyrus's own review page.
            name="ASUS ROG Zephyrus G14", price=1249.0, currency="USD",
            rating=4.6, review_count=2_310,
            url="https://asus.example/rog-zephyrus-g14",  # never searched
            # Nobody wrote it.
            opinions=["The battery lasts all day and the screen never reflects a thing."],
        ),
        ExtractedProduct(
            name="The 7 Best Gaming Laptops Under $1,500 in 2026", price=1500.0,
            currency="USD",
        ),
        # The shop.
        ExtractedProduct(name="LaptopLane", url=LAPTOPLANE),
        ExtractedProduct(
            # A month of 24 payments.
            name="Lenovo Legion Slim 5", price=52.04, currency="USD",
            rating=4.5, review_count=1_875, url=LAPTOPLANE,
            opinions=["Owners report the keyboard is the best on any laptop at this price."],
        ),
        # The Legion again, without its brand.
        ExtractedProduct(name="Legion Slim 5", price=1099.0, url=LAPTOPLANE),
        ExtractedProduct(
            # A dollar price called Canadian.
            name="Acer Nitro V 15", price=799.99, currency="CAD",
            rating=4.2, review_count=5_420, url=ROUNDUP,
            # "for money" on the page: close enough for the pipeline, not word for word.
            opinions=["Buyers found it heavy at 2.1 kg but excellent value for the money."],
        ),
        # Never reported: the cap ran out.
        ExtractedProduct(name="MSI Katana 15", price=949.0, currency="USD", url=STUDENTGEAR),
    ]
)
