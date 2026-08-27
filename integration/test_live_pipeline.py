"""One real run, read for the promises the pipeline makes whatever the model says.

Almost nothing here asserts that the model got the *right* answer. A 0.6B model
is not held to that, and a nightly job that failed because it wrote "Sony
WH-1000XM5 Wireless" where the fixture says "Sony WH-1000XM5" would be a test
about the model rather than about this code.

What is asserted instead is the set of properties that must hold no matter what
came back: every name is in the sources, every figure is in the sources, every
quote was printed on a page about that product, every link is a page that was
searched, and nothing is listed twice. Those are the guarantees the unit suite
checks against a ``FakeLLM`` that answers exactly what it was told to -- which
is the one thing a real model never does. This is where they meet an answer
nobody wrote.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from buy_agent.extraction import looks_like_a_product, merge_variants
from buy_agent.verification import (
    build_haystack,
    mentions_name,
    mentions_number,
    mentions_rating,
    quotes_sources,
    running_words,
    source_urls,
)

if TYPE_CHECKING:
    from buy_agent.models import Product

    from integration.conftest import LiveRun


@pytest.fixture(scope="session")
def products(live_run: LiveRun) -> list[Product]:
    return [entry.product for entry in live_run.ranked]


@pytest.fixture(scope="session")
def haystack(live_run: LiveRun) -> str:
    return build_haystack(live_run.pages)


def test_the_model_reads_products_out_of_the_pages(live_run: LiveRun) -> None:
    """The smoke test the rest of this file qualifies.

    Everything else here passes vacuously on an empty answer, so this is the one
    assertion that fails when the tiny model stops being able to fill in the
    schema at all -- a model update, an Ollama release that changes how
    ``json_schema`` decoding works, or a prompt grown past what it can follow.
    """
    assert live_run.extracted.products, "the model extracted nothing at all"
    assert live_run.ranked, "everything the model extracted was thrown out"


def test_the_model_quotes_the_pages_rather_than_only_pricing_them(
    live_run: LiveRun,
) -> None:
    """The same smoke test, for the field ADR-0024 and ADR-0025 are about.

    ``test_every_quote_was_printed_on_a_page_about_that_product`` passes
    vacuously on a run that quoted nothing, exactly as the assertions above
    would on a run that extracted nothing -- and a model that quotes nothing is
    the likeliest way for the whole opinion path to go green having checked
    none of itself.

    Asserted on what the *model* returned, not on what survived grounding. A
    0.6B model paraphrases, ``verify_opinions`` drops paraphrases, and holding
    the nightly to a quote surviving that would be holding it to the model
    being right -- which is the one thing this file refuses to do. What is
    asserted here is only that the schema and the prompt still get quotes out
    of it at all.
    """
    quoted = [item.name for item in live_run.extracted.products if item.opinions]

    assert quoted, "the model reported no opinions for any product"


def test_every_product_reported_is_named_in_the_sources(products, haystack) -> None:
    """ADR-0006: a name absent from every page cannot have been read off one."""
    for product in products:
        assert mentions_name(haystack, product.name), product.name


def test_no_page_is_reported_as_a_product(products) -> None:
    """One of the three pages is a listicle, which is the mistake ``clean_products``
    exists for: a small model reports "9 Best Noise Cancelling Headphones Under
    $400" as something you can buy."""
    for product in products:
        assert looks_like_a_product(product.name), product.name


def test_every_figure_reported_is_printed_in_the_sources(products, haystack) -> None:
    """A blank is fine -- it scores neutral. A number nobody printed is not."""
    for product in products:
        if product.price is not None:
            assert mentions_number(haystack, product.price), f"{product.name}: price"
        if product.rating is not None:
            assert mentions_rating(haystack, product.rating), f"{product.name}: rating"
        if product.review_count is not None:
            assert mentions_number(haystack, product.review_count), f"{product.name}: reviews"


def test_a_currency_never_outlives_the_price_it_qualifies(products) -> None:
    """ADR-0022, one stage earlier: a figure the sources do not back takes its
    qualifiers down with it, so neither can be left describing nothing."""
    for product in products:
        assert product.currency is None or product.price is not None, product.name
        assert product.review_count is None or product.rating is not None, product.name


def test_every_link_is_a_page_that_was_searched(products, live_run: LiveRun) -> None:
    """ADR-0017. A blanked price shows as "price unknown"; an invented link is one
    the shopper clicks."""
    known = source_urls(live_run.pages)
    for product in products:
        assert product.url is None or product.url in known, product.url


def test_every_quote_was_printed_on_a_page_about_that_product(products, live_run) -> None:
    """ADR-0025. Checked the way ``verify_opinions`` checks it -- as running text,
    on a page that names the product -- because a paraphrase is words put in a
    reviewer's mouth, and paraphrasing is what a small model does when it quotes."""
    pages = [
        (text, running_words(text))
        for text in (build_haystack([page]) for page in live_run.pages)
    ]
    for product in products:
        mine = [words for text, words in pages if mentions_name(text, product.name)]
        for opinion in product.opinions:
            assert any(quotes_sources(words, opinion) for words in mine), opinion


def test_no_product_is_reported_twice(products) -> None:
    """Eight of the ten pages price the Sony: without ``deduplicate`` one product
    takes every slot it is listed in.

    Checked by the rule ``deduplicate`` actually merges on and not by
    ``dedup_key``, which is strictly weaker -- it folds case, punctuation and
    spacing, so "Sony WH-1000XM5" and "Sony WH-1000XM5 Wireless" are two keys
    and one product. Dropping ``deduplicate`` from the pipeline left a
    ``dedup_key`` check entirely green, which is the whole failure it is here
    to see. Re-running the merge is the property instead: its output is
    supposed to be a fixed point, so a second pass must find nothing left to
    fold.

    If this ever fails on names that look correctly merged, suspect the fixed
    point rather than the run. ``merge_variants`` folds into the first match
    and ``_combine`` then shortens the name, so three spellings arriving in the
    wrong order -- "... Wireless", "... Black", then the bare name -- can leave
    a pair a second pass would still merge. That is worth fixing in
    ``deduplicate``; it is not worth weakening this back into a key comparison.
    """
    keys = [product.dedup_key for product in products]

    assert len(keys) == len(set(keys)), keys
    assert len(merge_variants(products)) == len(products), [p.name for p in products]


def test_the_ranking_is_ordered_and_numbered(live_run: LiveRun) -> None:
    """Ranks run 1..n with no gaps, and the scores never climb."""
    scores = [entry.score for entry in live_run.ranked]

    assert [entry.rank for entry in live_run.ranked] == list(range(1, len(scores) + 1))
    assert scores == sorted(scores, reverse=True)
    assert all(0.0 <= score <= 1.0 for score in scores)


def test_no_more_products_are_reported_than_were_asked_for(live_run, live_config) -> None:
    """``deduplicate``'s limit. The pages name seven distinct products against a
    ``num_products`` of five, so this is a cap that has to bite -- with a limit
    above what the pages can yield it passed however the pipeline behaved."""
    assert len(live_run.ranked) <= live_config.num_products
