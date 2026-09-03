"""The benchmark, checked without a model: is the score it reports the right one?

A benchmark decides an answer, so by this project's own rule it belongs where it
is testable rather than in a nightly job's output. The two scripted runs in
:mod:`benchmark.scripted` are how: both go through the *real* pipeline, with only
the model replaced, so what is pinned here is the whole thing end to end and not
the arithmetic in :mod:`benchmark.scoring` alone.

``PERFECT`` scoring 1.000 is the load-bearing one. It says the answer key is
*reachable*: a figure the fetch layer condenses away, a quote grounding refuses,
a name ``clean_products`` rewrites would each show up here as a reference run
that cannot reach full marks -- rather than as a silent ceiling under every
number the nightly ever reports, which nothing would notice. ``SLOPPY`` is the
other end, scored to the exact counts each of its seven mistakes should produce,
which is what turns "the scorer returned 0.726" into "it noticed the Bose's price
on the Sony, the shop reported as a product, and the paraphrase".
"""

from __future__ import annotations

import json
import logging

import pytest

from buy_agent import agent as agent_module
from buy_agent.models import Product
from buy_agent.search import SearchResult
from buy_agent.verification import (
    build_haystack,
    mentions_name,
    mentions_number,
    mentions_rating,
    mentions_review_count,
)
from benchmark import __main__ as benchmark_main
from benchmark.answers import ANSWER_KEY, Expected
from benchmark.corpus import NUM_PRODUCTS, PAGES, PAGE_TEXT, REQUEST, TOP_N, settings
from benchmark.runner import run_benchmark, serving_the_corpus
from benchmark.scoring import (
    FLOORS,
    METRICS,
    Scorecard,
    best_match,
    figure_verdicts,
    identifies,
    page_words,
    score_run,
)
from benchmark.scripted import PERFECT, SCRIPTS, SLOPPY, ScriptedLLM

SONY, BOSE, SENNHEISER, AIRPODS, ANKER, LIFE, JLAB = ANSWER_KEY


@pytest.fixture(autouse=True)
def restore_agent_log_level():
    """``python -m benchmark`` quiets the agent's own logger, which is global.

    Put back, or later tests run against a ``buy_agent`` logger somebody else
    silenced -- and a ``caplog`` assertion failing three files on says nothing
    about where."""
    agent_log = logging.getLogger("buy_agent")
    level = agent_log.level
    yield
    agent_log.setLevel(level)


@pytest.fixture(scope="module")
def perfect() -> Scorecard:
    """The reference run: the answer key copied out, through the whole pipeline."""
    return run_benchmark(llm=ScriptedLLM(PERFECT)).scorecard


@pytest.fixture(scope="module")
def sloppy() -> Scorecard:
    """The same run, wrong in the seven ways :mod:`benchmark.scripted` lists."""
    return run_benchmark(llm=ScriptedLLM(SLOPPY)).scorecard


@pytest.fixture(scope="module")
def served() -> tuple[SearchResult, ...]:
    """Every page as the model is shown it: condensed, which is the only text the
    answer key is allowed to be about. The raw fixture is mostly navigation and
    legal boilerplate the fetch layer throws away."""
    with serving_the_corpus() as pages:
        agent_module.enrich(agent_module.search_web(REQUEST, max_results=len(PAGES)))
        return tuple(pages)


@pytest.fixture(scope="module")
def corpus(served: tuple[SearchResult, ...]) -> str:
    """The pooled haystack ``verify_numbers`` grounds against."""
    return build_haystack(served)


# -- the answer key ------------------------------------------------------------


def test_the_corpus_is_about_more_products_than_a_run_can_report() -> None:
    """The cap has to bite, or recall is measured against a ceiling nothing
    reaches and ``deduplicate``'s limit is never exercised at all."""
    assert len(ANSWER_KEY) > NUM_PRODUCTS >= TOP_N


def test_the_corpus_pages_and_their_text_are_the_same_ten() -> None:
    """A page with no text is fetched as empty and contributes nothing; text with
    no page is a fixture nothing reads."""
    assert {page.url for page in PAGES} == set(PAGE_TEXT)
    assert len(PAGES) == 10


