"""The report the Saturday mutation run publishes, and the floor it fails under.

`scripts/mutation_report.py` decides whether a mutation run passes, which by the
rule the rest of this codebase follows -- whatever decides the answer belongs
where it is testable -- puts it here rather than in the workflow's shell.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from scripts.mutation_report import (
    FLOOR,
    main,
    module_of,
    parse,
    readable,
    report,
    score,
)

RESULTS = """\
    buy_agent.ranking.x_rank_products__mutmut_1: killed
    buy_agent.ranking.x_rank_products__mutmut_2: survived
    buy_agent.ranking.xǁScorerǁtotal__mutmut_3: timeout
    buy_agent.search.x_search_web__mutmut_1: survived
"""


def write(tmp_path: Path, text: str) -> str:
    path = tmp_path / "mutation-results.txt"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_a_results_listing_is_read_as_mutants_without_their_numbers() -> None:
    """Two mutants of one function are one place a test is missing, not two."""
    assert parse(RESULTS)[:2] == [
        ("buy_agent.ranking.x_rank_products", "killed"),
        ("buy_agent.ranking.x_rank_products", "survived"),
    ]


def test_anything_that_is_not_a_result_line_is_ignored() -> None:
    """mutmut prints progress and warnings into the same stream."""
    assert parse("Running mutation testing\n\n15.23 mutations/second\n") == []


def test_a_status_with_spaces_in_it_survives_the_parse() -> None:
    assert parse("    buy_agent.api.x__int__mutmut_4: no tests") == [
        ("buy_agent.api.x__int", "no tests")
    ]


def test_a_mutant_belongs_to_the_module_its_name_starts_with() -> None:
    assert module_of("buy_agent.ranking.x_rank_products") == "buy_agent.ranking"


@pytest.mark.parametrize(
    "mutant, expected",
    [
        ("buy_agent.ranking.x_rank_products", "buy_agent.ranking.rank_products"),
        ("buy_agent.agent.xǁBuyAgentǁrun", "buy_agent.agent.BuyAgent.run"),
        ("buy_agent.server.xǁHandlerǁ_send_bytes", "buy_agent.server.Handler._send_bytes"),
        ("buy_agent.api.x__int", "buy_agent.api._int"),
    ],
)
def test_mutmut_name_mangling_is_undone_for_the_reader(mutant: str, expected: str) -> None:
    """The name in the report should be the name in the file."""
    assert readable(mutant) == expected


def test_a_timeout_counts_as_caught_and_a_survivor_does_not() -> None:
    """A mutant that sent the tests into a loop is one the tests reacted to."""
    assert score(Counter({"killed": 1, "timeout": 1})) == 100.0
    assert score(Counter({"killed": 1, "survived": 1})) == 50.0


def test_mutants_that_were_never_tested_are_left_out_of_the_score() -> None:
    """A skipped mutant says nothing about the suite either way."""
    counted = Counter({"killed": 1, "survived": 1, "skipped": 98})

    assert score(counted) == 50.0


def test_a_run_with_nothing_tested_has_no_score() -> None:
    assert score(Counter({"skipped": 3})) is None


def test_the_report_names_the_score_the_modules_and_the_survivors() -> None:
    lines, _passed = report(parse(RESULTS))
    text = "\n".join(lines)

    assert "4 mutants, 2 caught, 2 survived: a score of 50.0%" in text
    assert "| `buy_agent.ranking` | 3 | 2 | 1 | 66.7% |" in text
    assert "- `buy_agent.search.search_web` -- 1" in text


def test_the_worst_module_is_the_first_row() -> None:
    """The table is read for where the next test goes, so it is sorted that way."""
    lines, _passed = report(parse(RESULTS))
    rows = [line for line in lines if line.startswith("| `buy_agent.")]

    assert rows[0].startswith("| `buy_agent.search`")


def test_a_run_that_caught_everything_says_so_rather_than_listing_nothing() -> None:
    lines, passed = report(parse("    buy_agent.ranking.x_rank__mutmut_1: killed"))

    assert passed
    assert "Every mutant was caught." in lines


def test_a_score_under_the_floor_fails_the_run(tmp_path: Path, capsys) -> None:
    assert main([write(tmp_path, RESULTS)]) == 1
    assert f"which is under the {FLOOR:.0f}% floor" in capsys.readouterr().out


def test_a_score_over_the_floor_passes_it(tmp_path: Path, capsys) -> None:
    killed = "".join(f"    buy_agent.ranking.x_rank__mutmut_{n}: killed\n" for n in range(100))

    assert main([write(tmp_path, killed)]) == 0
    assert f"which clears the {FLOOR:.0f}% floor" in capsys.readouterr().out


def test_a_run_that_produced_no_results_fails_rather_than_reporting_success(
    tmp_path: Path, capsys
) -> None:
    """An empty listing means the run died, not that there was nothing to test."""
    assert main([write(tmp_path, "")]) == 1
    assert "no results at all" in capsys.readouterr().out


def test_a_missing_results_file_is_told_apart_from_a_failed_run(tmp_path: Path, capsys) -> None:
    assert main([str(tmp_path / "absent.txt")]) == 2
    assert "no mutmut results at" in capsys.readouterr().err


def test_the_usage_line_says_what_the_argument_is(capsys) -> None:
    assert main([]) == 2
    assert "usage:" in capsys.readouterr().err
