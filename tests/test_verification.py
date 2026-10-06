"""Numbers the search results never mentioned must not survive into the ranking."""

from __future__ import annotations

import logging

import pytest

from buy_agent.models import Opinion, Product, Removal
from buy_agent.search import SearchResult
from buy_agent.verification import (
    attribute_sources,
    build_haystack,
    drop_ungrounded,
    ground,
    mentions_name,
    mentions_number,
    mentions_rating,
    mentions_review_count,
    verify_numbers,
    verify_opinions,
)
from tests.conftest import said

SOURCES = [
    SearchResult(
        title="Sony WH-CH720N deal",
        snippet="Now $129, rated 4.3 out of 5 from 12,500 shoppers.",
    ),
]
HAYSTACK = build_haystack(SOURCES)


def test_the_products_that_lost_a_figure_are_counted_once_each(caplog) -> None:
    """The other three grounding steps say what they took; this one says it too."""
    products = [
        Product(name="Sony WH-CH720N", price=99.0, rating=4.9),
        Product(name="Sony WH-CH720N", price=129.0, rating=4.3),
        Product(name="Sony WH-CH720N", review_count=90000),
    ]

    with caplog.at_level(logging.INFO, logger="buy_agent.verification"):
        verify_numbers(products, HAYSTACK)

    assert "Dropped unsupported figures on 2 product(s)" in caplog.text


def test_which_figures_went_is_said_for_the_reader_who_asked_for_detail(caplog) -> None:
    """The count is the headline and this is the detail behind it, which is the
    only place a run says *what* it disbelieved rather than how much."""
    product = Product(name="Sony WH-CH720N", price=99.0, rating=4.9, review_count=12500)

    with caplog.at_level(logging.DEBUG, logger="buy_agent.verification"):
        verify_numbers([product], HAYSTACK)

    assert "Unsupported currency/price/rating/review_count for 'Sony WH-CH720N'" in caplog.text


def test_a_no_break_space_groups_a_count_too() -> None:
    """A no-break space between digits is typesetting's thousands separator and nothing
    else, so it groups without a currency beside it."""
    haystack = build_haystack([SearchResult(snippet="4,6/5 z 12\u00a0500 opinii")])

    assert mentions_number(haystack, 12500)


@pytest.mark.parametrize(
    ("snippet", "rating"),
    [
        ("Rated 4.0 out of 5", 4.0),
        ("Scores 4.0/5 overall", 4.0),
        ("A solid 4.0 stars", 4.0),
        ("Rating: 4.0", 4.0),
        ("Rated 5.0 out of 5", 5.0),
        ("Scores 4.50/5", 4.5),
    ],
)
def test_a_whole_rating_is_supported_by_the_zero_the_page_printed(snippet, rating) -> None:
    """A page writing "4.0" states the 4 that was claimed, precisely."""
    assert mentions_rating(build_haystack([SearchResult(snippet=snippet)]), rating)


def test_a_decimal_price_matches_a_trailing_zero() -> None:
    """A page writes 10000.5 as "$10,000.50"."""
    sources = [SearchResult(snippet="Yours for $10,000.50 today")]

    assert mentions_number(build_haystack(sources), 10000.50)
    assert not mentions_number(build_haystack(sources), 10000.55)


def test_a_name_of_only_generic_words_is_not_grounded() -> None:
    assert not mentions_name(HAYSTACK, "Wireless Headphones")


def test_a_name_that_ends_one_page_is_not_run_into_the_next() -> None:
    """The pages are pooled into one haystack, and the last word of one is still a
    word of its own rather than the front half of the next page's first."""
    haystack = build_haystack(
        [
            # On the content, which is the end of a result as it is pooled.
            SearchResult(title="Best budget ANC", content="Our pick is the Anker Q30"),
            SearchResult(title="Another page entirely", content="Nothing to see."),
        ]
    )

    assert mentions_name(haystack, "Anker Q30")


def test_grounding_reports_what_it_dropped(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="buy_agent.verification"):
        ground([Product(name="Bonavita Gooseneck Kettle", price=80.0)], SOURCES)

    assert "Dropped 1 product(s) absent from the search results" in caplog.text


def test_the_products_grounding_dropped_are_named_for_the_reader_who_asked(caplog) -> None:
    """``mentions_name`` decides whether a product is real at all, so a real one it
    happens to fail is the run's worst miss -- and the count alone leaves nothing to
    find it by."""
    with caplog.at_level(logging.DEBUG, logger="buy_agent.verification"):
        ground([Product(name="Bonavita Gooseneck Kettle", price=80.0)], SOURCES)

    assert "Absent from the search results: 'Bonavita Gooseneck Kettle'" in caplog.text


def test_nothing_dropped_says_nothing_at_either_level(caplog) -> None:
    with caplog.at_level(logging.DEBUG, logger="buy_agent.verification"):
        ground([Product(name="Sony WH-CH720N")], SOURCES)

    assert "Absent from the search results" not in caplog.text


def test_a_rating_is_recognised_whatever_its_case() -> None:
    """Shop pages shout their figures in headings, with no lead-in word to lean on."""
    assert mentions_rating(build_haystack([SearchResult(snippet="A SOLID 4.6 STARS")]), 4.6)
    assert mentions_rating(build_haystack([SearchResult(snippet="4.6 OUT OF 5")]), 4.6)


def test_the_coverage_bar_is_three_distinctive_words_in_five() -> None:
    """Where the 0.6 floor falls decides whether an invented name can pass."""
    haystack = build_haystack([SearchResult(snippet="The Anker Soundcore Life sits at $99.")])

    assert mentions_name(haystack, "Anker Soundcore Life Q30 Pro"), "3 of 5 clears the bar"
    assert not mentions_name(haystack, "Anker Soundcore Boost Max Pro"), "2 of 5 does not"


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