@pytest.mark.parametrize("entry", ANSWER_KEY, ids=lambda entry: entry.name)
def test_every_answer_is_printed_in_the_corpus(
    entry: Expected, corpus: str, served: tuple[SearchResult, ...]
) -> None:
    """The test that keeps :mod:`benchmark.answers` a transcription rather than a
    wish. Every name, every figure canonical and alternative alike, and every
    page listed for a product -- read off the *condensed* corpus and checked the
    way grounding checks them, because a line the fetch layer throws away is a
    figure no run can ever be credited for and a page that does not mention the
    product is a link the pipeline would never have made.

    The canonical figures are checked to be among the printed ones as well:
    ``as_product`` says USD, so a canonical price read off the euro listing would
    rank the run against a pairing no page printed.
    """
    assert mentions_name(corpus, entry.name)
    assert (entry.price, "USD") in entry.prices
    assert (entry.rating, entry.review_count) in entry.ratings
    for price, _currency in entry.prices:
        assert mentions_number(corpus, price), f"{entry.name}: {price}"
    for rating, count in entry.ratings:
        assert mentions_rating(corpus, rating), f"{entry.name}: {rating}"
        assert mentions_review_count(corpus, count), f"{entry.name}: {count}"
    by_url = {page.url: page for page in served}
    for url in entry.pages:
        assert mentions_name(build_haystack([by_url[url]]), entry.name), url


# -- matching a reported name to the key ---------------------------------------


def test_a_name_missing_a_descriptive_word_is_the_same_product() -> None:
    """What a model does: copies the name off the page, with or without the words
    that describe rather than identify it."""
    assert best_match("Sony WH-1000XM5 Wireless Headphones") is SONY
    assert best_match("WH-1000XM5") is SONY


def test_a_brand_on_its_own_identifies_nothing() -> None:
    """Why :func:`identifies` looks both ways. Forwards alone, every distinctive
    word of "Sony" is in "Sony WH-1000XM5" and a model would score full marks for
    naming brands."""
    assert identifies("Sony", SONY) == 0.0
    assert best_match("Sony") is None


def test_two_products_sharing_a_word_are_told_apart() -> None:
    """"Soundcore" is in both of these, and they are $99 and $59."""
    assert best_match("Soundcore Space Q45") is ANKER
    assert best_match("Soundcore Life Q30") is LIFE


def test_the_publishers_name_is_not_a_product() -> None:
    """The mistake ``clean_products`` cannot catch: a shop is not a headline, and
    every word of its name is in the sources."""
    assert best_match("AudioSite") is None


def test_an_ambiguous_name_goes_to_its_best_match_and_stays_there() -> None:
    """Ties break on the key's order, so the same answer scores the same twice."""
    twin = Expected(
        name="Anker Space Q45",
        price=99.0,
        rating=4.4,
        review_count=31_200,
        prices=frozenset({(99.0, "USD")}),
        ratings=frozenset({(4.4, 31_200)}),
        pages=frozenset(),
    )

    assert best_match("Anker Space Q45", (twin, ANKER)) is twin
    assert best_match("Anker Space Q45", (ANKER, twin)) is twin


# -- the figures ---------------------------------------------------------------


def test_a_figure_the_pages_print_for_this_product_is_right() -> None:
    right = Product(name="Sony WH-1000XM5", price=328.0, rating=4.7, review_count=12_480)

    assert figure_verdicts(right, SONY) == [True, True, True]


def test_a_price_off_another_products_line_is_wrong() -> None:
    """The whole reason for a per-product key. 349 is in the corpus -- it is what
    the Bose costs -- so ``verify_numbers``, grounding against the pooled pages,
    keeps it on the Sony without a murmur."""
    assert figure_verdicts(Product(name="Sony WH-1000XM5", price=349.0), SONY)[0] is False


def test_a_qualifier_is_judged_with_the_figure_it_qualifies() -> None:
    """ADR-0022 as a score: the corpus prints 329 and it prints EUR, and never the
    two together, so that is one wrong price rather than two right halves -- and
    a rating paired with somebody else's review count takes the count with it."""
    assert figure_verdicts(Product(name="Sony", price=329.0), SONY)[0] is True
    assert figure_verdicts(Product(name="Sony", price=329.0, currency="EUR"), SONY)[0] is True
    assert figure_verdicts(Product(name="Sony", price=329.0, currency="USD"), SONY)[0] is False

    crossed = Product(name="Bose", rating=4.3, review_count=12_480)

    assert figure_verdicts(Product(name="Bose", rating=4.3, review_count=5_600), BOSE)[1:] == [
        True,
        True,
    ]
    assert figure_verdicts(crossed, BOSE)[1:] == [False, False]


