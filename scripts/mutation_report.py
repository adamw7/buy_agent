"""Turn a mutmut or Stryker run into the report it publishes -- the score, a row per
module worst first, where survivors cluster -- and hold a floor under it. What differs
per tester is `TOOLS` and nothing else (ADR-0061)."""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple


class Mutant(NamedTuple):
    """One mutant: its row, where a test goes, and how it went."""

    module: str
    where: str
    status: str


# ``    buy_agent.ranking.x_rank__mutmut_12: survived``
_RESULT = re.compile(r"^\s*(?P<mutant>[\w.]+)__mutmut_\d+: (?P<status>.+)$")

_ROWS = 25


def readable(mutant: str) -> str:
    """Undo mutmut's name mangling, which is what a reader trips over."""
    module, _, name = mutant.rpartition(".")
    name = name.removeprefix("x")
    if name[:1] in ("_", "ǁ"):
        name = name[1:]
    return f"{module}.{name.replace('ǁ', '.')}"


def read_mutmut(text: str) -> list[Mutant]:
    """Every mutant in a ``mutmut results --all true`` listing, its number dropped: two
    mutants of one function are one place a test is missing."""
    found = [match for match in map(_RESULT.match, text.splitlines()) if match]
    return [
        Mutant(
            match.group("mutant").rpartition(".")[0],
            readable(match.group("mutant")),
            match.group("status"),
        )
        for match in found
    ]


def read_stryker(text: str) -> list[Mutant]:
    """Every mutant in a Stryker JSON report, filed under its file and mutator: Stryker
    records a position, not a function."""
    report = json.loads(text)
    return [
        Mutant(path, f"{path} -- {mutant['mutatorName']}", mutant["status"])
        for path, mutated in report.get("files", {}).items()
        for mutant in mutated["mutants"]
    ]


@dataclass(frozen=True)
class Tool:
    """One mutation tester, how a run of it reads, and its floor: set where each half
    stands, not where it ought to (ADR-0016)."""

    name: str
    mutates: str
    #: What a row of the table is, and what a cluster of survivors is filed under.
    rows: str
    clusters: str
    floor: float
    #: The suite reacted; it got past (tested or not); it was never put to the tests.
    caught: frozenset[str]
    survived: frozenset[str]
    unchecked: frozenset[str]
    #: How a run of one is told from the other.
    suffix: str
    read: Callable[[str], list[Mutant]]


#: "no tests" is deliberately not unchecked: an uncovered mutant counts against the
#: score, as Stryker's `NoCoverage` does.
MUTMUT = Tool(
    name="mutmut",
    mutates="buy_agent",
    rows="Module",
    clusters="function",
    floor=75.0,
    caught=frozenset({"killed", "timeout", "caught by type check"}),
    survived=frozenset({"survived"}),
    unchecked=frozenset({"skipped", "not checked"}),
    suffix=".txt",
    read=read_mutmut,
)

#: `CompileError` and `RuntimeError` mutants never ran, so they are unchecked.
STRYKER = Tool(
    name="Stryker",
    mutates="ui/src/app",
    rows="File",
    clusters="file and mutator",
    floor=60.0,
    caught=frozenset({"Killed", "Timeout"}),
    survived=frozenset({"Survived", "NoCoverage"}),
    unchecked=frozenset({"Ignored", "Pending", "CompileError", "RuntimeError"}),
    suffix=".json",
    read=read_stryker,
)

TOOLS = (MUTMUT, STRYKER)


def tool_for(results: Path) -> Tool:
    """Which tester wrote a results file: JSON is Stryker's, anything else mutmut's."""
    for tool in TOOLS:
        if results.suffix == tool.suffix:
            return tool
    return MUTMUT


def caught(statuses: Counter[str], tool: Tool) -> int:
    return sum(count for status, count in statuses.items() if status in tool.caught)


def survived(statuses: Counter[str], tool: Tool) -> int:
    return sum(count for status, count in statuses.items() if status in tool.survived)


def score(statuses: Counter[str], tool: Tool) -> float | None:
    """Caught mutants as a percentage of those tested."""
    checked = sum(count for status, count in statuses.items() if status not in tool.unchecked)
    if not checked:
        return None
    return 100.0 * caught(statuses, tool) / checked


def percentage(value: float | None) -> str:
    return "--" if value is None else f"{value:.1f}%"


def module_table(mutants: list[Mutant], tool: Tool) -> list[str]:
    """A row per module, worst score first."""
    statuses: dict[str, Counter[str]] = {}
    for mutant in mutants:
        statuses.setdefault(mutant.module, Counter())[mutant.status] += 1

    lines = [
        f"| {tool.rows} | Mutants | Caught | Survived | Score |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    worst_first = sorted(statuses.items(), key=lambda item: (score(item[1], tool) or 0.0, item[0]))
    for module, counted in worst_first:
        lines.append(
            f"| `{module}` | {counted.total()} | {caught(counted, tool)} "
            f"| {survived(counted, tool)} | {percentage(score(counted, tool))} |"
        )
    return lines


def survivor_list(mutants: list[Mutant], tool: Tool) -> list[str]:
    """Where survivors cluster, the thickest first."""
    survivors = Counter(mutant.where for mutant in mutants if mutant.status in tool.survived)
    if not survivors:
        return ["Every mutant was caught."]

    lines = [f"<details><summary>Survivors by {tool.clusters}</summary>", ""]
    for where, count in survivors.most_common(_ROWS):
        lines.append(f"- `{where}` -- {count}")
    if len(survivors) > _ROWS:
        lines.append(f"- ... and {len(survivors) - _ROWS} more, in the run's artifact")
    lines += ["", "</details>"]
    return lines


def report(mutants: list[Mutant], tool: Tool) -> tuple[list[str], bool]:
    """The Markdown to publish, and whether the run cleared the floor."""
    heading = f"## Mutation testing: `{tool.mutates}`"
    statuses = Counter(mutant.status for mutant in mutants)
    total = statuses.total()
    if not total:
        return [heading, "", "The run produced no results at all."], False

    achieved = score(statuses, tool)
    if achieved is None:
        # Nothing was tested -- not "every mutant was caught": mutmut marks all "not
        # checked" when a test fails on the code as written.
        return [
            heading,
            "",
            f"{total} mutants and not one of them tested, so there is no score to hold "
            f"to the {tool.floor:.0f}% floor. A run that stops before its first mutant "
            "reads like this -- mutmut's does when a test fails on the code as written "
            "-- and the job's log names that test.",
        ], False

    passed = achieved >= tool.floor
    verdict = "clears" if passed else "is under"
    lines = [
        heading,
        "",
        f"{total} mutants, {caught(statuses, tool)} caught, "
        f"{survived(statuses, tool)} survived: a score of {percentage(achieved)}, "
        f"which {verdict} the {tool.floor:.0f}% floor.",
        "",
    ]
    lines += module_table(mutants, tool) + [""] + survivor_list(mutants, tool)
    return lines, passed


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(
            "usage: mutation_report.py <mutmut results --all true output "
            "| Stryker mutation.json>",
            file=sys.stderr,
        )
        return 2

    results = Path(argv[0])
    tool = tool_for(results)
    if not results.is_file():
        print(f"no {tool.name} results at {results}", file=sys.stderr)
        return 2

    lines, passed = report(tool.read(results.read_text(encoding="utf-8")), tool)
    print("\n".join(lines))
    if not passed:
        print(f"Mutation score is under the {tool.floor:.0f}% floor.", file=sys.stderr)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
