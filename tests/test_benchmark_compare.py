"""The comparison: several models over every case, timed, ranked and kept (ADR-0070)."""

from __future__ import annotations

import itertools
import json
import logging
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from ollama import ResponseError

from buy_agent.agent import ModelUnavailableError
from buy_agent.chat import UnreadableAnswerError
from buy_agent.models import Product, ProductList, RankedProduct, ScoreParts, SearchQuery
from benchmark import __main__ as benchmark_main
from benchmark import pipeline
from benchmark.baseline import against, read_baseline
from benchmark.board import BOARD, FILENAME, VERSION, Board
from benchmark.cases import CASES, ESPRESSO, HEADPHONES, LAPTOPS
from benchmark.compare import (
    CaseRun,
    Contender,
    Stopwatch,
    case_payload,
    describe,
    finished_label,
    metrics_payload,
    build_label,
    reported,
    run_case,
    run_payload,
    scored_run,
    scored_under,
    seconds_label,
    settings_now,
    standing_payload,
    standings,
    standings_payload,
    summary_markdown,
    write_standings,
)
from benchmark.query import QueryCheck, QueryVerdict
from benchmark.runner import run_benchmark
from benchmark.scoring import FLOORS, MEANINGS, METRICS
from benchmark.scripted import ScriptedLLM

#: A scorecard's counts at every share: full, half and none.
FULL = {name: (2, 2) for name in METRICS}
HALF = {name: (1, 2) for name in METRICS}
NONE = {name: (0, 2) for name in METRICS}

PERFECT = Contender.scripted("perfect")
SLOPPY = Contender.scripted("sloppy")
TINY = Contender(provider="ollama", model="tiny:1b", base_url="http://127.0.0.1:11434")
LARGE = Contender(provider="ollama", model="large:12b", base_url="http://127.0.0.1:11434")


def kept(
    contender: Contender,
    case: str = "headphones",
    *,
    counts: dict[str, tuple[int, int]] | None = FULL,
    seconds: float = 1.0,
    passed: bool = True,
    failure: str | None = None,
) -> CaseRun:
    """A run as the board keeps one, made to order, and made by this checkout."""
    return CaseRun(
        contender=contender,
        case=case,
        fingerprint=CASES[case].fingerprint,
        finished="2026-10-02T09:05:30+00:00",
        query=QueryVerdict(query="q", checks=[QueryCheck(check="Keeps 'q'", passed=passed)]),
        seconds={"query": 0.0, "extract": seconds},
        counts=None if failure else counts,
        failure=failure,
        pipeline=pipeline.code(),
        settings=pipeline.settings(CASES[case].settings(**contender.settings())),
    )