def test_a_blank_figure_is_a_miss_and_not_an_error() -> None:
    """Grounding blanks what it cannot back, so a blank is the pipeline working.
    It costs ``figures`` and must not cost ``attribution``."""
    assert figure_verdicts(Product(name="Sony WH-1000XM5"), SONY) == [None, None, None]

    card = score_run([Product(name="Sony WH-1000XM5")], [])

    assert (card.metrics["figures"], card.metrics["attribution"]) == (0.0, 1.0)


# -- the scorecard ------------------------------------------------------------


def test_the_perfect_run_scores_full_marks(perfect: Scorecard) -> None:
    """The reference. If this drops, the answer key has gone out of step with the
    corpus or with the pipeline -- not with the model, of which there is none."""
    assert perfect.score == pytest.approx(1.0)
    assert perfect.metrics == {name: pytest.approx(1.0) for name in METRICS}
    assert perfect.counts["identified"] == (5, 5)
    assert perfect.counts["figures"] == (15, 15)
    assert (perfect.invented, perfect.repeated) == (0, 0)


def test_the_perfect_run_clears_every_floor(perfect: Scorecard) -> None:
    """A floor above what the reference answer itself can reach would fail the
    nightly for a model that read the pages perfectly."""
    for metric, value in perfect.metrics.items():
        assert value >= FLOORS[metric], metric
    assert perfect.score >= FLOORS["score"]
    assert "UNDER" not in perfect.table()


def test_the_sloppy_run_scores_exactly_what_its_mistakes_cost(sloppy: Scorecard) -> None:
    """The whole scorecard, pinned. Any change to the scorer, the corpus or the
    key that moves a number moves this, which is what makes a benchmark score
    comparable between two runs a month apart.

    Reading the counts: five entries scored out of the seven the model reported,
    because ``clean_products`` dropped the listicle headline and the cap took the
    Sennheiser. Of those five, three are real products (one shop invented, one
    repeat). They fill in all nine of their figures and get seven right, the
    other two being somebody else's; all three link home; and of their three
    quotes one is a paraphrase.
    """
    assert sloppy.counts == {
        "identified": (3, 5),
        "genuine": (3, 5),
        "figures": (7, 9),
        "attribution": (7, 9),
        "links": (3, 3),
        "quotes": (2, 3),
        "faithful": (2, 3),
        "order": (3, 3),
    }
    assert (sloppy.invented, sloppy.repeated) == (1, 1)
    assert sloppy.score == pytest.approx(0.7264957264957265)


def test_the_scorer_catches_the_three_the_pipeline_cannot(sloppy: Scorecard) -> None:
    """The argument for the benchmark, as counts: a shop reported as a product, a
    product reported twice under names ``deduplicate`` does not merge, and two
    figures printed for somebody else. Every invariant
    ``integration/test_live_pipeline.py`` asserts holds on this same answer."""
    right, reported = sloppy.counts["attribution"]

    assert (sloppy.invented, sloppy.repeated) == (1, 1)
    assert reported - right == 2
    assert sloppy.counts["faithful"] == (2, 3), "a paraphrase verify_opinions tolerates"


def test_a_ranking_in_the_wrong_order_scores_less(perfect: Scorecard) -> None:
    """``order`` is the one metric both scripted runs max out, the ideal and the
    actual being ranked off the same figures -- so it is exercised by handing the
    scorer an answer in the wrong order instead."""
    right = [entry.as_product() for entry in (ANKER, SENNHEISER, SONY)]

    assert (score_run(right, []).metrics["order"], perfect.metrics["order"]) == (1.0, 1.0)
    assert score_run(list(reversed(right)), []).metrics["order"] == 0.0


def test_a_run_that_reported_nothing_falls_under_every_floor() -> None:
    """The five metrics measured over what was *found* go vacuous on an empty
    answer, so each answers 0.0 rather than "nothing was wrong". The three error
    halves honestly stay at 1.0 -- nothing reported is nothing misattributed --
    which is why the overall floor has to sit above what those three alone can
    carry, or a model that answered nothing would pass the nightly."""
    card = score_run([], [])

    assert card.metrics["identified"] == 0.0
    assert card.metrics["attribution"] == 1.0
    assert card.score < FLOORS["score"]
    assert "UNDER" in card.table(), "the nightly logs this pass or fail"


