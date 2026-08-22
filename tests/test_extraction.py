"""Prompt formatting, name cleaning and deduplication."""

from __future__ import annotations

import pytest

from buy_agent import verification
from buy_agent.extraction import (
    EXTRACTION_PROMPT,
    GENERIC_WORDS,
    NAME_TOKENS,
    QUERY_PROMPT,
    build_extraction_chain,
    build_query_chain,
    clean_name,
    clean_products,
    deduplicate,
    format_results,
    looks_like_a_product,
    merge_variants,
)
from buy_agent.models import Product, ProductList, SearchQuery
from buy_agent.search import SearchResult

from tests.conftest import FakeLLM


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


def test_deduplicate_merges_figures_across_identical_names() -> None:
    """Two pages, one product: one quoted the price, the other the rating."""
    deduped = deduplicate(
        [
            Product(name="Sony WH-1000XM5", price=328.0, currency="USD"),
            Product(name="Sony WH-1000XM5", rating=4.6, review_count=3200, url="http://x"),
        ],
        limit=10,
    )

    assert len(deduped) == 1
    assert deduped[0].price == 328.0
    assert deduped[0].currency == "USD"
    assert deduped[0].rating == 4.6
    assert deduped[0].review_count == 3200


def test_deduplicate_does_not_overwrite_a_figure_it_already_has() -> None:
    deduped = deduplicate(
        [
            Product(name="Sony WH-1000XM5", price=328.0, rating=4.6),
            Product(name="Sony WH-1000XM5", price=399.0),
        ],
        limit=10,
    )

    assert [product.price for product in deduped] == [328.0]


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


def test_the_query_prompt_asks_for_a_shopping_query() -> None:
    messages = QUERY_PROMPT.format_messages(request="a two person tent under $300")

    assert "search query" in messages[0].content.lower()
    assert "a two person tent under $300" in messages[1].content


def test_the_query_chain_answers_with_a_search_query() -> None:
    llm = FakeLLM(query=SearchQuery(query="two person tent price review"))

    answer = build_query_chain(llm).invoke({"request": "a two person tent"})

    assert answer.query == "two person tent price review"


def test_the_extraction_chain_answers_with_a_product_list() -> None:
    llm = FakeLLM(products=ProductList(products=[]))

    answer = build_extraction_chain(llm).invoke(
        {"request": "tents", "results": "[1] nothing", "limit": 3}
    )

    assert isinstance(answer, ProductList)


def test_both_chains_ask_for_a_schema_constrained_answer() -> None:
    """json_schema is what stops a small model answering with prose."""
    calls: list[dict] = []

    class Recorder(FakeLLM):
        def with_structured_output(self, schema, **kwargs):
            calls.append({"schema": schema, **kwargs})
            return super().with_structured_output(schema, **kwargs)

    llm = Recorder()
    build_query_chain(llm)
    build_extraction_chain(llm)

    assert [call["schema"] for call in calls] == [SearchQuery, ProductList]
    assert all(call["method"] == "json_schema" for call in calls)


def test_format_results_includes_the_fetched_page_text() -> None:
    rendered = format_results(
        [SearchResult(title="Shop", url="https://s", snippet="s", content="JBL Live 780NC\n$149")]
    )

    assert "PAGE:" in rendered
    assert "$149" in rendered


def test_format_results_of_nothing_is_empty() -> None:
    assert format_results([]) == ""


def test_clean_name_keeps_only_what_precedes_the_first_publisher_bar() -> None:
    assert clean_name("Sony WH-1000XM5 | Audio | Site") == "Sony WH-1000XM5"


def test_clean_name_of_pure_page_furniture_is_empty() -> None:
    """And an empty name never gets past looks_like_a_product."""
    assert clean_name("Reviews") == ""
    assert not looks_like_a_product("")


def test_clean_name_leaves_a_name_with_nothing_to_strip_alone() -> None:
    assert clean_name("Sony WH-1000XM5") == "Sony WH-1000XM5"


def test_a_question_is_an_article_not_a_product() -> None:
    assert not looks_like_a_product("Are the Sony XM5 worth it?")


def test_a_blank_name_is_not_a_product() -> None:
    assert not looks_like_a_product("   ")


def test_clean_products_of_nothing_is_nothing() -> None:
    assert clean_products([]) == []


def test_clean_products_copies_rather_than_renaming_in_place() -> None:
    original = Product(name="Sony WH-1000XM5 Review", price=328.0)

    cleaned = clean_products([original])

    assert original.name == "Sony WH-1000XM5 Review"
    assert cleaned[0].name == "Sony WH-1000XM5"


def test_deduplicate_of_nothing_is_nothing() -> None:
    assert deduplicate([], limit=10) == []


def test_the_limit_is_applied_after_merging_not_before() -> None:
    """Two listings of one product plus a second product must not cost a slot."""
    deduped = deduplicate(
        [Product(name="Sony XM5"), Product(name="sony xm5"), Product(name="JBL 770NC")],
        limit=2,
    )

    assert [product.name for product in deduped] == ["Sony XM5", "JBL 770NC"]


def test_a_name_with_no_words_at_all_is_never_merged() -> None:
    """Two nameless entries share no tokens, so they are not one product."""
    assert len(merge_variants([Product(name="---"), Product(name="***")])) == 2


def test_merging_ignores_case_and_word_order() -> None:
    merged = merge_variants(
        [Product(name="Sony WH-CH720N"), Product(name="sony wireless wh-ch720n")]
    )

    assert len(merged) == 1


def test_merge_variants_of_nothing_is_nothing() -> None:
    assert merge_variants([]) == []


def test_generic_words_identify_nothing_on_their_own() -> None:
    """A brand or a model number in this set would let an invented product pass grounding."""
    assert not any(character.isdigit() for word in GENERIC_WORDS for character in word)
    assert not {"sony", "bose", "anker", "jbl", "apple"} & GENERIC_WORDS


def test_merging_and_grounding_read_a_name_the_same_way() -> None:
    """They must agree on what a name's words are, or one contradicts the other."""
    assert verification.NAME_TOKENS is NAME_TOKENS
    assert verification.GENERIC_WORDS is GENERIC_WORDS
