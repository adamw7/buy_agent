"""Ranking is the part that decides the answer, so it gets the most tests."""

from __future__ import annotations

import pytest

from buy_agent.models import Product
from buy_agent.ranking import NEUTRAL, RankingWeights, rank_products, score_product


def product(name: str, **kwargs: object) -> Product:
    return Product(name=name, **kwargs)


def test_ranks_are_sequential_and_sorted_by_score() -> None:
    ranked = rank_products(
        [
            product("cheap and loved", price=50.0, rating=4.8, review_count=5000),
            product("dear and mediocre", price=900.0, rating=3.1, review_count=12),
        ]
    )
    assert [entry.rank for entry in ranked] == [1, 2]
    assert ranked[0].product.name == "cheap and loved"
    assert ranked[0].score > ranked[1].score


def test_higher_rating_wins_when_price_is_equal() -> None:
    ranked = rank_products(
        [
            product("meh", price=100.0, rating=3.0, review_count=100),
            product("great", price=100.0, rating=4.9, review_count=100),
        ]
    )
    assert ranked[0].product.name == "great"


def test_cheaper_wins_when_rating_is_equal() -> None:
    ranked = rank_products(
        [
            product("pricey", price=500.0, rating=4.5, review_count=100),
            product("bargain", price=100.0, rating=4.5, review_count=100),
        ]
    )
    assert ranked[0].product.name == "bargain"


def test_missing_data_scores_neutral_not_last() -> None:
    """A listing that published no price should beat one that is genuinely bad."""
    ranked = rank_products(
        [
            product("no data at all"),
            product("bad and expensive", price=1000.0, rating=1.0, review_count=3),
            product("good and cheap", price=10.0, rating=5.0, review_count=9000),
        ]
    )
    assert [entry.product.name for entry in ranked] == [
        "good and cheap",
        "no data at all",
        "bad and expensive",
    ]


def test_single_price_ties_on_the_price_criterion() -> None:
    score = score_product(
        product("only one", price=42.0),
        cheapest=42.0,
        priciest=42.0,
        weights=RankingWeights(),
    )
    assert score.total == pytest.approx(NEUTRAL)
    assert score.price == pytest.approx(NEUTRAL)
    # Nothing in the set separates it from anything else, which is an assumption
    # and not a reading -- so it is named as one (ADR-0041).
    assert "price" in score.neutral


def test_a_price_scale_narrower_than_a_pound_is_still_a_scale() -> None:
    """Two listings of one product differ by pennies, and the cheaper is still the
    cheaper -- so the criterion has to separate them rather than call the spread
    close enough to be no spread. Anything but zero is a scale."""
    cheapest, priciest = 129.0, 129.5
    weights = RankingWeights()

    assert (
        score_product(
            product("a", price=cheapest),
            cheapest=cheapest,
            priciest=priciest,
            weights=weights,
        ).total
        > score_product(
            product("b", price=priciest),
            cheapest=cheapest,
            priciest=priciest,
            weights=weights,
        ).total
    )


def test_scores_stay_within_zero_and_one() -> None:
    products = [
        product("floor", price=999.0, rating=0.0, review_count=1),
        product("ceiling", price=1.0, rating=5.0, review_count=1_000_000),
    ]
    for entry in rank_products(products):
        assert 0.0 <= entry.score <= 1.0


def test_weights_change_the_winner() -> None:
    products = [
        product("cheap, poorly rated", price=10.0, rating=2.0, review_count=100),
        product("costly, well rated", price=800.0, rating=5.0, review_count=100),
    ]
    price_first = RankingWeights(rating=0.0, popularity=0.0, price=1.0)
    rating_first = RankingWeights(rating=1.0, popularity=0.0, price=0.0)
    assert rank_products(products, weights=price_first)[0].product.name.startswith("cheap")
    assert rank_products(products, weights=rating_first)[0].product.name.startswith("costly")


def test_sort_by_price_puts_unpriced_products_last() -> None:
    ranked = rank_products(
        [
            product("unpriced", rating=5.0),
            product("dear", price=300.0),
            product("cheap", price=30.0),
        ],
        sort_by="price",
    )
    assert [entry.product.name for entry in ranked] == ["cheap", "dear", "unpriced"]


def test_sort_by_rating_puts_unrated_products_last() -> None:
    ranked = rank_products(
        [
            product("unrated", price=5.0),
            product("ok", rating=3.0),
            product("excellent", rating=4.9),
        ],
        sort_by="rating",
    )
    assert [entry.product.name for entry in ranked] == ["excellent", "ok", "unrated"]


def test_equal_scores_break_ties_by_name() -> None:
    ranked = rank_products([product("beta"), product("alpha")])
    assert [entry.product.name for entry in ranked] == ["alpha", "beta"]


def test_empty_input_gives_empty_ranking() -> None:
    assert rank_products([]) == []


