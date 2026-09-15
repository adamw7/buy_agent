"""Turn a mutmut run into a report, and hold a floor under the mutation score."""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

# Below this, the run fails.
FLOOR = 75.0

# A mutant is caught when the suite reacts to it at all.
CAUGHT = frozenset({"killed", "timeout", "caught by type check"})

# Mutants that were never put to the tests, and so say nothing about them.
UNCHECKED = frozenset({"skipped", "not checked"})

# ``    buy_agent.ranking.x_rank__mutmut_12: survived``
_RESULT = re.compile(r"^\s*(?P<mutant>[\w.]+)__mutmut_\d+: (?P<status>.+)$")

_ROWS = 25


def parse(text: str) -> list[tuple[str, str]]:
    """Every mutant in a results listing, as ``(name without its number, status)``."""
    return [
        (match.group("mutant"), match.group("status"))
        for match in map(_RESULT.match, text.splitlines())
        if match
    ]


def module_of(mutant: str) -> str:
    """``buy_agent.ranking.x_rank`` -> ``buy_agent.ranking``."""
    return mutant.rpartition(".")[0]


def readable(mutant: str) -> str:
    """Undo mutmut's name mangling, which is what a reader trips over."""
    module, _, name = mutant.rpartition(".")
    name = name.removeprefix("x")
    if name[:1] in ("_", "ǁ"):
        name = name[1:]
    return f"{module}.{name.replace('ǁ', '.')}"


def caught(statuses: Counter[str]) -> int:
    """How many of these mutants the suite reacted to."""
    return sum(count for status, count in statuses.items() if status in CAUGHT)


def score(statuses: Counter[str]) -> float | None:
    """Caught mutants as a percentage of the ones that were actually tested."""
    checked = sum(count for status, count in statuses.items() if status not in UNCHECKED)
    if not checked:
        return None
    return 100.0 * caught(statuses) / checked


def percentage(value: float | None) -> str:
    return "--" if value is None else f"{value:.1f}%"


def module_table(results: list[tuple[str, str]]) -> list[str]:
    """A row per module, worst score first -- where the next test should go."""
    statuses: dict[str, Counter[str]] = {}
    for mutant, status in results:
        statuses.setdefault(module_of(mutant), Counter())[status] += 1

    lines = [
        "| Module | Mutants | Caught | Survived | Score |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    worst_first = sorted(statuses.items(), key=lambda item: (score(item[1]) or 0.0, item[0]))
    for module, counted in worst_first:
        lines.append(
            f"| `{module}` | {counted.total()} | {caught(counted)} "
            f"| {counted['survived']} | {percentage(score(counted))} |"
        )
    return lines


def survivor_list(results: list[tuple[str, str]]) -> list[str]:
    """The functions the survivors cluster in, the thickest cluster first."""
    survivors = Counter(mutant for mutant, status in results if status == "survived")
    if not survivors:
        return ["Every mutant was caught."]

    lines = ["<details><summary>Survivors by function</summary>", ""]
    for mutant, count in survivors.most_common(_ROWS):
        lines.append(f"- `{readable(mutant)}` -- {count}")
    if len(survivors) > _ROWS:
        lines.append(f"- ... and {len(survivors) - _ROWS} more functions, in the run's artifact")
    lines += ["", "</details>"]
    return lines


def report(results: list[tuple[str, str]]) -> tuple[list[str], bool]:
    """The Markdown to publish, and whether the run cleared the floor."""
    statuses = Counter(status for _, status in results)
    total = statuses.total()
    if not total:
        return ["## Mutation testing", "", "The run produced no results at all."], False

    achieved = score(statuses)
    passed = achieved is not None and achieved >= FLOOR
    verdict = "clears" if passed else "is under"
    lines = [
        "## Mutation testing",
        "",
        f"{total} mutants, {caught(statuses)} caught, "
        f"{statuses['survived']} survived: a score of {percentage(achieved)}, "
        f"which {verdict} the {FLOOR:.0f}% floor.",
        "",
    ]
    lines += module_table(results) + [""] + survivor_list(results)
    return lines, passed


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: mutation_report.py <mutmut results --all true output>", file=sys.stderr)
        return 2

    results = Path(argv[0])
    if not results.is_file():
        print(f"no mutmut results at {results}", file=sys.stderr)
        return 2

    lines, passed = report(parse(results.read_text(encoding="utf-8")))
    print("\n".join(lines))
    if not passed:
        print(f"Mutation score is under the {FLOOR:.0f}% floor.", file=sys.stderr)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
