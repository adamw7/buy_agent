"""The benchmark, checked without a model: is the score it reports the right one?"""

from __future__ import annotations

import json
import logging

import pytest

from buy_agent import agent as agent_module
from buy_agent.agent import ModelUnavailableError
from buy_agent.models import Opinion, Product, ProductList
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
from benchmark.cases import ESPRESSO, HEADPHONES, LAPTOPS
from benchmark.corpus import NUM_PRODUCTS, PAGES, PAGE_TEXT, REQUEST, TOP_N
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


@pytest.fixture(scope="module")
def perfect() -> Scorecard:
    """The reference run: the answer key copied out, through the whole pipeline."""
    return run_benchmark(llm=ScriptedLLM(PERFECT)).scorecard


@pytest.fixture(scope="module")
def sloppy() -> Scorecard:
    """The same run, wrong in the ways :data:`benchmark.scripted.SLOPPY` lists."""
    return run_benchmark(llm=ScriptedLLM(SLOPPY)).scorecard


@pytest.fixture(scope="module")
def served() -> tuple[SearchResult, ...]:
    """Every page as the model is shown it: condensed, which is the only text the answer
    key is allowed to be about."""
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
    """The test that keeps :mod:`benchmark.answers` a transcription rather than a wish."""
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
    """Why :func:`identifies` looks both ways."""
    assert identifies("Sony", SONY) == 0.0
    assert best_match("Sony") is None


def test_two_products_sharing_a_word_are_told_apart() -> None:
    """"Soundcore" is in both of these, and they are $99 and $59."""
    assert best_match("Soundcore Space Q45") is ANKER
    assert best_match("Soundcore Life Q30") is LIFE


def test_a_name_with_another_model_number_is_another_product() -> None:
    """The mistake a 0.6 bar cannot see: "Sony WH-1000XM4" shares two of its three words
    with the XM5, and grounding keeps it off pages about the XM5 (ADR-0073)."""
    assert best_match("Sony WH-1000XM4") is None
    assert best_match("Sony WH-1000XM6") is None
    assert best_match("Soundcore Life Q35") is None
    assert best_match("Razer Blade 16", LAPTOPS.key) is None
    assert best_match("Lenovo Legion Slim 7", LAPTOPS.key) is None
    assert best_match("Philips 2200 LatteGo", ESPRESSO.key) is None


def test_a_model_number_on_one_side_only_is_a_spec_or_a_shortening() -> None:
    """What the rule above must not catch: a name carrying more numbers than the key's,
    or fewer, is the same product told more or less of."""
    by_name = {entry.name: entry for entry in (*LAPTOPS.key, *ESPRESSO.key)}

    assert best_match("Sony WH-1000XM5 (2022)") is SONY
    assert best_match("Lenovo Legion Slim 5 16GB", LAPTOPS.key) is by_name["Lenovo Legion Slim 5"]
    assert (
        best_match("ASUS ROG Zephyrus G14 RTX 4070", LAPTOPS.key)
        is by_name["ASUS ROG Zephyrus G14"]
    )
    assert best_match("De'Longhi Dedica", ESPRESSO.key) is by_name["De'Longhi Dedica Arte EC885"]


def test_a_run_reporting_the_wrong_generation_has_invented_a_product() -> None:
    """Through the whole pipeline: grounding lets the XM4 through, and the scorer is what
    says it is not what the pages are about."""
    renamed = PERFECT.products[0].model_copy(update={"name": "Sony WH-1000XM4"})
    answer = ProductList(products=[renamed, *PERFECT.products[1:]])

    card = run_benchmark(llm=ScriptedLLM(answer)).scorecard

    assert card.invented == 1
    assert card.counts["identified"] == (4, 5)
    assert card.counts["genuine"] == (4, 5)


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
    """Grounding blanks what it cannot back, so a blank is the pipeline working."""
    assert figure_verdicts(Product(name="Sony WH-1000XM5"), SONY) == [None, None, None]

    card = score_run([Product(name="Sony WH-1000XM5")], [])

    assert (card.metrics["figures"], card.metrics["attribution"]) == (0.0, 1.0)


# -- the quotes ----------------------------------------------------------------


