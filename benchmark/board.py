"""Every run a comparison kept, so a model scored last week stands beside one scored
today (ADR-0070). Kept beside the cache, as the run journal is (ADR-0060), and read
afresh on every look so the command line and the page share it."""

from __future__ import annotations

import json
import logging
import threading
from typing import TYPE_CHECKING

from pydantic import ValidationError

from buy_agent.cache import default_dir, write_atomically
from benchmark.cases import CASES
from benchmark.compare import CaseRun

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

#: The directory the board is kept in, beside ``pages/``, ``answers/`` and ``runs/``.
BOARD = "benchmark"

#: The board's file in it.
FILENAME = "board.json"

#: Raised when a kept run changes shape, so an older board reads as empty, not wrongly.
VERSION = 1


class Board:
    """The kept runs: one per contender and case, the latest."""

    def __init__(self, path: Path | None = None) -> None:
        #: ``$BUY_AGENT_CACHE_DIR/benchmark/board.json`` unless named.
        self.path = path or default_dir(BOARD) / FILENAME
        self._lock = threading.Lock()

    def runs(self) -> list[CaseRun]:
        """Every kept run still scored against the case this checkout has."""
        with self._lock:
            return self._read()

    def add(self, run: CaseRun) -> None:
        """Keep ``run``, in place of any earlier run of the same contender and case."""
        with self._lock:
            kept = [
                old
                for old in self._read()
                if (old.contender, old.case) != (run.contender, run.case)
            ]
            self._write([*kept, run])

    def clear(self) -> None:
        """Forget every run."""
        with self._lock:
            self._write([])

    def _read(self) -> list[CaseRun]:
        """The board's runs, less any it cannot read or that scored another case."""
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:
            logger.warning("Could not read the board at %s (%s); starting afresh.", self.path, exc)
            return []
        if (
            not isinstance(document, dict)
            or document.get("version") != VERSION
            or not isinstance(entries := document.get("runs"), list)
        ):
            logger.warning("The board at %s is not one this can read; starting afresh.", self.path)
            return []

        runs: list[CaseRun] = []
        for entry in entries:
            try:
                run = CaseRun.model_validate(entry)
            except ValidationError:
                continue
            case = CASES.get(run.case)
            # A case edited since was scored against another key (ADR-0036).
            if case is not None and case.fingerprint == run.fingerprint:
                runs.append(run)
        if len(runs) < len(entries):
            logger.info(
                "Left out %d kept run(s) scored against cases this checkout does not have",
                len(entries) - len(runs),
            )
        return runs

    def _write(self, runs: list[CaseRun]) -> None:
        """Put ``runs`` on disk whole, or say why not."""
        document = {"version": VERSION, "runs": [run.model_dump(mode="json") for run in runs]}
        failed = write_atomically(self.path.parent, self.path, json.dumps(document, indent=1))
        if failed is not None:
            logger.warning("Could not keep the board at %s: %s", self.path, failed)


__all__ = ["BOARD", "FILENAME", "VERSION", "Board"]
