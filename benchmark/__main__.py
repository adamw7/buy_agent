"""``python -m benchmark`` -- score local models on every case and print the standings."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from buy_agent.agent import ModelUnavailableError
from buy_agent.config import DEFAULT_PROVIDER, AgentConfig
from buy_agent.logging_setup import configure_logging
from buy_agent.providers import PROVIDERS
from benchmark.board import Board
from benchmark.cases import CASES, SCRIPTS
from benchmark.compare import (
    CaseRun,
    Contender,
    Standing,
    run_case,
    seconds_label,
    standings,
    standings_payload,
)

if TYPE_CHECKING:
    from benchmark.cases import Case


def build_parser() -> argparse.ArgumentParser:
    """The command line. Every default comes from the agent's own config."""
    parser = argparse.ArgumentParser(
        prog="python -m benchmark",
        description=(
            "Score local models on what this agent asks of one -- a search query, then "
            "the products on a fixed set of pages -- over every case: "
            + "; ".join(f"{case.name} ({case.request!r})" for case in CASES.values())
            + ". Nothing touches the web."
        ),
    )
    add = parser.add_argument
    add("--model", action="append", default=[], metavar="MODEL",
        help="A model to score, repeatable (default: the provider's own, when no "
             "--scripted or --all-models is given).")
    add("--all-models", action="store_true",
        help="Score every model the server holds that can answer a prompt.")
    add("--scripted", action="append", default=[], choices=sorted(SCRIPTS),
        help="Score a hand-written answer, beside the models or instead of them: no "
             "network, and 'perfect' scores 1.000 by construction.")
    add("--case", action="append", default=[], choices=list(CASES),
        help=f"A case to run, repeatable (default: all of them, {', '.join(CASES)}).")
    add("--provider", choices=sorted(PROVIDERS), default=DEFAULT_PROVIDER,
        help="Which model server to score (default: %(default)s).")
    add("--base-url", default="", help="Where it listens, empty for its own default.")
    add("--json", type=Path, help="Also write the standings, every run included, to this file.")
    add("--no-save", action="store_true",
        help="Keep nothing (default: keep every run on the board that python -m "
             "benchmark.server shows).")
    add("-v", "--verbose", action="store_true", help="Show each run's progress log.")
    return parser


def _contenders(args: argparse.Namespace) -> list[Contender]:
    """Who the arguments name, scripts first.

    Raises:
        ModelUnavailableError: if ``--all-models`` could not ask the server what it has.
    """
    scripted = [Contender.scripted(script) for script in dict.fromkeys(args.scripted)]
    models = list(dict.fromkeys(args.model))
    if args.all_models:
        config = AgentConfig(provider=args.provider, base_url=args.base_url)
        server = config.model_server
        try:
            installed = server.installed(config)
        except Exception as exc:  # whatever the listing raises means "not there"
            raise ModelUnavailableError(server.hint(config, exc)) from exc
        answering = [model.name for model in installed if model.completion]
        if not answering:
            raise ModelUnavailableError(
                f"{server.label} at {config.base_url} holds no model that can answer a "
                f"prompt (holding: {', '.join(m.name for m in installed) or 'none'})."
            )
        models += [name for name in answering if name not in models]
    elif not models and not scripted:
        # The provider's own default, as every run without a model is.
        models = [""]
    return scripted + [Contender.served(args.provider, model, args.base_url) for model in models]


def describe(run: CaseRun, case: Case) -> str:
    """One run: its scorecard, the query it searched with, and the products it reported."""
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
    if run.products:
        lines.append("")
        lines += [
            f"  {product.rank}. {product.line}"
            + ("" if product.verdict == "real" else f"   [{product.verdict}]")
            for product in run.products
        ]
    return "\n".join(lines)


def standings_table(rows: list[Standing], cases: list[Case]) -> str:
    """The standings as lines: rank, score, a column per case, the query and the time."""
    width = max(len("model"), *(len(row.contender.label) for row in rows))
    columns = [max(len(case.name), 7) for case in cases]
    header = (
        f"  {'#':>2}  {'model':<{width}}  {'score':>6}  "
        + "  ".join(f"{case.name:>{column}}" for case, column in zip(cases, columns, strict=True))
        + f"  {'query':>6}  {'time/case':>10}"
    )
    lines = [header]
    for row in rows:
        by_case = {run.case: run for run in row.runs}
        cells = [
            "not run" if (run := by_case.get(case.name)) is None
            else "failed" if run.failure is not None
            else f"{run.score:.3f}"
            for case in cases
        ]
        lines.append(
            f"  {row.rank:>2}  {row.contender.label:<{width}}  {row.score:>6.3f}  "
            + "  ".join(f"{cell:>{column}}" for cell, column in zip(cells, columns, strict=True))
            + f"  {row.query:>6.2f}  {seconds_label(row.seconds):>10}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Run the comparison. Returns 0 when every run finished and cleared every floor,
    1 otherwise."""
    args = build_parser().parse_args(argv)
    configure_logging(verbose=args.verbose)
    if not args.verbose:
        # The runs' own narration and their top-3 reports are the agent's output, not
        # the benchmark's; quiet unless asked, so the scorecards are what a shell
        # redirect catches. ``-v`` puts both back.
        logging.getLogger("buy_agent").setLevel(logging.WARNING)

    cases = [CASES[name] for name in dict.fromkeys(args.case)] or list(CASES.values())
    try:
        contenders = _contenders(args)
    except ModelUnavailableError as exc:
        print(exc, file=sys.stderr)
        return 1

    board = None if args.no_save else Board()
    runs: list[CaseRun] = []
    unasked = False
    for contender in contenders:
        for case in cases:
            try:
                run = run_case(contender, case)
            except ModelUnavailableError as exc:
                # One of ``BuyAgent.run``'s three failures (ADR-0009) and the only one
                # reachable from here: the pages are served rather than searched, and
                # every request is a constant. Its other cases would fail the same way.
                print(f"{contender.label}: {exc}", file=sys.stderr)
                unasked = True
                break
            runs.append(run)
            if board is not None:
                board.add(run)
            print(describe(run, case), end="\n\n")

    if not runs:
        return 1
    print(standings_table(standings(runs, cases), cases))
    if board is not None:
        print(f"\nKept on the board at {board.path}; python -m benchmark.server shows it.")
    if args.json:
        args.json.write_text(
            json.dumps(standings_payload(runs, cases), indent=2), encoding="utf-8"
        )
    cleared = all(run.scorecard is not None and run.scorecard.cleared for run in runs)
    return 0 if cleared and not unasked else 1


if __name__ == "__main__":
    raise SystemExit(main())
