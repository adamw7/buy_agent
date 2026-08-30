"""Command line entry point:  python -m buy_agent "wireless headphones under $200"."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Callable, get_args

from buy_agent.agent import BuyAgent, ModelUnavailableError
from buy_agent.config import LIMITS, AgentConfig
from buy_agent.logging_setup import configure_logging
from buy_agent.providers import PROVIDERS
from buy_agent.ranking import SortBy
from buy_agent.search import SearchError
from buy_agent.sources import parse_sources

logger = logging.getLogger("buy_agent")

#: The flag defaults are the config's own, so the two cannot drift apart.
_DEFAULTS = AgentConfig()

#: Exit code for a run that worked and found nothing: the search reached the web,
#: the model answered, and no product survived. Its own code because a shell
#: cannot otherwise tell it from a stopped model server, and the two want
#: different things done about them. 2 is argparse's own, for a usage error.
NOTHING_FOUND = 3

#: What ``--num-ctx`` holds when it was not given. A sentinel rather than the
#: config's default, because "8192" and "the default, which is 8192" are the same
#: number and different requests: only the first is worth telling a shopper their
#: provider is going to ignore.
_UNSET = object()


def _provider_defaults(setting: str) -> str:
    """One column of :data:`buy_agent.providers.PROVIDERS`, as ``--help`` prints it.

    ``--model`` and ``--base-url`` have a default per provider, so the help names
    them all: "gemma4:12b for ollama, Qwen/Qwen3-8B for vllm". Read off the table,
    so a third provider appears here by being added there.
    """
    return ", ".join(
        f"{getattr(server, setting)} for {name}" for name, server in PROVIDERS.items()
    )


def _bounded(kind: Callable[[str], Any], field: str) -> Callable[[str], Any]:
    """``--results`` and the rest, held to the range the API holds them to.

    Read off :data:`buy_agent.config.LIMITS` so the two front ends cannot come to
    disagree: unchecked, ``--results 0`` searches the web, reads ten pages and
    then asks the model for no products at all -- a minute spent on an answer the
    browser refuses before starting. The bound is checked here rather than after
    parsing so that it is a usage error, printed with the flag that carries it.
    """
    minimum, maximum = LIMITS[field]

    def parse(text: str) -> Any:
        value = kind(text)  # argparse turns the ValueError into "invalid value"
        if not minimum <= value <= maximum:
            raise argparse.ArgumentTypeError(
                f"must be between {minimum} and {maximum}; got {value}"
            )
        return value

    # argparse names the type in its own message for anything this does not
    # catch, and "invalid int value" is what a mistyped number deserves to read.
    parse.__name__ = kind.__name__
    return parse


def _source(spec: str) -> str:
    """``--source`` as argparse takes it: checked here, kept as text.

    Checked here so an unusable source is a usage error carrying the shapes that
    work -- argparse turns a type function's ``ValueError`` into "invalid _source
    value" and throws away the reason, which is the whole message. Kept as text so
    ``main`` parses every flag together: two naming one site are one source, and
    that can only be seen from all of them.
    """
    try:
        parse_sources(spec)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return spec


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="buy_agent",
        description="Search the web for what you want to buy, rank it, log the best.",
        # The report goes to stdout and the progress to stderr, so a redirect
        # catches the answer alone -- worth saying here, since --help is the only
        # documentation the CLI has, and so are the codes a script branches on.
        epilog=(
            "The report is written to stdout and the progress to stderr, so "
            "`... > top.txt` keeps the report and leaves the narration on screen.\n"
            "\n"
            "Exit codes:\n"
            "  0  products were found and reported\n"
            "  1  the run failed -- the reason is the last line on stderr\n"
            "  2  the command line could not be understood\n"
            f"  {NOTHING_FOUND}  the run worked and found nothing\n"
            "  130  interrupted with Ctrl-C\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("request", help="What you want to buy, in plain words.")
    parser.add_argument(
        "--provider",
        choices=tuple(PROVIDERS),
        default=_DEFAULTS.provider,
        help=f"Which model server to talk to (default: {_DEFAULTS.provider}, override "
        "with $BUY_AGENT_PROVIDER). It decides what --model and --base-url mean.",
    )
    # Both default to "" rather than a value, because which value is right depends
    # on --provider, which argparse has not read yet. The config resolves an empty
    # one per provider, so the help quotes every pair rather than one that would be
    # wrong half the time.
    parser.add_argument(
        "--model",
        default="",
        help="Model to use, empty for the provider's own default "
        f"({_provider_defaults('model')}). Override with $OLLAMA_MODEL or $VLLM_MODEL.",
    )
    parser.add_argument(
        "--base-url",
        default="",
        help="Model server URL, empty for the provider's own default "
        f"({_provider_defaults('base_url')}). Override with $OLLAMA_HOST or $VLLM_HOST.",
    )
    parser.add_argument(
        "--results",
        type=_bounded(int, "num_products"),
        default=_DEFAULTS.num_products,
        help=f"How many products to find (default: {_DEFAULTS.num_products}).",
    )
    parser.add_argument(
        "--top",
        type=_bounded(int, "top_n"),
        default=_DEFAULTS.top_n,
        help=f"How many products to log (default: {_DEFAULTS.top_n}).",
    )
    parser.add_argument(
        "--sort-by",
        # Read off the type: rank_products has a branch per criterion, and a
        # fourth must not be offered here without one there.
        choices=get_args(SortBy),
        default="score",
        help="Ranking criterion (default: score, a blend of rating, reviews and price).",
    )
    parser.add_argument(
        "--region", default=_DEFAULTS.region, help="Search region, e.g. us-en, uk-en, pl-pl."
    )
    parser.add_argument(
        "--source",
        action="append",
        metavar="SITE",
        type=_source,
        help="Take the facts from this source only; repeat for several. A site "
        "(rtings.com), a section of one (rtings.com/headphones) or a YouTube "
        "handle (@mkbhd). Without it the whole web is searched.",
    )
    parser.add_argument(
        "--temperature",
        type=_bounded(float, "temperature"),
        default=_DEFAULTS.temperature,
        help=f"Model temperature (default: {_DEFAULTS.temperature}).",
    )
    parser.add_argument(
        "--num-ctx",
        type=_bounded(int, "num_ctx"),
        # The sentinel, not the value: which is the default is written into the
        # help below either way, and only a number the shopper actually typed is
        # worth warning them a vLLM will ignore.
        default=_UNSET,
        help=f"Context window in tokens (default: {_DEFAULTS.num_ctx}). The "
        "extraction prompt runs to ~4.3k tokens, so a larger window leaves room for "
        "more products; a model that need not think is fine on Ollama's own 4096. "
        "Ollama only -- vLLM fixes its window with --max-model-len when it starts.",
    )
    parser.add_argument(
        "--think",
        action=argparse.BooleanOptionalAction,
        default=_DEFAULTS.reasoning,
        help="Force the model's thinking mode on or off (default: --no-think). "
        "Thinking models need --no-think: they reason until the context runs out and "
        "never answer; a model that cannot think ignores either.",
    )
    parser.add_argument(
        "--no-fetch",
        dest="fetch",
        action="store_false",
        help="Extract from search snippets only, without opening the result pages "
        "(much faster, but snippets rarely quote a price).",
    )
    parser.add_argument("--json", type=Path, help="Also write all results to this JSON file.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(verbose=args.verbose)

    config = AgentConfig(
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
        temperature=args.temperature,
        num_ctx=_DEFAULTS.num_ctx if args.num_ctx is _UNSET else args.num_ctx,
        reasoning=args.think,
        search_results=max(args.results, args.top),
        num_products=args.results,
        top_n=args.top,
        region=args.region,
        # Repeated flags build a list; no flag leaves None, and the fallback is
        # the config's own default rather than an empty one written down again.
        sources=parse_sources(args.source) if args.source else _DEFAULTS.sources,
        fetch_pages=args.fetch,
    )

    if args.num_ctx is not _UNSET and not config.model_server.takes_num_ctx:
        # The form disables the field and says so; the CLI has no field to disable,
        # so it says it here rather than dropping the number without a word.
        logger.warning(
            "%s fixes its context window when it starts (--max-model-len), so "
            "--num-ctx %s is ignored on this run.",
            config.model_server.label,
            args.num_ctx,
        )

    try:
        ranked = BuyAgent(config).run(args.request, sort_by=args.sort_by)
    except (ModelUnavailableError, SearchError, ValueError) as exc:
        logger.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        logger.warning("Interrupted.")
        return 130

    if args.json:
        # Written even when the run found nothing, and so before the exit code
        # is decided: skipped, a script waiting on this file gets no file and no
        # reason, with the last run's results left sitting there looking current.
        payload = [
            {"rank": entry.rank, "score": round(entry.score, 4), **entry.product.model_dump()}
            for entry in ranked
        ]
        try:
            args.json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as exc:
            # Worth an exit code and not a traceback: the report is already on
            # stdout, so what failed is the copy, and a run that took a minute
            # should not end by looking like a crash.
            logger.error("Could not write %s (%s)", args.json, exc)
            return 1
        logger.info("Wrote %d products to %s", len(payload), args.json)

    # A run that worked and found nothing is not a failure, and a shell told it
    # was one cannot tell it from a model server that never answered.
    return 0 if ranked else NOTHING_FOUND


if __name__ == "__main__":
    sys.exit(main())
