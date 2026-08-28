"""Command line entry point:  python -m buy_agent "wireless headphones under $200"."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import get_args

from buy_agent.agent import BuyAgent, ModelUnavailableError
from buy_agent.config import AgentConfig
from buy_agent.logging_setup import configure_logging
from buy_agent.providers import PROVIDERS
from buy_agent.ranking import SortBy
from buy_agent.search import SearchError
from buy_agent.sources import parse_sources

logger = logging.getLogger("buy_agent")

#: The flag defaults are the config's own, so the two cannot drift apart.
_DEFAULTS = AgentConfig()


def _provider_defaults(setting: str) -> str:
    """One column of :data:`buy_agent.providers.PROVIDERS`, as ``--help`` prints it.

    ``--model`` and ``--base-url`` each have a default per provider rather than
    one, so the help names all of them: "gemma4:12b for ollama, Qwen/Qwen3-8B for
    vllm". Read off the table so a third provider appears here by being added
    there.
    """
    return ", ".join(
        f"{getattr(server, setting)} for {name}" for name, server in PROVIDERS.items()
    )


def _source(spec: str) -> str:
    """``--source`` as argparse takes it: checked here, kept as text.

    Checked here so that an unusable source is a usage error carrying the shapes
    that do work -- argparse turns a type function's ``ValueError`` into "invalid
    _source value" and throws the reason away, which is the whole message.

    Kept as text so that ``main`` can parse every flag together: two of them
    naming one site are one source, and that can only be seen from all of them.
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
    )
    parser.add_argument("request", help="What you want to buy, in plain words.")
    parser.add_argument(
        "--provider",
        choices=tuple(PROVIDERS),
        default=_DEFAULTS.provider,
        help=f"Which model server to talk to (default: {_DEFAULTS.provider}, override "
        "with $BUY_AGENT_PROVIDER). It decides what --model and --base-url mean.",
    )
    # Both default to the empty string rather than to a value, because which
    # value is right depends on --provider, which argparse has not read yet. The
    # config resolves an empty one per provider, so the help quotes the pair for
    # each rather than one pair that would be wrong half the time.
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
        type=int,
        default=_DEFAULTS.num_products,
        help=f"How many products to find (default: {_DEFAULTS.num_products}).",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=_DEFAULTS.top_n,
        help=f"How many products to log (default: {_DEFAULTS.top_n}).",
    )
    parser.add_argument(
        "--sort-by",
        # Read off the type rather than repeated: rank_products has a branch per
        # criterion, and a fourth one must not be offered here without one there.
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
        type=float,
        default=_DEFAULTS.temperature,
        help=f"Model temperature (default: {_DEFAULTS.temperature}).",
    )
    parser.add_argument(
        "--num-ctx",
        type=int,
        default=_DEFAULTS.num_ctx,
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
        num_ctx=args.num_ctx,
        reasoning=args.think,
        search_results=max(args.results, args.top),
        num_products=args.results,
        top_n=args.top,
        region=args.region,
        # Repeated flags build a list, and no flag at all leaves None -- which is
        # the config's own default and not an empty one written down again.
        sources=parse_sources(args.source) if args.source else _DEFAULTS.sources,
        fetch_pages=args.fetch,
    )

    try:
        ranked = BuyAgent(config).run(args.request, sort_by=args.sort_by)
    except (ModelUnavailableError, SearchError, ValueError) as exc:
        logger.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        logger.warning("Interrupted.")
        return 130

    if not ranked:
        return 1

    if args.json:
        payload = [
            {"rank": entry.rank, "score": round(entry.score, 4), **entry.product.model_dump()}
            for entry in ranked
        ]
        try:
            args.json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as exc:
            # A missing directory or a read-only path is worth an exit code and
            # not a traceback: the report is already on stderr by now, so what
            # failed is the copy, and a run that took a minute should not end by
            # looking like a crash.
            logger.error("Could not write %s (%s)", args.json, exc)
            return 1
        logger.info("Wrote %d products to %s", len(payload), args.json)

    return 0


if __name__ == "__main__":
    sys.exit(main())
