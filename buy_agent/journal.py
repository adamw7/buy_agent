"""What past runs of one search reported, so the next can say what moved (ADR-0060).
Not a cache: it never expires, and is bounded by count instead."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from buy_agent.cache import default_dir, file_for, write_atomically
from buy_agent.models import Product, dedup_key, price_label
from buy_agent.money import amount_label

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

logger = logging.getLogger(__name__)

RUNS = "runs"
MAX_RUNS = 10
#: The least recently *run* goes first.
MAX_SEARCHES = 200

#: ``unplaced`` for differing currencies (ADR-0043).
Movement = Literal["new", "gone", "cheaper", "dearer", "steady", "unplaced"]

#: The day goes in front unpadded: ``%d`` wrote "01 Oct", and ``%-d`` is not portable.
_MONTH = "%b"


class Recorded(BaseModel):
    """One product as a past run reported it."""

    name: str
    # A NaN read back would be a movement of NaN, and invalid JSON.
    price: Annotated[float | None, Field(allow_inf_nan=False)] = None
    currency: str | None = None

    @classmethod
    def of(cls, product: Product) -> Recorded:
        return cls(name=product.name, price=product.price, currency=product.currency)

    @property
    def key(self) -> str:
        return dedup_key(self.name)

    def label(self) -> str:
        return price_label(self.price, self.currency)


class Entry(BaseModel):
    """One past run of one search."""

    at: float
    products: list[Recorded] = []

    @field_validator("at")
    @classmethod
    def _dated(cls, at: float) -> float:
        """A time :meth:`when` can date; anything else is a damaged file."""
        try:
            datetime.fromtimestamp(at)
        # NaN is a ``ValueError``; too far out, ``OverflowError`` or (Windows) ``OSError``.
        except (ValueError, OverflowError, OSError) as exc:
            raise ValueError(f"{at!r} is not a time this machine can date") from exc
        return at

    def when(self) -> str:
        """The day it ran, on this machine's calendar, as its log lines are timed."""
        ran = datetime.fromtimestamp(self.at)
        return f"{ran.day} {ran.strftime(_MONTH)}"


class Change(BaseModel):
    """What one product did since the last run of this search."""

    name: str
    movement: Movement
    price_label: str | None = None
    was_label: str | None = None
    #: Negative is cheaper.
    delta: float | None = None
    #: The sentence both front ends show.
    detail: str


class Journal:
    """The runs of one search, built before the run and asked after. Never fails a
    run: off or unwritable, it remembers and compares nothing."""

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
        return self.directory is not None

    def compared_with(self) -> str | None:
        """The day this run is being compared against."""
        return self.before.when() if self.before else None

    def against(self, products: Sequence[Product]) -> list[Change]:
        """Record this run, unless empty, and say what moved since the last."""
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
        path = self._path()
        if path is None:
            return None
        runs = self._read(path)
        return runs[-1] if runs else None

    def _read(self, path: Path) -> list[Entry]:
        """Every run of this search on disk, oldest first; unreadable is empty."""
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(stored, dict) or stored.get("key") != self.key:
            return []
        try:
            return [Entry.model_validate(run) for run in stored.get("runs", [])]
        except ValidationError:
            logger.debug("A journal entry could not be read back", exc_info=True)
            return []

    def _append(self, entry: Entry) -> None:
        path = self._path()
        if path is None or self.directory is None:
            return
        runs = [*self._read(path), entry][-self.keep :]
        document = json.dumps(
            {"key": self.key, "runs": [run.model_dump() for run in runs]},
            separators=(",", ":"),
        )
        failed = write_atomically(self.directory, path, document)
        if failed is not None:
            logger.debug("Could not write the journal in %s", self.directory, exc_info=failed)
            return
        _forget_the_least_recent(self.directory, self.searches)


def open_journal(
    request: str, *, asked: Mapping[str, Any], keeping: bool, directory: Path | None = None
) -> Journal:
    """The journal for this search, keyed by ``request`` and ``asked``; ``directory``
    defaults to ``runs/`` under the cache root."""
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
    """This run's products in rank order, then those that are gone."""
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
    """Delete the least recently run searches (by mtime) beyond ``searches``."""
    dated: list[tuple[float, Path]] = []
    for path in directory.glob("*.json"):
        try:
            dated.append((path.stat().st_mtime, path))
        except OSError:  # another run replacing it right now
            continue
    forgotten = sum(_unlink(path) for _, path in sorted(dated)[: max(0, len(dated) - searches)])
    if forgotten:
        logger.debug("Forgot %d search(es) nobody has run lately, out of %s", forgotten, directory)
    return forgotten


def _unlink(path: Path) -> bool:
    try:
        path.unlink()
    except OSError:
        return False
    return True
