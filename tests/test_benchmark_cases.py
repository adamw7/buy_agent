"""Every case the benchmark runs, checked without a model: is each key what its pages say,
and is a query held to what its request asked? (ADR-0036, ADR-0070)"""

from __future__ import annotations

import dataclasses

import pytest

from buy_agent import agent as agent_module
from buy_agent.models import ProductList, SearchQuery
from buy_agent.search import SearchResult
from buy_agent.verification import (
    build_haystack,
    mentions_name,
    mentions_number,
    mentions_rating,
    mentions_review_count,
)
from benchmark import corpus
from benchmark.cases import CASES, ESPRESSO, HEADPHONES, LAPTOPS, SCRIPTS, Case, case_for
from benchmark.query import MAX_WORDS, NO_QUERY, QueryKey, QueryVerdict, judge_query
from benchmark.runner import run_benchmark, serving_the_corpus
from benchmark.scoring import METRICS, Scorecard
from benchmark.scripted import ScriptedLLM

#: Every case, for the rules that hold of each one.
EVERY_CASE = pytest.mark.parametrize("case", list(CASES.values()), ids=list(CASES))


def served(case: Case) -> tuple[SearchResult, ...]:
    """Every page of a case as the model is shown it: condensed."""
    with serving_the_corpus(case.pages, case.page_text) as pages:
        agent_module.enrich(agent_module.search_web(case.request, max_results=len(case.pages)))
        return tuple(pages)


def scored(case: Case, script: str) -> Scorecard:
    """A case's script, run through the whole pipeline and scored."""
    return run_benchmark(llm=case.scripted(script), case=case).scorecard


# -- the registry --------------------------------------------------------------


def test_the_cases_are_three_uses_of_the_agent_in_the_order_they_run() -> None:
    assert list(CASES) == ["headphones", "laptops", "espresso"]
    assert [CASES[name] for name in CASES] == [HEADPHONES, LAPTOPS, ESPRESSO]


def test_a_case_is_found_by_its_name_and_an_unknown_one_is_named_back() -> None:
    assert case_for("laptops") is LAPTOPS

    with pytest.raises(ValueError, match="'kettles'.*headphones, laptops, espresso"):
        case_for("kettles")


def test_the_headphones_case_is_the_corpus_the_nightly_scores() -> None:
    """One corpus, read by the nightly run and the comparison alike (ADR-0036)."""
    assert HEADPHONES.request == corpus.REQUEST
    assert HEADPHONES.pages is corpus.PAGES
    assert HEADPHONES.settings() == corpus.settings()


@EVERY_CASE
def test_a_case_runs_on_the_shipped_defaults_with_its_own_widths(case: Case) -> None:
    config = case.settings(model="tiny:1b")

    assert config.search_results == len(case.pages)
    assert config.num_products == case.num_products
    assert config.top_n == case.top_n
    assert config.cache_ttl == 0, "nothing is remembered between runs (ADR-0044)"
    assert config.model == "tiny:1b"


@EVERY_CASE
def test_every_case_carries_every_script(case: Case) -> None:
    """The page and ``--scripted`` offer each script for every case alike."""
    assert set(case.scripts) == set(SCRIPTS)


@EVERY_CASE
def test_a_scripted_case_answers_with_its_script_and_its_own_query(case: Case) -> None:
    model = case.scripted("perfect")

    assert isinstance(model, ScriptedLLM)
    assert model.answer([], SearchQuery) == SearchQuery(query=case.refined)
    assert model.answer([], ProductList) is case.scripts["perfect"]


# -- what a fingerprint says ---------------------------------------------------


@EVERY_CASE
def test_a_fingerprint_is_the_same_every_time_it_is_asked(case: Case) -> None:
    assert case.fingerprint == case.fingerprint
    assert len(case.fingerprint) == 16


def test_every_case_has_a_fingerprint_of_its_own() -> None:
    assert len({case.fingerprint for case in CASES.values()}) == len(CASES)


