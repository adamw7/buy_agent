"""Prompt formatting, name cleaning and deduplication."""

from __future__ import annotations

import logging


from buy_agent.extraction import (
    _MAX_NAME_LENGTH,
    clean_name,
    clean_products,
    deduplicate,
    format_results,
    looks_like_a_product,
    merge_variants,
)
from buy_agent.models import Offer, Product, Removal

from tests.conftest import said


def test_format_results_numbers_every_result(search_results) -> None:
    rendered = format_results(search_results)
    assert rendered.startswith("[1]\n"), "counted from one, the way the prompt reads"
    assert "[0]" not in rendered
    assert "[2]" in rendered
    assert "https://example.com/sony" in rendered


def test_deduplicate_counts_merges_and_nameless_drops_apart(caplog) -> None:
    """Two different things happen here, and one number reported them as merges."""
    products = [
        Product(name="Sony WH-1000XM5"),
        Product(name="Sony WH-1000XM5 Wireless"),
        Product(name="   "),
    ]

    with caplog.at_level(logging.INFO, logger="buy_agent.extraction"):
        deduplicate(products, limit=10)

    assert "Merged 1 duplicate listing(s)" in caplog.text
    assert "Dropped 1 result(s) whose name identifies nothing" in caplog.text


def test_both_of_those_name_what_they_took_for_the_reader_who_asked(caplog) -> None:
    """A merge is the other way a product leaves the report, and the only one a
    reader cannot reconstruct from what survived: the longer name is gone."""
    products = [
        Product(name="Sony WH-1000XM5"),
        Product(name="Sony WH-1000XM5 Wireless"),
        Product(name="   "),
    ]

    with caplog.at_level(logging.DEBUG, logger="buy_agent.extraction"):
        deduplicate(products, limit=10)

    assert "Folded 'Sony WH-1000XM5' together with 'Sony WH-1000XM5 Wireless'" in caplog.text
    assert "Nothing to identify them by: '   '" in caplog.text


def test_a_product_named_after_a_superlative_is_kept() -> None:
    """A brand may open on the word a headline opens on."""
    for name in (
        "Best Buy Essentials BE-HAPB02",
        "Top Rated Sony WH-1000XM5",
        # The model number does not have to be the last word: a page copies the
        # descriptor along with the name, and looking only at the end of the line
        # would throw this away as a headline.
        "Best Buy Essentials BE-HAPB02 Headphones",
    ):
        assert looks_like_a_product(name), name


def test_a_headline_qualified_by_a_model_number_is_still_a_headline() -> None:
    """The qualifier sits between the superlative and the category, and nothing
    names a single model after it -- which is what tells these from a brand."""
    for headline in (
        "Best PS5 Headsets",
        "Cheapest 4K TVs",
        "Best 1080p Monitors",
    ):
        assert not looks_like_a_product(headline), headline


def test_article_headlines_are_not_products() -> None:
    for headline in (
        "12 Best Noise Cancelling Headphones Under $200 (August 2026)",
        "Best Headphones under $200 - SoundGuys",
        "The Top 5 Laptops",
        # Nothing after the category at all, so there is no word to look for a
        # model number in -- the question has to be answered before it is asked.
        "Best Headphones",
        "How to choose a laptop",
        "Which headphones are best?",
        "Laptop Buying Guide",
        "Black Friday deals on headphones",
    ):
        assert not looks_like_a_product(headline), headline


def test_a_name_exactly_at_the_limit_is_still_a_name() -> None:
    """The ceiling is what an article title runs past, and 80 characters is the
    last length that is not one -- a real model name padded out with the variant
    words a shop puts after it reaches this before any headline does."""
    name = "Sony WH-1000XM5 Wireless Noise Cancelling Over-Ear Bluetooth Headphones in Black"
    assert len(name) == _MAX_NAME_LENGTH

    assert looks_like_a_product(name)


def test_cleaning_before_dedup_merges_the_same_product() -> None:
    """'Sony WH-1000XM5 Review' and 'Sony WH-1000XM5' are one product, not two."""
    cleaned = clean_products(
        [Product(name="Sony WH-1000XM5 Review"), Product(name="Sony WH-1000XM5", price=328.0)]
    )
    assert len(deduplicate(cleaned, limit=10)) == 1


def test_the_shorter_name_wins_even_where_it_sorts_later() -> None:
    """Shorter, not first in the alphabet: the descriptive word here is at the front."""
    merged = merge_variants(
        [Product(name="Black JBL Live 780NC"), Product(name="JBL Live 780NC")]
    )

    assert merged[0].name == "JBL Live 780NC"


