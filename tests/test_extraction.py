"""Prompt formatting, name cleaning and deduplication."""

from __future__ import annotations

import pytest

from buy_agent.extraction import (
    EXTRACTION_PROMPT,
    clean_name,
    clean_products,
    deduplicate,
    format_results,
    looks_like_a_product,
    merge_variants,
)
from buy_agent.models import Product


def test_format_results_numbers_every_result(search_results) -> None:
    rendered = format_results(search_results)
    assert "[1]" in rendered
    assert "[2]" in rendered
    assert "https://example.com/sony" in rendered


def test_extraction_prompt_carries_the_limit_through() -> None:
    messages = EXTRACTION_PROMPT.format_messages(request="headphones", results="none", limit=7)
    assert "at most 7 distinct products" in messages[0].content
    assert "headphones" in messages[1].content


def test_deduplicate_keeps_the_most_complete_listing() -> None:
    deduped = deduplicate(
        [
            Product(name="Sony WH-1000XM5"),
            Product(name="sony wh-1000xm5", price=328.0, rating=4.7),
            Product(name="Bose QC Ultra", price=429.0),
        ],
        limit=10,
    )
    assert len(deduped) == 2
    assert deduped[0].price == 328.0
    assert deduped[0].rating == 4.7


def test_deduplicate_preserves_first_seen_order() -> None:
    deduped = deduplicate(
        [Product(name="B"), Product(name="A"), Product(name="b", price=1.0)], limit=10
    )
    assert [product.name for product in deduped] == ["b", "A"]


def test_deduplicate_enforces_the_limit() -> None:
    products = [Product(name=f"Product {index}") for index in range(20)]
    assert len(deduplicate(products, limit=10)) == 10


def test_deduplicate_drops_nameless_entries() -> None:
    assert deduplicate([Product(name="   "), Product(name="Real")], limit=10) == [
        Product(name="Real")
    ]


def test_article_headlines_are_not_products() -> None:
    for headline in (
        "12 Best Noise Cancelling Headphones Under $200 (August 2026)",
        "Best Headphones under $200 - SoundGuys",
        "The Top 5 Laptops",
        "How to choose a laptop",
        "Which headphones are best?",
        "Laptop Buying Guide",
        "Black Friday deals on headphones",
    ):
        assert not looks_like_a_product(headline), headline


def test_real_product_names_survive() -> None:
    for name in (
        "Sony WH-1000XM5",
        "Anker Soundcore Q30",
        "Bose QuietComfort Ultra Headphones",
        "JBL Tune 770NC",
        "AirPods",
    ):
        assert looks_like_a_product(name), name


def test_overlong_names_are_rejected() -> None:
    assert not looks_like_a_product("Sony " + "very " * 20 + "long name")


def test_clean_name_strips_publisher_and_trailing_noise() -> None:
    assert clean_name("Sony WH-1000XM5 | AudioSite") == "Sony WH-1000XM5"
    assert clean_name("Sennheiser HD 450BT Review") == "Sennheiser HD 450BT"
    assert clean_name("Anker Soundcore Q30 - price") == "Anker Soundcore Q30"


def test_clean_name_leaves_a_genuine_variant_alone() -> None:
    assert clean_name("Sony WH-1000XM5 - Black") == "Sony WH-1000XM5 - Black"


def test_clean_products_renames_instead_of_dropping() -> None:
    cleaned = clean_products(
        [
            Product(name="Sennheiser HD 450BT Review", price=129.0),
            Product(name="12 Best Headphones Under $200"),
        ]
    )
    assert [product.name for product in cleaned] == ["Sennheiser HD 450BT"]
    assert cleaned[0].price == 129.0


def test_cleaning_before_dedup_merges_the_same_product() -> None:
    """'Sony WH-1000XM5 Review' and 'Sony WH-1000XM5' are one product, not two."""
    cleaned = clean_products(
        [Product(name="Sony WH-1000XM5 Review"), Product(name="Sony WH-1000XM5", price=328.0)]
    )
    assert len(deduplicate(cleaned, limit=10)) == 1


def test_a_descriptive_suffix_does_not_make_a_second_product() -> None:
    """Two of three reported slots went to one pair of headphones before this."""
    merged = deduplicate(
        [
            Product(name="Sony WH-CH720N Noise Canceling Wireless Headphones", price=98.0),
            Product(name="Sony WH-CH720N", price=29.99),
        ],
        limit=10,
    )

    assert len(merged) == 1
    assert merged[0].price == 98.0, "the more complete listing wins the conflict"


def test_merging_fills_gaps_from_the_weaker_listing() -> None:
    merged = merge_variants(
        [
            Product(name="JBL Live 780NC", url="https://shop.example/jbl"),
            Product(name="JBL Live 780NC Headphones", price=149.0, rating=4.4),
        ]
    )

    assert len(merged) == 1
    assert merged[0].price == 149.0
    assert merged[0].url == "https://shop.example/jbl"


def test_the_shorter_name_wins_a_tie() -> None:
    merged = merge_variants(
        [Product(name="JBL Live 780NC Wireless Headphones"), Product(name="JBL Live 780NC")]
    )

    assert merged[0].name == "JBL Live 780NC"


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("AirPods", "AirPods Pro"),
        ("Sony WH-1000XM4", "Sony WH-1000XM5"),
        ("Bose QuietComfort", "Bose QuietComfort Ultra"),
        ("Anker Q30", "Anker Q45"),
    ],
)
def test_different_models_are_never_merged(left: str, right: str) -> None:
    assert len(merge_variants([Product(name=left), Product(name=right)])) == 2


def test_unrelated_products_are_left_alone() -> None:
    products = [Product(name="Sony WH-CH720N"), Product(name="JBL Tune 770NC")]

    assert len(merge_variants(products)) == 2
