"""What a run can reuse from the last one, kept on disk (ADR-0040, ADR-0044, ADR-0052)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from buy_agent.chat import UnreadableAnswerError, read_answer, release

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from buy_agent.chat import ChatModel, Message, SchemaT

logger = logging.getLogger(__name__)

#: How long a stored entry stays usable, in seconds.
DEFAULT_TTL = 86_400.0

#: How much disk one kind of entry may take up, oldest first out -- age alone being no
#: bound on size (ADR-0052).
MAX_BYTES = 256 * 1024 * 1024

#: Under the directory each platform keeps disposable things in: deleting the whole of
#: it costs one slow run.
_DIRECTORY = "buy-agent"

#: The two kinds of entry, each in its own directory: pruned and counted separately, and
#: keyed differently -- a URL against a whole request.
PAGES = "pages"
ANSWERS = "answers"


def default_dir(kind: str) -> Path:
    """Where entries of one kind live."""
    named = os.getenv("BUY_AGENT_CACHE_DIR")
    if named:
        return Path(named) / kind
    # LOCALAPPDATA on Windows, XDG_CACHE_HOME where it is set, and ~/.cache -- which is
    # the fallback on every platform that named neither.
    base = os.getenv("LOCALAPPDATA") or os.getenv("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / _DIRECTORY / kind


def file_for(directory: Path, key: str) -> Path:
    """Which file in ``directory`` holds what was stored under ``key``.

    Hashed rather than spelled out, a key here being a URL or a whole request, and
    shared with :mod:`buy_agent.journal` for the plainer reason two spellings of one
    key would be two files.
    """
    return directory / f"{hashlib.sha256(key.encode('utf-8')).hexdigest()}.json"


def write_atomically(directory: Path, destination: Path, text: str) -> OSError | None:
    """Put ``text`` at ``destination``, and say what stopped it where something did.

    Written beside the destination and moved onto it, so nothing ever reads half a
    file, and the half-written one is taken back where the move failed -- left behind
    it would be read as an entry. Shared with :mod:`buy_agent.journal`, which keeps a
    different thing under different rules (ADR-0060) and keeps it the same way: what is
    written down here is worth less than the run it would otherwise interrupt, so
    neither caller ever raises.

    The failure is answered rather than logged, because what to call it is the caller's:
    a page not cached and a journal not written are the same ``OSError`` and not the
    same sentence.
    """
    temporary = ""
    try:
        directory.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=directory, suffix=".tmp")
        with os.fdopen(handle, "w", encoding="utf-8") as entry:
            entry.write(text)
        os.replace(temporary, destination)
    except OSError as exc:
        with suppress(OSError):
            # Empty only where ``mkstemp`` is what failed, and then there is nothing on
            # disk to take back.
            if temporary:
                Path(temporary).unlink(missing_ok=True)
        return exc
    return None


class DiskCache:
    """Text kept on disk under a key, one JSON file each, expiring by age."""

    def __init__(
        self, directory: Path, *, ttl: float = DEFAULT_TTL, max_bytes: int = MAX_BYTES
    ) -> None:
        self.directory = directory
        self.ttl = ttl
        #: The most this directory may hold once the expired entries are out of it -- a
        #: bound on disk rather than on age, which :meth:`prune` enforces by deleting
        #: the oldest first (ADR-0052).
        self.max_bytes = max_bytes

    def get(self, key: str) -> str | None:
        """The text stored for ``key``, or None for a miss."""
        path = self._path(key)
        try:
            if time.time() - path.stat().st_mtime > self.ttl:
                return None
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # ValueError covers both ways a file can fail to be an entry: bytes that are
            # not UTF-8 (UnicodeDecodeError) and text that is not JSON.
            return None
        if not isinstance(entry, dict) or entry.get("key") != key:
            return None
        text = entry.get("value")
        return text if isinstance(text, str) else None

    def put(self, key: str, value: str) -> None:
        """Store ``value`` under ``key``, replacing whatever was there."""
        failed = write_atomically(
            self.directory, self._path(key), json.dumps({"key": key, "value": value})
        )
        if failed is not None:
            logger.debug(
                "Could not cache an entry in %s", self.directory, exc_info=failed
            )

    def prune(self) -> int:
        """Delete what has expired and what no longer fits, and say how many went
        (ADR-0052)."""
        cutoff = time.time() - self.ttl
        removed = 0
        leftovers = 0
        live: list[tuple[float, int, Path]] = []
        # ``glob`` answers an empty iterator for a directory it cannot list, so with the
        # three calls below guarded this cannot raise -- which is what lets
        # ``open_cache`` call it unguarded.
        for path in (*self.directory.glob("*.json"), *self.directory.glob("*.tmp")):
            try:
                stat = path.stat()
                if stat.st_mtime >= cutoff:
                    if path.suffix == ".json":
                        live.append((stat.st_mtime, stat.st_size, path))
                    continue
                path.unlink()
            except OSError:  # a file another run is replacing right now
                continue
            if path.suffix == ".json":
                removed += 1
            else:
                leftovers += 1
        if leftovers:
            logger.debug(
                "Cleared %d abandoned temporary file(s) in %s", leftovers, self.directory
            )
        return removed + self._evict(live)

    def _evict(self, live: list[tuple[float, int, Path]]) -> int:
        """Delete the oldest of ``live`` until the rest fits, and say how many went."""
        total = sum(size for _, size, _ in live)
        if total <= self.max_bytes:
            return 0
        evicted = 0
        for _, size, path in sorted(live):
            try:
                path.unlink()
            except OSError:  # as above: another run got there first
                continue
            evicted += 1
            total -= size
            if total <= self.max_bytes:
                break
        # DEBUG like everything else here: a cache tidying itself is nobody's news, and
        # the line a shopper reads about the cache is ``enrich``'s count of how many
        # pages came off disk.
        logger.debug(
            "Dropped %d cached entr%s from %s to stay under %d bytes",
            evicted,
            "y" if evicted == 1 else "ies",
            self.directory,
            self.max_bytes,
        )
        return evicted

    def _path(self, key: str) -> Path:
        return file_for(self.directory, key)


def open_cache(kind: str, ttl: float) -> DiskCache | None:
    """The cache of one kind a run should use, or None for a run using none."""
    if ttl <= 0:
        return None
    cache = DiskCache(default_dir(kind), ttl=ttl)
    cache.prune()
    return cache


class RememberedAnswers:
    """A model server, with the answers it has already given handed back (ADR-0044)."""

    def __init__(
        self, model: ChatModel, cache: DiskCache, fingerprint: Mapping[str, Any]
    ) -> None:
        self.model = model
        self.cache = cache
        self.fingerprint = dict(fingerprint)

    def answer(self, messages: Sequence[Message], schema: type[SchemaT]) -> SchemaT:
        """This chain's answer, off disk where the same question was asked before."""
        key = self._key(messages, schema)
        stored = self.cache.get(key)
        if stored is not None:
            try:
                remembered = read_answer(stored, schema)
            except UnreadableAnswerError:
                # A miss like any other: it should not happen, the schema being part of
                # the key, and it costs a model call rather than a run.
                logger.debug("A remembered answer could not be read back")
            else:
                logger.info("Reused a remembered %s answer", schema.__name__)
                return remembered

        answer = self.model.answer(messages, schema)
        self.cache.put(key, answer.model_dump_json())
        return answer

    def close(self) -> None:
        """Let go of what the model underneath holds open."""
        release(self.model)

    def _key(self, messages: Sequence[Message], schema: type[SchemaT]) -> str:
        """Everything this question is: the request, the schema, and the run (ADR-0004)."""
        return json.dumps(
            {
                **self.fingerprint,
                "schema": schema.model_json_schema(),
                "messages": [dict(message) for message in messages],
            },
            sort_keys=True,
            separators=(",", ":"),
        )


def remember_answers(
    model: ChatModel,
    *,
    fingerprint: Mapping[str, Any],
    ttl: float,
    deterministic: bool,
) -> ChatModel:
    """``model``, answering off disk where it may, or ``model`` itself where not
    (ADR-0044)."""
    if not deterministic:
        return model
    cache = open_cache(ANSWERS, ttl)
    return model if cache is None else RememberedAnswers(model, cache, fingerprint)
