"""Logging setup and the top-N report the agent exists to produce."""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

from buy_agent.ranking import CRITERIA, RankingWeights

if TYPE_CHECKING:
    from collections.abc import Sequence

    from buy_agent.models import RankedProduct, ScoreParts

logger = logging.getLogger("buy_agent")

#: Libraries that log a line per call: one per model server (ADR-0028), plus the
#: search backend, which prints "Error in engine ..." for each engine that failed
#: even when the rest answered. Quietened by default and left alone by
#: ``--verbose``: a line per request is what somebody debugging wants.
_NOISY_LIBRARIES = ("httpx", "openai", "ddgs")

#: The transport underneath those, held down at ``--verbose`` too: httpcore
#: traces every request in a dozen DEBUG lines, burying the ones ``-v`` was asked
#: for. INFO rather than WARNING, httpcore saying nothing at INFO.
_TRACE_LIBRARIES = ("httpcore",)

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"
_DATEFMT = "%H:%M:%S"

#: The attribute marking the records that *are* the report, as against the
#: narration around it. One logger and one format either way, so the SSE relay
#: sees a single stream -- but on a terminal the report goes to stdout, where
#: ``> top.txt`` catches it and nothing else, and the progress to stderr.
_REPORT = "report"

#: Names the stdout handler, so a second ``configure_logging`` replaces it rather
#: than printing every line of the report twice.
_REPORT_HANDLER = "buy_agent-report"


def configure_logging(*, verbose: bool = False) -> None:
    """Send agent logs to stderr and the report to stdout.

    ``verbose`` also turns on DEBUG from libraries -- all but the transport trace
    :data:`_TRACE_LIBRARIES` names, which is what ``-v`` would otherwise be spent
    on.
    """
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format=_FORMAT, datefmt=_DATEFMT)
    # ``basicConfig`` does nothing where the root logger already has a handler --
    # an embedder's, or pytest's -- and the level is what it silently skips, so
    # ``--verbose`` asked for DEBUG and got INFO. Set here instead.
    logging.getLogger().setLevel(level)
    _split_report_from_progress()
    for plumbing in _TRACE_LIBRARIES:
        logging.getLogger(plumbing).setLevel(logging.INFO)
    if not verbose:
        # All three narrate at INFO and drown out the report: httpx logs every
        # request the ollama client makes, the OpenAI client a line per retry,
        # and ddgs a line per search engine that did not answer.
        for chatty in _NOISY_LIBRARIES:
            logging.getLogger(chatty).setLevel(logging.WARNING)


def _split_report_from_progress() -> None:
    """Route the report to stdout and everything else to stderr.

    A run narrates for a minute and then answers; without the split a ``> top.txt``
    asking for the answer catches neither. Split by handler and not by logger, so the
    records are unchanged and a single stream still reaches
    :class:`~buy_agent.server._LogRelay`.
    """
    package = logging.getLogger("buy_agent")
    for previous in [
        handler for handler in package.handlers if handler.name == _REPORT_HANDLER
    ]:
        package.removeHandler(previous)

    handler = logging.StreamHandler(sys.stdout)
    handler.set_name(_REPORT_HANDLER)
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
    handler.addFilter(_is_report)
    package.addHandler(handler)

    # The record still propagates to whatever basicConfig put on the root, so the
    # other half of the split is telling that handler to leave the report alone --
    # only the console one, a handler writing elsewhere being nobody's stream to
    # take lines out of. A named function, so repeated calls re-add one filter.
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
    """The three scores behind a blend, each with the weight it went in at.

    On the score's own line rather than three of its own: it is what the number is
    made of, and a report is read down the left edge. "assumed" and not a blank,
    because ``NEUTRAL`` is a real 0.5 in the blend (ADR-0041). The ``x0.50`` is the
    other half: each criterion is scored out of 1, so three beside a total they do not
    add up to is a sum that looks wrong until the weights are there.
    """
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
) -> None:
    """Log the best ``top_n`` products, one block each.

    ``weights`` is what the scores were blended by, for the score line to name: the
    run's own, or the defaults ``rank_products`` would have used.
    """
    weights = weights or RankingWeights()
    if not ranked:
        # Not part of the report: there is none. It is the run saying why.
        logger.warning("No products to report.")
        return

    top = ranked[:top_n]
    separator = "=" * 62
    _report(separator)
    _report("TOP %d OF %d PRODUCTS", len(top), len(ranked))
    _report(separator)
    for entry in top:
        product = entry.product
        _report("#%d  %s", entry.rank, product.name)
        _report(
            "     score  : %.3f  (%s)", entry.score, _parts(entry.breakdown, weights)
        )
        _report("     price  : %s", product.price_label())
        _report("     rating : %s", product.rating_label())
        if product.seller:
            _report("     seller : %s", product.seller)
        if product.url:
            _report("     url    : %s", product.url)
        if product.notes:
            _report("     note   : %s", product.notes)
        # Quoted rather than summarised, and last: the longer read. The page is
        # named only where it is not the product's own link (ADR-0042).
        for opinion in product.opinions:
            elsewhere = opinion.url and opinion.url != product.url
            _report(
                "     says   : %s%s", opinion.text, f"  -- {opinion.url}" if elsewhere else ""
            )
    _report(separator)
