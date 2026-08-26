"""Numbers the search results never mentioned must not survive into the ranking."""

from __future__ import annotations

import logging

import pytest

from buy_agent.models import Product
from buy_agent.search import SearchResult
from buy_agent.verification import (
    attribute_sources,
    build_haystack,
    drop_ungrounded,
    ground,
    mentions_name,
    mentions_number,
    mentions_rating,
    source_urls,
    verify_numbers,
    verify_opinions,
)

SOURCES = [
    SearchResult(
        title="Sony WH-CH720N deal",
        snippet="Now $129, rated 4.3 out of 5 from 12,500 shoppers.",
    ),
]
HAYSTACK = build_haystack(SOURCES)


def test_supported_figures_are_kept() -> None:
    product = Product(
        name="Sony WH-CH720N", price=129.0, currency="USD", rating=4.3, review_count=12500
    )

    verified = verify_numbers([product], HAYSTACK)[0]

    assert verified.price == 129.0
    assert verified.currency == "USD"
    assert verified.rating == 4.3
    assert verified.review_count == 12500


def test_an_invented_price_is_dropped() -> None:
    """The classic failure: a figure carried over from the prompt's own example."""
    product = Product(name="Sony WH-CH720N", price=99.0, currency="USD", rating=4.3)

    verified = verify_numbers([product], HAYSTACK)[0]

    assert verified.price is None
    assert verified.currency is None
    assert verified.rating == 4.3


def test_an_invented_review_count_is_dropped() -> None:
    product = Product(name="Sony WH-CH720N", price=129.0, review_count=90000)

    verified = verify_numbers([product], HAYSTACK)[0]

    assert verified.review_count is None
    assert verified.price == 129.0


def test_a_rejected_rating_takes_its_review_count_with_it() -> None:
    """ADR-0022's pairing, one stage earlier than the merge it was written for.

    12,500 really is in the sources, so checked on its own it survives -- and
    then qualifies a rating that has just been thrown out. The card reads
    "unrated" beside nothing, and the ranking still scores the popularity of a
    count that no longer has a rating to be the popularity of, which is the
    invented 4.9 earning its place after all.
    """
    product = Product(name="Sony WH-CH720N", rating=4.9, review_count=12500)

    verified = verify_numbers([product], HAYSTACK)[0]

    assert verified.rating is None
    assert verified.review_count is None


def test_a_kept_rating_keeps_the_review_count_the_sources_back() -> None:
    """Only unsupported pairings go; a pair the sources back is left whole."""
    product = Product(name="Sony WH-CH720N", rating=4.3, review_count=12500)

    verified = verify_numbers([product], HAYSTACK)[0]

    assert (verified.rating, verified.review_count) == (4.3, 12500)


def test_a_rejected_review_count_leaves_its_rating_alone() -> None:
    """The qualifier follows the figure, never the figure the qualifier."""
    product = Product(name="Sony WH-CH720N", rating=4.3, review_count=90000)

    verified = verify_numbers([product], HAYSTACK)[0]

    assert verified.rating == 4.3
    assert verified.review_count is None


def test_verification_never_drops_the_product_itself() -> None:
    products = [Product(name="Ghost Model", price=1.0, rating=1.0, review_count=1)]

    verified = verify_numbers(products, HAYSTACK)

    assert len(verified) == 1
    assert verified[0].name == "Ghost Model"


def test_thousands_separators_are_normalised() -> None:
    assert build_haystack(SOURCES).count("12500") == 1


def test_a_decimal_comma_is_not_a_thousands_separator() -> None:
    """A euro-language page prices in "129,99", which is 129.99 and not 12999.

    Stripping every comma between digits alike made every price on a German or
    Polish page a hundred times too big, so a figure the model had read off it
    correctly was no longer in the haystack and lost its grounding. ``--region
    pl-pl`` and the currency codes in ``fetch`` are what put those pages in reach.
    """
    haystack = build_haystack([SearchResult(snippet="Cena: 129,99 PLN za sztuke")])

    assert mentions_number(haystack, 129.99)
    assert not mentions_number(haystack, 12999)


