"""What past runs of one search reported, kept so the next one can say what moved
(ADR-0060).

Deliberately not a third kind of cache entry. :mod:`buy_agent.cache` holds what a run
can *reuse* -- the page text (ADR-0040) and the model's answers (ADR-0044) -- and its
docstring says "and nothing else" on purpose, because everything in it expires on one
clock: a stale page is not evidence. A journal is the opposite object. It is a record
kept for a person to read, and the one question it exists to answer is what this cost
last week, which an expiry would take away. So it has rules of its own:

- **Retention is a count, not an age.** At most :data:`MAX_RUNS` runs of one search, and
  at most :data:`MAX_SEARCHES` searches, the least recently run out first. ADR-0052
  prunes the cache oldest-first on the way in, and doing that here would delete exactly
  the entry a comparison wants.
- **It holds three figures per product and nothing else.** A shopping history on disk is
  not a page cache, so what is written down is the name, the price and the currency --
  what a comparison needs -- and no links, quotes, notes or scores.
- **It is one setting off**, and one directory to delete: ``runs/`` beside ``pages/``
  and ``answers/`` under ``$BUY_AGENT_CACHE_DIR``.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ValidationError

from buy_agent.cache import default_dir, file_for, write_atomically
from buy_agent.models import Product, dedup_key
from buy_agent.money import amount_label

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

logger = logging.getLogger(__name__)

#: Where a journal lives, beside the two kinds of cache entry and under the same root:
#: one directory to delete throws the whole of it away.
RUNS = "runs"

#: How many runs of one search are kept. Enough to see a price move and little enough
#: that a journal of a year's shopping is still kilobytes.
MAX_RUNS = 10

#: How many searches are remembered at all. Full, the least recently *run* one goes --
#: not the oldest entry, which is the one a comparison is about.
MAX_SEARCHES = 200

#: What one product did between two runs of the same search. ``unplaced`` is the honest
#: sixth: two prices in two currencies have nothing between them, and this run converts
#: nothing (ADR-0043).
Movement = Literal["new", "gone", "cheaper", "dearer", "steady", "unplaced"]

#: How a date reads in a sentence a shopper is shown -- "11 Sep", not a timestamp.
_WHEN = "%d %b"


class Recorded(BaseModel):
    """One product as a past run reported it (ADR-0060)."""

    name: str
    price: float | None = None
    currency: str | None = None

    @classmethod
    def of(cls, product: Product) -> Recorded:
        """What is worth writing down about a product this run reported."""
        return cls(name=product.name, price=product.price, currency=product.currency)

    @property
    def key(self) -> str:
        """What two runs match this product across by -- ``Product``'s own identity, so
        there are never two spellings of "the same product"."""
        return dedup_key(self.name)

    def label(self) -> str:
        """The price as a person reads it, written the way every other surface writes
        one (ADR-0012)."""
        if self.price is None:
            return "price unknown"
        return amount_label(self.price, self.currency)


class Entry(BaseModel):
    """One past run of one search."""

    #: When it ran, as seconds since the epoch: a journal outlives a time zone.
    at: float
    products: list[Recorded] = []

    def when(self) -> str:
        """The day it ran, as the sentences below name it."""
        return datetime.fromtimestamp(self.at, tz=timezone.utc).strftime(_WHEN)


class Change(BaseModel):
    """What one product did since the last run of this search (ADR-0060)."""

    name: str
    movement: Movement
    #: What it costs now, and what it cost then -- each written by Python, so the page
    #: and the report cannot spell one amount two ways.
    price_label: str | None = None
    was_label: str | None = None
    #: How much it moved, where both runs priced it on one scale. Signed: negative is
    #: cheaper, which is the direction a shopper is looking for.
    delta: float | None = None
    #: The whole of it as a sentence, which is what both front ends show (ADR-0012).
    detail: str


class Journal:
    """The runs of one search, and the comparison between the last and this one.

    Built before the run and asked afterwards, which is the order the two doors call it
    in. A journal that is off, or one whose directory cannot be written, remembers
    nothing and compares nothing -- never a failure: a shopping history is worth less
    than the run it would have interrupted.
    """

    def __init__(
        self,
        directory: Path | None,
        key: str,
        *,
        keep: int = MAX_RUNS,
        searches: int = MAX_SEARCHES,
    ) -> None:
        self.directory = directory
        self.key = key
        self.keep = keep
        self.searches = searches
        #: What the last run of this search reported, read now rather than after this
        #: one has overwritten it.
        self.before: Entry | None = self._last()

    @property
    def keeping(self) -> bool:
        """Whether anything is being written down at all."""
        return self.directory is not None

    def compared_with(self) -> str | None:
        """The day this run is being compared against, for a heading to name."""
        return self.before.when() if self.before else None

    def against(self, products: Sequence[Product]) -> list[Change]:
        """Write this run down and say what moved since the last one (ADR-0060).

        A run that found nothing is not written down: it says nothing about a price, and
        recorded it would make every product of the next run read as new. It is still
        compared, against the last run that did find something.
        """
        recorded = [Recorded.of(product) for product in products]
        if recorded:
            self._append(Entry(at=time.time(), products=recorded))
        if self.before is None:
            return []
        return compare(self.before, recorded)

    # -- the disk ------------------------------------------------------------------

    def _path(self) -> Path | None:
        if self.directory is None:
            return None
        return file_for(self.directory, self.key)

    def _last(self) -> Entry | None:
        """The most recent run of this search that was written down."""
        path = self._path()
        if path is None:
            return None
        return (self._read(path) or [None])[-1]

    def _read(self, path: Path) -> list[Entry]:
        """Every run of this search on disk, oldest first, or nothing at all."""
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # Unreadable is empty, the way a cache miss is: the worst this costs is a
            # run with nothing to compare against.
            return []
        if not isinstance(stored, dict) or stored.get("key") != self.key:
            return []
        try:
            return [Entry.model_validate(run) for run in stored.get("runs", [])]
        except ValidationError:
            logger.debug("A journal entry could not be read back", exc_info=True)
            return []

    def _append(self, entry: Entry) -> None:
        """Add this run to the search's own file, oldest out once it is full."""
        path = self._path()
        if path is None or self.directory is None:
            return
        runs = [*self._read(path), entry][-self.keep :]
        document = json.dumps(
            {"key": self.key, "runs": [run.model_dump() for run in runs]},
            separators=(",", ":"),
        )
        # Written the way a cache entry is -- beside the file and moved onto it -- and
        # never a failure, for the reason given there: a shopping history is worth less
        # than the run it would have interrupted.
        failed = write_atomically(self.directory, path, document)
        if failed is not None:
            logger.debug(
                "Could not write the journal in %s", self.directory, exc_info=failed
            )
            return
        _forget_the_least_recent(self.directory, self.searches)