def quoted(product: str, *quotes: str) -> Product:
    """A product as the run reported it, carrying ``quotes``."""
    return Product(name=product, opinions=[Opinion(text=quote) for quote in quotes])


def test_a_verdict_on_another_product_on_the_same_page_is_not_faithful(
    served: tuple[SearchResult, ...],
) -> None:
    """The mistake the prompt forbids and the pipeline cannot see: AudioSite names the
    Bose, so ``verify_opinions`` keeps the Sony's verdict on it, and a key of pages
    would have too (ADR-0073)."""
    sonys = "In our tests the noise cancelling was still the best of anything at this price."

    card = score_run([quoted("Bose QuietComfort Ultra", sonys)], served)

    assert card.counts["faithful"] == (0, 1)
    assert card.counts["quotes"] == (0, 1)


def test_a_line_that_judges_nothing_is_not_a_verdict(served: tuple[SearchResult, ...]) -> None:
    """Word for word on the Sony's own page, and a price, not a verdict."""
    price = "The Sony WH-1000XM5 costs $328 at most shops."

    card = score_run([quoted("Sony WH-1000XM5", price)], served)

    assert card.counts["faithful"] == (0, 1)


def test_a_quote_may_be_a_run_of_words_out_of_a_verdict(served: tuple[SearchResult, ...]) -> None:
    """Models trim, and what they keep is still one verdict's words in its order. Two
    verdicts run together are on the page -- the lines are consecutive -- and are no
    verdict anybody passed."""
    trimmed = quoted("Sony WH-1000XM5", "the earcups roomy enough for an eight-hour flight")
    stitched = quoted(
        "Sony WH-1000XM5",
        "roomy enough for an eight-hour flight. The downside is that the case no longer folds",
    )

    assert score_run([trimmed], served).counts["faithful"] == (1, 1)
    assert score_run([stitched], served).counts["faithful"] == (0, 1)
    assert score_run([quoted("Sony WH-1000XM5", "--")], served).counts["faithful"] == (0, 1)


def test_a_verdict_counts_only_on_a_page_the_run_was_shown(
    served: tuple[SearchResult, ...],
) -> None:
    """The key's verdicts are the condensed pages' lines; a run shown fewer pages could
    not have copied one off a page it never saw (ADR-0036)."""
    sonys = "Owners recommend buying while the sale lasts."
    shown = [page for page in served if page.url != "https://audiodeal.example/sony-wh-1000xm5"]

    assert score_run([quoted("Sony WH-1000XM5", sonys)], served).counts["faithful"] == (1, 1)
    assert score_run([quoted("Sony WH-1000XM5", sonys)], shown).counts["faithful"] == (0, 1)


def test_a_product_no_page_judges_is_not_asked_for_a_quote(
    served: tuple[SearchResult, ...],
) -> None:
    """The AirPods Max is priced on two pages and judged on none, so finding it costs a
    run no quote it could not have given."""
    card = score_run([SONY.as_product(), AIRPODS.as_product()], served)

    assert card.counts["quotes"] == (0, 1)


# -- the scorecard ------------------------------------------------------------


def test_the_perfect_run_scores_full_marks(perfect: Scorecard) -> None:
    """The reference."""
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
    """The whole scorecard, pinned."""
    assert sloppy.counts == {
        "identified": (3, 5),
        "genuine": (3, 5),
        "figures": (7, 9),
        "attribution": (7, 9),
        "links": (3, 3),
        "quotes": (2, 3),
        "faithful": (2, 3),
        "order": (2, 3),
    }
    assert (sloppy.invented, sloppy.repeated) == (1, 1)
    assert sloppy.score == pytest.approx(0.6752136752136753)


def test_the_scorer_catches_the_three_the_pipeline_cannot(sloppy: Scorecard) -> None:
    """The argument for the benchmark, as counts: a shop reported as a product, a product
    reported twice under names ``deduplicate`` does not merge, and two figures printed
    for somebody else."""
    right, reported = sloppy.counts["attribution"]

    assert (sloppy.invented, sloppy.repeated) == (1, 1)
    assert reported - right == 2
    assert sloppy.counts["faithful"] == (2, 3), "a paraphrase verify_opinions tolerates"