def test_a_name_cleaned_away_to_nothing_is_still_named_in_the_log(caplog) -> None:
    """The names are logged so a heuristic that dropped a real product can be
    diagnosed, and the one it is hardest to guess at is the one cleaning emptied:
    a line reading '' says a product went and nothing about which."""
    with caplog.at_level(logging.DEBUG, logger="buy_agent.extraction"):
        clean_products([Product(name="Reviews")])

    assert "'Reviews'" in caplog.text
    assert not looks_like_a_product("")


def test_clean_name_strips_furniture_and_not_the_letters_a_name_ends_in() -> None:
    """What comes off the ends is separators and spaces, never a model's own letter."""
    assert clean_name("Xbox Series X | GameSite") == "Xbox Series X"


def test_a_question_is_an_article_not_a_product() -> None:
    assert not looks_like_a_product("Are the Sony XM5 worth it?")


def test_a_name_with_no_words_at_all_is_never_merged() -> None:
    """Two nameless entries share no tokens, so they are not one product."""
    assert len(merge_variants([Product(name="---"), Product(name="***")])) == 2


def test_clean_name_drops_punctuation_left_behind() -> None:
    """Whatever the strip leaves dangling must not become part of the dedup key."""
    assert clean_name("Sony WH-1000XM5,") == "Sony WH-1000XM5"
    assert clean_name("Sony WH-1000XM5 -") == "Sony WH-1000XM5"
    assert clean_name("Sony WH-1000XM5: Price") == "Sony WH-1000XM5"


def test_a_listing_with_a_link_beats_one_without() -> None:
    """A URL counts towards completeness, so the linked listing wins a conflict."""
    merged = merge_variants(
        [
            Product(name="JBL Live 780NC Wireless Headphones", notes="from the roundup"),
            Product(name="JBL Live 780NC", url="https://shop.example/jbl", notes="from the shop"),
        ]
    )

    assert len(merged) == 1
    assert merged[0].notes == "from the shop", "the linked listing is the more complete one"
    assert merged[0].url == "https://shop.example/jbl"


def test_a_currency_never_moves_to_a_price_from_another_page() -> None:
    """Two listings, two prices: the surviving figure keeps its own currency."""
    merged = merge_variants(
        [
            Product(name="Sony WH-CH720N", price=129.0, review_count=800, url="https://us/a"),
            Product(
                name="Sony WH-CH720N Wireless Headphones",
                price=249.0,
                currency="EUR",
                review_count=90,
                url="https://eu/b",
            ),
        ]
    )

    assert len(merged) == 1
    assert merged[0].price == 129.0
    assert merged[0].currency is None
    assert merged[0].price_label() == "129.00"


def test_a_qualifier_moves_with_the_figure_it_describes() -> None:
    """The whole group travels when the winner had no figure to hold it."""
    merged = merge_variants(
        [
            Product(name="Acme X1", rating=4.5, url="https://a"),
            Product(name="Acme X1 Wireless", price=199.0, currency="USD"),
        ]
    )

    assert len(merged) == 1
    assert (merged[0].price, merged[0].currency) == (199.0, "USD")


def test_a_qualifier_moves_when_both_pages_quote_the_same_figure() -> None:
    """Same rating on both pages: only one of them said what it averages."""
    merged = merge_variants(
        [
            Product(name="Acme X1", price=199.0, rating=4.5, url="https://a"),
            Product(name="Acme X1 Wireless", rating=4.5, review_count=800),
        ]
    )

    assert len(merged) == 1
    assert merged[0].rating_label() == "4.5/5 (800 reviews)"


def test_a_count_of_its_own_is_kept_when_both_pages_quote_the_same_rating() -> None:
    """The other side of that: a qualifier moves only into a gap. Two pages averaging
    4.5 over different numbers of reviews are two facts, and the winner's stays."""
    merged = merge_variants(
        [
            Product(name="Acme X1", price=199.0, rating=4.5, review_count=800, url="https://a"),
            Product(name="Acme X1 Wireless", rating=4.5, review_count=20),
        ]
    )

    assert len(merged) == 1
    assert merged[0].rating_label() == "4.5/5 (800 reviews)"


# -- opinions across two listings ----------------------------------------------