def test_a_thousands_separator_is_still_one() -> None:
    """Three digits behind the comma is the only shape that means thousands."""
    haystack = build_haystack([SearchResult(snippet="Was $1,299 from 12,500 shoppers")])

    assert mentions_number(haystack, 1299)
    assert mentions_number(haystack, 12500)


@pytest.mark.parametrize("value", [129, 129.0, 4.3])
def test_numbers_present_in_the_text_are_found(value: float) -> None:
    assert mentions_number(HAYSTACK, value)


@pytest.mark.parametrize("value", [12, 29, 1129, 4.5, 99])
def test_digits_inside_a_longer_number_do_not_count(value: float) -> None:
    assert not mentions_number(HAYSTACK, value)


def test_a_price_written_with_decimals_still_matches() -> None:
    sources = [SearchResult(title="", snippet="On sale for $179.99 today")]

    assert mentions_number(build_haystack(sources), 179.99)


@pytest.mark.parametrize(
    ("snippet", "rating"),
    [
        ("Scores 4.6/5 overall", 4.6),
        ("Rated 4.6 out of 5", 4.6),
        ("A solid 4.6 stars", 4.6),
        ("Rating: 4.6", 4.6),
        ("Rated 5 stars", 5.0),
    ],
)
def test_ratings_are_recognised_however_they_are_written(snippet, rating) -> None:
    assert mentions_rating(build_haystack([SearchResult(snippet=snippet)]), rating)


def test_a_bare_number_is_not_a_rating() -> None:
    """'out of 5' must not vouch for a claimed 5.0, or every fake rating passes."""
    haystack = build_haystack([SearchResult(snippet="Rated 4.7 out of 5 by 500 people")])

    assert not mentions_rating(haystack, 5.0)
    assert not mentions_rating(haystack, 500.0)


def test_a_rounded_rating_is_not_treated_as_supported() -> None:
    haystack = build_haystack([SearchResult(snippet="Rated 4.3 out of 5")])

    assert not mentions_rating(haystack, 4.0)


def test_a_rounded_price_is_still_accepted() -> None:
    """Prices are quoted loosely ('about $180'), so rounding stays acceptable."""
    assert mentions_number(build_haystack([SearchResult(snippet="$179.99")]), 179.0)


def test_a_four_figure_price_with_decimals_survives() -> None:
    """Six significant digits are not enough: 12999.95 must not become "13000"."""
    sources = [SearchResult(snippet="The rig is $12,999.95 with the upgrade.")]

    assert mentions_number(build_haystack(sources), 12999.95)


def test_a_decimal_price_matches_a_trailing_zero() -> None:
    """A page writes 10000.5 as "$10,000.50"."""
    sources = [SearchResult(snippet="Yours for $10,000.50 today")]

    assert mentions_number(build_haystack(sources), 10000.50)
    assert not mentions_number(build_haystack(sources), 10000.55)


def test_an_expensive_product_keeps_its_price() -> None:
    sources = [SearchResult(title="Studio rig", snippet="Ampex ATR-102 -- $12,999.95")]
    product = Product(name="Ampex ATR-102", price=12999.95, currency="USD")

    verified = verify_numbers([product], build_haystack(sources))[0]

    assert verified.price == 12999.95
    assert verified.currency == "USD"


def test_a_score_out_of_ten_does_not_vouch_for_a_rating_out_of_five() -> None:
    """4.5/10 means 2.25/5; taking it as a 4.5 puts a mediocre product on top."""
    haystack = build_haystack([SearchResult(snippet="Our testers scored it 4.5 out of 10")])

    assert not mentions_rating(haystack, 4.5)
    assert not mentions_rating(build_haystack([SearchResult(snippet="Scores 4.5/10")]), 4.5)


def test_a_product_absent_from_the_sources_is_dropped() -> None:
    kept = drop_ungrounded(
        [Product(name="Sony WH-CH720N"), Product(name="Bonavita Gooseneck Kettle")], HAYSTACK
    )

    assert [product.name for product in kept] == ["Sony WH-CH720N"]


def test_descriptive_words_do_not_have_to_appear() -> None:
    """The page says 'WH-CH720N'; the model wrote a longer marketing name."""
    assert mentions_name(HAYSTACK, "Sony WH-CH720N Wireless Noise Cancelling Headphones")


