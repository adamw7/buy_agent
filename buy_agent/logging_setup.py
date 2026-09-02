"""Logging setup and the top-N report the agent exists to produce."""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from buy_agent.models import RankedProduct

logger = logging.getLogger("buy_agent")

#: Libraries that log a line per HTTP call, one per model server (ADR-0028).
_NOISY_LIBRARIES = ("httpx", "openai")

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"
_DATEFMT = "%H:%M:%S"

#: The attribute marking the records that *are* the report, as against the
#: narration around it. One logger and one format either way, so the SSE relay
#: still sees a single stream -- but on a terminal the two are split by handler:
#: the report on stdout, where ``python -m buy_agent ... > top.txt`` catches it
#: and nothing else, and the progress on stderr.
_REPORT = "report"

#: Names the stdout handler, so a second ``configure_logging`` replaces it rather
#: than printing every line of the report twice.
_REPORT_HANDLER = "buy_agent-report"


def configure_logging(*, verbose: bool = False) -> None:
    """Send agent logs to stderr and the report to stdout.

    ``verbose`` also turns on DEBUG from libraries.
    """
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format=_FORMAT,
        datefmt=_DATEFMT,
    )
    _split_report_from_progress()
    if not verbose:
        # Both narrate at INFO and drown out the report: httpx logs every request
        # under ChatOllama, the OpenAI client a line per retry -- so a stopped vLLM
        # prints its retries above the message saying what to do about it.
        for chatty in _NOISY_LIBRARIES:
            logging.getLogger(chatty).setLevel(logging.WARNING)


def _split_report_from_progress() -> None:
    """Route the report to stdout and everything else to stderr.

    A run narrates for a minute and then answers; without the split a ``> top.txt``
    asking for the answer catches neither, all of it having gone to stderr. Split
    by handler and not by logger, so the records are unchanged -- same name, same
    format, same single stream reaching :class:`~buy_agent.server._LogRelay`.
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
    # other half of the split is telling that handler to leave the report alone.
    # Only the console one: a handler writing anywhere else -- a file, a test's
    # capture buffer -- is nobody's stream to take lines out of. A named function
    # rather than a lambda, so repeated calls re-add the same filter instead of
    # stacking a new one each time.
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


def log_top_products(ranked: Sequence[RankedProduct], top_n: int) -> None:
    """Log the best ``top_n`` products, one block each."""
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
        _report("     score  : %.3f", entry.score)
        _report("     price  : %s", product.price_label())
        _report("     rating : %s", product.rating_label())
        if product.seller:
            _report("     seller : %s", product.seller)
        if product.url:
            _report("     url    : %s", product.url)
        if product.notes:
            _report("     note   : %s", product.notes)
        # Quoted rather than summarised, and last: the longer read.
        for opinion in product.opinions:
            _report("     says   : %s", opinion)
    _report(separator)
