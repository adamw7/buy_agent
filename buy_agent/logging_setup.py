"""Logging setup and the top-N report the agent exists to produce."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from buy_agent.models import RankedProduct

logger = logging.getLogger("buy_agent")

#: Libraries that log a line per HTTP call, one per model server (ADR-0028).
_NOISY_LIBRARIES = ("httpx", "openai")


def configure_logging(*, verbose: bool = False) -> None:
    """Send agent logs to stderr. ``verbose`` also turns on DEBUG from libraries."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    if not verbose:
        # Both clients narrate at INFO and drown out the report: httpx logs every
        # request under ChatOllama, and the OpenAI client under ChatOpenAI logs a
        # line per retry -- so a stopped vLLM prints its own retries above the one
        # message that says what to do about it.
        for chatty in _NOISY_LIBRARIES:
            logging.getLogger(chatty).setLevel(logging.WARNING)


def log_top_products(ranked: Sequence[RankedProduct], top_n: int) -> None:
    """Log the best ``top_n`` products, one block each."""
    if not ranked:
        logger.warning("No products to report.")
        return

    top = ranked[:top_n]
    separator = "=" * 62
    logger.info(separator)
    logger.info("TOP %d OF %d PRODUCTS", len(top), len(ranked))
    logger.info(separator)
    for entry in top:
        product = entry.product
        logger.info("#%d  %s", entry.rank, product.name)
        logger.info("     score  : %.3f", entry.score)
        logger.info("     price  : %s", product.price_label())
        logger.info("     rating : %s", product.rating_label())
        if product.seller:
            logger.info("     seller : %s", product.seller)
        if product.url:
            logger.info("     url    : %s", product.url)
        if product.notes:
            logger.info("     note   : %s", product.notes)
        # Quoted rather than summarised, and last: the figures say what it costs
        # and the opinions say whether to want it, which is the longer read.
        for opinion in product.opinions:
            logger.info("     says   : %s", opinion)
    logger.info(separator)
