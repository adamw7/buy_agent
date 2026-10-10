"""Several models over several cases: which one does what this agent asks of a model
best, and how long it takes (ADR-0070)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from statistics import fmean
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from buy_agent.agent import ModelUnavailableError, every_step_passes
from buy_agent.chat import UnreadableAnswerError, release
from buy_agent.config import AgentConfig
from buy_agent.models import ProductList, SearchQuery
from buy_agent.providers import PROVIDERS
from benchmark import pipeline
from benchmark.cases import CASES, SCRIPTS
from benchmark.query import QueryVerdict, judge_query
from benchmark.runner import run_benchmark
from benchmark.scoring import FLOORS, MEANINGS, METRICS, Scorecard, match_products

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence
    from pathlib import Path

    from buy_agent.agent import Checkpoint
    from buy_agent.chat import ChatModel, Message, SchemaT
    from buy_agent.models import RankedProduct
    from benchmark.cases import Case
    from benchmark.runner import Report

STEPS: dict[type, str] = {SearchQuery: "query", ProductList: "extract"}


class Contender(BaseModel):
    """Whoever is being scored: a model on a server, or a hand-written answer."""

    model_config = ConfigDict(frozen=True)

    provider: str = ""
    model: str = ""
    base_url: str = ""
    #: One of :data:`benchmark.cases.SCRIPTS`.
    script: str = ""

    @classmethod
    def served(cls, provider: str, model: str = "", base_url: str = "") -> Contender:
        """A model on a server, its blanks filled as a run fills them, so two spellings
        of one model are one contender."""
        config = AgentConfig(provider=provider, model=model, base_url=base_url)
        return cls(provider=config.provider, model=config.model, base_url=config.base_url)

    @classmethod
    def scripted(cls, script: str) -> Contender:
        if script not in SCRIPTS:
            raise ValueError(f"Unknown script {script!r}; expected one of {', '.join(SCRIPTS)}.")
        return cls(script=script)

    @property
    def key(self) -> str:
        if self.script:
            return f"script:{self.script}"
        return f"{self.provider}:{self.model}@{self.base_url}"

    @property
    def label(self) -> str:
        return f"{self.script} (scripted)" if self.script else self.model

    @property
    def where(self) -> str:
        if self.script:
            return "A hand-written answer; no model is asked."
        server = PROVIDERS.get(self.provider)
        return f"{server.label if server else self.provider} at {self.base_url}"

    def settings(self) -> dict[str, str]:
        if self.script:
            return {}
        return {"provider": self.provider, "model": self.model, "base_url": self.base_url}

    def model_for(self, case: Case, config: AgentConfig) -> ChatModel:
        if self.script:
            return case.scripted(self.script)
        return config.model_server.chat_model(config)

    def build_on(self, config: AgentConfig) -> str:
        """The digest its server lists for its model (ADR-0075), or ""."""
        if self.script:
            return ""
        try:
            installed = config.model_server.installed(config)
        # Whatever the listing raises says nothing about the build.
        except Exception:  # pylint: disable=broad-exception-caught
            return ""
        # Ollama lists a tag asked for bare ("llama3.2") as ":latest".
        named = {config.model, f"{config.model}:latest"}
        return next((model.digest for model in installed if model.name in named), "")


class Stopwatch:
    """A chat model, timed, keeping the query it refined."""

    def __init__(self, model: ChatModel, clock: Callable[[], float] = time.perf_counter) -> None:
        self.model = model
        self.clock = clock
        #: Per step, failures included: a timeout is time.
        self.seconds: dict[str, float] = {}
        self.query: str | None = None

    def answer(self, messages: Sequence[Message], schema: type[SchemaT]) -> SchemaT:
        step = STEPS.get(schema, schema.__name__)
        started = self.clock()
        try:
            answer = self.model.answer(messages, schema)
        finally:
            self.seconds[step] = self.seconds.get(step, 0.0) + self.clock() - started
        if isinstance(answer, SearchQuery):
            self.query = answer.query
        return answer


class Reported(BaseModel):
    """One product as a run reported it, and what the key makes of it."""

    rank: int
    name: str
    price: str
    rating: str
    quotes: int
    #: What the key calls it.
    matches: str | None = None
    #: :data:`~benchmark.scoring.REAL`, ``REPEATED`` or ``INVENTED``.
    verdict: str

    @property
    def line(self) -> str:
        return f"{self.name} -- {self.price}, {self.rating}, {self.quotes} quote(s)"


class CaseRun(BaseModel):
    """One contender over one case: the scorecard's counts, the query, the time taken."""

    contender: Contender
    case: str
    fingerprint: str
    #: In UTC.
    finished: str
    query: QueryVerdict
    seconds: dict[str, float] = {}
    #: ``None`` where the run failed.
    counts: dict[str, tuple[int, int]] | None = None
    invented: int = 0
    repeated: int = 0
    #: The model answered with something unreadable.
    failure: str | None = None
    products: list[Reported] = []
    #: :func:`benchmark.pipeline.code` (ADR-0075); "" for a run kept before it was.
    pipeline: str = ""
    settings: dict[str, Any] = {}
    build: str = ""

    @property
    def scorecard(self) -> Scorecard | None:
        """On today's weights."""
        if self.counts is None:
            return None
        return Scorecard(
            counts=dict(self.counts), invented=self.invented, repeated=self.repeated
        )

    @property
    def score(self) -> float:
        """0.0 for a run that failed."""
        card = self.scorecard
        return card.score if card else 0.0

    @property
    def model_seconds(self) -> float:
        return sum(self.seconds.values())


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def run_case(
    contender: Contender,
    case: Case,
    *,
    clock: Callable[[], float] = time.perf_counter,
    checkpoint: Checkpoint = every_step_passes,
) -> CaseRun:
    """Run one contender over one case and score what it reported. A model that could
    not be asked raises ``ModelUnavailableError``; one that answered unreadably is a
    failed run."""
    config = case.settings(**contender.settings())
    watched = Stopwatch(contender.model_for(case, config), clock)
    try:
        report = run_benchmark(llm=watched, config=config, case=case, checkpoint=checkpoint)
    except ModelUnavailableError as exc:
        if not isinstance(exc.__cause__, UnreadableAnswerError):
            raise
        return _kept(contender, case, config, watched, failure=str(exc))
    finally:
        # The agent releases only a model it opened, and this one was handed in.
        release(watched.model)
    return scored_run(contender, case, config, watched, report)


