"""What a page declares about its products in JSON-LD, written as lines (ADR-0078)."""

from __future__ import annotations

import json

from buy_agent.structured import declared
from buy_agent.verification import (
    build_haystack,
    mentions_number,
    mentions_rating,
    mentions_review_count,
    mentions_standing,
)
from buy_agent.search import SearchResult


def script(*objects: object) -> str:
    return json.dumps(objects[0] if len(objects) == 1 else list(objects))


SONY = {
    "@context": "https://schema.org",
    "@type": "Product",
    "name": "Sony WH-1000XM5",
    "offers": {
        "@type": "Offer",
        "price": "348.00",
        "priceCurrency": "USD",
        "availability": "https://schema.org/InStock",
        "itemCondition": "https://schema.org/NewCondition",
        "seller": {"@type": "Organization", "name": "AudioShop"},
    },
    "aggregateRating": {"@type": "AggregateRating", "ratingValue": 4.6, "reviewCount": 3200},
}


def test_an_offer_and_a_rating_are_written_as_a_shop_prints_them() -> None:
    assert declared([script(SONY)]) == [
        "Sony WH-1000XM5: 348.00 USD, in stock, condition: new, sold by AudioShop",
        "Sony WH-1000XM5: rated 4.6/5 from 3200 reviews",
    ]


def test_what_is_written_is_what_grounding_accepts() -> None:
    """The lines are page text, so the model and the checks read the same thing; a
    figure written any other way would be blanked by the step that trusts it."""
    page = SearchResult(title="", url="https://shop.example/xm5", snippet="")
    haystack = build_haystack(
        [page.model_copy(update={"content": "\n".join(declared([script(SONY)]))})]
    )

    assert mentions_number(haystack, 348)
    assert mentions_rating(haystack, 4.6)
    assert mentions_review_count(haystack, 3200)
    assert mentions_standing(haystack, "in stock")
    assert mentions_standing(haystack, "new")


def test_every_standing_schema_org_names_is_written_in_words_grounding_reads() -> None:
    for term, standing in (
        ("OutOfStock", "out of stock"),
        ("SoldOut", "out of stock"),
        ("Discontinued", "out of stock"),
        ("LimitedAvailability", "in stock"),
        ("OnlineOnly", "in stock"),
    ):
        offer = {"@type": "Offer", "price": 10, "availability": f"http://schema.org/{term}"}
        [line] = declared([script({"@type": "Product", "name": "Kettle", "offers": offer})])
        assert mentions_standing(line, standing), term


def test_a_pre_order_is_said_and_claims_neither_standing() -> None:
    offer = {"@type": "Offer", "price": 10, "availability": "PreOrder"}
    [line] = declared([script({"@type": "Product", "name": "Kettle", "offers": offer})])

    assert line == "Kettle: 10.00, available to pre-order"
    assert not mentions_standing(line, "in stock")
    assert not mentions_standing(line, "out of stock")


def test_a_used_or_refurbished_condition_reads_as_one() -> None:
    for term, standing in (("UsedCondition", "used"), ("RefurbishedCondition", "refurbished")):
        offer = {"@type": "Offer", "price": 10, "itemCondition": term}
        [line] = declared([script({"@type": "Product", "name": "Kettle", "offers": offer})])
        assert mentions_standing(line, standing), term


def test_products_are_found_in_a_graph_and_in_a_list_of_items() -> None:
    graph = {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "WebPage", "name": "Best kettles"},
            {
                "@type": "ItemList",
                "itemListElement": [
                    {"@type": "ListItem", "item": {**SONY, "name": "Fellow Stagg EKG"}},
                ],
            },
        ],
    }

    lines = declared([script(graph)])

    assert lines[0].startswith("Fellow Stagg EKG: 348.00 USD")
    assert not any("Best kettles" in line for line in lines)


def test_a_type_given_as_a_list_counts() -> None:
    product = {**SONY, "@type": ["Product", "Thing"]}

    assert len(declared([script(product)])) == 2


def test_every_offer_of_a_list_is_written_once() -> None:
    offers = [
        {"@type": "Offer", "price": 348, "priceCurrency": "USD"},
        {"@type": "Offer", "price": 348, "priceCurrency": "USD"},
        {"@type": "Offer", "price": 329, "priceCurrency": "usd"},
        "not an offer",
    ]

    assert declared([script({"@type": "Product", "name": "Kettle", "offers": offers})]) == [
        "Kettle: 348.00 USD",
        "Kettle: 329.00 USD",
    ]


