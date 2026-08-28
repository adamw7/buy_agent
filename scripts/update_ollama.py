"""Re-pull the models Ollama has, and say which of them actually moved.

`ollama pull` on a tag that is already installed *is* how a model is updated:
the tag follows the registry, so a pull replaces the local blobs when the
publisher has re-cut them and does nothing when they are current. What a pull
does not do is say which of the two just happened -- it prints `success` either
way -- so this reads the digests Ollama reports before and after and prints a
line per model that tells them apart.

Only the models are updated. The Ollama server itself is a platform install
(winget on Windows, the install script on Linux, the app on macOS) and is left
to its own updater: pulling models is the part this project has an opinion
about, and nothing here could restart a server it did not start.

Run it from the repository root, which is what puts `buy_agent` on the path --
the defaults are the agent's own, `$OLLAMA_HOST` included:

    python -m scripts.update_ollama                     # every installed model
    python -m scripts.update_ollama llama3.2 qwen2.5:7b
    python -m scripts.update_ollama --base-url http://10.0.0.5:11434
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

import httpx
from ollama import Client, RequestError, ResponseError

from buy_agent.providers import OLLAMA

#: Transport failures that mean "the server is not there" -- the tuple
#: ``BuyAgent._invoke`` catches, for the reasons documented there: ollama's
#: ``RequestError`` is not httpx's, and a refused connection surfaces as either
#: one depending on which path the client took.
UNREACHABLE = (RequestError, OSError, httpx.HTTPError)

#: What a status reads as in the report, in the order the summary lists them.
LABELS = {
    "updated": "updated",
    "installed": "installed",
    "current": "already current",
    "failed": "failed",
}


@dataclass(frozen=True, slots=True)
class Outcome:
    """One pull: the digests either side of it, or why there is no second one.

    ``before`` is empty when the tag was not installed, ``after`` when the pull
    failed -- the two ends of the same comparison, which is what makes an
    unchanged digest distinguishable from a model that was never there.
    """

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
    """Every tag Ollama has pulled, and the digest it holds for each.

    Read straight off the client rather than through ``agent.list_models``,
    which answers with tags alone: the digest is the whole point here.
    """
    return {model.model: model.digest or "" for model in client.list().models if model.model}


def stream(client: Any, model: str) -> Iterator[str]:
    """The distinct statuses of a streaming pull, as they arrive.

    A pull of several gigabytes reports progress hundreds of times; the status
    changes a handful of times ("pulling manifest", "verifying sha256 digest",
    "success"). Only the changes are worth a line -- a percentage redrawn on a
    log that does not move the cursor is noise.
    """
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
    """Pull each named model, or every installed one, and report what changed.

    The digests are read once before the pulls and once after, rather than
    around each one: two listings answer the same question as a listing per
    model. A pull the registry refuses (an unknown tag, say) is recorded against
    that model and the rest still run; a transport failure means the server has
    gone, so it is left to reach the caller.
    """
    before = digests(client)
    names = sorted(models) or sorted(before)

    errors: dict[str, str] = {}
    for name in names:
        echo(f"{name}: pulling")
        try:
            for line in stream(client, name):
                echo(f"  {line}")
        except ResponseError as exc:
            # ``str`` on one of these appends the HTTP status, which is noise next
            # to a message the registry already wrote for a human. An empty one is
            # the case where the status is all there is to say.
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
    counted = [
        (kind, sum(1 for outcome in outcomes if status(outcome) == kind)) for kind in LABELS
    ]
    parts = [f"{count} {LABELS[kind]}" for kind, count in counted if count]
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
