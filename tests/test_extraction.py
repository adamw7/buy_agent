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
    """A later listing merging in does not move the first sighting down the list."""
    deduped = deduplicate(
        [Product(name="B"), Product(name="A"), Product(name="b", price=1.0)], limit=10
    )
    assert [product.name for product in deduped] == ["B", "A"]
    assert deduped[0].price == 1.0, "the merged-in listing still contributed its price"


def test_two_spellings_of_one_length_keep_the_one_seen_first() -> None:
    """Names tie on length, so the tie-break decides -- and it is search order."""
    deduped = deduplicate(
        [Product(name="Sony WH-1000XM5"), Product(name="sony wh-1000xm5", price=328.0)],
        limit=10,
    )
    assert [product.name for product in deduped] == ["Sony WH-1000XM5"]


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


def test_where_to_buy_is_an_article_not_a_product() -> None:
    """A "where/how/why" opener is a guide about the product, not the product."""
    assert not looks_like_a_product("Where to buy the Sony WH-1000XM5")


def test_a_coupon_page_is_not_a_product() -> None:
    assert not looks_like_a_product("Sony WH-1000XM5 coupon code")


def test_a_greatest_headline_is_not_a_product() -> None:
    """Every superlative the rule lists has to bite, not just "best" and "top"."""
    assert not looks_like_a_product("The Greatest Headphones of 2026")
    assert not looks_like_a_product("Cheapest Headphones Right Now")
    assert not looks_like_a_product("Worst Headphones We Tested")


def test_clean_name_strips_a_hands_on_suffix() -> None:
    """Hyphenated or not, it is the article's angle rather than part of the name."""
    assert clean_name("Sony WH-1000XM5 Hands-On") == "Sony WH-1000XM5"
    assert clean_name("Sony WH-1000XM5 Hands on") == "Sony WH-1000XM5"


def test_clean_name_strips_an_on_sale_suffix() -> None:
    assert clean_name("Sony WH-1000XM5 on sale") == "Sony WH-1000XM5"
    assert clean_name("Sony WH-1000XM5 - Tested") == "Sony WH-1000XM5"


def test_clean_name_drops_punctuation_left_behind() -> None:
    """Whatever the strip leaves dangling must not become part of the dedup key."""
    assert clean_name("Sony WH-1000XM5,") == "Sony WH-1000XM5"
    assert clean_name("Sony WH-1000XM5 -") == "Sony WH-1000XM5"
    assert clean_name("Sony WH-1000XM5: Price") == "Sony WH-1000XM5"


def test_only_the_first_publisher_bar_splits_the_name() -> None:
    """"Name | Review | Site" keeps the name alone, not the name and the angle."""
    assert clean_name("Sony WH-1000XM5 | Review | AudioSite") == "Sony WH-1000XM5"


def test_colour_variants_are_not_merged_into_one_product() -> None:
    """Black and white are both generic, but neither name contains the other.

    Without the subset check the symmetric difference is generic on its own, so
    two genuinely different listings would collapse into one.
    """
    merged = merge_variants(
        [Product(name="Sony WH-CH720N Black"), Product(name="Sony WH-CH720N White")]
    )

    assert len(merged) == 2


def test_merging_carries_over_the_seller_and_the_notes() -> None:
    """Text fields are worth as much as figures: only one page may carry them."""
    merged = merge_variants(
        [
            Product(name="JBL Live 780NC", price=149.0, rating=4.4, review_count=800),
            Product(name="JBL Live 780NC Headphones", seller="Amazon", notes="Great value."),
        ]
    )

    assert len(merged) == 1
    assert merged[0].seller == "Amazon"
    assert merged[0].notes == "Great value."


def test_a_listing_with_a_link_beats_one_without() -> None:
    """A URL counts towards completeness, so the linked listing wins a conflict.

    Both listings fill in ``notes``, and only the winner's survives -- which is
    the only way to see which of the two was judged the more complete.
    """
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
    """Two listings, two prices: the surviving figure keeps its own currency.

    Both halves are grounded -- one page really did say 129, the other really
    did say "249 EUR" -- so verification cannot catch the pairing, which is
    invented by the merge itself and would be reported as "129.00 EUR".
    """
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


def test_a_review_count_never_moves_to_a_rating_from_another_page() -> None:
    """A count is what *its own* rating was averaged over, so it stays with it.

    Carried over alone it both misreports the rating -- "4.4/5 (12,000
    reviews)" -- and lifts the popularity half of the score off a figure that
    belongs to the 4.9 next door.
    """
    merged = merge_variants(
        [
            Product(name="Acme X1", price=100.0, rating=4.4, url="https://a"),
            Product(name="Acme X1 Wireless", rating=4.9, review_count=12_000),
        ]
    )

    assert len(merged) == 1
    assert merged[0].rating == 4.4
    assert merged[0].review_count is None
    assert merged[0].rating_label() == "4.4/5"


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


def test_an_orphaned_count_is_replaced_along_with_the_rating_it_lost() -> None:
    """Grounding blanks a rating and its count separately, so a merge can meet
    a listing holding a count for a rating that is no longer there. Adopting the
    other listing's rating adopts its count too, rather than pairing the new
    figure with the leftover."""
    merged = merge_variants(
        [
            Product(name="Acme X1", price=199.0, review_count=800, url="https://a"),
            Product(name="Acme X1 Wireless", rating=4.9, review_count=12_000),
        ]
    )

    assert len(merged) == 1
    assert (merged[0].rating, merged[0].review_count) == (4.9, 12_000)


def test_exact_duplicates_pair_their_figures_too() -> None:
    """``deduplicate`` merges same-name listings on the same rule as variants."""
    deduped = deduplicate(
        [
            Product(name="Sony WH-CH720N", price=129.0, review_count=800, url="https://us/a"),
            Product(name="Sony WH-CH720N", price=249.0, currency="EUR", url="https://eu/b"),
        ],
        10,
    )

    assert len(deduped) == 1
    assert (deduped[0].price, deduped[0].currency) == (129.0, None)
