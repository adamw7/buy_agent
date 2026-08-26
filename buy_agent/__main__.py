"""Command line entry point:  python -m buy_agent "wireless headphones under $200"."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import get_args

from buy_agent.agent import BuyAgent, OllamaUnavailableError
from buy_agent.config import AgentConfig
from buy_agent.logging_setup import configure_logging
from buy_agent.ranking import SortBy
from buy_agent.search import SearchError

logger = logging.getLogger("buy_agent")

#: The flag defaults are the config's own, so the two cannot drift apart.
_DEFAULTS = AgentConfig()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="buy_agent",
        description="Search the web for what you want to buy, rank it, log the best.",
    )
    parser.add_argument("request", help="What you want to buy, in plain words.")
    parser.add_argument(
        "--model",
        default=_DEFAULTS.model,
        help=f"Ollama model tag (default: {_DEFAULTS.model}, override with $OLLAMA_MODEL).",
    )
    parser.add_argument(
        "--base-url",
        default=_DEFAULTS.base_url,
        help=f"Ollama server URL (default: {_DEFAULTS.base_url}, override with $OLLAMA_HOST).",
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
        "more products; a model that need not think is fine on Ollama's own 4096.",
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
        model=args.model,
        base_url=args.base_url,
        temperature=args.temperature,
        num_ctx=args.num_ctx,
        reasoning=args.think,
        search_results=max(args.results, args.top),
        num_products=args.results,
        top_n=args.top,
        region=args.region,
        fetch_pages=args.fetch,
    )

    try:
        ranked = BuyAgent(config).run(args.request, sort_by=args.sort_by)
    except (OllamaUnavailableError, SearchError, ValueError) as exc:
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
        args.json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        logger.info("Wrote %d products to %s", len(payload), args.json)

    return 0


if __name__ == "__main__":
    sys.exit(main())