# -- the plumbing --------------------------------------------------------------


def test_a_quote_is_checked_against_the_condensed_page() -> None:
    """``page_words`` reads the text the model was shown, not the fixture: the
    fetch layer throws most of a page away, and a quote off a discarded line is
    one the model could not have copied."""
    with serving_the_corpus():
        results = agent_module.enrich(agent_module.search_web(REQUEST, max_results=2))

    words = page_words(results)

    assert set(words) == {page.url for page in PAGES[:2]}
    assert "copyright 2026 audiosite media" not in words[PAGES[0].url]
    # Running words, so "4.7" is "4 7" and "12,480" is "12480": compared word for
    # word after ``normalise_numbers``, which lets a quote match a page that
    # grouped its thousands differently.
    assert "rated 4 7 out of 5 from 12480 reviews" in words[PAGES[0].url]


def test_the_corpus_is_put_back_when_the_run_is_over() -> None:
    """Two names on :mod:`buy_agent.agent` are replaced for the length of a run.
    Left replaced, every later test would search a corpus it never asked for --
    and one that forgot to fake the web would pass."""
    original = agent_module.search_web, agent_module.enrich

    with serving_the_corpus():
        assert agent_module.search_web is not original[0]

    assert (agent_module.search_web, agent_module.enrich) == original


def test_the_run_is_scored_on_the_pages_it_was_given() -> None:
    """The report carries the condensed corpus, not the raw fixture, so a caller
    reading ``pages`` is reading what the model read."""
    report = run_benchmark(llm=ScriptedLLM(PERFECT))

    assert len(report.pages) == len(PAGES)
    assert all(page.content for page in report.pages)
    assert report.pages[0].content != PAGE_TEXT[PAGES[0].url]


def test_widening_the_run_widens_the_slots() -> None:
    """Recall is measured against the cap, so a run allowed more products is
    scored against more of the key rather than against a ceiling it has left."""
    report = run_benchmark(llm=ScriptedLLM(PERFECT), config=settings(num_products=7))

    assert report.scorecard.counts["identified"] == (5, len(ANSWER_KEY))


# -- python -m benchmark -------------------------------------------------------


def test_the_command_line_scores_a_scripted_run(capsys: pytest.CaptureFixture) -> None:
    """The reference run is reachable with nothing installed and nothing running,
    which is what makes it usable as a reference. The agent's own narration and
    top-3 report stay quiet, so a shell redirect catches the scorecard."""
    code = benchmark_main.main(["--scripted", "perfect"])
    printed = capsys.readouterr().out

    assert code == 0
    assert "score         1.000" in printed
    assert "Sony WH-1000XM5" in printed
    assert "TOP 3 OF" not in printed
    assert logging.getLogger("buy_agent").level == logging.WARNING


def test_the_command_line_fails_when_a_floor_is_missed(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A benchmark that always exits 0 is one nothing can be automated around."""
    monkeypatch.setitem(FLOORS, "figures", 1.01)

    assert benchmark_main.main(["--scripted", "perfect"]) == 1
    assert "UNDER" in capsys.readouterr().out


def test_the_command_line_writes_the_scorecard_as_a_record(tmp_path, capsys) -> None:
    """``--json`` is how two runs a month apart are compared without either being
    repeated, so it carries every metric and the overall score."""
    target = tmp_path / "scorecard.json"
    benchmark_main.main(["--scripted", "sloppy", "--json", str(target)])
    written = json.loads(target.read_text(encoding="utf-8"))

    assert set(written) == set(METRICS) | {"score"}
    assert written["score"] == pytest.approx(0.7264957264957265)
    capsys.readouterr()


def test_the_command_line_offers_every_script(capsys: pytest.CaptureFixture) -> None:
    """A script added to :data:`benchmark.scripted.SCRIPTS` is offered by name
    rather than listed a second time in the parser."""
    parser = benchmark_main.build_parser()
    action = next(item for item in parser._actions if item.dest == "scripted")

    assert set(action.choices) == set(SCRIPTS)
    capsys.readouterr()
