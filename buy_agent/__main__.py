"""Command line entry point:  python -m buy_agent "wireless headphones under $200"."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from buy_agent.agent import BuyAgent, OllamaUnavailableError
from buy_agent.config import DEFAULT_BASE_URL, DEFAULT_MODEL, AgentConfig
from buy_agent.logging_setup import configure_logging
from buy_agent.ranking import RankingWeights
from buy_agent.search import SearchError

logger = logging.getLogger("buy_agent")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="buy_agent",
        description="Search the web for what you want to buy, rank it, log the best.",
    )
    parser.add_argument("request", help="What you want to buy, in plain words.")
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Ollama model tag (default: {DEFAULT_MODEL}, override with $OLLAMA_MODEL).",
    )
    parser.add_argument(
        "--base-url", default=os.getenv("OLLAMA_HOST", DEFAULT_BASE_URL),
        help=f"Ollama server URL (default: {DEFAULT_BASE_URL}, override with $OLLAMA_HOST).",
    )
    parser.add_argument(
        "--results", type=int, default=10, help="How many products to find (default: 10)."
    )
    parser.add_argument(
        "--top", type=int, default=3, help="How many products to log (default: 3)."
    )
    parser.add_argument(
        "--sort-by",
        choices=("score", "price", "rating"),
        default="score",
        help="Ranking criterion (default: score, a blend of rating, reviews and price).",
    )
    parser.add_argument(
        "--region", default="us-en", help="Search region, e.g. us-en, uk-en, pl-pl."
    )
    parser.add_argument(
        "--temperature", type=float, default=0.0, help="Model temperature (default: 0.0)."
    )
    parser.add_argument(
        "--num-ctx",
        type=int,
        default=None,
        help="Context window in tokens (default: Ollama's own, usually 4096). The "
        "extraction prompt runs to ~3.3k tokens, so a larger window leaves room for "
        "more products; 8192 is a good starting point.",
    )
    parser.add_argument(
        "--think",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Force the model's thinking mode on or off (default: leave it alone). "
        "Thinking models need --no-think: they reason until the context runs out and "
        "never answer.",
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
        weights=RankingWeights(),
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