def test_a_ranking_in_the_wrong_order_scores_less(perfect: Scorecard) -> None:
    """``order`` is maxed out by the perfect run, the ideal and the actual being
    ranked off the same figures -- so both ends of it are exercised by handing the
    scorer an answer in the wrong order instead."""
    right = [entry.as_product() for entry in (ANKER, SENNHEISER, SONY)]

    assert (score_run(right, []).metrics["order"], perfect.metrics["order"]) == (1.0, 1.0)
    assert score_run(list(reversed(right)), []).metrics["order"] == 0.0


def test_a_run_that_reported_nothing_falls_under_every_floor() -> None:
    """The five metrics measured over what was *found* go vacuous on an empty answer, so
    each answers 0.0 rather than "nothing was wrong"."""
    card = score_run([], [])

    assert card.metrics["identified"] == 0.0
    assert card.metrics["attribution"] == 1.0
    assert card.score < FLOORS["score"]
    assert "UNDER" in card.table(), "the nightly logs this pass or fail"


# -- how the score is weighed --------------------------------------------------


def card_of(**counts: tuple[int, int]) -> Scorecard:
    """A scorecard with every metric full but the ones named."""
    return Scorecard(counts={name: (5, 5) for name in METRICS} | counts, invented=0, repeated=0)


def test_reporting_nothing_scores_nothing() -> None:
    """``attribution``, ``faithful`` and ``order`` read 1.0 with nothing to count, and are
    floored that way; the score is paid for none of them (ADR-0074). It used to pay 0.308
    for an empty answer."""
    assert score_run([], []).score == 0.0


def test_a_pair_counts_only_as_far_as_both_halves_do() -> None:
    """Five products found and not a figure copied: the error half has nothing to be
    wrong about, and the pair counts nothing for it."""
    card = card_of(figures=(0, 15), attribution=(0, 0))

    assert card.metrics["attribution"] == 1.0, "shown as nothing wrong"
    assert card.parts["figures/attribution"] == (4.0, 0.0)
    assert card.parts["identified/genuine"] == (5.0, 1.0)


def test_a_pair_is_its_halves_weighed_harmonically() -> None:
    """``identified`` weighs 3 to ``genuine``'s 2, so finding three of five slots costs
    more than reporting a shop beside them; and one right figure out of fifteen is worth
    little however right it is."""
    found = card_of(identified=(3, 5), genuine=(3, 3)).parts["identified/genuine"]
    sparse = card_of(figures=(1, 15), attribution=(1, 1)).parts["figures/attribution"]

    assert found == (5.0, pytest.approx(5 / (3 / 0.6 + 2 / 1.0)))
    assert sparse == (4.0, pytest.approx(2 / (1 / (1 / 15) + 1 / 1.0)))


def test_order_is_paid_only_above_a_shuffle() -> None:
    """A shuffled ranking puts half its pairs in order on average; the metric shows that
    half, and the score pays for what is above it (ADR-0074)."""
    assert card_of(order=(5, 10)).parts["order"] == (1.0, 0.0)
    assert card_of(order=(3, 10)).parts["order"] == (1.0, 0.0)
    assert card_of(order=(8, 10)).parts["order"] == (1.0, pytest.approx(0.6))
    assert card_of(order=(0, 0)).parts["order"] == (1.0, 0.0), "no pair to put in order"
    assert card_of(order=(0, 0)).metrics["order"] == 1.0


def test_a_run_that_copies_no_figure_scores_under_one_that_copies_most(sloppy) -> None:
    """Five products named and linked, and not a price, rating or quote among them, gave
    a shopper nothing to rank on. It used to outscore ``SLOPPY``, which copied seven
    figures of nine: 0.731 to 0.701."""
    names = ProductList(
        products=[
            product.model_copy(
                update={"price": -1, "currency": "", "rating": -1, "review_count": 0,
                        "opinions": []}
            )
            for product in PERFECT.products
        ]
    )

    card = run_benchmark(llm=ScriptedLLM(names)).scorecard

    assert card.counts["identified"] == (5, 5)
    assert card.counts["figures"] == (0, 15)
    assert card.score < sloppy.score