def test_a_name_of_only_generic_words_is_not_grounded() -> None:
    assert not mentions_name(HAYSTACK, "Wireless Headphones")


def test_a_partly_matching_name_still_counts() -> None:
    """Two of three distinctive words is over the coverage floor."""
    assert mentions_name(HAYSTACK, "Sony WH-CH720N Studio")


def test_ground_drops_the_product_and_then_its_figures() -> None:
    grounded = ground(
        [
            Product(name="Sony WH-CH720N", price=1.0, rating=4.3),
            Product(name="Bonavita Gooseneck", price=80.0),
        ],
        SOURCES,
    )

    assert len(grounded) == 1
    assert grounded[0].price is None, "1.0 appears nowhere in the sources"
    assert grounded[0].rating == 4.3


def test_page_content_counts_as_a_source() -> None:
    """Figures come from the fetched page, not just the snippet."""
    sources = [SearchResult(title="Shop", snippet="Headphones", content="JBL Live 780NC\n$149")]

    assert mentions_name(build_haystack(sources), "JBL Live 780NC")
    assert mentions_number(build_haystack(sources), 149)


def test_an_empty_source_set_grounds_nothing() -> None:
    """No sources means no support -- not a free pass."""
    assert build_haystack([]) == ""
    assert drop_ungrounded([Product(name="Sony WH-CH720N")], "") == []


def test_grounding_nothing_yields_nothing() -> None:
    assert ground([], SOURCES) == []


def test_a_wholly_invented_listing_keeps_only_its_name() -> None:
    product = Product(
        name="Sony WH-CH720N", price=11.0, currency="USD", rating=1.1, review_count=7
    )

    verified = verify_numbers([product], HAYSTACK)[0]

    assert verified.name == "Sony WH-CH720N"
    assert verified.price is None
    assert verified.currency is None
    assert verified.rating is None
    assert verified.review_count is None


def test_verification_copies_rather_than_editing_in_place() -> None:
    original = Product(name="Sony WH-CH720N", price=99.0)

    verified = verify_numbers([original], HAYSTACK)[0]

    assert original.price == 99.0
    assert verified.price is None


def test_a_bare_zero_is_matched_like_any_other_number() -> None:
    """What ``mentions_number`` does with a zero, which is nothing special.

    Nothing in the pipeline hands it one any more -- ``to_product`` reads a zero
    price and a zero review count alike as unknown -- but the rule this function
    applies should not quietly depend on that.
    """
    haystack = build_haystack([SearchResult(snippet="Bundled adapter: $0 with purchase")])

    assert mentions_number(haystack, 0)


def test_a_price_inside_a_longer_price_is_not_a_match() -> None:
    haystack = build_haystack([SearchResult(snippet="Was $1299.99, now less")])

    assert not mentions_number(haystack, 129.0)


def test_a_rating_lead_in_out_of_ten_is_rejected() -> None:
    """'scored it 8 out of 10' means 4/5, and must not vouch for a claimed 8."""
    haystack = build_haystack([SearchResult(snippet="Our testers scored it 8 out of 10")])

    assert not mentions_rating(haystack, 8)


def test_a_rating_below_the_claimed_one_does_not_vouch_for_it() -> None:
    haystack = build_haystack([SearchResult(snippet="Rated 4.3 out of 5")])

    assert not mentions_rating(haystack, 4.7)


def test_one_distinctive_word_in_four_is_not_a_mention() -> None:
    assert not mentions_name(HAYSTACK, "Bose QuietComfort Ultra Sony")


def test_a_name_whose_only_distinctive_word_is_present_counts() -> None:
    assert mentions_name(HAYSTACK, "Wireless Sony Headphones")
    assert not mentions_name(HAYSTACK, "Wireless Bose Headphones")


def test_a_nameless_product_is_never_grounded() -> None:
    assert not mentions_name(HAYSTACK, "")
    assert not mentions_name(HAYSTACK, "--- !!!")