def test_a_fingerprint_moves_with_the_pages_the_key_and_the_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A kept result is thrown out once any of these change under it (ADR-0070)."""
    before = LAPTOPS.fingerprint
    reworded = dict(LAPTOPS.page_text)
    first = next(iter(reworded))
    reworded[first] += "One more line.\n"

    assert dataclasses.replace(LAPTOPS, page_text=reworded).fingerprint != before
    assert dataclasses.replace(LAPTOPS, key=LAPTOPS.key[:-1]).fingerprint != before
    assert dataclasses.replace(LAPTOPS, request="a laptop").fingerprint != before

    monkeypatch.setitem(METRICS, "novelty", (1.0, 0.0))
    assert LAPTOPS.fingerprint != before


# -- each key, read back off its pages -----------------------------------------


@EVERY_CASE
def test_every_case_is_about_more_products_than_a_run_can_report(case: Case) -> None:
    """The cap has to bite, or recall is measured against a ceiling nothing reaches."""
    assert len(case.key) > case.num_products >= case.top_n


@EVERY_CASE
def test_every_case_pages_and_their_text_are_the_same_set(case: Case) -> None:
    assert {page.url for page in case.pages} == set(case.page_text)


@EVERY_CASE
def test_every_answer_is_printed_in_its_case(case: Case) -> None:
    """What keeps each key a transcription rather than a wish."""
    pages = served(case)
    corpus_text = build_haystack(pages)
    by_url = {page.url: page for page in pages}

    for entry in case.key:
        assert mentions_name(corpus_text, entry.name), entry.name
        assert (entry.price, entry.currency) in entry.prices, entry.name
        assert (entry.rating, entry.review_count) in entry.ratings, entry.name
        for price, _currency in entry.prices:
            assert mentions_number(corpus_text, price), f"{entry.name}: {price}"
        for rating, count in entry.ratings:
            assert mentions_rating(corpus_text, rating), f"{entry.name}: {rating}"
            assert mentions_review_count(corpus_text, count), f"{entry.name}: {count}"
        for url in entry.pages:
            assert mentions_name(build_haystack([by_url[url]]), entry.name), url


@EVERY_CASE
def test_every_page_about_a_product_is_one_its_entry_lists(case: Case) -> None:
    """The other half: a page left off an entry is a right link scored as a wrong one,
    and a quote off it as one nobody printed."""
    pages = served(case)

    for entry in case.key:
        mentioning = {
            page.url for page in pages if mentions_name(build_haystack([page]), entry.name)
        }
        assert mentioning == entry.pages, entry.name


@EVERY_CASE
def test_the_perfect_answer_scores_full_marks_on_every_case(case: Case) -> None:
    """The reference, through the whole real pipeline."""
    card = scored(case, "perfect")

    assert card.score == pytest.approx(1.0)
    assert card.counts["identified"] == (case.num_products, case.num_products)
    assert (card.invented, card.repeated) == (0, 0)
    assert card.cleared


def test_the_sloppy_laptops_score_exactly_what_their_mistakes_cost() -> None:
    """A price off another laptop's line, a monthly payment and a dollar price called
    Canadian are three misattributed figures; the shop and the brandless repeat take two
    slots; the invented quote is the pipeline's to drop and the paraphrase is not."""
    card = scored(LAPTOPS, "sloppy")

    assert card.counts == {
        "identified": (3, 5),
        "genuine": (3, 5),
        "figures": (6, 9),
        "attribution": (6, 9),
        "links": (3, 3),
        "quotes": (1, 3),
        "faithful": (1, 2),
        "order": (2, 3),
    }
    assert (card.invented, card.repeated) == (1, 1)
    assert card.score == pytest.approx(0.6282051282051282)


def test_the_sloppy_espresso_scores_exactly_what_its_mistakes_cost() -> None:
    """A euro price called dollars and the cashback taken for a price are two wrong
    figures. "3.412 reviews" read as three is two more: the rating goes with its count
    (ADR-0022), and the count survives grounding, because the roundup's "reviews." is
    followed by a list's "3." -- a pooled-haystack match the key exists to catch."""
    card = scored(ESPRESSO, "sloppy")

    assert card.counts == {
        "identified": (3, 5),
        "genuine": (3, 5),
        "figures": (5, 9),
        "attribution": (5, 9),
        "links": (3, 3),
        "quotes": (1, 3),
        "faithful": (1, 2),
        "order": (0, 3),
    }
    assert (card.invented, card.repeated) == (1, 1)
    assert card.score == pytest.approx(0.5427350427350427)
    assert not card.cleared, "the cashback ranks the dearest machine first"


# -- the query -----------------------------------------------------------------


