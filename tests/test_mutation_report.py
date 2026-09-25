"""The report the two Saturday mutation runs publish, and the floors they fail under."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from scripts.mutation_report import (
    MUTMUT,
    STRYKER,
    Tool,
    main,
    read_mutmut,
    read_stryker,
    readable,
    report,
    score,
    tool_for,
)

RESULTS = """\
    buy_agent.ranking.x_rank_products__mutmut_1: killed
    buy_agent.ranking.x_rank_products__mutmut_2: survived
    buy_agent.ranking.xǁScorerǁtotal__mutmut_3: timeout
    buy_agent.search.x_search_web__mutmut_1: survived
"""

#: A Stryker report as the `json` reporter writes one, cut to what the report reads.
STRYKER_REPORT = {
    "schemaVersion": "1.0",
    "files": {
        "src/app/app.ts": {
            "language": "typescript",
            "source": "export class App {}",
            "mutants": [
                {"id": "1", "mutatorName": "ConditionalExpression", "status": "Killed"},
                {"id": "2", "mutatorName": "ConditionalExpression", "status": "Survived"},
                {"id": "3", "mutatorName": "StringLiteral", "status": "Timeout"},
            ],
        },
        "src/app/save.ts": {
            "language": "typescript",
            "source": "export function save() {}",
            "mutants": [
                {"id": "4", "mutatorName": "ObjectLiteral", "status": "NoCoverage"},
            ],
        },
    },
}


def write(tmp_path: Path, text: str, name: str = "mutation-results.txt") -> str:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def write_stryker(tmp_path: Path, report_json: dict[str, object]) -> str:
    return write(tmp_path, json.dumps(report_json), "mutation.json")


def test_a_results_listing_is_read_as_mutants_without_their_numbers() -> None:
    """Two mutants of one function are one place a test is missing, not two."""
    assert [(mutant.module, mutant.where) for mutant in read_mutmut(RESULTS)][:2] == [
        ("buy_agent.ranking", "buy_agent.ranking.rank_products"),
        ("buy_agent.ranking", "buy_agent.ranking.rank_products"),
    ]


def test_anything_that_is_not_a_result_line_is_ignored() -> None:
    """mutmut prints progress and warnings into the same stream."""
    assert read_mutmut("Running mutation testing\n\n15.23 mutations/second\n") == []


def test_a_status_with_spaces_in_it_survives_the_parse() -> None:
    assert [mutant.status for mutant in read_mutmut("  buy_agent.api.x__int__mutmut_4: no tests")]\
        == ["no tests"]


def test_a_mutant_belongs_to_the_module_its_name_starts_with() -> None:
    assert read_mutmut(RESULTS)[0].module == "buy_agent.ranking"


@pytest.mark.parametrize(
    "mutant, expected",
    [
        ("buy_agent.ranking.x_rank_products", "buy_agent.ranking.rank_products"),
        ("buy_agent.agent.xǁBuyAgentǁrun", "buy_agent.agent.BuyAgent.run"),
        ("buy_agent.server.xǁHandlerǁ_send_bytes", "buy_agent.server.Handler._send_bytes"),
        ("buy_agent.api.x__int", "buy_agent.api._int"),
        # A name carrying none of that mangling is left alone rather than having a
        # character cut off it: the prefix is what marks a mutant, so a listing that
        # stops writing one is read as the names it actually holds.
        ("buy_agent.ranking.rank_products", "buy_agent.ranking.rank_products"),
    ],
)
def test_mutmut_name_mangling_is_undone_for_the_reader(mutant: str, expected: str) -> None:
    """The name in the report should be the name in the file."""
    assert readable(mutant) == expected


def test_a_stryker_report_is_read_as_a_file_and_the_mutator_that_made_it() -> None:
    """Stryker records a position rather than a function, so the mutator is what a
    cluster of survivors in one file is told apart by."""
    assert read_stryker(json.dumps(STRYKER_REPORT))[:2] == [
        ("src/app/app.ts", "src/app/app.ts -- ConditionalExpression", "Killed"),
        ("src/app/app.ts", "src/app/app.ts -- ConditionalExpression", "Survived"),
    ]


def test_a_stryker_report_with_nothing_mutated_reads_as_no_mutants() -> None:
    """Which is a run that died, and is reported as one rather than as a clean sheet."""
    assert read_stryker(json.dumps({"schemaVersion": "1.0"})) == []


@pytest.mark.parametrize(
    "results, tool",
    [("mutation-results.txt", MUTMUT), ("mutation.json", STRYKER), ("results.log", MUTMUT)],
)
def test_which_tester_wrote_a_run_is_read_off_the_file(results: str, tool: Tool) -> None:
    """One report, two readers: Stryker answers JSON and mutmut a listing, and the
    floors under the two halves are not the same number either."""
    assert tool_for(Path(results)) is tool


def test_a_timeout_counts_as_caught_and_a_survivor_does_not() -> None:
    """A mutant that sent the tests into a loop is one the tests reacted to."""
    assert score(Counter({"killed": 1, "timeout": 1}), MUTMUT) == 100.0
    assert score(Counter({"killed": 1, "survived": 1}), MUTMUT) == 50.0


def test_mutants_that_were_never_tested_are_left_out_of_the_score() -> None:
    """A skipped mutant says nothing about the suite either way."""
    counted = Counter({"killed": 1, "survived": 1, "skipped": 98})

    assert score(counted, MUTMUT) == 50.0


@pytest.mark.parametrize(
    "status, expected", [("NoCoverage", 50.0), ("CompileError", 100.0), ("Ignored", 100.0)]
)
def test_a_mutant_no_test_covers_counts_against_the_score_and_an_unmade_one_does_not(
    status: str, expected: float
) -> None:
    """The other side of that: a mutation Stryker could not make says nothing about the
    specs, while one no spec runs against is precisely what this run is looking for."""
    assert score(Counter({"Killed": 1, status: 1}), STRYKER) == expected


def test_a_run_with_nothing_tested_has_no_score() -> None:
    assert score(Counter({"skipped": 3}), MUTMUT) is None


def test_the_report_names_the_score_the_modules_and_the_survivors() -> None:
    lines, _passed = report(read_mutmut(RESULTS), MUTMUT)
    text = "\n".join(lines)

    assert "4 mutants, 2 caught, 2 survived: a score of 50.0%" in text
    assert "| `buy_agent.ranking` | 3 | 2 | 1 | 66.7% |" in text
    assert "- `buy_agent.search.search_web` -- 1" in text


def test_the_report_says_which_half_of_the_project_was_mutated() -> None:
    """Two runs publish into two job summaries, and a heading that said neither which
    is which would leave the reader to tell them apart by the names in the table."""
    for tool in (MUTMUT, STRYKER):
        assert f"## Mutation testing: `{tool.mutates}`" == report([], tool)[0][0]


def test_a_stryker_run_is_reported_file_by_file() -> None:
    lines, _passed = report(read_stryker(json.dumps(STRYKER_REPORT)), STRYKER)
    text = "\n".join(lines)

    assert "4 mutants, 2 caught, 2 survived: a score of 50.0%" in text
    assert "| File | Mutants | Caught | Survived | Score |" in text
    assert "| `src/app/save.ts` | 1 | 0 | 1 | 0.0% |" in text
    assert "- `src/app/app.ts -- ConditionalExpression` -- 1" in text


def test_the_worst_module_is_the_first_row() -> None:
    """The table is read for where the next test goes, so it is sorted that way."""
    lines, _passed = report(read_mutmut(RESULTS), MUTMUT)
    rows = [line for line in lines if line.startswith("| `buy_agent.")]

    assert rows[0].startswith("| `buy_agent.search`")


def test_only_the_thickest_clusters_of_survivors_are_listed(tmp_path: Path) -> None:
    """A hundred rows is a list nobody reads; the run's own report has the rest."""
    many = {
        "schemaVersion": "1.0",
        "files": {
            f"src/app/{index}.ts": {
                "mutants": [{"mutatorName": "StringLiteral", "status": "Survived"}]
            }
            for index in range(30)
        },
    }
    lines, _passed = report(read_stryker(json.dumps(many)), STRYKER)

    assert sum(line.startswith("- `src/app/") for line in lines) == 25
    assert "- ... and 5 more, in the run's artifact" in lines