@pytest.fixture
def ollama(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stand in for Ollama, so a served contender is asked through the real
    ``_OllamaChat``: each model answers each case from the script named for it in
    ``behaves`` (``perfect`` unless named), or raises what is named there instead, and
    the server lists ``tags`` -- or fails to, with ``unlisted`` -- without a socket."""
    asked: dict[str, Any] = {
        "chats": [],
        "closed": 0,
        "behaves": {},
        "tags": [],
        "digests": {},
        "unlisted": None,
    }

    class Listing:
        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict:
            return {
                "models": [
                    {"model": name, "digest": asked["digests"].get(name, "")}
                    for name in asked["tags"]
                ]
            }

    def get(_url: str, **_kwargs: Any) -> Any:
        if asked["unlisted"] is not None:
            raise asked["unlisted"]
        return Listing()

    class FakeClient:
        def __init__(self, base_url: str, **_kwargs: Any) -> None:
            asked["base_url"] = base_url
            self.chatted = False

        def chat(self, *, model: str, messages: list, format: dict, **options: Any) -> Any:
            self.chatted = True
            asked["chats"].append({"model": model, "schema": format["title"], **options})
            behaviour = asked["behaves"].get(model, "perfect")
            if isinstance(behaviour, Exception):
                raise behaviour
            case = next(case for case in CASES.values() if case.request in messages[-1]["content"])
            if format["title"] == SearchQuery.__name__:
                content = "not json" if behaviour == "mute" else json.dumps({"query": case.refined})
            elif behaviour == "unreadable":
                content = '{"products": [{"name": "Sony'
            else:
                script = "perfect" if behaviour == "mute" else behaviour
                content = case.scripts[script].model_dump_json()
            return SimpleNamespace(message=SimpleNamespace(content=content))

        @staticmethod
        def show(name: str) -> Any:
            return SimpleNamespace(capabilities=["embedding" if "embed" in name else "completion"])

        def close(self) -> None:
            # A run's model, not the client a listing opens and closes for itself.
            asked["closed"] += self.chatted

    monkeypatch.setattr("buy_agent.providers.Client", FakeClient)
    monkeypatch.setattr("buy_agent.providers.httpx.get", get)
    return asked


# -- who is scored -------------------------------------------------------------


def test_a_served_contender_fills_its_blanks_the_way_a_run_does() -> None:
    """So a model asked for by default and by name is one contender, not two (ADR-0012)."""
    defaulted = Contender.served("ollama")
    named = Contender.served("ollama", defaulted.model, defaulted.base_url)

    assert defaulted == named
    assert defaulted.model and defaulted.base_url
    assert hash(defaulted) == hash(named)


def test_a_served_contender_names_its_model_and_where_it_answers() -> None:
    assert TINY.key == "ollama:tiny:1b@http://127.0.0.1:11434"
    assert TINY.label == "tiny:1b"
    assert TINY.where == "Ollama at http://127.0.0.1:11434"
    assert TINY.settings() == {
        "provider": "ollama",
        "model": "tiny:1b",
        "base_url": "http://127.0.0.1:11434",
    }


def test_a_scripted_contender_asks_no_model() -> None:
    assert PERFECT.key == "script:perfect"
    assert PERFECT.label == "perfect (scripted)"
    assert PERFECT.where == "A hand-written answer; no model is asked."
    assert PERFECT.settings() == {}

    model = PERFECT.model_for(LAPTOPS, LAPTOPS.settings())

    assert isinstance(model, ScriptedLLM)
    assert model.script is LAPTOPS.scripts["perfect"]


def test_a_contender_kept_from_a_server_since_removed_still_says_where_it_was() -> None:
    """The board outlives the table it was written beside."""
    gone = Contender(provider="lmstudio", model="m", base_url="http://127.0.0.1:1234")

    assert gone.where == "lmstudio at http://127.0.0.1:1234"


@pytest.mark.parametrize(
    ("make", "named"),
    [
        (lambda: Contender.scripted("lucky"), "'lucky'"),
        (lambda: Contender.served("kobold"), "'kobold'"),
    ],
)
def test_a_contender_nothing_can_answer_for_is_refused(make, named: str) -> None:
    with pytest.raises(ValueError, match=named):
        make()


# -- the stopwatch -------------------------------------------------------------


def ticking(step: float = 0.5):
    """A clock that moves ``step`` seconds every time it is read."""
    ticks = itertools.count(0.0, step)
    return lambda: next(ticks)


def test_the_stopwatch_times_each_question_and_keeps_the_query() -> None:
    watched = Stopwatch(ScriptedLLM(ProductList(), "a query"), ticking(0.5))

    assert watched.answer([], SearchQuery) == SearchQuery(query="a query")
    watched.answer([], ProductList)
    watched.answer([], ProductList)

    assert watched.seconds == {"query": 0.5, "extract": 1.0}
    assert watched.query == "a query"


def test_the_stopwatch_counts_the_time_a_failed_question_took() -> None:
    """A model that times out has spent the time all the same."""

    class Failing:
        def answer(self, _messages: Any, _schema: type) -> Any:
            raise UnreadableAnswerError("Invalid JSON answer: {")

    watched = Stopwatch(Failing(), ticking(2.0))

    with pytest.raises(UnreadableAnswerError):
        watched.answer([], ProductList)

    assert watched.seconds == {"extract": 2.0}
    assert watched.query is None


def test_the_stopwatch_names_a_question_it_does_not_know_by_its_schema() -> None:
    watched = Stopwatch(SimpleNamespace(answer=lambda _messages, _schema: None), ticking())

    watched.answer([], Product)

    assert set(watched.seconds) == {"Product"}


# -- one run -------------------------------------------------------------------


def test_a_scripted_run_is_scored_timed_and_explained() -> None:
    run = run_case(PERFECT, ESPRESSO, clock=ticking(0.25))

    assert run.case == "espresso"
    assert run.fingerprint == ESPRESSO.fingerprint
    assert run.score == pytest.approx(1.0)
    assert run.failure is None
    assert run.seconds == {"query": 0.25, "extract": 0.25}
    assert run.model_seconds == 0.5
    assert run.query.query == ESPRESSO.refined
    assert run.query.score == 1.0
    assert [product.verdict for product in run.products] == ["real"] * 5
    assert run.finished.endswith("+00:00")


def test_a_served_model_is_asked_through_its_own_server(ollama: dict[str, Any]) -> None:
    run = run_case(Contender.served("ollama", "tiny:1b", "http://127.0.0.1:11555"), LAPTOPS)

    assert run.score == pytest.approx(1.0)
    assert ollama["base_url"] == "http://127.0.0.1:11555"
    assert [chat["schema"] for chat in ollama["chats"]] == ["SearchQuery", "ProductList"]
    assert {chat["model"] for chat in ollama["chats"]} == {"tiny:1b"}
    # The shipped defaults, as the nightly runs them (ADR-0019, ADR-0050).
    assert ollama["chats"][-1]["options"]["num_ctx"] == 16384
    assert ollama["chats"][-1]["think"] is False
    assert ollama["closed"] == 1, "the run releases the model it opened"


def test_an_unreadable_answer_is_this_cases_result(ollama: dict[str, Any]) -> None:
    """A model that cannot write the JSON asked for has failed the case, and says so in
    the server's own words (ADR-0019)."""
    ollama["behaves"]["tiny:1b"] = "unreadable"

    run = run_case(Contender.served("ollama", "tiny:1b"), HEADPHONES)

    assert run.counts is None
    assert run.score == 0.0
    assert run.scorecard is None
    assert "not the JSON this asks for" in (run.failure or "")
    assert run.products == []
    assert run.query.score == 1.0, "the query step answered"
    assert ollama["closed"] == 1


def test_a_model_that_cannot_be_asked_is_no_result_at_all(ollama: dict[str, Any]) -> None:
    """Not pulled, not running, too slow: nothing about the case was learned."""
    ollama["behaves"]["missing:1b"] = ResponseError("model 'missing:1b' not found", 404)

    ollama["tags"] = ["tiny:1b"]

    remedy = r"ollama pull missing:1b  \(installed: tiny:1b\)"
    with pytest.raises(ModelUnavailableError, match=remedy):
        run_case(Contender.served("ollama", "missing:1b"), HEADPHONES)


def test_a_query_the_model_garbled_is_scored_as_no_query(ollama: dict[str, Any]) -> None:
    """The agent recovers by searching with the request; the query step scores 0."""
    ollama["behaves"]["tiny:1b"] = "mute"

    run = run_case(Contender.served("ollama", "tiny:1b"), HEADPHONES)

    assert run.score == pytest.approx(1.0)
    assert run.query.query is None
    assert run.query.score == 0.0


def test_a_run_scored_elsewhere_is_kept_as_one_scored_here() -> None:
    """The nightly shares one run between its live tests and builds its kept run off it
    (ADR-0072): the same scorecard, query and products a comparison would have kept."""
    watched = Stopwatch(LAPTOPS.scripted("sloppy"), ticking(0.25))
    report = run_benchmark(llm=watched, case=LAPTOPS)

    elsewhere = scored_run(SLOPPY, LAPTOPS, LAPTOPS.settings(), watched, report)
    here = run_case(SLOPPY, LAPTOPS, clock=ticking(0.25))

    assert elsewhere.model_dump(exclude={"finished"}) == here.model_dump(exclude={"finished"})


def test_a_job_summary_is_the_run_described_under_its_score() -> None:
    """What ``$GITHUB_STEP_SUMMARY`` is given: a heading a reader scans for, then the very
    lines the job's log carries."""
    run = run_case(SLOPPY, HEADPHONES)

    shown = summary_markdown(run, HEADPHONES)

    assert shown.startswith(f"### sloppy (scripted) on headphones: {run.score:.3f}\n\n")
    assert f"```text\n{describe(run, HEADPHONES)}\n```\n" in shown
    failed = summary_markdown(kept(TINY, failure="answered with something unreadable"), HEADPHONES)
    assert failed.startswith("### tiny:1b on headphones: failed\n")
    assert "  failed: answered with something unreadable" in failed


def test_the_standings_are_written_as_the_page_reads_them(tmp_path: Path) -> None:
    runs = [kept(PERFECT), kept(TINY, counts=HALF)]
    target = tmp_path / "standings.json"

    write_standings(target, runs, [HEADPHONES])

    assert json.loads(target.read_text(encoding="utf-8")) == json.loads(
        json.dumps(standings_payload(runs, [HEADPHONES]))
    )


def test_a_run_is_stopped_at_the_step_its_checkpoint_refuses() -> None:
    class Halt(Exception):
        pass

    def checkpoint(step: str) -> None:
        if step == "extract":
            raise Halt(step)

    with pytest.raises(Halt):
        run_case(SLOPPY, LAPTOPS, checkpoint=checkpoint)


def test_what_a_run_reported_is_read_against_the_key_in_its_order() -> None:
    def ranked(name: str, rank: int) -> RankedProduct:
        parts = ScoreParts(rating=0.5, popularity=0.5, price=0.5, total=0.5)
        return RankedProduct(product=Product(name=name, price=99.0), breakdown=parts, rank=rank)

    found = reported(
        [
            ranked("Soundcore Space Q45", 1),
            ranked("Anker Soundcore Space Q45", 2),
            ranked("AudioSite", 3),
        ],
        HEADPHONES,
    )

    assert [(item.rank, item.matches, item.verdict) for item in found] == [
        (1, "Anker Soundcore Space Q45", "real"),
        (2, "Anker Soundcore Space Q45", "repeated"),
        (3, None, "invented"),
    ]
    assert found[0].price == "99.00"
    assert found[0].rating == "unrated"
    assert found[0].line == "Soundcore Space Q45 -- 99.00, unrated, 0 quote(s)"


# -- the standings -------------------------------------------------------------


CASE_LIST = list(CASES.values())


def test_the_standings_rank_the_best_score_first() -> None:
    runs = [kept(TINY, counts=HALF), kept(LARGE, counts=FULL), kept(PERFECT, counts=NONE)]

    table = standings(runs, [HEADPHONES])

    assert [(row.rank, row.contender) for row in table] == [(1, LARGE), (2, TINY), (3, PERFECT)]


def test_a_contender_that_ran_more_cases_ranks_above_one_that_ran_fewer() -> None:
    """A mean over the easiest case is no comparison with a mean over all three."""
    runs = [kept(TINY, counts=FULL), *(kept(LARGE, name, counts=HALF) for name in CASES)]

    assert [row.contender for row in standings(runs, CASE_LIST)] == [LARGE, TINY]


def test_equal_scores_are_ordered_by_the_query_then_the_time() -> None:
    runs = [
        kept(TINY, seconds=9.0, passed=False),
        kept(LARGE, seconds=9.0),
        kept(PERFECT, seconds=1.0, passed=False),
        kept(SLOPPY, seconds=1.0, passed=False),
    ]

    assert [row.contender for row in standings(runs, [HEADPHONES])] == [
        LARGE,
        PERFECT,
        SLOPPY,
        TINY,
    ]


def test_a_contenders_latest_run_of_a_case_is_the_one_that_stands() -> None:
    runs = [kept(TINY, counts=NONE), kept(TINY, counts=FULL)]

    (row,) = standings(runs, [HEADPHONES])

    assert row.score == 1.0
    assert len(row.runs) == 1


def test_a_standing_averages_its_runs_and_counts_its_failures() -> None:
    runs = [
        kept(TINY, "headphones", counts=FULL, seconds=2.0),
        kept(TINY, "laptops", failure="answered with something unreadable", seconds=4.0),
    ]

    (row,) = standings(runs, CASE_LIST)

    assert row.score == 0.5, "a failed run counts 0"
    assert row.seconds == 3.0
    assert row.query == 1.0
    assert row.failed == 1


def test_a_case_nobody_asked_about_puts_nobody_on_the_board() -> None:
    assert standings([kept(TINY, "laptops")], [HEADPHONES]) == []


# -- what the page and --json are given ----------------------------------------


@pytest.mark.parametrize(
    ("seconds", "label"),
    [
        (0.04, "0.0 s"),
        (12.34, "12.3 s"),
        (59.9, "59.9 s"),
        (60.0, "1 min 00 s"),
        (187.4, "3 min 07 s"),
    ],
)
def test_a_duration_is_written_the_same_way_everywhere(seconds: float, label: str) -> None:
    assert seconds_label(seconds) == label


def test_a_finish_time_is_written_to_the_minute_in_utc() -> None:
    assert finished_label("2026-10-02T11:05:30+02:00") == "2026-10-02 09:05 UTC"
    assert finished_label("yesterday") == "yesterday", "a kept value that is no time"


def test_a_case_is_listed_with_what_it_asks() -> None:
    assert case_payload(LAPTOPS) == {
        "name": "laptops",
        "title": LAPTOPS.title,
        "asks": LAPTOPS.asks,
        "request": LAPTOPS.request,
        "pages": 8,
        "slots": 5,
    }


def test_every_metric_is_listed_with_its_weight_floor_and_meaning() -> None:
    listed = metrics_payload()

    assert [metric["name"] for metric in listed] == list(METRICS)
    assert set(MEANINGS) == set(METRICS)
    assert all(metric["floor"] == FLOORS[metric["name"]] for metric in listed)


def test_a_scored_run_is_shown_with_its_scorecard_query_and_products() -> None:
    shown = run_payload(run_case(SLOPPY, ESPRESSO, clock=ticking(0.25)))

    assert shown["case"] == "espresso"
    assert shown["score_label"] == "0.540"
    assert not shown["cleared"]
    assert shown["summary"].startswith("3 of 5 slots hold a real product")
    order = next(metric for metric in shown["metrics"] if metric["name"] == "order")
    assert order == {
        "name": "order",
        "value": 0.0,
        "label": "0.000",
        "counts": "0 of 3",
        "floor": "0.25",
        "under": True,
        "means": MEANINGS["order"],
    }
    assert shown["query"]["label"] == "1.00"
    assert shown["seconds_label"] == "0.5 s"
    assert shown["steps_label"] == "query 0.2 s, extract 0.2 s"
    assert [product["verdict"] for product in shown["products"]].count("invented") == 1
    assert shown["finished_label"].endswith("UTC")


def test_a_failed_run_is_shown_with_its_reason_and_no_scorecard() -> None:
    shown = run_payload(kept(TINY, failure="The model ran out of room."))

    assert shown["failure"] == "The model ran out of room."
    assert shown["score_label"] == "failed"
    assert (shown["metrics"], shown["summary"], shown["cleared"]) == ([], None, False)


def test_a_row_carries_a_cell_per_case_whatever_became_of_it() -> None:
    runs = [kept(TINY, "headphones", counts=HALF), kept(TINY, "laptops", failure="unreadable")]
    (row,) = standings(runs, CASE_LIST)

    shown = standing_payload(row, CASE_LIST)

    assert shown["cells"] == [
        {"case": "headphones", "state": "scored", "label": "0.462"},
        {"case": "laptops", "state": "failed", "label": "failed"},
        {"case": "espresso", "state": "missing", "label": "not run"},
    ]
    assert (shown["ran_label"], shown["complete"], shown["failed"]) == ("2 of 3", False, 1)
    assert (shown["rank"], shown["key"], shown["label"]) == (1, TINY.key, "tiny:1b")
    assert (shown["score_label"], shown["query_label"]) == ("0.231", "1.00")
    assert shown["reference"] is False
    assert [run["case"] for run in shown["runs"]] == ["headphones", "laptops"]


def test_the_standings_payload_is_the_cases_and_the_rows() -> None:
    shown = standings_payload([kept(PERFECT), kept(TINY, counts=HALF)], [HEADPHONES])

    assert [case["name"] for case in shown["cases"]] == ["headphones"]
    assert [row["label"] for row in shown["standings"]] == ["perfect (scripted)", "tiny:1b"]
    assert shown["standings"][0]["reference"] is True


# -- what a run was scored under -----------------------------------------------


def module_of(tmp_path: Path, file: str, source: str) -> ModuleType:
    """A module named ``step`` whose code is ``source``, as the fingerprint reads one."""
    path = tmp_path / file
    path.write_text(source, encoding="utf-8")
    module = ModuleType("step")
    module.__file__ = str(path)
    return module


#: A step with a prompt in it.
STEP = 'PROMPT = "Answer with the query only."\n\n\ndef step(x):\n    return x + 1\n'


def test_a_comment_a_docstring_or_a_reflowed_line_moves_no_fingerprint(tmp_path: Path) -> None:
    """Docs are edited here more often than code; a reworded one is the same pipeline."""
    reworded = (
        '"""A step."""\n# Asked once.\nPROMPT = (\n    "Answer with the query only."\n)\n\n\n'
        'def step(x):\n    """One more."""\n    return x + 1  # and no more\n'
    )

    assert pipeline.code((module_of(tmp_path, "a.py", STEP),)) == pipeline.code(
        (module_of(tmp_path, "b.py", reworded),)
    )


def test_a_prompt_or_a_step_that_changes_moves_the_fingerprint(tmp_path: Path) -> None:
    """What a run could notice: the words a model is asked with, or what a step does."""
    prompt = STEP.replace("query only", "query alone")
    step = STEP.replace("x + 1", "x + 2")

    codes = {
        pipeline.code((module_of(tmp_path, f"{index}.py", source),))
        for index, source in enumerate([STEP, prompt, step])
    }

    assert len(codes) == 3


def test_the_pipeline_is_the_code_between_the_pages_and_the_scorecard() -> None:
    """The steps, the prompts, the serving and the scoring; never a case, which has a
    fingerprint of its own and leaves the board when it changes (ADR-0070)."""
    names = {module.__name__ for module in pipeline.MODULES}

    assert {"buy_agent.extraction", "buy_agent.verification", "benchmark.scoring"} <= names
    assert not names & {"benchmark.cases", "benchmark.corpus", "benchmark.answers"}
    assert pipeline.code() == pipeline.code()
    assert len(pipeline.code()) == 16


def test_the_settings_are_what_reaches_the_model_and_what_it_is_shown() -> None:
    """Named as the doors name them; ``num_ctx`` only where the server takes it."""
    assert pipeline.settings(HEADPHONES.settings()) == {
        "temperature": 0.0,
        "think": False,
        "num_ctx": 16384,
        "page_chars": 1200,
        "opinion_chars": 400,
    }
    assert "num_ctx" not in pipeline.settings(HEADPHONES.settings(provider="vllm", model="m"))
    assert pipeline.settings_label({"temperature": 0.7, "think": None, "num_ctx": 8192}) == (
        "temperature 0.7, think unset, num_ctx 8192"
    )


def test_a_run_says_what_it_was_scored_under() -> None:
    run = run_case(PERFECT, HEADPHONES)

    assert run.pipeline == pipeline.code()
    assert run.settings == pipeline.settings(HEADPHONES.settings())
    assert run.build == "", "a script has no build"
    assert scored_under(run) == []


def test_a_served_run_says_which_build_of_its_model_answered(ollama: dict[str, Any]) -> None:
    """Asked of the server's own listing, through its provider row (ADR-0075)."""
    build = "sha256:" + "ab" * 32
    ollama["tags"] = ["large:12b", "tiny:1b"]
    ollama["digests"] = {"tiny:1b": build, "large:12b": "sha256:" + "cd" * 32}

    run = run_case(Contender.served("ollama", "tiny:1b"), HEADPHONES)

    assert run.build == build
    assert ollama["closed"] == 1, "the listing closed its own client, and the run its model"
    assert "  build        abababababab" in describe(run, HEADPHONES)
    assert build_label(build) == "abababababab"


def test_a_tag_asked_for_bare_is_found_as_ollama_lists_it(ollama: dict[str, Any]) -> None:
    ollama["tags"] = ["tiny:latest"]
    ollama["digests"] = {"tiny:latest": "sha256:" + "ef" * 32}

    assert run_case(Contender.served("ollama", "tiny"), HEADPHONES).build == "sha256:" + "ef" * 32


def test_a_build_nothing_will_list_is_left_blank(ollama: dict[str, Any]) -> None:
    """The run answered, so the model is there; a listing that fails says nothing more."""
    ollama["unlisted"] = ConnectionRefusedError("[Errno 111] Connection refused")

    run = run_case(Contender.served("ollama", "tiny:1b"), HEADPHONES)

    assert (run.build, run.score) == ("", pytest.approx(1.0))


def test_a_run_kept_under_another_pipeline_is_marked_and_still_ranked() -> None:
    """Its counts are true of the code that made them: kept and marked, not dropped."""
    old = kept(LARGE).model_copy(update={"pipeline": "0" * 16})
    rows = standings([kept(TINY, counts=HALF), old], [HEADPHONES])

    marked, current = (standing_payload(row, [HEADPHONES]) for row in rows)

    assert [row.contender for row in rows] == [LARGE, TINY]
    assert marked["current"] is False
    assert marked["notes"] == [
        "1 of its 1 run(s) scored under another version of the pipeline or with other "
        "settings: run them again to compare."
    ]
    assert marked["runs"][0]["pipeline_note"] == (
        "Scored under another version of the pipeline: its code, prompts or scoring have "
        "changed since."
    )
    assert (current["current"], current["notes"], current["runs"][0]["pipeline_note"]) == (
        True,
        [],
        None,
    )


def test_a_run_kept_with_other_settings_says_which() -> None:
    now = kept(TINY)
    old = now.model_copy(update={"settings": {**now.settings, "num_ctx": 8192, "think": True}})

    assert scored_under(old) == [
        "Scored with think on, num_ctx 8192; this checkout runs think off, num_ctx 16384."
    ]


def test_a_run_whose_case_or_server_is_gone_is_judged_on_its_code_alone() -> None:
    """Nothing here can say what settings it would run them on now."""
    gone = Contender(provider="lmstudio", model="m", base_url="http://127.0.0.1:1234")
    unserved = kept(TINY).model_copy(update={"contender": gone, "settings": {"num_ctx": 1}})
    retired = kept(TINY).model_copy(update={"case": "kettles", "settings": {"num_ctx": 1}})

    assert (settings_now(unserved), settings_now(retired)) == (None, None)
    assert scored_under(unserved) == scored_under(retired) == []


def test_runs_on_two_builds_of_one_tag_are_said_to_be() -> None:
    """A tag pulled again between two cases: comparable code, other weights."""
    runs = [
        kept(TINY, "headphones").model_copy(update={"build": "sha256:aa"}),
        kept(TINY, "laptops").model_copy(update={"build": "sha256:bb"}),
    ]
    (row,) = standings(runs, CASE_LIST)

    assert row.notes == ["Its runs were scored on 2 builds of this model."]
    assert row.stale == 0


def test_a_run_says_when_with_what_and_on_which_build_it_ran() -> None:
    run = kept(TINY).model_copy(update={"build": "sha256:" + "ab" * 32})
    bare = kept(TINY).model_copy(update={"settings": {}})

    assert run_payload(run)["scored_with_label"] == (
        "Ran 2026-10-02 09:05 UTC with temperature 0, think off, num_ctx 16384, "
        "page_chars 1200, opinion_chars 400, on build abababababab"
    )
    assert run_payload(bare)["scored_with_label"] == "Ran 2026-10-02 09:05 UTC"


# -- the board -----------------------------------------------------------------


def test_the_board_lives_beside_the_cache() -> None:
    """Under ``$BUY_AGENT_CACHE_DIR``, which every test points somewhere disposable."""
    board = Board()

    assert board.path.parent.name == BOARD
    assert board.path.name == FILENAME
    assert board.runs() == []


def test_a_kept_run_is_read_back_whole(tmp_path: Path) -> None:
    run = run_case(SLOPPY, LAPTOPS)
    board = Board(tmp_path / "board.json")

    board.add(run)

    (back,) = Board(tmp_path / "board.json").runs()
    assert back == run
    assert back.score == pytest.approx(run.score)


def test_a_later_run_of_a_contender_on_a_case_replaces_the_earlier(tmp_path: Path) -> None:
    board = Board(tmp_path / "board.json")
    board.add(kept(TINY, counts=NONE))
    board.add(kept(TINY, "laptops"))
    board.add(kept(LARGE))
    board.add(kept(TINY, counts=FULL))

    runs = board.runs()

    assert [(run.contender, run.case) for run in runs] == [
        (TINY, "laptops"),
        (LARGE, "headphones"),
        (TINY, "headphones"),
    ]
    assert runs[-1].score == 1.0


def test_clearing_the_board_forgets_every_run(tmp_path: Path) -> None:
    board = Board(tmp_path / "board.json")
    board.add(kept(TINY))

    board.clear()

    assert board.runs() == []
    assert json.loads(board.path.read_text(encoding="utf-8")) == {"version": VERSION, "runs": []}


def written(path: Path, document: Any) -> Board:
    text = document if isinstance(document, str) else json.dumps(document)
    path.write_text(text, encoding="utf-8")
    return Board(path)


@pytest.mark.parametrize(
    "document",
    [
        "{not json",
        [],
        {"version": VERSION + 1, "runs": []},
        {"version": VERSION, "runs": {"a": 1}},
    ],
    ids=["unreadable", "not an object", "another version", "runs not a list"],
)
def test_a_board_this_cannot_read_is_an_empty_one(
    tmp_path: Path, document: Any, caplog: pytest.LogCaptureFixture
) -> None:
    board = written(tmp_path / "board.json", document)

    with caplog.at_level(logging.WARNING, logger="benchmark.board"):
        assert board.runs() == []

    assert "starting afresh" in caplog.text


def test_a_kept_run_scored_against_another_version_of_its_case_is_left_out(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Editing a case's pages or key makes every score kept against it incomparable."""
    current = kept(TINY)
    stale = kept(LARGE).model_copy(update={"fingerprint": "0" * 16})
    retired = kept(PERFECT).model_copy(update={"case": "kettles"})
    board = written(
        tmp_path / "board.json",
        {
            "version": VERSION,
            "runs": [
                run.model_dump(mode="json") for run in (current, stale, retired)
            ] + [{"case": "headphones"}],
        },
    )

    with caplog.at_level(logging.INFO, logger="benchmark.board"):
        assert board.runs() == [current]

    assert "Left out 3 kept run(s)" in caplog.text


def test_a_board_that_cannot_be_written_says_so_and_runs_on(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    blocked = tmp_path / "a-file"
    blocked.write_text("", encoding="utf-8")
    board = Board(blocked / "board.json")

    with caplog.at_level(logging.WARNING, logger="benchmark.board"):
        board.add(kept(TINY))

    assert "Could not keep the board" in caplog.text


# -- python -m benchmark -------------------------------------------------------


def test_the_command_line_compares_several_models_over_every_case(
    ollama: dict[str, Any], capsys: pytest.CaptureFixture
) -> None:
    ollama["behaves"]["sloppy:3b"] = "sloppy"

    code = benchmark_main.main(["--model", "tiny:1b", "--model", "sloppy:3b", "--model", "tiny:1b"])
    printed = capsys.readouterr().out

    assert code == 1, "the sloppy model misses the espresso case's order floor"
    for case in CASES.values():
        assert f"tiny:1b on {case.name} -- {case.title}" in printed
    standings_lines = printed.split("time/case")[1].splitlines()
    assert "1  tiny:1b" in standings_lines[1]
    assert "2  sloppy:3b" in standings_lines[2]
    assert [chat["model"] for chat in ollama["chats"]].count("tiny:1b") == 6, "asked once each"


def test_the_command_line_runs_only_the_cases_named(capsys: pytest.CaptureFixture) -> None:
    code = benchmark_main.main(["--scripted", "perfect", "--case", "laptops", "--case", "laptops"])
    printed = capsys.readouterr().out

    assert code == 0
    assert printed.count("perfect (scripted) on laptops") == 1
    assert "on headphones" not in printed
    assert "laptops" in printed.split("time/case")[0].splitlines()[-1]


def test_the_command_line_names_what_a_run_got_wrong(capsys: pytest.CaptureFixture) -> None:
    benchmark_main.main(["--scripted", "sloppy", "--case", "headphones"])
    printed = capsys.readouterr().out

    assert "5. AudioSite -- price unknown, unrated, 0 quote(s)   [invented]" in printed
    assert "[repeated]" in printed


def test_the_command_line_shows_a_failed_run_and_the_query_it_missed(
    ollama: dict[str, Any], capsys: pytest.CaptureFixture
) -> None:
    ollama["behaves"]["tiny:1b"] = "unreadable"

    code = benchmark_main.main(["--model", "tiny:1b", "--case", "espresso"])
    printed = capsys.readouterr().out

    assert code == 1
    assert "  failed: Ollama at" in printed
    assert "failed" in printed.split("time/case")[1]


def test_the_command_line_lists_a_query_check_that_failed(
    ollama: dict[str, Any], capsys: pytest.CaptureFixture
) -> None:
    ollama["behaves"]["tiny:1b"] = "mute"

    benchmark_main.main(["--model", "tiny:1b", "--case", "laptops"])
    printed = capsys.readouterr().out

    assert "no query: searched with the request" in printed
    assert "    - Answered with a query the run could search with" in printed


def test_the_command_line_skips_a_model_it_cannot_ask_and_scores_the_rest(
    ollama: dict[str, Any], capsys: pytest.CaptureFixture
) -> None:
    ollama["behaves"]["missing:1b"] = ResponseError("model 'missing:1b' not found", 404)

    code = benchmark_main.main(["--model", "missing:1b", "--model", "tiny:1b", "--case", "laptops"])
    captured = capsys.readouterr()

    assert code == 1, "a model left out is not a clean comparison"
    assert "missing:1b: Ollama has no model named 'missing:1b'" in captured.err
    assert "tiny:1b on laptops" in captured.out
    assert [chat["model"] for chat in ollama["chats"]].count("missing:1b") == 1, "asked once"


def test_every_model_the_server_holds_can_be_scored(
    ollama: dict[str, Any], capsys: pytest.CaptureFixture
) -> None:
    """Embedding models are pulled like chat models and cannot answer (ADR-0032)."""
    ollama["tags"] = ["tiny:1b", "nomic-embed-text:latest", "large:12b"]

    code = benchmark_main.main(["--all-models", "--model", "large:12b", "--case", "headphones"])
    printed = capsys.readouterr().out

    assert code == 0
    assert [chat["model"] for chat in ollama["chats"]] == ["large:12b"] * 2 + ["tiny:1b"] * 2
    assert "nomic-embed-text" not in printed


def test_a_server_holding_nothing_that_answers_is_said_to(
    ollama: dict[str, Any], capsys: pytest.CaptureFixture
) -> None:
    ollama["tags"] = ["nomic-embed-text:latest"]

    code = benchmark_main.main(["--all-models"])
    captured = capsys.readouterr()

    assert code == 1
    said = "holds no model that can answer a prompt (holding: nomic-embed-text:latest)"
    assert said in captured.err
    assert captured.out == ""


def test_a_server_that_will_not_list_is_answered_with_its_remedy(
    ollama: dict[str, Any], capsys: pytest.CaptureFixture
) -> None:
    ollama["unlisted"] = ConnectionRefusedError("[Errno 111] Connection refused")

    code = benchmark_main.main(["--all-models", "--base-url", "http://127.0.0.1:9"])
    captured = capsys.readouterr()

    assert code == 1
    assert "Could not reach Ollama at http://127.0.0.1:9" in captured.err
    assert "ollama serve" in captured.err


def test_the_command_line_scores_the_providers_own_model_when_none_is_named(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    asked: list[Contender] = []

    def recorded(contender: Contender, case: Any, **_kwargs: Any) -> CaseRun:
        asked.append(contender)
        return kept(contender, case.name)

    monkeypatch.setattr(benchmark_main, "run_case", recorded)
    benchmark_main.main(["--provider", "vllm", "--case", "headphones"])
    capsys.readouterr()

    assert asked == [Contender.served("vllm")]


def test_the_command_line_keeps_every_run_on_the_board(capsys: pytest.CaptureFixture) -> None:
    benchmark_main.main(["--scripted", "sloppy", "--case", "laptops"])
    printed = capsys.readouterr().out

    (run,) = Board().runs()
    assert (run.contender, run.case) == (SLOPPY, "laptops")
    assert f"Kept on the board at {Board().path}" in printed


def test_the_command_line_keeps_nothing_when_asked_not_to(capsys: pytest.CaptureFixture) -> None:
    benchmark_main.main(["--scripted", "sloppy", "--case", "laptops", "--no-save"])
    printed = capsys.readouterr().out

    assert Board().runs() == []
    assert "Kept on the board" not in printed


def test_the_command_line_writes_the_standings_as_the_page_reads_them(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    target = tmp_path / "standings.json"

    benchmark_main.main(["--scripted", "perfect", "--scripted", "sloppy", "--json", str(target)])
    capsys.readouterr()
    written_out = json.loads(target.read_text(encoding="utf-8"))

    assert [case["name"] for case in written_out["cases"]] == list(CASES)
    assert [row["label"] for row in written_out["standings"]] == [
        "perfect (scripted)",
        "sloppy (scripted)",
    ]
    assert written_out["standings"][1]["score_label"] == "0.605"


# -- --baseline ----------------------------------------------------------------


def test_a_baseline_is_the_standings_json_wrote(tmp_path: Path) -> None:
    """Including the nightly's kept scorecard, which is written the same way (ADR-0072)."""
    target = tmp_path / "before.json"
    write_standings(target, [run_case(SLOPPY, LAPTOPS)], [LAPTOPS])

    baseline = read_baseline(str(target))

    assert set(baseline.runs) == {(SLOPPY.key, "laptops")}
    assert baseline.path == str(target)


@pytest.mark.parametrize(
    ("document", "said"),
    [
        (None, "cannot read"),
        ("{not json", "is not JSON"),
        ({"cases": []}, "holds no standings"),
        ([], "holds no standings"),
    ],
    ids=["missing", "not json", "no standings", "not an object"],
)
def test_a_file_that_holds_no_standings_is_refused_in_a_sentence(
    tmp_path: Path, document: Any, said: str
) -> None:
    target = tmp_path / "before.json"
    if document is not None:
        written_out = document if isinstance(document, str) else json.dumps(document)
        target.write_text(written_out, encoding="utf-8")

    with pytest.raises(ValueError, match=said):
        read_baseline(str(target))


def test_what_a_file_cannot_have_meant_is_passed_over(tmp_path: Path) -> None:
    target = tmp_path / "before.json"
    kept_run = {"case": "laptops", "score": 0.5}
    rows = ["junk", {"key": "k", "runs": "none"}, {"key": "k", "runs": [1, {"case": 3}, kept_run]}]
    target.write_text(json.dumps({"standings": rows}), encoding="utf-8")

    assert read_baseline(str(target)).runs == {("k", "laptops"): kept_run}


def test_a_run_is_compared_with_its_baseline_metric_by_metric() -> None:
    """Only what moved, then the query and the time, and a word where the baseline was
    scored against another version of the case."""
    now = run_case(SLOPPY, LAPTOPS, clock=ticking(0.25))
    earlier = json.loads(json.dumps(run_payload(now)))
    earlier.update(score=0.55, seconds=3.0, fingerprint="0" * 16)
    earlier["query"]["score"] = 0.5
    next(metric for metric in earlier["metrics"] if metric["name"] == "figures")["value"] = 0.5

    assert against(now, earlier) == [
        "    score 0.550 -> 0.600 (+0.050)",
        "    figures 0.500 -> 0.667 (+0.167)",
        "    query 0.50 -> 1.00 (+0.50), model time 3.0 s -> 0.5 s",
        "    (the baseline was scored against another version of this case)",
    ]


def test_a_run_nothing_moved_in_says_so() -> None:
    now = run_case(PERFECT, LAPTOPS)

    assert against(now, run_payload(now))[1] == "    no metric moved"


def test_a_baseline_written_before_values_were_kept_compares_its_score() -> None:
    """Its metrics carry labels only, and no line claims nothing moved."""
    earlier = {"case": "laptops", "score": 0.5, "metrics": [{"name": "figures", "label": "x"}]}

    assert against(run_case(PERFECT, LAPTOPS), earlier) == ["    score 0.500 -> 1.000 (+0.500)"]


def test_a_baseline_run_without_a_score_compares_what_it_has() -> None:
    earlier = {"case": "laptops", "metrics": [{"name": "figures", "value": 0.5}]}

    assert against(run_case(PERFECT, LAPTOPS), earlier) == ["    figures 0.500 -> 1.000 (+0.500)"]


def test_a_failed_run_is_compared_as_failed() -> None:
    failed = kept(TINY, failure="answered with something unreadable")

    assert against(failed, {"case": "headphones", "score": 0.5}) == ["    score 0.500 -> failed"]
    assert against(run_case(PERFECT, HEADPHONES), {"failure": "unreadable", "score": 0}) == [
        "    score failed -> 1.000"
    ]


def test_the_command_line_compares_its_runs_with_a_baseline(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """The maintainer's question -- did that change make it better? -- with the run from
    before kept somewhere the board's latest-run-only rule cannot reach (ADR-0075)."""
    target = tmp_path / "before.json"
    benchmark_main.main(["--scripted", "sloppy", "--case", "laptops", "--json", str(target)])
    capsys.readouterr()

    code = benchmark_main.main(
        ["--scripted", "sloppy", "--scripted", "perfect", "--case", "laptops",
         "--baseline", str(target)]
    )
    printed = capsys.readouterr().out

    assert code == 0
    assert f"\nAgainst {target}:\n  sloppy (scripted) on laptops:\n" in printed
    assert "    score 0.600 -> 0.600 (+0.000)\n    no metric moved\n" in printed
    assert "  perfect (scripted) on laptops: not in the baseline" in printed


def test_the_command_line_refuses_a_baseline_it_cannot_read(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    with pytest.raises(SystemExit) as stopped:
        benchmark_main.main(["--baseline", str(tmp_path / "nope.json")])

    assert stopped.value.code == 2
    assert "argument --baseline: cannot read" in capsys.readouterr().err