def test_zero_weights_do_not_divide_by_zero() -> None:
    weights = RankingWeights(rating=0.0, popularity=0.0, price=0.0)
    ranked = rank_products([product("anything", price=10.0)], weights=weights)
    assert ranked[0].score == 0.0


def popularity_of(review_count: int | None) -> float:
    """The popularity term on its own, with the other criteria weighted out."""
    return score_product(
        product("x", review_count=review_count),
        cheapest=None,
        priciest=None,
        weights=RankingWeights(rating=0.0, popularity=1.0, price=0.0),
    ).total


def test_popularity_saturates_past_a_thousand_reviews() -> None:
    """Beyond a thousand, another thousand reviews says nothing new."""
    assert popularity_of(999) == pytest.approx(1.0)
    assert popularity_of(10_000) == pytest.approx(1.0)
    assert popularity_of(1_000_000) == pytest.approx(1.0)


def test_early_reviews_are_worth_more_than_late_ones() -> None:
    """Ten reviews say much more than nothing; ten thousand say no more than a thousand."""
    assert popularity_of(10) - popularity_of(1) > popularity_of(10_000) - popularity_of(1_000)


def test_more_reviews_never_score_worse() -> None:
    counts = [1, 10, 100, 999, 1_000, 50_000]
    scores = [popularity_of(count) for count in counts]

    assert scores == sorted(scores)


def test_no_reviews_scores_neutral_rather_than_zero() -> None:
    """An unreviewed listing is unknown, not unpopular."""
    assert popularity_of(None) == pytest.approx(NEUTRAL)
    assert popularity_of(0) == pytest.approx(NEUTRAL)


def test_a_zero_rating_is_the_floor_and_a_missing_one_is_not() -> None:
    """0.0 is a real, terrible score; None is an absent one. They must not tie."""
    rating_only = RankingWeights(rating=1.0, popularity=0.0, price=0.0)
    scored = score_product(
        product("awful", rating=0.0), cheapest=None, priciest=None, weights=rating_only
    )
    absent = score_product(
        product("unrated"), cheapest=None, priciest=None, weights=rating_only
    )

    assert scored.total == 0.0
    assert absent.total == pytest.approx(NEUTRAL)
    # The pair this whole scheme exists to keep apart: 0.0 and NEUTRAL are both
    # shares of the rating criterion, and only one of them was read off a page.
    assert "rating" not in scored.neutral
    assert "rating" in absent.neutral


def price_score(value: float, *, cheapest: float | None, priciest: float | None) -> float:
    """The price term on its own."""
    return score_product(
        product("x", price=value),
        cheapest=cheapest,
        priciest=priciest,
        weights=RankingWeights(rating=0.0, popularity=0.0, price=1.0),
    ).total


def test_the_cheapest_and_dearest_candidates_anchor_the_price_scale() -> None:
    assert price_score(100.0, cheapest=100.0, priciest=200.0) == pytest.approx(1.0)
    assert price_score(200.0, cheapest=100.0, priciest=200.0) == pytest.approx(0.0)
    assert price_score(150.0, cheapest=100.0, priciest=200.0) == pytest.approx(0.5)


def test_price_scores_neutral_when_nothing_in_the_set_has_a_price() -> None:
    assert price_score(10.0, cheapest=None, priciest=None) == pytest.approx(NEUTRAL)


def test_an_unpriced_product_among_priced_ones_scores_neutral_on_price() -> None:
    scored = score_product(
        product("unpriced"),
        cheapest=10.0,
        priciest=900.0,
        weights=RankingWeights(rating=0.0, popularity=0.0, price=1.0),
    )

    assert scored.total == pytest.approx(NEUTRAL)
    assert "price" in scored.neutral


def test_ranking_does_not_reorder_the_caller_s_list() -> None:
    products = [product("zeta", rating=1.0), product("alpha", rating=5.0)]

    rank_products(products)

    assert [entry.name for entry in products] == ["zeta", "alpha"]


def test_ranking_keeps_every_product_it_was_given() -> None:
    """rank_products orders; dropping is verification's job, done earlier."""
    products = [product(f"product {index}") for index in range(7)]

    assert len(rank_products(products)) == 7


def test_sorting_by_price_still_reports_the_blended_score() -> None:
    """--sort-by price changes the order, not what a score means."""
    ranked = rank_products(
        [
            product("dear but excellent", price=300.0, rating=5.0),
            product("cheap but poor", price=30.0, rating=1.0),
        ],
        sort_by="price",
    )

    assert [entry.rank for entry in ranked] == [1, 2]
    assert ranked[0].product.name == "cheap but poor"
    assert ranked[1].score > ranked[0].score, "the score is unaffected by the sort order"


def test_a_single_product_ranks_first() -> None:
    ranked = rank_products([product("alone", price=10.0, rating=4.0)])

    assert ranked[0].rank == 1
    assert 0.0 <= ranked[0].score <= 1.0