def test_an_aggregate_offer_gives_its_listings_else_its_lowest_price() -> None:
    inner = {
        "@type": "AggregateOffer",
        "lowPrice": 99,
        "priceCurrency": "EUR",
        "offers": [{"@type": "Offer", "price": 105, "priceCurrency": "EUR"}],
    }
    bare = {"@type": "AggregateOffer", "lowPrice": "99.50", "priceCurrency": "EUR"}
    empty = {"@type": "AggregateOffer", "offerCount": 3}

    def lines(offers: dict) -> list[str]:
        return declared([script({"@type": "Product", "name": "Kettle", "offers": offers})])

    assert lines(inner) == ["Kettle: 105.00 EUR"]
    assert lines(bare) == ["Kettle: 99.50 EUR"]
    assert lines(empty) == []


def test_a_price_may_be_given_as_a_specification() -> None:
    for specification in (
        {"price": "12.5"},
        [{"price": 12.5}],
    ):
        offer = {"@type": "Offer", "priceSpecification": specification, "priceCurrency": "GBP"}
        assert declared([script({"@type": "Product", "name": "Kettle", "offers": offer})]) == [
            "Kettle: 12.50 GBP"
        ]


def test_a_price_that_is_no_number_or_no_amount_is_left_out() -> None:
    for price in ("call us", "NaN", True, 0, -5, None, ["9"]):
        offer = {"@type": "Offer", "price": price, "priceCurrency": "USD"}
        assert declared([script({"@type": "Product", "name": "Kettle", "offers": offer})]) == [], (
            price
        )


def test_a_currency_that_is_no_code_is_not_written() -> None:
    offer = {"@type": "Offer", "price": 9, "priceCurrency": "dollars"}

    assert declared([script({"@type": "Product", "name": "Kettle", "offers": offer})]) == [
        "Kettle: 9.00"
    ]


def test_a_seller_may_be_a_name_and_a_long_one_is_dropped() -> None:
    def line(seller: object) -> str:
        offer = {"@type": "Offer", "price": 9, "seller": seller}
        [written] = declared([script({"@type": "Product", "name": "Kettle", "offers": offer})])
        return written

    assert line("Kettle Co") == "Kettle: 9.00, sold by Kettle Co"
    assert line("x" * 200) == "Kettle: 9.00"
    assert line(42) == "Kettle: 9.00"


def test_a_rating_on_another_scale_is_put_on_five() -> None:
    rating = {"ratingValue": "9.2", "bestRating": "10", "ratingCount": "41"}

    assert declared([script({"@type": "Product", "name": "Kettle", "aggregateRating": rating})]) == [
        "Kettle: rated 4.6/5 from 41 reviews"
    ]


def test_a_rating_outside_its_scale_or_without_a_value_is_left_out() -> None:
    for rating in (
        {"ratingValue": 6},
        {"ratingValue": -1},
        {"bestRating": 5},
        {"ratingValue": 4, "bestRating": -5},
        "4.5",
    ):
        product = {"@type": "Product", "name": "Kettle", "aggregateRating": rating}
        assert declared([script(product)]) == [], rating


def test_a_count_that_is_no_whole_number_of_people_is_left_off_the_rating() -> None:
    for count in (0, 2.5, "many"):
        rating = {"ratingValue": 4.5, "reviewCount": count}
        product = {"@type": "Product", "name": "Kettle", "aggregateRating": rating}
        assert declared([script(product)]) == ["Kettle: rated 4.5/5"], count


def test_a_product_with_nothing_to_say_or_no_usable_name_writes_nothing() -> None:
    assert declared([script({"@type": "Product", "name": "Kettle"})]) == []
    assert declared([script({**SONY, "name": None})]) == []
    assert declared([script({**SONY, "name": "word " * 40})]) == []


def test_a_script_that_is_not_json_is_skipped_as_a_browser_skips_it() -> None:
    assert declared(["{ not json", "", script(SONY), "42"]) == declared([script(SONY)])


def test_a_catalogue_is_cut_at_twenty_products() -> None:
    catalogue = [{**SONY, "name": f"Kettle {index}"} for index in range(30)]

    lines = declared([script(*catalogue)])

    assert {line.split(":")[0] for line in lines} == {f"Kettle {index}" for index in range(20)}


def test_nesting_past_any_real_page_is_not_followed() -> None:
    deep: dict = {**SONY}
    for _ in range(12):
        deep = {"@type": "Thing", "about": deep}

    assert declared([script(deep)]) == []


def test_a_line_two_products_share_is_written_once() -> None:
    assert declared([script(SONY), script(SONY)]) == declared([script(SONY)])
