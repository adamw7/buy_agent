"""Command line entry point:  python -m buy_agent "wireless headphones under $200"."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Callable, get_args

from buy_agent.agent import BuyAgent, ModelUnavailableError
from buy_agent.api import results_payload
from buy_agent.config import DEFAULT_PROVIDER, LIMITS, AgentConfig, parse_region
from buy_agent.logging_setup import configure_logging
from buy_agent.providers import PROVIDERS, provider_for
from buy_agent.ranking import SortBy
from buy_agent.search import SearchError
from buy_agent.sources import parse_sources

logger = logging.getLogger("buy_agent")

def _defaults() -> AgentConfig:
    """Every flag's default, off one config so the two cannot drift apart.

    Built on a provider that exists rather than on ``$BUY_AGENT_PROVIDER``
    itself, which is a shopper's to misspell: resolved at import time, a bad one
    was a ``ValueError`` out of importing this module -- a traceback before
    ``main`` had run, with ``--help`` and its list of the servers there are
    unreachable too. The name is still read below, where :func:`_provider` turns
    it into the usage error it deserves; every other field here is a plain
    default that no environment variable can make unusable.
    """
    known = DEFAULT_PROVIDER in PROVIDERS
    return AgentConfig(provider=DEFAULT_PROVIDER if known else next(iter(PROVIDERS)))


#: The flag defaults are the config's own, so the two cannot drift apart.
_DEFAULTS = _defaults()

#: Exit code for a run that worked and found nothing: the search reached the web,
#: the model answered, and no product survived. Its own code because a shell
#: cannot otherwise tell it from a stopped model server. 2 is argparse's own.
NOTHING_FOUND = 3

#: What ``--num-ctx`` holds when it was not given. A sentinel rather than the
#: config's default: "8192" and "the default, which is 8192" are the same number
#: and different requests, and only the first is worth warning a shopper about.
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

    Read off :data:`buy_agent.config.LIMITS` so the two front ends cannot disagree:
    unchecked, ``--results 0`` searches the web and reads ten pages to ask the
    model for no products at all. Checked here rather than after parsing, so it is
    a usage error printed with the flag that carries it.
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


def _checked(check: Callable[[str], object]) -> Callable[[str], str]:
    """A flag's value as argparse takes it: refused here, and kept as written.

    Three settings are judged before the run rather than after parsing -- a
    source, a provider and a region -- and each is judged by the same function a
    run would have used, so there is no second rule to keep true. The wrapper is
    what argparse needs: a ``ValueError`` out of a ``type`` function becomes
    "invalid value" with the sentence thrown away, and the sentence is the whole
    message -- the shapes a source can have, the providers there are, the two
    halves of a region code.

    Checked *here* because two of the three otherwise fail quietly. A source that
    names no site and a region no engine knows both search for nothing and come
    back as an empty report with nothing to explain it (ADR-0027, ADR-0031). The
    third is checked here because a ``type`` function also runs over a string
    *default*, where ``choices`` does not: ``$BUY_AGENT_PROVIDER=olama`` sailed
    past ``choices`` and reached ``AgentConfig`` in :func:`main`, outside the
    ``try`` that names the three failures a run has.

    The text comes back as it was typed rather than as ``check`` read it, so
    ``main`` parses every ``--source`` together -- two flags naming one site are
    one source -- and so the region is lower-cased where every other caller
    lower-cases it, in ``AgentConfig``.
    """

    def parse(text: str) -> str:
        try:
            check(text)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(str(exc)) from exc
        return text

    return parse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="buy_agent",
        description="Search the web for what you want to buy, rank it, log the best.",
        # --help is the only documentation the CLI has, so the split between the
        # two streams and the codes a script branches on are both worth saying.
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
        type=_checked(provider_for),
        choices=tuple(PROVIDERS),
        default=DEFAULT_PROVIDER,
        help=f"Which model server to talk to (default: {DEFAULT_PROVIDER}, override "
        "with $BUY_AGENT_PROVIDER). It decides what --model and --base-url mean.",
    )
    # Both default to "" rather than a value: which one is right depends on
    # --provider, which argparse has not read yet. The config resolves an empty one
    # per provider, so the help quotes every pair.
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
        "--region",
        type=_checked(parse_region),
        default=_DEFAULTS.region,
        help="Search region: a country and then a language, hyphenated (default: "
        f"{_DEFAULTS.region}; also uk-en, pl-pl). Anything else is a usage error, "
        "since a region no search engine knows returns nothing at all.",
    )
    parser.add_argument(
        "--source",
        action="append",
        metavar="SITE",
        type=_checked(parse_sources),
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
        # The sentinel, not the value: the help below names the default either
        # way, and only a number actually typed is worth a warning.
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
        # The form disables the field; the CLI has none to disable, so it says so
        # here rather than dropping the number without a word.
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
        # Written even when the run found nothing, and so before the exit code is
        # decided: skipped, a script waiting on this file finds the last run's
        # results sitting there looking current. The API's own shaping, not a
        # second one -- the file the page hands over is that same answer saved.
        payload = results_payload(ranked)
        try:
            args.json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as exc:
            # Worth an exit code and not a traceback: the report is already on
            # stdout, so what failed is the copy.
            logger.error("Could not write %s (%s)", args.json, exc)
            return 1
        logger.info("Wrote %d products to %s", len(payload), args.json)

    # A run that worked and found nothing is not a failure, and a shell told it was
    # cannot tell it from a model server that never answered.
    return 0 if ranked else NOTHING_FOUND


if __name__ == "__main__":
    sys.exit(main())