def test_a_run_that_caught_everything_says_so_rather_than_listing_nothing() -> None:
    lines, passed = report(read_mutmut("    buy_agent.ranking.x_rank__mutmut_1: killed"), MUTMUT)

    assert passed
    assert "Every mutant was caught." in lines


def test_a_score_under_the_floor_fails_the_run(tmp_path: Path, capsys) -> None:
    assert main([write(tmp_path, RESULTS)]) == 1
    assert f"which is under the {MUTMUT.floor:.0f}% floor" in capsys.readouterr().out


def test_a_score_over_the_floor_passes_it(tmp_path: Path, capsys) -> None:
    killed = "".join(f"    buy_agent.ranking.x_rank__mutmut_{n}: killed\n" for n in range(100))

    assert main([write(tmp_path, killed)]) == 0
    assert f"which clears the {MUTMUT.floor:.0f}% floor" in capsys.readouterr().out


def test_a_stryker_run_is_held_to_the_ui_s_own_floor(tmp_path: Path, capsys) -> None:
    """The two halves are tested by different tools against different suites, so one
    floor over both would be the lower of the two doing nothing for the other."""
    assert main([write_stryker(tmp_path, STRYKER_REPORT)]) == 1
    assert f"which is under the {STRYKER.floor:.0f}% floor" in capsys.readouterr().out


def test_a_run_that_produced_no_results_fails_rather_than_reporting_success(
    tmp_path: Path, capsys
) -> None:
    """An empty listing means the run died, not that there was nothing to test."""
    assert main([write(tmp_path, "")]) == 1
    assert "no results at all" in capsys.readouterr().out


def test_a_run_that_stopped_at_its_baseline_is_not_reported_as_a_clean_sheet(
    tmp_path: Path, capsys
) -> None:
    """What mutmut lists when a test fails on the unmutated code: every mutant, none
    of them tried. The score is undefined, not low -- so the report says nothing was
    tested rather than publishing a column of dashes under "Every mutant was caught",
    which is what the Saturday job summary carried the week that happened."""
    listing = "".join(
        f"    buy_agent.{module}.x_f__mutmut_{n}: not checked\n"
        for module in ("ranking", "search")
        for n in range(3)
    )

    assert main([write(tmp_path, listing)]) == 1
    published = capsys.readouterr().out
    assert "6 mutants and not one of them tested" in published
    assert "Every mutant was caught." not in published
    assert "| `buy_agent." not in published


@pytest.mark.parametrize(
    "name, tool", [("absent.txt", MUTMUT), ("absent.json", STRYKER)]
)
def test_a_missing_results_file_is_told_apart_from_a_failed_run(
    name: str, tool: Tool, tmp_path: Path, capsys
) -> None:
    assert main([str(tmp_path / name)]) == 2
    assert f"no {tool.name} results at" in capsys.readouterr().err


def test_the_usage_line_says_what_the_argument_is(capsys) -> None:
    assert main([]) == 2
    assert "usage:" in capsys.readouterr().err