def test_a_figure_the_key_accepts_never_costs_the_order() -> None:
    """The Sennheiser's euro listing is a price the pages print for it, so a run that
    reports it is right, and is ranked against a key that says so (ADR-0074). Against the
    key's own dollar price it used to cost a tenth of ``order``."""
    euro = PERFECT.products[2].model_copy(update={"price": 169.0, "currency": "EUR"})
    answer = ProductList(products=[*PERFECT.products[:2], euro, *PERFECT.products[3:]])

    card = run_benchmark(llm=ScriptedLLM(answer)).scorecard

    assert card.counts["figures"] == (15, 15)
    assert card.counts["order"] == (10, 10)
    assert card.score == pytest.approx(1.0)


def test_the_score_says_what_it_is_made_of(sloppy: Scorecard) -> None:
    """The score is no longer a weighted mean of the rows above it, so the table says
    what it is a mean of."""
    expected = (
        "identified/genuine 0.600 x5, figures/attribution 0.778 x4, links 1.000 x1, "
        "quotes/faithful 0.667 x2, order 0.333 x1"
    )

    assert sloppy.parts_label() == expected
    assert f"  weighed as   {expected}" in sloppy.table()


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
    # word after ``plain_figures``, which lets a quote match a page that
    # grouped its thousands differently.
    assert "rated 4 7 out of 5 from 12480 reviews" in words[PAGES[0].url]


def test_the_corpus_is_put_back_when_the_run_is_over() -> None:
    """Two names on :mod:`buy_agent.agent` are replaced for the length of a run."""
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
    report = run_benchmark(llm=ScriptedLLM(PERFECT), config=HEADPHONES.settings(num_products=7))

    assert report.scorecard.counts["identified"] == (5, len(ANSWER_KEY))


# -- python -m benchmark -------------------------------------------------------


def test_the_command_line_scores_a_scripted_run(capsys: pytest.CaptureFixture) -> None:
    """The reference run is reachable with nothing installed and nothing running, which is
    what makes it usable as a reference."""
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
    repeated, so it carries every metric of every run and the overall score."""
    target = tmp_path / "scorecard.json"
    benchmark_main.main(["--scripted", "sloppy", "--case", "headphones", "--json", str(target)])
    (row,) = json.loads(target.read_text(encoding="utf-8"))["standings"]
    (run,) = row["runs"]

    assert [metric["name"] for metric in run["metrics"]] == list(METRICS)
    assert run["score"] == pytest.approx(0.6752136752136753, abs=1e-4)
    assert row["score_label"] == "0.675"
    capsys.readouterr()


def test_the_command_line_offers_every_script(capsys: pytest.CaptureFixture) -> None:
    """A script added to :data:`benchmark.scripted.SCRIPTS` is offered by name
    rather than listed a second time in the parser."""
    parser = benchmark_main.build_parser()
    action = next(item for item in parser._actions if item.dest == "scripted")

    assert set(action.choices) == set(SCRIPTS)
    capsys.readouterr()


def test_the_command_line_puts_the_runs_own_report_back_when_asked(
    capsys: pytest.CaptureFixture,
) -> None:
    """``-v`` is the other half of the quietening above: the agent's narration and its
    top-3 report are its output rather than the benchmark's, so they are held back
    unless somebody asks for them and the scorecard is what a shell redirect catches."""
    code = benchmark_main.main(["--scripted", "perfect", "-v"])
    printed = capsys.readouterr().out

    assert code == 0
    assert "score         1.000" in printed
    assert "TOP 3 OF" in printed
    assert logging.getLogger("buy_agent").level != logging.WARNING


def test_the_command_line_reports_a_model_it_could_not_use(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one of ``BuyAgent.run``'s three failures this door can see: the corpus is
    served rather than searched and the request is a constant, so a stopped model
    server is the only way in. It is a sentence on stderr and exit 1, not a
    traceback over the scorecard that was never computed."""

    def unavailable(*_args: object, **_kwargs: object) -> None:
        raise ModelUnavailableError("Ollama is not answering on http://localhost:11434")

    monkeypatch.setattr(benchmark_main, "run_case", unavailable)
    code = benchmark_main.main([])
    captured = capsys.readouterr()

    assert code == 1
    assert "Ollama is not answering" in captured.err
    assert captured.err.count("Ollama is not answering") == 1, "its other cases are skipped"
    assert captured.out == ""