def test_the_title_is_searched_as_well_as_the_snippet() -> None:
    sources = [SearchResult(title="Ampex ATR-102 for sale", snippet="Studio gear")]

    assert mentions_name(build_haystack(sources), "Ampex ATR-102")


def test_figures_are_verified_across_all_of_the_results() -> None:
    """One page named the product, another quoted the price; both are sources."""
    sources = [
        SearchResult(title="JBL Live 780NC hands-on", snippet="A solid pair."),
        SearchResult(title="Deals roundup", snippet="The 780NC is $149 this week."),
    ]

    grounded = ground([Product(name="JBL Live 780NC", price=149.0)], sources)

    assert grounded[0].price == 149.0


def test_grounding_reports_what_it_dropped(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="buy_agent.verification"):
        ground([Product(name="Bonavita Gooseneck Kettle", price=80.0)], SOURCES)

    assert "Dropped 1 product(s) absent from the search results" in caplog.text


def test_a_slash_rating_needs_no_lead_in_word() -> None:
    """"4.6/5" is a rating on its own; nothing has to introduce it."""
    haystack = build_haystack([SearchResult(snippet="The Sony sits at 4.6/5 overall")])

    assert mentions_rating(haystack, 4.6)


def test_a_rating_written_as_of_five_is_recognised() -> None:
    """Pages drop the "out": "a steady 4.6 of 5"."""
    haystack = build_haystack([SearchResult(snippet="A steady 4.6 of 5 across the panel")])

    assert mentions_rating(haystack, 4.6)


def test_a_rating_is_recognised_whatever_its_case() -> None:
    """Shop pages shout their figures in headings, with no lead-in word to lean on."""
    assert mentions_rating(build_haystack([SearchResult(snippet="A SOLID 4.6 STARS")]), 4.6)
    assert mentions_rating(build_haystack([SearchResult(snippet="4.6 OUT OF 5")]), 4.6)


def test_a_scored_lead_in_is_a_rating() -> None:
    """"scored it 4.6" names a rating without ever writing the scale."""
    assert mentions_rating(build_haystack([SearchResult(snippet="Reviewers scored it 4.6")]), 4.6)
    assert mentions_rating(build_haystack([SearchResult(snippet="We score it 4.6 here")]), 4.6)


def test_a_lead_in_word_far_from_the_figure_does_not_vouch_for_it() -> None:
    """"rated" and a number a sentence apart are not one claim about one product."""
    near = build_haystack([SearchResult(snippet="rated a solid 4.6 by our testers")])
    far = build_haystack([SearchResult(snippet="rated by our whole panel of testers, 4.6")])

    assert mentions_rating(near, 4.6)
    assert not mentions_rating(far, 4.6)


def test_a_counted_headline_does_not_vouch_for_a_rating() -> None:
    """"rated the 5 best headphones" is an article's title, not a 5 out of 5.

    The figure belongs to the noun after it. Any twelve non-digits used to count
    as "one claim about one product", which handed a listing claiming a perfect
    score the one page that could never support it. The tell sits on either side
    of the figure, so both are ruled out.
    """
    after = build_haystack([SearchResult(snippet="We rated the 5 best headphones of 2026")])
    before = build_haystack([SearchResult(snippet="We rated the top 3 headphones of 2026")])

    assert not mentions_rating(after, 5)
    assert not mentions_rating(before, 3)


def test_a_superlative_beside_a_real_rating_does_not_cost_it() -> None:
    """Only the counting sense is ruled out, not the word wherever it turns up."""
    haystack = build_haystack([SearchResult(snippet="Rating 4.5 -- the best value on test")])

    assert mentions_rating(haystack, 4.5)


def test_a_rating_that_is_only_the_tail_of_a_longer_number_is_rejected() -> None:
    """As with prices, a figure must not be verified by digits it merely ends."""
    haystack = build_haystack([SearchResult(snippet="Rated 14.6 out of 5")])

    assert not mentions_rating(haystack, 4.6)


def test_the_coverage_bar_is_three_distinctive_words_in_five() -> None:
    """Where the 0.6 floor falls decides whether an invented name can pass."""
    haystack = build_haystack([SearchResult(snippet="The Anker Soundcore Life sits at $99.")])

    assert mentions_name(haystack, "Anker Soundcore Life Q30 Pro"), "3 of 5 clears the bar"
    assert not mentions_name(haystack, "Anker Soundcore Boost Max Pro"), "2 of 5 does not"