def scored_run(
    contender: Contender, case: Case, config: AgentConfig, watched: Stopwatch, report: Report
) -> CaseRun:
    """A finished run as it is kept; the nightly builds its own this way (ADR-0072)."""
    card = report.scorecard
    return _kept(
        contender,
        case,
        config,
        watched,
        counts=card.counts,
        invented=card.invented,
        repeated=card.repeated,
        products=reported(report.ranked, case),
    )


def _kept(
    contender: Contender, case: Case, config: AgentConfig, watched: Stopwatch, **outcome: Any
) -> CaseRun:
    """With what it was scored under (ADR-0075)."""
    return CaseRun(
        contender=contender,
        case=case.name,
        fingerprint=case.fingerprint,
        finished=_now(),
        query=judge_query(watched.query, case.request, case.query),
        seconds=dict(watched.seconds),
        pipeline=pipeline.code(),
        settings=pipeline.settings(config),
        build=contender.build_on(config),
        **outcome,
    )


def settings_now(run: CaseRun) -> dict[str, Any] | None:
    """The settings this checkout would rerun ``run`` with, or None where it cannot."""
    case = CASES.get(run.case)
    if case is None:
        return None
    try:
        return pipeline.settings(case.settings(**run.contender.settings()))
    except ValueError:
        return None


def scored_under(run: CaseRun) -> list[str]:
    """Why ``run`` is no comparison with one this checkout would make (ADR-0075)."""
    reasons = []
    if run.pipeline != pipeline.code():
        reasons.append(
            "Scored under another version of the pipeline: its code, prompts or scoring "
            "have changed since."
        )
    now = settings_now(run)
    if now is not None and run.settings != now:
        changed = [name for name in now if run.settings.get(name) != now[name]]
        changed += [name for name in run.settings if name not in now]
        reasons.append(
            "Scored with "
            + ", ".join(pipeline.setting_label(name, run.settings.get(name)) for name in changed)
            + "; this checkout runs "
            + ", ".join(pipeline.setting_label(name, now.get(name)) for name in changed)
            + "."
        )
    return reasons


def reported(ranked: Sequence[RankedProduct], case: Case) -> list[Reported]:
    products = [entry.product for entry in ranked]
    return [
        Reported(
            rank=entry.rank,
            name=entry.product.name,
            price=entry.product.price_label(),
            rating=entry.product.rating_label(),
            quotes=len(entry.product.opinions),
            matches=expected.name if expected else None,
            verdict=verdict,
        )
        for entry, (expected, verdict) in zip(
            ranked, match_products(products, case.key), strict=True
        )
    ]