@EVERY_CASE
def test_every_scripted_query_keeps_what_its_request_asked(case: Case) -> None:
    """The scripts are the reference, so they search with a query that passes."""
    verdict = judge_query(case.refined, case.request, case.query)

    assert verdict.score == 1.0, [check.check for check in verdict.checks if not check.passed]


@EVERY_CASE
def test_the_request_itself_names_no_brand_a_query_is_held_to(case: Case) -> None:
    """A brand the shopper named is theirs to search for, so a key never lists one."""
    verdict = judge_query(case.request, case.request, case.query)

    assert all(check.passed for check in verdict.checks if "brand" in check.check)


KEY = QueryKey(
    keeps=(("espresso",), ("400",), ("euro", "euros", "€")),
    brands=("sage", "de longhi"),
)


def test_a_query_keeping_everything_and_adding_nothing_passes_every_check() -> None:
    verdict = judge_query("compact espresso machine under €400", "espresso, 400 euros", KEY)

    assert verdict.score == 1.0
    assert [check.check for check in verdict.checks] == [
        "Keeps 'espresso'",
        "Keeps '400'",
        "Keeps 'euro'",
        "Names no brand the shopper did not",
        "Adds no figure the shopper did not give",
        "Reads as one query (5 words)",
    ]


def test_a_sign_is_kept_as_written_and_a_word_only_as_a_word() -> None:
    """"€" has no words for :func:`running_words` to keep, and "euro" is not kept by
    "eurozone"."""
    assert judge_query("espresso 400 €", "espresso 400", KEY).checks[2].passed
    assert not judge_query("espresso 400 eurozone", "espresso 400", KEY).checks[2].passed


def test_a_figure_is_read_however_it_is_grouped() -> None:
    key = QueryKey(keeps=(("1500",),), brands=())

    assert judge_query("laptop under $1,500", "under $1500", key).score == 1.0
    assert judge_query("laptop under $1,500.00", "under $1500", key).score == 1.0


def test_a_constraint_dropped_is_named_as_dropped() -> None:
    verdict = judge_query("espresso machine euros", "espresso, 400 euros", KEY)

    assert verdict.checks[1].model_dump() == {"check": "Drops '400'", "passed": False}


def test_a_brand_the_shopper_never_named_is_a_narrowed_search() -> None:
    verdict = judge_query("De'Longhi espresso 400 euros", "espresso, 400 euros", KEY)

    assert verdict.checks[3].model_dump() == {
        "check": "Names a brand the shopper did not: de longhi",
        "passed": False,
    }


def test_a_brand_the_shopper_named_is_theirs() -> None:
    verdict = judge_query("sage espresso 400 euros", "a sage espresso under 400 euros", KEY)

    assert verdict.checks[3].passed


def test_a_figure_the_shopper_never_gave_is_an_added_constraint() -> None:
    """A year or a size narrows the search as a brand does; each is named once."""
    verdict = judge_query("espresso 400 euros 2026 15cm 2026", "espresso, 400 euros", KEY)

    assert verdict.checks[4].model_dump() == {
        "check": "Adds a figure the shopper did not give: 2026, 15cm",
        "passed": False,
    }


def test_an_explanation_is_not_a_query() -> None:
    chatty = "Sure! Here is a search query you could use: " + "espresso 400 euros " * 6

    verdict = judge_query(chatty, "espresso, 400 euros", KEY)
    words = len(chatty.split())

    assert words > MAX_WORDS
    assert verdict.checks[-1].model_dump() == {
        "check": f"Reads as an explanation, not a query ({words} words)",
        "passed": False,
    }


@pytest.mark.parametrize("query", [None, "", "   "])
def test_no_query_is_one_failed_check(query: str | None) -> None:
    """The agent searched with the request itself, so nothing else can be judged."""
    verdict = judge_query(query, "espresso, 400 euros", KEY)

    assert verdict.query is None
    assert [check.model_dump() for check in verdict.checks] == [
        {"check": NO_QUERY, "passed": False}
    ]
    assert verdict.score == 0.0


def test_a_verdict_with_no_checks_scores_nothing() -> None:
    """Not reachable through :func:`judge_query`, which always checks something -- but a
    kept verdict is read back from disk, where it could be anything."""
    assert QueryVerdict(query="espresso", checks=[]).score == 0.0