def test_half_a_name_is_not_enough_to_ground_it() -> None:
    """The half-way case, which is what actually fixes the floor at 0.6.

    Three-in-five above and two-in-five below leave every threshold from 0.5 to
    0.6 looking identical, and a 0.5 bar is the one that lets a model pair a real
    brand with an invented model number and have it pass.
    """
    haystack = build_haystack([SearchResult(snippet="The Bose QuietComfort is $279.")])

    assert not mentions_name(haystack, "Bose QuietComfort Ultra 2024"), "2 of 4 is short"
    assert mentions_name(haystack, "Bose QuietComfort Ultra"), "2 of 3 clears it"


PAGES = [
    SearchResult(
        title="Sony WH-CH720N deal",
        url="https://shop.example/sony",
        snippet="Now $129, rated 4.3 out of 5 from 12,500 shoppers.",
    ),
    SearchResult(
        title="Anker Soundcore Q30 review",
        url="https://review.example/anker",
        snippet="The Q30 is $79.",
    ),
]


def test_a_product_is_linked_to_the_page_that_mentions_it() -> None:
    """The usual case: the model reports no link at all, so one is worked out."""
    linked = attribute_sources([Product(name="Anker Soundcore Q30")], PAGES)[0]

    assert linked.url == "https://review.example/anker"


def test_a_link_to_a_page_that_was_never_searched_is_replaced() -> None:
    """An invented link is the one hallucination the shopper would click."""
    product = Product(name="Sony WH-CH720N", url="https://invented.example/deal")

    linked = attribute_sources([product], PAGES)[0]

    assert linked.url == "https://shop.example/sony"


def test_a_link_the_model_copied_off_a_searched_page_is_kept() -> None:
    product = Product(name="Sony WH-CH720N", url="https://shop.example/sony")

    linked = attribute_sources([product], PAGES)[0]

    assert linked.url == "https://shop.example/sony"


def test_each_product_gets_its_own_page() -> None:
    """Attribution is per product, not one link for the whole run."""
    linked = attribute_sources(
        [Product(name="Sony WH-CH720N"), Product(name="Anker Soundcore Q30")], PAGES
    )

    assert [product.url for product in linked] == [
        "https://shop.example/sony",
        "https://review.example/anker",
    ]


def test_a_product_no_single_page_mentions_keeps_no_link() -> None:
    """Better no link than one borrowed from a page about something else."""
    linked = attribute_sources([Product(name="Bose QuietComfort Ultra")], PAGES)[0]

    assert linked.url is None


def test_a_name_split_across_two_pages_is_not_attributed_to_either() -> None:
    """``ground`` clears a name the sources cover jointly; a link needs one page.

    ``drop_ungrounded`` asks whether the results as a whole mention the product,
    so a name each page only half covers survives -- and then has nowhere to
    point, because neither page is the one it was found on.
    """
    pages = [
        SearchResult(url="https://a.example", snippet="Sony WH headphones are here."),
        SearchResult(url="https://b.example", snippet="The CH720N Ultra is in stock."),
    ]
    name = "Sony WH-CH720N Ultra Max"

    assert mentions_name(build_haystack(pages), name), "4 of 5 tokens, jointly"
    assert not any(mentions_name(build_haystack([page]), name) for page in pages)
    assert attribute_sources([Product(name=name)], pages)[0].url is None


def test_sources_without_urls_leave_the_link_blank() -> None:
    """Fetching can hand back a result the search never gave a URL."""
    product = Product(name="Sony WH-CH720N", url="https://invented.example")

    assert attribute_sources([product], SOURCES)[0].url is None


def test_attribution_copies_rather_than_editing_in_place() -> None:
    original = Product(name="Sony WH-CH720N", url="https://invented.example")

    linked = attribute_sources([original], PAGES)[0]

    assert original.url == "https://invented.example"
    assert linked.url == "https://shop.example/sony"


