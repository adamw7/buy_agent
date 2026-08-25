"""Turn a mutmut run into a report, and hold a floor under the mutation score.

`coverage` says which lines ran; it cannot say whether anything would have
noticed had they run differently. Mutation testing answers that by breaking the
code on purpose -- an `and` for an `or`, a `+= 1` for a `= 1` -- and asking
whether the suite fails. A mutant the suite still passes on is a line that is
covered and unchecked, which is exactly what a suite at 100% coverage can no
longer point at (see ADR-0016).

This reads the output of `mutmut results --all true`, one
``    <mutant name>: <status>`` line per mutant, because that is mutmut's only
stable textual view of a finished run; its JSON export carries the totals but not
which mutants they are, and importing mutmut's own state module would tie this to
internals that move between releases.

What comes out is Markdown, for `$GITHUB_STEP_SUMMARY`: the score, a row per
module, and the functions the survivors cluster in -- the answer to "where does
the next test go". The exit code is 1 when the score falls under FLOOR, so that a
module arriving with thin tests fails the Saturday run rather than sitting in an
artifact nobody opens.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

# Below this, the run fails. It sits under the score the suite holds today rather
# than at it: a mutation score moves a little with the mutants mutmut chooses to
# generate, and a floor that fails on noise is a floor nobody keeps.
FLOOR = 75.0

# A mutant is caught when the suite reacts to it at all. A timeout is a reaction:
# the mutant sent the tests into a loop rather than past a missing assertion.
CAUGHT = frozenset({"killed", "timeout", "caught by type check"})

# Mutants that were never put to the tests, and so say nothing about them.
UNCHECKED = frozenset({"skipped", "not checked"})

# ``    buy_agent.ranking.x_rank__mutmut_12: survived``
_RESULT = re.compile(r"^\s*(?P<mutant>[\w.]+)__mutmut_\d+: (?P<status>.+)$")

_ROWS = 25


def parse(text: str) -> list[tuple[str, str]]:
    """Every mutant in a results listing, as ``(name without its number, status)``.

    The number is what makes two mutants of one function distinct; dropping it is
    what lets them be counted together as a place tests are missing.
    """
    return [
        (match.group("mutant"), match.group("status"))
        for match in map(_RESULT.match, text.splitlines())
        if match
    ]


def module_of(mutant: str) -> str:
    """``buy_agent.ranking.x_rank`` -> ``buy_agent.ranking``."""
    return mutant.rpartition(".")[0]


def readable(mutant: str) -> str:
    """Undo mutmut's name mangling, which is what a reader trips over.

    mutmut rewrites each function into numbered copies of itself, so the name it
    reports is not the name in the file: a function gains an ``x`` prefix, and a
    method's class is joined on with ``ǁ`` rather than a dot, which would collide
    with the module path. ``xǁBuyAgentǁrun`` is ``BuyAgent.run``.
    """
    module, _, name = mutant.rpartition(".")
    name = name.removeprefix("x")
    if name[:1] in ("_", "ǁ"):
        name = name[1:]
    return f"{module}.{name.replace('ǁ', '.')}"


def caught(statuses: Counter[str]) -> int:
    """How many of these mutants the suite reacted to."""
    return sum(count for status, count in statuses.items() if status in CAUGHT)


def score(statuses: Counter[str]) -> float | None:
    """Caught mutants as a percentage of the ones that were actually tested.

    ``None`` when nothing was: an empty run has no score, and reporting 0% or
    100% for one would read as a verdict on the suite.
    """
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
