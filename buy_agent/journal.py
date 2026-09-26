"""What past runs of one search reported, so the next can say what moved (ADR-0060).

Not a cache: a record for a person, which never expires.

- Bounded by count: :data:`MAX_RUNS` per search, :data:`MAX_SEARCHES` searches, least
  recently run out first.
- Holds name, price and currency per product, nothing else.
- Off with one setting; stored in ``runs/`` under ``$BUY_AGENT_CACHE_DIR``.
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

#: The journal's directory under the cache root.
RUNS = "runs"

#: Runs kept per search.
MAX_RUNS = 10

#: Searches kept; the least recently *run* goes first.
MAX_SEARCHES = 200

#: What a product did between two runs; ``unplaced`` for differing currencies (ADR-0043).
Movement = Literal["new", "gone", "cheaper", "dearer", "steady", "unplaced"]

#: A date as shown: "11 Sep".
_WHEN = "%d %b"


class Recorded(BaseModel):
    """One product as a past run reported it (ADR-0060)."""

    name: str
    price: float | None = None
    currency: str | None = None

    @classmethod
    def of(cls, product: Product) -> Recorded:
        """The recorded fields of a product."""
        return cls(name=product.name, price=product.price, currency=product.currency)

    @property
    def key(self) -> str:
        """``Product``'s own identity, matched across runs."""
        return dedup_key(self.name)

    def label(self) -> str:
        """The price as every surface writes it (ADR-0012)."""
        if self.price is None:
            return "price unknown"
        return amount_label(self.price, self.currency)


class Entry(BaseModel):
    """One past run of one search."""

    #: When it ran, in epoch seconds.
    at: float
    products: list[Recorded] = []

    def when(self) -> str:
        """The day it ran."""
        return datetime.fromtimestamp(self.at, tz=timezone.utc).strftime(_WHEN)


class Change(BaseModel):
    """What one product did since the last run of this search (ADR-0060)."""

    name: str
    movement: Movement
    #: Now and then, as Python writes amounts.
    price_label: str | None = None
    was_label: str | None = None
    #: Signed movement on one scale; negative is cheaper.
    delta: float | None = None
    #: The sentence both front ends show (ADR-0012).
    detail: str


class Journal:
    """The runs of one search, and the comparison of this one with the last.

    Built before the run, asked after. Never fails a run: off or unwritable, it
    remembers and compares nothing.
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
        #: The last run, read before this one overwrites it.
        self.before: Entry | None = self._last()

    @property
    def keeping(self) -> bool:
        """Whether anything is being written down at all."""
        return self.directory is not None

    def compared_with(self) -> str | None:
        """The day this run is being compared against, for a heading to name."""
        return self.before.when() if self.before else None

    def against(self, products: Sequence[Product]) -> list[Change]:
        """Record this run and say what moved since the last (ADR-0060).

        An empty run is compared but not recorded.
        """
        recorded = [Recorded.of(product) for product in products]
        if recorded:
            self._append(Entry(at=time.time(), products=recorded))
        if self.before is None:
            return []
        return compare(self.before, recorded)

    # -- the disk ------------------------------------------------------------------

    def _path(self) -> Path | None:
        return None if self.directory is None else file_for(self.directory, self.key)

    def _last(self) -> Entry | None:
        """The most recent run of this search that was written down."""
        path = self._path()
        if path is None:
            return None
        runs = self._read(path)
        return runs[-1] if runs else None

    def _read(self, path: Path) -> list[Entry]:
        """Every run of this search on disk, oldest first, or nothing at all."""
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # Unreadable is empty, like a cache miss.
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
        # Atomic, and never a failure.
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
    """The journal for this search, keyed by ``request`` and ``asked`` (ADR-0060).

    ``directory`` defaults to ``runs/`` under the cache root.
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
    """What changed between two runs (ADR-0060): this run's products in rank order,
    then those that are gone."""
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
        detail = f"{label}, and not in the run of {when}."
        return Change(name=now.name, movement="new", price_label=label, detail=detail)
    was = before.label()
    if now.price is None or before.price is None or now.currency != before.currency:
        # A missing price says why itself; two currencies need the reason (ADR-0043).
        missing = now.price is None or before.price is None
        why = "" if missing else "; nothing is converted"
        return Change(
            name=now.name,
            movement="unplaced",
            price_label=label,
            was_label=was,
            detail=f"{label} now and {was} on {when}{why}, so there is no movement to report.",
        )
    delta = round(now.price - before.price, 2)
    movement: Movement = "steady" if delta == 0 else "cheaper" if delta < 0 else "dearer"
    moved = f"{amount_label(abs(delta), now.currency)} {movement} than on {when}"
    return Change(
        name=now.name,
        movement=movement,
        price_label=label,
        was_label=was,
        delta=delta or 0.0,
        detail=f"{label}, {moved}." if delta else f"{label}, unchanged since {when}.",
    )


def _forget_the_least_recent(directory: Path, searches: int) -> int:
    """Delete the least recently run searches beyond ``searches`` (by mtime), and say
    how many went (ADR-0060)."""
    dated: list[tuple[float, Path]] = []
    for path in directory.glob("*.json"):
        try:
            dated.append((path.stat().st_mtime, path))
        except OSError:  # another run replacing it right now
            continue
    forgotten = sum(_unlink(path) for _, path in sorted(dated)[: max(0, len(dated) - searches)])
    if forgotten:
        logger.debug(
            "Forgot %d search(es) nobody has run lately, out of %s", forgotten, directory
        )
    return forgotten


def _unlink(path: Path) -> bool:
    """Delete a file, saying whether it went."""

    try:
        path.unlink()
    except OSError:
        return False
    return True