def test_a_searched_link_is_kept_over_an_earlier_page_that_also_mentions_it() -> None:
    """The model's link is a choice among the searched pages, not a guess to be
    replaced by the first one that happens to name the product: a roundup ahead of
    the shop in the results mentions it too, and the shop is where the model was
    pointing (ADR-0017)."""
    roundup = SearchResult(
        title="The best cheap headphones",
        url="https://roundup.example/cheap",
        snippet="Our pick is the Sony WH-CH720N.",
    )
    product = Product(name="Sony WH-CH720N", url="https://shop.example/sony")

    linked = attribute_sources([product], [roundup, *PAGES])[0]

    assert linked.url == "https://shop.example/sony"


def test_every_replaced_link_is_counted(caplog) -> None:
    """The count is what the INFO line is for, so one link is not enough to show it
    is one."""
    products = [
        Product(name="Sony WH-CH720N", url="https://invented.example/a"),
        Product(name="Anker Soundcore Q30", url="https://invented.example/b"),
    ]

    with caplog.at_level(logging.INFO, logger="buy_agent.verification"):
        attribute_sources(products, PAGES)

    assert "Dropped 2 link(s)" in caplog.text


def test_the_link_that_was_replaced_is_named_for_the_reader_who_asked(caplog) -> None:
    """A link is the field the model is worst at and the one the shopper clicks
    (ADR-0017), so which page it invented is worth being able to see."""
    product = Product(name="Sony WH-CH720N", url="https://invented.example")

    with caplog.at_level(logging.DEBUG, logger="buy_agent.verification"):
        attribute_sources([product], PAGES)

    assert "Never searched: 'https://invented.example' for 'Sony WH-CH720N'" in caplog.text


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


def quoted_from(*quotes: str, name: str = "Sony WH-CH720N") -> list[Opinion]:
    """What survives grounding, for a product the sources do mention: the words
    and the page that printed them."""
    product = Product(name=name, opinions=said(*quotes))
    return verify_opinions([product], OPINIONATED)[0].opinions


def opinions_after(*quotes: str, name: str = "Sony WH-CH720N") -> list[str]:
    """The same, as the words alone -- which is what most of these are about."""
    return [opinion.text for opinion in quoted_from(*quotes, name=name)]


#: Nine words the page never printed together: the first seven are on it word for
#: word, the last two are the model's own.
_NINE_WORDS = "reviewers found the noise cancelling uncanny for its price"
#: ...and eight, of which only the first six are.
_EIGHT_WORDS = "reviewers found the noise cancelling uncanny in practice"


def test_a_quote_with_exactly_the_share_of_its_runs_found_is_kept() -> None:
    """Five runs of five words, three of them on the page: the bar is at least
    ``_QUOTE_COVERAGE``, and a quote sitting on it is one the page printed with its
    tail reworded -- which is the end the tolerance is for."""
    assert opinions_after(_NINE_WORDS) == [_NINE_WORDS]


def test_a_quote_whose_second_half_is_invented_is_dropped() -> None:
    """Four runs, two found: half a sentence off the page is still a sentence
    nobody wrote, whatever it opens with."""
    assert opinions_after(_EIGHT_WORDS) == []


def test_a_quote_of_nothing_is_not_a_quote() -> None:
    """``running_words`` empties a quote of punctuation alone, which grounds nothing."""
    assert opinions_after("!!!") == []


def test_the_opinions_dropped_are_counted_across_products_and_not_the_kept_ones(
    caplog,
) -> None:
    """Two products, one invented quote each and a real one beside the first: two
    dropped, which is neither the last product's count nor every quote there was."""
    products = [
        Product(name="Sony WH-CH720N", opinions=said("battery life is poor", "too bulky")),
        Product(name="Anker Q45", opinions=said("the bass is muddy")),
    ]

    with caplog.at_level(logging.INFO, logger="buy_agent.verification"):
        verify_opinions(products, OPINIONATED)

    assert "Dropped 2 opinion(s)" in caplog.text


# -- and the page it came off (ADR-0042) ---------------------------------------


def test_a_quote_is_never_linked_to_a_page_about_another_product() -> None:
    """The link follows the check, and the check is page by page (ADR-0025): the
    Anker's page cannot be cited for the Sony even though it printed the words."""
    kept = quoted_from("Great sound, and it ships in black", name="Anker Q45")

    assert kept[0].url == "https://audiosite.example/q45"


@pytest.mark.parametrize(
    "snippet",
    [
        "Rated 4.3 out of 5 from 12,500 shoppers",
        "4.6 out of 5 from 12500 reviews",
        "(12,500 global ratings)",
        "12,500 verified customer reviews",
        "Reviews (12,500)",
        "12,500 reviewers agreed",
        # A shop's heading capitalises the noun, on either side of the figure.
        "12,500 Ratings",
        "Customer Reviews: 12,500",
    ],
)
def test_review_counts_are_recognised_however_they_are_written(snippet: str) -> None:
    assert mentions_review_count(build_haystack([SearchResult(snippet=snippet)]), 12500)


# -- what grounding says it took out (ADR-0055) --------------------------------


def test_a_product_no_page_mentions_is_removed_and_says_so() -> None:
    """The panel's sentence, pinned beside the step that writes it."""
    removed: list[Removal] = []
    drop_ungrounded(
        [Product(name="Bonavita Gooseneck Kettle")], "", record=removed.append
    )

    assert [entry.name for entry in removed] == ["Bonavita Gooseneck Kettle"]
    assert removed[0].step == "ground"
    assert removed[0].reason == "No page that was searched mentions it."