def open_journal(
    request: str, *, asked: Mapping[str, Any], keeping: bool, directory: Path | None = None
) -> Journal:
    """The journal for this exact search, or one that remembers nothing (ADR-0060).

    ``asked`` is what made this a different question from the last one; ``request`` and
    it together are the key. ``directory`` is for a caller that wants to say where --
    the run's own is ``runs/`` under the cache root, which is what moves with
    ``$BUY_AGENT_CACHE_DIR`` and what deleting throws the whole history away.
    """
    if not keeping:
        return Journal(None, "")
    where = directory if directory is not None else default_dir(RUNS)
    key = json.dumps(
        {"request": request.strip().casefold(), **dict(asked)},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return Journal(where, key)


def compare(before: Entry, now: Sequence[Recorded]) -> list[Change]:
    """What changed between one run of a search and the next (ADR-0060).

    This run's products first, in the order it ranked them, and then whatever the last
    run reported that this one did not -- which is the half a shopper notices, since a
    product that has left the report leaves nothing behind to read.
    """
    was = {item.key: item for item in before.products}
    when = before.when()
    changes = [_moved(item, was.get(item.key), when) for item in now]
    found = {item.key for item in now}
    changes += [
        Change(
            name=item.name,
            movement="gone",
            was_label=item.label(),
            detail=f"Reported at {item.label()} on {when}, and not in this run.",
        )
        for item in before.products
        if item.key not in found
    ]
    return changes


def _moved(now: Recorded, before: Recorded | None, when: str) -> Change:
    """What one product of this run did, against what the last one said about it."""
    label = now.label()
    if before is None:
        return Change(
            name=now.name,
            movement="new",
            price_label=label,
            detail=f"{label}, and not in the run of {when}.",
        )
    was = before.label()
    if now.price is None or before.price is None or now.currency != before.currency:
        # One of the two is a figure this cannot be held against the other: a price no
        # page printed this time, or one printed in another currency. Nothing is
        # converted, so there is no movement to report (ADR-0043).
        return Change(
            name=now.name,
            movement="unplaced",
            price_label=label,
            was_label=was,
            detail=(
                f"{label} now and {was} on {when}; nothing is converted, so there is "
                f"no movement to report."
            ),
        )
    delta = round(now.price - before.price, 2)
    if delta == 0:
        return Change(
            name=now.name,
            movement="steady",
            price_label=label,
            was_label=was,
            delta=0.0,
            detail=f"{label}, unchanged since {when}.",
        )
    direction = "cheaper" if delta < 0 else "dearer"
    return Change(
        name=now.name,
        movement=direction,
        price_label=label,
        was_label=was,
        delta=delta,
        detail=(
            f"{label}, {amount_label(abs(delta), now.currency)} {direction} than "
            f"on {when}."
        ),
    )


def _forget_the_least_recent(directory: Path, searches: int) -> int:
    """Delete the searches nobody has run lately, and say how many went (ADR-0060).

    By the file's own mtime, which is when that search last ran: the entry a comparison
    wants is the newest one of a search somebody is still running, and pruning the
    oldest *entry* would be deleting exactly that.
    """
    dated: list[tuple[float, Path]] = []
    for path in directory.glob("*.json"):
        try:
            dated.append((path.stat().st_mtime, path))
        except OSError:  # another run replacing it right now
            continue
    forgotten = 0
    for _, path in sorted(dated)[: max(0, len(dated) - searches)]:
        if _unlink(path):
            forgotten += 1
    if forgotten:
        logger.debug(
            "Forgot %d search(es) nobody has run lately, out of %s", forgotten, directory
        )
    return forgotten


def _unlink(path: Path) -> bool:
    """Delete a file, saying whether it went. A journal never fails a run over one."""
    try:
        path.unlink()
    except OSError:
        return False
    return True
