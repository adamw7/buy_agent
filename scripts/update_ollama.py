"""Re-pull the models Ollama has, and say which of them actually moved."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from ollama import Client, ResponseError

from buy_agent.providers import OLLAMA

#: Transport failures that mean "the server is not there": the tuple ``BuyAgent._invoke``
#: catches, read off the provider's own row rather than written down again -- what a
#: stopped Ollama raises is that row's to say (ADR-0029), and it is wider than it looks.
UNREACHABLE = tuple(
    failure for failure in OLLAMA.transport_errors if failure is not ResponseError
)

#: What a status reads as in the report, in the order the summary lists them.
LABELS = {
    "updated": "updated",
    "installed": "installed",
    "current": "already current",
    "failed": "failed",
}


@dataclass(frozen=True, slots=True)
class Outcome:
    """One pull: the digests either side of it, or why there is no second one."""

    model: str
    before: str
    after: str
    error: str = ""


def status(outcome: Outcome) -> str:
    """``failed``, ``installed``, ``updated`` or ``current``."""
    if outcome.error:
        return "failed"
    if not outcome.before:
        return "installed"
    return "current" if outcome.after == outcome.before else "updated"


def short(digest: str) -> str:
    """``sha256:3f2a1b9c...`` -> ``3f2a1b9c1d2e``, which is what identifies a build."""
    return digest.removeprefix("sha256:")[:12] or "unknown"


def digests(client: Any) -> dict[str, str]:
    """Every tag Ollama has pulled, and the digest it holds for each."""
    return {model.model: model.digest or "" for model in client.list().models if model.model}


def stream(client: Any, model: str) -> Iterator[str]:
    """The distinct statuses of a streaming pull, as they arrive."""
    previous = ""
    for progress in client.pull(model, stream=True):
        if progress.status and progress.status != previous:
            previous = progress.status
            yield previous


def update(
    client: Any,
    models: Iterable[str] = (),
    echo: Callable[[str], None] = print,
) -> list[Outcome]:
    """Pull each named model, or every installed one, and report what changed."""
    before = digests(client)
    names = sorted(models) or sorted(before)

    errors: dict[str, str] = {}
    for name in names:
        echo(f"{name}: pulling")
        try:
            for line in stream(client, name):
                echo(f"  {line}")
        except ResponseError as exc:
            # ``str`` on one of these appends the HTTP status, which is noise next to a
            # message the registry already wrote for a human.
            errors[name] = exc.error.strip() or f"HTTP {exc.status_code}"

    after = digests(client) if names else {}
    return [
        Outcome(name, before.get(name, ""), after.get(name, ""), errors.get(name, ""))
        for name in names
    ]


def describe(outcome: Outcome) -> str:
    """The right-hand half of a report line: what happened, and to which build."""
    kind = status(outcome)
    if kind == "failed":
        return f"failed -- {outcome.error}"
    if kind == "updated":
        return f"updated ({short(outcome.before)} -> {short(outcome.after)})"
    return f"{LABELS[kind]} ({short(outcome.after)})"


def summary(outcomes: list[Outcome]) -> str:
    """``3 models: 1 updated, 2 already current.``"""
    counted = Counter(status(outcome) for outcome in outcomes)
    parts = [f"{counted[kind]} {label}" for kind, label in LABELS.items() if counted[kind]]
    return f"{len(outcomes)} model(s): {', '.join(parts)}."


def report(outcomes: list[Outcome]) -> tuple[list[str], bool]:
    """The lines to print, and whether every pull succeeded."""
    width = max(len(outcome.model) for outcome in outcomes)
    lines = [f"{outcome.model:<{width}}  {describe(outcome)}" for outcome in outcomes]
    lines += ["", summary(outcomes)]
    return lines, all(not outcome.error for outcome in outcomes)


def hint(base_url: str, exc: Exception) -> str:
    """What to do about an Ollama that did not answer at all."""
    return f"Ollama did not answer at {base_url} ({exc}).\nStart it with:  ollama serve"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.update_ollama",
        description="Re-pull Ollama's models so each tag is the registry's latest build.",
    )
    parser.add_argument(
        "models",
        nargs="*",
        metavar="MODEL",
        help="Model tags to pull; default is every model Ollama has already pulled.",
    )
    parser.add_argument(
        "--base-url",
        default=OLLAMA.base_url,
        help=f"Ollama server (default: {OLLAMA.base_url})",
    )
    return parser


def main(argv: Sequence[str], client_factory: Callable[[str], Any] = Client) -> int:
    args = build_parser().parse_args(argv)
    try:
        outcomes = update(client_factory(args.base_url), args.models)
    except UNREACHABLE as exc:
        print(hint(args.base_url, exc), file=sys.stderr)
        return 1

    if not outcomes:
        print(
            f"Ollama at {args.base_url} has no models pulled, so there is nothing to "
            f"update. Pull one with:  ollama pull {OLLAMA.model}"
        )
        return 0

    lines, passed = report(outcomes)
    print("\n".join(lines))
    if not passed:
        print("Some models were not pulled.", file=sys.stderr)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