@dataclass(frozen=True, slots=True)
class Standing:
    """One contender's place on the board, over the cases it has run."""

    rank: int
    contender: Contender
    #: Its latest run of each case.
    runs: tuple[CaseRun, ...]

    @property
    def score(self) -> float:
        return fmean(run.score for run in self.runs)

    @property
    def query(self) -> float:
        return fmean(run.query.score for run in self.runs)

    @property
    def seconds(self) -> float:
        return fmean(run.model_seconds for run in self.runs)

    @property
    def failed(self) -> int:
        return sum(run.failure is not None for run in self.runs)

    @property
    def stale(self) -> int:
        """Runs scored under another pipeline or other settings (ADR-0075)."""
        return sum(bool(scored_under(run)) for run in self.runs)

    @property
    def notes(self) -> list[str]:
        notes = []
        if stale := self.stale:
            notes.append(
                f"{stale} of its {len(self.runs)} run(s) scored under another version of "
                "the pipeline or with other settings: run them again to compare."
            )
        if len(builds := {run.build for run in self.runs if run.build}) > 1:
            notes.append(f"Its runs were scored on {len(builds)} builds of this model.")
        return notes


def standings(runs: Iterable[CaseRun], cases: Sequence[Case]) -> list[Standing]:
    """Each contender's latest run of each case, ranked: more cases run first (a mean
    over fewer is no comparison), then score, query and time."""
    latest: dict[Contender, dict[str, CaseRun]] = {}
    for run in runs:
        latest.setdefault(run.contender, {})[run.case] = run
    unranked = [
        Standing(rank=0, contender=contender, runs=ran)
        for contender, by_case in latest.items()
        if (ran := tuple(by_case[case.name] for case in cases if case.name in by_case))
    ]
    unranked.sort(
        key=lambda standing: (
            -len(standing.runs),
            -standing.score,
            -standing.query,
            standing.seconds,
            standing.contender.label,
        )
    )
    return [replace(standing, rank=rank) for rank, standing in enumerate(unranked, 1)]


# -- what the page and ``--json`` are given --------------------------------------


def seconds_label(seconds: float) -> str:
    """As both doors write it: "4.2 s", "3 min 07 s"."""
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes} min {rest:02d} s"


def finished_label(finished: str) -> str:
    """To the minute: "2026-10-02 09:05 UTC"."""
    try:
        when = datetime.fromisoformat(finished)
    except ValueError:
        return finished
    return when.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def case_payload(case: Case) -> dict[str, Any]:
    return {
        "name": case.name,
        "title": case.title,
        "asks": case.asks,
        "request": case.request,
        "pages": len(case.pages),
        "slots": case.num_products,
    }


def metrics_payload() -> list[dict[str, Any]]:
    return [
        {"name": name, "weight": weight, "floor": FLOORS[name], "means": MEANINGS[name]}
        for name, (weight, _) in METRICS.items()
    ]


def run_payload(run: CaseRun) -> dict[str, Any]:
    """One run, everything the page shows when it is opened."""
    case = CASES[run.case]
    card = run.scorecard
    return {
        "case": case.name,
        "title": case.title,
        "request": case.request,
        "failure": run.failure,
        "score": round(run.score, 4),
        "score_label": "failed" if card is None else f"{card.score:.3f}",
        "cleared": card is not None and card.cleared,
        "summary": None if card is None else card.summary(),
        "parts_label": None if card is None else card.parts_label(),
        "metrics": []
        if card is None
        else [
            {
                "name": name,
                "value": round(value, 4),
                "label": f"{value:.3f}",
                "counts": f"{right} of {out_of}",
                "floor": f"{FLOORS[name]:.2f}",
                "under": value < FLOORS[name],
                "means": MEANINGS[name],
            }
            for name, value in card.metrics.items()
            for right, out_of in [card.counts[name]]
        ],
        "query": {
            "text": run.query.query,
            "score": round(run.query.score, 4),
            "label": f"{run.query.score:.2f}",
            "checks": [check.model_dump() for check in run.query.checks],
        },
        "seconds": round(run.model_seconds, 2),
        "seconds_label": seconds_label(run.model_seconds),
        "steps_label": ", ".join(
            f"{step} {seconds_label(seconds)}" for step, seconds in run.seconds.items()
        ),
        "products": [
            {**product.model_dump(), "line": product.line} for product in run.products
        ],
        "finished": run.finished,
        "finished_label": finished_label(run.finished),
        "scored_with_label": scored_with(run),
        "fingerprint": run.fingerprint,
        "pipeline": run.pipeline,
        "pipeline_note": " ".join(scored_under(run)) or None,
        "build": run.build,
    }


