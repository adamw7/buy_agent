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

#: Libraries that log a line per call; quiet unless ``--verbose`` (ADR-0028).
_NOISY_LIBRARIES = ("httpx", "openai", "ddgs")

#: Held at INFO even with ``--verbose``: a dozen DEBUG lines per request.
_TRACE_LIBRARIES = ("httpcore",)

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"
_DATEFMT = "%H:%M:%S"

#: The report is written plainly; the narration keeps ``_FORMAT``.
_REPORT_FORMAT = "%(message)s"

#: The record attribute marking report lines.
_REPORT = "report"

#: Names the stdout handler, so reconfiguring replaces rather than duplicates it.
_REPORT_HANDLER = "buy_agent-report"

#: The rule around each block of the report.
_RULE = "=" * 62


def configure_logging(*, verbose: bool = False) -> None:
    """Send agent logs to stderr and the report to stdout."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format=_FORMAT, datefmt=_DATEFMT)
    # ``basicConfig`` is a no-op once the root has a handler, level included.
    logging.getLogger().setLevel(level)
    _split_report_from_progress()
    for plumbing in _TRACE_LIBRARIES:
        logging.getLogger(plumbing).setLevel(logging.INFO)
    if not verbose:
        # They narrate at INFO and drown out the report.
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

    # Keep the report off the stderr console handler only; other handlers still get it.
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
        # Narration, not report: there is no report.
        logger.warning("No products to report.")
        return

    top = ranked[:top_n]
    _report(_RULE)
    _report("TOP %d OF %d PRODUCTS, %s", len(top), len(ranked), ORDERINGS[sort_by].upper())
    _report(_RULE)
    for entry in top:
        product = entry.product
        _report("#%d  %s", entry.rank, product.name)
        _report(
            "     score  : %.3f  (%s)", entry.score, _parts(entry.breakdown, weights)
        )
        _report("     price  : %s", product.price_label())
        # Only where several pages priced it (ADR-0058).
        if (offers := product.offers_label()) is not None:
            _report("     offers : %s", offers)
        _report("     rating : %s", product.rating_label())
        if product.seller:
            _report("     seller : %s", product.seller)
        if product.url:
            _report("     url    : %s", product.url)
        if product.notes:
            _report("     note   : %s", product.notes)
        # Quotes last: the longer read (ADR-0042).
        for opinion in product.opinions:
            elsewhere = opinion.url and opinion.url != product.url
            _report(
                "     says   : %s%s", opinion.text, f"  -- {opinion.url}" if elsewhere else ""
            )
    _report(_RULE)


def log_changes(changes: Sequence[Change], since: str | None) -> None:
    """Log what moved since the last run of this search, one line each (ADR-0060).

    Part of the report (stdout). The sentences are the journal's, shared with the
    browser.
    """
    if not changes or since is None:
        # A first run is the ordinary case, not a warning.
        logger.info("Nothing to compare: no earlier run of this search was kept.")
        return

    _report(_RULE)
    _report("WHAT CHANGED SINCE %s", since.upper())
    _report(_RULE)
    for change in changes:
        _report("  %-32s %s", change.name[:32], change.detail)
    _report(_RULE)