def test_grounding_links_what_it_keeps() -> None:
    """The link is attached inside ``ground``, so ranking never sees a bare product."""
    grounded = ground([Product(name="Sony WH-CH720N", price=129.0)], PAGES)

    assert grounded[0].url == "https://shop.example/sony"
    assert grounded[0].price == 129.0


def test_a_replaced_link_is_reported(caplog) -> None:
    product = Product(name="Sony WH-CH720N", url="https://invented.example")

    with caplog.at_level(logging.INFO, logger="buy_agent.verification"):
        attribute_sources([product], PAGES)

    assert "1 link(s)" in caplog.text


def test_a_missing_link_is_not_reported_as_dropped(caplog) -> None:
    """Nothing was dropped when the model never offered a link in the first place."""
    with caplog.at_level(logging.INFO, logger="buy_agent.verification"):
        attribute_sources([Product(name="Sony WH-CH720N")], PAGES)

    assert "link(s)" not in caplog.text


def test_the_searched_pages_are_the_ones_with_urls() -> None:
    assert source_urls(PAGES) == {"https://shop.example/sony", "https://review.example/anker"}
    assert source_urls(SOURCES) == set()


def test_a_model_number_is_not_found_inside_a_longer_number() -> None:
    """The name check matches words, not substrings.

    ``mentions_number`` is careful that 129 is not read out of 1299; the name
    check has to be as careful, or the digits of an invented model number are
    "supported" by any longer number on the page -- one "$1700" vouching for a
    Bose 700 and a Bose 170 alike.
    """
    sources = [
        SearchResult(
            title="Bose QuietComfort Ultra review",
            snippet="Our pick is the Bose QuietComfort Ultra at $1700 after tax.",
        )
    ]
    haystack = build_haystack(sources)

    assert mentions_name(haystack, "Bose QuietComfort Ultra")
    assert not mentions_name(haystack, "Bose 700")
    assert not mentions_name(haystack, "Bose 170")


def test_a_hyphenated_model_number_is_still_matched_a_word_at_a_time() -> None:
    """What the 0.6 bar exists for: the page writes less of the name than the
    model did, and the two are split into words by the same rule either side."""
    sources = [SearchResult(title="WH-CH720N tested", snippet="The WH-CH720N is $99.")]

    assert mentions_name(build_haystack(sources), "Sony WH-CH720N Wireless")


def test_an_invented_product_is_dropped_rather_than_grounded_on_a_substring() -> None:
    sources = [
        SearchResult(
            title="Anker Soundcore Space Q45",
            snippet="The Anker Soundcore Space Q45 is $1499 in the sale.",
            url="https://audiosite.example/q45",
        )
    ]

    kept = ground(
        [Product(name="Soundcore 149"), Product(name="Anker Soundcore Space Q45")],
        sources,
    )

    assert [product.name for product in kept] == ["Anker Soundcore Space Q45"]


# -- quoted opinions -----------------------------------------------------------

OPINIONATED = [
    SearchResult(
        title="Sony WH-CH720N review",
        snippet="Reviewers found the noise cancelling uncanny for the money, "
        "though the case is too bulky for a coat pocket.",
        url="https://audiosite.example/ch720n",
    ),
    SearchResult(
        title="Anker Q45 review",
        snippet="Great sound, and it ships in black.",
        url="https://audiosite.example/q45",
    ),
]


def opinions_after(*quotes: str, name: str = "Sony WH-CH720N") -> list[str]:
    """What survives grounding, for a product the sources do mention."""
    product = Product(name=name, opinions=list(quotes))
    return verify_opinions([product], OPINIONATED)[0].opinions


def test_a_quote_the_page_printed_survives() -> None:
    assert opinions_after("the noise cancelling uncanny for the money") == [
        "the noise cancelling uncanny for the money"
    ]


def test_punctuation_and_case_are_not_what_a_quote_is_checked_on() -> None:
    """The page's commas are not the shopper's business, and the model drops them."""
    assert opinions_after("Reviewers found, the NOISE cancelling uncanny!")


def test_an_invented_opinion_is_dropped() -> None:
    """The failure this exists for: a verdict nobody wrote, in quotation marks."""
    assert opinions_after("battery life is disappointing") == []