def build_label(build: str) -> str:
    """A digest as ``ollama list`` shortens one."""
    return build.removeprefix("sha256:")[:12]


def scored_with(run: CaseRun) -> str:
    """When a run finished, with what, and on which build."""
    label = f"Ran {finished_label(run.finished)}"
    if run.settings:
        label += f" with {pipeline.settings_label(run.settings)}"
    if run.build:
        label += f", on build {build_label(run.build)}"
    return label


def _cell(run: CaseRun | None) -> dict[str, str]:
    if run is None:
        return {"state": "missing", "label": "not run"}
    if run.failure is not None:
        return {"state": "failed", "label": "failed"}
    return {"state": "scored", "label": f"{run.score:.3f}"}


def standing_payload(standing: Standing, cases: Sequence[Case]) -> dict[str, Any]:
    contender = standing.contender
    by_case = {run.case: run for run in standing.runs}
    return {
        "rank": standing.rank,
        "key": contender.key,
        "label": contender.label,
        "where": contender.where,
        "reference": bool(contender.script),
        "score_label": f"{standing.score:.3f}",
        "query_label": f"{standing.query:.2f}",
        "seconds_label": seconds_label(standing.seconds),
        "ran_label": f"{len(standing.runs)} of {len(cases)}",
        "complete": len(standing.runs) == len(cases),
        "failed": standing.failed,
        "current": not standing.stale,
        "notes": standing.notes,
        "cells": [{"case": case.name, **_cell(by_case.get(case.name))} for case in cases],
        "runs": [run_payload(run) for run in standing.runs],
    }


def standings_payload(runs: Iterable[CaseRun], cases: Sequence[Case]) -> dict[str, Any]:
    """The leaderboard over ``cases``: what ``--json`` writes and the page draws."""
    return {
        "cases": [case_payload(case) for case in cases],
        "standings": [
            standing_payload(standing, cases) for standing in standings(runs, cases)
        ],
    }


def write_standings(path: Path, runs: Iterable[CaseRun], cases: Sequence[Case]) -> None:
    """:func:`standings_payload`, to a file: ``--json``, and what the nightly keeps."""
    path.write_text(json.dumps(standings_payload(runs, cases), indent=2), encoding="utf-8")


# -- what a log is given -------------------------------------------------------


def describe(run: CaseRun, case: Case) -> str:
    """One run's scorecard, query and products, for a log."""
    heading = f"{run.contender.label} on {case.name} -- {case.title}"
    timing = ", ".join(f"{step} {seconds_label(took)}" for step, took in run.seconds.items())
    query = run.query.query
    lines = [heading, "-" * len(heading)]
    card = run.scorecard
    if card is None:
        lines.append(f"  failed: {run.failure}")
    else:
        lines.append(card.table())
    lines += [
        f"  query        {run.query.score:>6.3f}   "
        + (f"searched {query!r}" if query else "no query: searched with the request"),
        *(f"    - {check.check}" for check in run.query.checks if not check.passed),
        f"  model time   {seconds_label(run.model_seconds)}" + (f" ({timing})" if timing else ""),
    ]
    if run.build:
        lines.append(f"  build        {build_label(run.build)}")
    if run.products:
        lines.append("")
        lines += [
            f"  {product.rank}. {product.line}"
            + ("" if product.verdict == "real" else f"   [{product.verdict}]")
            for product in run.products
        ]
    return "\n".join(lines)


def summary_markdown(run: CaseRun, case: Case) -> str:
    """One run as a job's summary page renders it (ADR-0072)."""
    score = "failed" if run.scorecard is None else f"{run.score:.3f}"
    return (
        f"### {run.contender.label} on {case.name}: {score}\n\n"
        f"```text\n{describe(run, case)}\n```\n"
    )


__all__ = [
    "STEPS",
    "CaseRun",
    "Contender",
    "Reported",
    "Standing",
    "Stopwatch",
    "build_label",
    "case_payload",
    "describe",
    "finished_label",
    "metrics_payload",
    "reported",
    "run_case",
    "run_payload",
    "scored_run",
    "scored_under",
    "scored_with",
    "seconds_label",
    "settings_now",
    "standing_payload",
    "standings",
    "standings_payload",
    "summary_markdown",
    "write_standings",
]