def test_the_name_tiebreak_ignores_case() -> None:
    """Otherwise every capitalised name sorts ahead of every lowercase one."""
    ranked = rank_products([Product(name="Zebra"), Product(name="apple")])

    assert [entry.product.name for entry in ranked] == ["apple", "Zebra"]


def test_a_product_with_no_data_scores_exactly_neutral() -> None:
    """Mid-field, not last: a listing that published nothing is not thereby bad."""
    score = score_product(
        Product(name="Unknown Brand Buds"),
        cheapest=None,
        priciest=None,
        weights=RankingWeights(),
    )

    assert score.total == 0.5 == NEUTRAL
    assert score.neutral == ["rating", "popularity", "price"]


def test_the_default_weights_favour_rating_then_price_then_popularity() -> None:
    """The blend is the product's opinion about what makes a good buy."""
    weights = RankingWeights()

    assert (weights.rating, weights.popularity, weights.price) == (0.5, 0.2, 0.3)


def test_scores_are_normalised_by_the_total_weight() -> None:
    """Weights that do not sum to 1 must not push scores outside [0, 1]."""
    weights = RankingWeights(rating=2.0, popularity=1.0, price=1.0)
    best = score_product(
        Product(name="Best", rating=5.0, review_count=10_000, price=10.0),
        cheapest=10.0,
        priciest=100.0,
        weights=weights,
    )

    assert best.total == pytest.approx(1.0)


# -- one scale, one currency (ADR-0043) ----------------------------------------

USD = Product(name="Cheap", price=100.0, currency="USD")
DEARER = Product(name="Dear", price=200.0, currency="USD")
YEN = Product(name="Elsewhere", price=12_800.0, currency="JPY")
EURO = Product(name="Elsewhere", price=90.0, currency="EUR")


@pytest.mark.parametrize(
    ("products", "expected"),
    [
        # The bug this closes: price scores relative to the set, so one yen figure
        # used to put every dollar price at the cheap end of a range five orders
        # of magnitude wide.
        pytest.param([USD, DEARER, YEN], {"Cheap": 1.0, "Dear": 0.0, "Elsewhere": NEUTRAL},
                     id="another currency does not stretch the scale"),
        # A bare price is the set's own -- which is what every price here was, and
        # what a search in one region overwhelmingly returns.
        pytest.param([USD, Product(name="Bare", price=200.0)], {"Cheap": 1.0, "Bare": 0.0},
                     id="a price with no currency"),
        pytest.param([Product(name="Cheap", price=100.0), Product(name="Dear", price=200.0)],
                     {"Cheap": 1.0, "Dear": 0.0}, id="a set that named no currency at all"),
    ],
)
def test_price_scores_on_the_scale_the_set_is_counted_in(
    products: list[Product], expected: dict[str, float]
) -> None:
    """Prices are compared inside one currency and converted never, so what is
    outside it scores ``NEUTRAL`` rather than the number it happens to be."""
    ranked = rank_products(products)

    assert {e.product.name: e.breakdown.price for e in ranked} == expected


@pytest.mark.parametrize(
    "products",
    [
        pytest.param([USD, DEARER, EURO], id="the odd currency out"),
        # A majority of one listing is still the majority: what is being asked is
        # which scale the most of these figures are on.
        pytest.param([EURO, USD, DEARER], id="the commonest wins, whoever came first"),
    ],
)
def test_a_price_the_set_cannot_place_says_so(products: list[Product]) -> None:
    """``NEUTRAL`` is what an unread figure scores too, and the two are one number
    until something names the difference (ADR-0041)."""
    assumed = {e.product.name: e.breakdown.neutral for e in rank_products(products)}

    assert "price" in assumed["Elsewhere"]
    assert "price" not in assumed["Cheap"]


def test_sorting_by_price_sinks_a_price_the_set_cannot_place() -> None:
    """It sinks with the prices nobody published, for the reason those do:
    ordering by a figure means ordering by one that means the same thing all the
    way down the column."""
    ranked = rank_products([YEN, Product(name="Unpriced"), DEARER, USD], sort_by="price")
    names = [entry.product.name for entry in ranked]

    assert names[:2] == ["Cheap", "Dear"]
    assert set(names[2:]) == {"Elsewhere", "Unpriced"}


def test_one_product_on_its_own_is_not_asked_about_a_set() -> None:
    """``score_product`` takes the currency as "nothing says otherwise", so a
    caller scoring one product against two figures need not have a set."""
    score = score_product(
        Product(name="Alone", price=50.0, currency="EUR"),
        cheapest=10.0,
        priciest=100.0,
        weights=RankingWeights(),
    )

    assert score.price == pytest.approx(0.5555555555555556)
    assert "price" not in score.neutral
