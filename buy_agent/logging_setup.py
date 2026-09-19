"""Logging setup and the top-N report the agent exists to produce."""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

from buy_agent.ranking import CRITERIA, ORDERINGS, RankingWeights

if TYPE_CHECKING:
    from collections.abc import Sequence

    from buy_agent.journal import Change
    from buy_agent.models import RankedProduct, ScoreParts
    from buy_agent.ranking import SortBy

logger = logging.getLogger("buy_agent")

#: Libraries that log a line per call: one per model server (ADR-0028), plus the search
#: backend, which prints "Error in engine ..." for each engine that failed even when the
#: rest answered.
_NOISY_LIBRARIES = ("httpx", "openai", "ddgs")

#: The transport underneath those, held down at ``--verbose`` too: httpcore traces every
#: request in a dozen DEBUG lines, burying the ones ``-v`` was asked for.
_TRACE_LIBRARIES = ("httpcore",)

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"
_DATEFMT = "%H:%M:%S"

#: How the report itself is written, which is not how the narration is.
_REPORT_FORMAT = "%(message)s"

#: The attribute marking the records that *are* the report, as against the narration
#: around it.
_REPORT = "report"

#: Names the stdout handler, so a second ``configure_logging`` replaces it rather than
#: printing every line of the report twice.
_REPORT_HANDLER = "buy_agent-report"


def configure_logging(*, verbose: bool = False) -> None:
    """Send agent logs to stderr and the report to stdout."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format=_FORMAT, datefmt=_DATEFMT)
    # ``basicConfig`` does nothing where the root logger already has a handler -- an
    # embedder's, or pytest's -- and the level is what it silently skips, so
    # ``--verbose`` asked for DEBUG and got INFO.
    logging.getLogger().setLevel(level)
    _split_report_from_progress()
    for plumbing in _TRACE_LIBRARIES:
        logging.getLogger(plumbing).setLevel(logging.INFO)
    if not verbose:
        # All three narrate at INFO and drown out the report: httpx logs every request
        # the ollama client makes, the OpenAI client a line per retry, and ddgs a line
        # per search engine that did not answer.
        for chatty in _NOISY_LIBRARIES:
            logging.getLogger(chatty).setLevel(logging.WARNING)


def _split_report_from_progress() -> None:
    """Route the report to stdout and everything else to stderr."""
    package = logging.getLogger("buy_agent")
    for previous in [
        handler for handler in package.handlers if handler.name == _REPORT_HANDLER
    ]:
        package.removeHandler(previous)

    handler = logging.StreamHandler(sys.stdout)
    handler.set_name(_REPORT_HANDLER)
    handler.setFormatter(logging.Formatter(_REPORT_FORMAT))
    handler.addFilter(_is_report)
    package.addHandler(handler)

    # The record still propagates to whatever basicConfig put on the root, so the other
    # half of the split is telling that handler to leave the report alone -- only the
    # console one, a handler writing elsewhere being nobody's stream to take lines out of.
    for console in logging.getLogger().handlers:
        if getattr(console, "stream", None) is sys.stderr and _not_report not in console.filters:
            console.addFilter(_not_report)


def _is_report(record: logging.LogRecord) -> bool:
    return bool(getattr(record, _REPORT, False))


def _not_report(record: logging.LogRecord) -> bool:
    return not _is_report(record)


def _report(message: str, *args: object) -> None:
    """One line of the report, marked as such so it goes to stdout."""
    logger.info(message, *args, extra={_REPORT: True})


def _parts(breakdown: ScoreParts, weights: RankingWeights) -> str:
    """The three scores behind a blend, each with the weight it went in at (ADR-0041)."""
    fractions = weights.fractions
    return ", ".join(
        f"{name} {getattr(breakdown, name):.2f} x{fractions[name]:.2f}"
        + (" assumed" if name in breakdown.neutral else "")
        for name in CRITERIA
    )


def log_top_products(
    ranked: Sequence[RankedProduct],
    top_n: int,
    *,
    weights: RankingWeights | None = None,
    sort_by: SortBy = "score",
) -> None:
    """Log the best ``top_n`` products, one block each."""
    weights = weights or RankingWeights()
    if not ranked:
        # Not part of the report: there is none. It is the run saying why.
        logger.warning("No products to report.")
        return

    top = ranked[:top_n]
    separator = "=" * 62
    _report(separator)
    _report("TOP %d OF %d PRODUCTS, %s", len(top), len(ranked), ORDERINGS[sort_by].upper())
    _report(separator)
    for entry in top:
        product = entry.product
        _report("#%d  %s", entry.rank, product.name)
        _report(
            "     score  : %.3f  (%s)", entry.score, _parts(entry.breakdown, weights)
        )
        _report("     price  : %s", product.price_label())
        # Under the price it is a spread of, and only where several pages priced it:
        # what the run read and used to throw all but one of away (ADR-0058).
        if (offers := product.offers_label()) is not None:
            _report("     offers : %s", offers)
        _report("     rating : %s", product.rating_label())
        if product.seller:
            _report("     seller : %s", product.seller)
        if product.url:
            _report("     url    : %s", product.url)
        if product.notes:
            _report("     note   : %s", product.notes)
        # Quoted rather than summarised, and last: the longer read (ADR-0042).
        for opinion in product.opinions:
            elsewhere = opinion.url and opinion.url != product.url
            _report(
                "     says   : %s%s", opinion.text, f"  -- {opinion.url}" if elsewhere else ""
            )
    _report(separator)


def log_changes(changes: Sequence[Change], since: str | None) -> None:
    """Log what moved since the last run of this search, one line each (ADR-0060).

    Part of the report and not of the narration: it is an answer somebody asked for, it
    goes to stdout with the products, and a ``> top.txt`` keeps it. Every sentence in it
    is the journal's own -- the browser shows the same ones, and two wordings for one
    judgement is how the two come to disagree.
    """
    if not changes or since is None:
        # Not a warning and not a line: a first run of a search has nothing to compare
        # against, which is the ordinary case and not a failure.
        logger.info("Nothing to compare: no earlier run of this search was kept.")
        return

    separator = "=" * 62
    _report(separator)
    _report("WHAT CHANGED SINCE %s", since.upper())
    _report(separator)
    for change in changes:
        _report("  %-32s %s", change.name[:32], change.detail)
    _report(separator)