def test_merging_keeps_what_both_pages_said_about_the_product() -> None:
    """The one field taken from both listings rather than from the fuller one."""
    merged = merge_variants(
        [
            Product(name="JBL Live 780NC", price=149.0, opinions=said("the fit is snug")),
            Product(name="JBL Live 780NC Headphones", opinions=said("the case is bulky")),
        ]
    )

    assert merged[0].opinions == said("the fit is snug", "the case is bulky")


# -- what each step says it took out (ADR-0055) --------------------------------


def taken(step) -> list[Removal]:
    """Drive one step with somebody keeping what it removed."""
    removed: list[Removal] = []
    step(removed.append)
    return removed


def test_a_headline_is_removed_as_a_page_and_says_so() -> None:
    """The panel's sentence, pinned where the step that writes it is tested: the
    browser shows this and composes nothing of its own."""
    removed = taken(
        lambda record: clean_products(
            [Product(name="The 5 best headphones of 2026")], record=record
        )
    )

    assert [entry.step for entry in removed] == ["clean"]
    assert removed[0].reason == "Reads as an article or a shop, not a product."


def test_a_name_identifying_nothing_is_removed_and_says_so() -> None:
    removed = taken(lambda record: deduplicate([Product(name="   ")], 10, record=record))

    assert [entry.step for entry in removed] == ["deduplicate"]
    assert removed[0].reason == "The name identifies nothing."


def test_the_surviving_name_is_the_one_the_merge_kept_not_the_first_seen() -> None:
    """``_combine`` keeps the shorter name whichever order they arrived in, so the
    sentence has to be built off the merged entry rather than off the loop."""
    removed = taken(
        lambda record: merge_variants(
            [
                Product(name="Sony WH-CH720N Wireless Headphones"),
                Product(name="Sony WH-CH720N"),
            ],
            record=record,
        )
    )

    assert [entry.name for entry in removed] == ["Sony WH-CH720N Wireless Headphones"]
    assert "Folded into Sony WH-CH720N," in removed[0].reason


# -- the offers a merge keeps (ADR-0058) ---------------------------------------


def test_every_listing_that_was_priced_becomes_an_offer() -> None:
    """One ``Product`` is still one listing at this point, so its own price is the
    offer it is."""
    kept = deduplicate(
        [
            Product(
                name="Sony WH-1000XM5",
                price=329.0,
                currency="USD",
                seller="Shop",
                url="https://shop.example/xm5",
            )
        ],
        10,
    )

    # The page too, which is what a cart for this offer names (ADR-0058).
    assert kept[0].offers == [
        Offer(price=329.0, currency="USD", seller="Shop", url="https://shop.example/xm5")
    ]


def test_a_merge_keeps_both_listings_prices() -> None:
    """Two shops are no conflict, the way two reviewers are not (ADR-0042): the
    headline price is still the winner's and the other is no longer thrown away."""
    merged = deduplicate(
        [
            Product(
                name="Sony WH-1000XM5 Wireless",
                price=149.0,
                currency="USD",
                seller="ShopB",
                url="https://b.example/p",
                rating=4.5,
                review_count=100,
            ),
            Product(
                name="Sony WH-1000XM5",
                price=129.0,
                currency="USD",
                seller="ShopA",
                url="https://a.example/p",
            ),
        ],
        10,
    )[0]

    assert merged.price == 149.0
    assert [(offer.price, offer.seller) for offer in merged.offers] == [
        (149.0, "ShopB"),
        (129.0, "ShopA"),
    ]


# -- a listing's stock and condition travel with its price (ADR-0079) ----------


def test_a_listings_standing_is_seeded_onto_its_offer() -> None:
    [kept] = deduplicate(
        [
            Product(
                name="Sony WH-1000XM5",
                price=299.0,
                currency="USD",
                availability="in stock",
                condition="refurbished",
            )
        ],
        10,
    )

    assert kept.offers == [
        Offer(price=299.0, currency="USD", availability="in stock", condition="refurbished")
    ]


def test_a_price_filled_in_by_a_merge_brings_its_standing_with_it() -> None:
    """The listing's stock describes its price (ADR-0022): never one shop's price
    beside another shop's "in stock"."""
    [merged] = deduplicate(
        [
            Product(
                name="Sony WH-1000XM5 Wireless",
                rating=4.6,
                review_count=900,
                url="https://reviews.example/xm5",
            ),
            Product(
                name="Sony WH-1000XM5",
                price=249.0,
                currency="USD",
                availability="out of stock",
                condition="used",
            ),
        ],
        10,
    )

    assert (merged.price, merged.availability, merged.condition) == (
        249.0,
        "out of stock",
        "used",
    )
