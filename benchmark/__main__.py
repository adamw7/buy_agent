"""``python -m benchmark`` -- run the benchmark and print the scorecard.

The metrics against their floors, then the products the run reported, so a score
that moved can be read beside the answer that moved it. ``--json`` keeps the same
numbers as a record, which is how two runs a month apart are compared without
either being repeated. Exits 1 where a floor was missed.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from buy_agent.agent import ModelUnavailableError
from buy_agent.config import DEFAULT_PROVIDER
from buy_agent.logging_setup import configure_logging
from buy_agent.providers import PROVIDERS
from benchmark.corpus import REQUEST, settings
from benchmark.runner import Report, run_benchmark
from benchmark.scoring import FLOORS
from benchmark.scripted import SCRIPTS, ScriptedLLM


def build_parser() -> argparse.ArgumentParser:
    """The command line. Every default comes from the agent's own config."""
    parser = argparse.ArgumentParser(
        prog="python -m benchmark",
        description=f"Score the agent on a fixed corpus. The request is: {REQUEST!r}",
    )
    add = parser.add_argument
    add("--scripted", choices=sorted(SCRIPTS),
        help="Score a hand-written answer instead of a model: no network, and "
             "'perfect' scores 1.000 by construction.")
    add("--provider", choices=sorted(PROVIDERS), default=DEFAULT_PROVIDER,
        help="Which model server to score (default: %(default)s).")
    add("--model", default="", help="Model to score, empty for the provider's own default.")
    add("--base-url", default="", help="Where it listens, empty for its own default.")
    add("--json", type=Path, help="Also write the scorecard to this file.")
    add("-v", "--verbose", action="store_true", help="Show the run's progress log.")
    return parser


def describe(report: Report) -> str:
    """The scorecard, then the products that produced it."""
    products = [
        f"  {entry.rank}. {entry.product.name} -- {entry.product.price_label()}, "
        f"{entry.product.rating_label()}, {len(entry.product.opinions)} quote(s)"
        for entry in report.ranked
    ]
    return "\n".join([report.scorecard.table(), "", *products])


def main(argv: list[str] | None = None) -> int:
    """Run the benchmark. Returns 0 when every floor was cleared, 1 otherwise."""
    args = build_parser().parse_args(argv)
    configure_logging(verbose=args.verbose)
    if not args.verbose:
        # The run's own narration and its top-3 report are the agent's output, not
        # the benchmark's; quiet unless asked, so the scorecard is what a shell
        # redirect catches. ``-v`` puts both back.
        logging.getLogger("buy_agent").setLevel(logging.WARNING)

    llm = ScriptedLLM(SCRIPTS[args.scripted]) if args.scripted else None
    config = settings(provider=args.provider, model=args.model, base_url=args.base_url)
    try:
        report = run_benchmark(llm=llm, config=config)
    except ModelUnavailableError as exc:
        # One of ``BuyAgent.run``'s three failures (ADR-0009) and the only one
        # reachable from here: the corpus is served rather than searched, so
        # ``SearchError`` cannot happen, and the request is a constant, so
        # neither can the ``ValueError`` for an empty one.
        print(exc, file=sys.stderr)
        return 1

    print(describe(report))
    measured = report.scorecard.metrics | {"score": report.scorecard.score}
    if args.json:
        args.json.write_text(json.dumps(measured, indent=2), encoding="utf-8")
    return 0 if all(value >= FLOORS[name] for name, value in measured.items()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
