"""``python -m benchmark --baseline FILE``: each run beside the same contender's run of the
same case in standings written earlier, metric by metric (ADR-0075).

The board keeps one run per contender and case, so the run before a change to a prompt
is gone once the run after it is kept. A ``--json`` file -- one written by hand, or the
nightly's kept scorecard (ADR-0072) -- is how "did that make it better?" is answered.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from benchmark.compare import seconds_label

if TYPE_CHECKING:
    from collections.abc import Sequence

    from benchmark.compare import CaseRun


@dataclass(frozen=True, slots=True)
class Baseline:
    """The runs one standings file holds, by contender and case."""

    #: Where it was read from, as it was named.
    path: str
    #: Each run as the file has it (:func:`benchmark.compare.run_payload`'s shape), keyed
    #: by its contender's ``key`` and its case's name.
    runs: dict[tuple[str, str], dict[str, Any]]


def read_baseline(path: str) -> Baseline:
    """The standings ``--json`` wrote at ``path``.

    Raises:
        ValueError: naming what is wrong with the file, for the command line to show.
    """
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read {path} ({exc.strerror or exc})") from exc
    except ValueError as exc:
        raise ValueError(f"{path} is not JSON ({exc})") from exc
    standings = document.get("standings") if isinstance(document, dict) else None
    if not isinstance(standings, list):
        raise ValueError(f"{path} holds no standings; python -m benchmark --json writes them")
    runs: dict[tuple[str, str], dict[str, Any]] = {}
    for row in standings:
        if not isinstance(row, dict) or not isinstance(row.get("runs"), list):
            continue
        for run in row["runs"]:
            if isinstance(run, dict) and isinstance(run.get("case"), str):
                runs[(str(row.get("key")), run["case"])] = run
    return Baseline(path=path, runs=runs)


def _number(value: Any) -> float | None:
    """A kept number, or None for anything a file could hold instead."""
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _moved(name: str, then: float, now: float, digits: int = 3) -> str:
    """One figure, then and now: "identified 0.600 -> 0.800 (+0.200)"."""
    return f"{name} {then:.{digits}f} -> {now:.{digits}f} ({now - then:+.{digits}f})"


def against(run: CaseRun, earlier: dict[str, Any]) -> list[str]:
    """What moved between ``earlier`` and ``run``, a line each, indented under the run."""
    card = run.scorecard
    if earlier.get("failure") or card is None:
        then = "failed" if earlier.get("failure") else f"{_number(earlier.get('score')) or 0:.3f}"
        now = "failed" if card is None else f"{run.score:.3f}"
        return [f"    score {then} -> {now}"]

    lines = []
    if (then_score := _number(earlier.get("score"))) is not None:
        lines.append(f"    {_moved('score', then_score, run.score)}")
    values = {
        metric.get("name"): value
        for metric in earlier.get("metrics", [])
        if isinstance(metric, dict) and (value := _number(metric.get("value"))) is not None
    }
    # A file written before the values were kept has only labels, and says nothing here.
    if values:
        moved = [
            _moved(name, before, now)
            for name, now in card.metrics.items()
            if (before := values.get(name)) is not None and round(before, 4) != round(now, 4)
        ]
        lines.append("    " + (", ".join(moved) if moved else "no metric moved"))
    query = earlier.get("query")
    then_query = _number(query.get("score")) if isinstance(query, dict) else None
    then_seconds = _number(earlier.get("seconds"))
    tail = []
    if then_query is not None:
        tail.append(_moved("query", then_query, run.query.score, digits=2))
    if then_seconds is not None:
        tail.append(
            f"model time {seconds_label(then_seconds)} -> {seconds_label(run.model_seconds)}"
        )
    if tail:
        lines.append("    " + ", ".join(tail))
    if earlier.get("fingerprint") not in (None, run.fingerprint):
        lines.append("    (the baseline was scored against another version of this case)")
    return lines


def compared(runs: Sequence[CaseRun], baseline: Baseline) -> str:
    """Every run of ``runs`` beside the baseline's run of the same contender and case."""
    lines = [f"Against {baseline.path}:"]
    for run in runs:
        heading = f"  {run.contender.label} on {run.case}"
        earlier = baseline.runs.get((run.contender.key, run.case))
        if earlier is None:
            lines.append(f"{heading}: not in the baseline")
            continue
        lines.append(f"{heading}:")
        lines += against(run, earlier)
    return "\n".join(lines)


__all__ = ["Baseline", "against", "compared", "read_baseline"]