def test_a_quote_assembled_out_of_scattered_words_is_dropped() -> None:
    """Every word here is in the sources; the sentence is in none of them.

    This is why a quote is checked as runs of words rather than word by word: a
    small model paraphrasing out of the vocabulary it has just read would clear
    any bar that only asks whether the words occur.
    """
    assert opinions_after("the case is uncanny for a coat pocket") == []


def test_a_word_the_model_added_at_the_front_does_not_cost_the_quote() -> None:
    """Only the runs at that end break, and the rest still quote the page."""
    assert opinions_after("But reviewers found the noise cancelling uncanny for the money")


def test_a_word_changed_in_the_middle_of_a_quote_fails() -> None:
    """The middle is where a paraphrase happens, so nothing there is forgiven."""
    assert opinions_after("the noise cancelling is uncanny for the money") == []


def test_a_quote_shorter_than_one_run_has_to_appear_whole() -> None:
    assert opinions_after("too bulky") == ["too bulky"]
    assert opinions_after("too heavy") == []


def test_a_quote_of_nothing_is_not_a_quote() -> None:
    """``running_words`` empties a quote of punctuation alone, which grounds nothing."""
    assert opinions_after("!!!") == []


def test_the_real_quote_survives_the_invented_one_beside_it() -> None:
    """Per quote, not per product: one made-up verdict does not silence the page."""
    kept = opinions_after("battery life is disappointing", "too bulky")

    assert kept == ["too bulky"]


def test_a_verdict_on_another_product_does_not_transfer_to_this_one() -> None:
    """The point of checking page by page: "great sound" is the Anker's, not the Sony's.

    Every word of it is in the sources and it is running text of a real page, so
    one pooled haystack passed it -- which is a genuine reviewer's sentence filed
    under the wrong product, the failure ADR-0025 closes.
    """
    assert opinions_after("Great sound, and it ships in black") == []


def test_the_product_the_verdict_is_about_still_keeps_it() -> None:
    """The other half of the same rule: narrowing must not drop a real quote."""
    assert opinions_after("Great sound, and it ships in black", name="Anker Q45") == [
        "Great sound, and it ships in black"
    ]


def test_a_product_no_page_mentions_keeps_no_quotes() -> None:
    """No page to be quoted from is no quote, the way it is no link."""
    assert opinions_after("the noise cancelling uncanny for the money", name="Bose 700") == []


def test_grounding_a_product_grounds_the_opinions_it_arrived_with() -> None:
    """``ground`` is the one door the pipeline goes through, so it does all four."""
    product = Product(
        name="Sony WH-CH720N",
        opinions=["the noise cancelling uncanny for the money", "battery life is poor"],
    )

    grounded = ground([product], OPINIONATED)[0]

    assert grounded.opinions == ["the noise cancelling uncanny for the money"]
    assert grounded.url == "https://audiosite.example/ch720n"


def test_dropped_opinions_are_reported(caplog) -> None:
    product = Product(name="Sony WH-CH720N", opinions=["battery life is poor"])

    with caplog.at_level(logging.INFO, logger="buy_agent.verification"):
        verify_opinions([product], OPINIONATED)

    assert "Dropped 1 opinion" in caplog.text


def test_a_grouped_number_in_a_quote_matches_the_page_that_grouped_it() -> None:
    """Both sides of a quote comparison go through the same normalisation.

    The pages were normalised and the model's own words were not, so a quote of
    "over 1,299 owners" could never match a haystack in which the page's own
    "1,299" had already become "1299" -- three words against one.
    """
    results = [
        SearchResult(
            title="Sony WH-CH720N review",
            snippet="Reviewers found over 1,299 owners said the same thing.",
        )
    ]
    product = Product(name="Sony WH-CH720N", opinions=["found over 1,299 owners said"])

    assert verify_opinions([product], results)[0].opinions == [
        "found over 1,299 owners said"
    ]


def test_a_product_with_nothing_said_about_it_is_left_alone() -> None:
    product = Product(name="Sony WH-CH720N")

    assert verify_opinions([product], OPINIONATED) == [product]
