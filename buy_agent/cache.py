"""What a run can reuse from the last one, kept on disk.

The pages :mod:`buy_agent.fetch` read (ADR-0040) and the answers a model server
gave (ADR-0044), on the same rules and the same time to live. A page is stored as
its *visible text* rather than the condensed excerpt, so moving ``page_chars`` or
``opinion_chars`` does not replay a stale one; both are stored whole, so a cached
run reports what a fresh one would have.

Both are bounded twice over: by age, which is ``cache_ttl``, and by size, which
is :data:`MAX_BYTES` and is what keeps a long-lived cache from being every page
ever read (ADR-0052).

Every operation is best-effort. Nothing here raises: an unwritable directory, a
half-written entry and a full disk all read as a miss.
"""

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

#: How long a stored entry stays usable, in seconds. A day: prices move slower
#: than that, so two runs an afternoon apart compare the same pages.
DEFAULT_TTL = 86_400.0

#: How much disk one kind of entry may take up, oldest first out. Age alone is no
#: bound on size: ``cache_ttl`` may be set to the thirty days
#: :data:`buy_agent.config.LIMITS` allows, a stored page is the whole visible text
#: of one rather than the excerpt a prompt saw (ADR-0040), and nothing here ever
#: deleted an entry that had not expired -- so a month of shopping was a month of
#: pages (ADR-0052).
#:
#: A quarter of a gigabyte per kind, which is thousands of pages: the cap is there
#: to have an upper bound at all, not to make a run choose between pages. It has
#: no flag and no form field, for the reason ``$BUY_AGENT_CACHE_DIR`` has none --
#: how much of the server's disk this may use is not a browser's to decide.
MAX_BYTES = 256 * 1024 * 1024

#: Under the directory each platform keeps disposable things in: deleting the
#: whole of it costs one slow run.
_DIRECTORY = "buy-agent"

#: The two kinds of entry, each in its own directory: pruned and counted
#: separately, and keyed differently -- a URL against a whole request.
PAGES = "pages"
ANSWERS = "answers"


def default_dir(kind: str) -> Path:
    """Where entries of one kind live.

    ``$BUY_AGENT_CACHE_DIR`` wins outright and holds both kinds, which is how a
    run is pointed at a scratch directory or a volume. It has no flag and no form
    field, for the reason ``$VLLM_API_KEY`` has none: a path on the server's disk
    is not a browser's to choose.
    """
    named = os.getenv("BUY_AGENT_CACHE_DIR")
    if named:
        return Path(named) / kind
    # LOCALAPPDATA on Windows, XDG_CACHE_HOME where it is set, and ~/.cache --
    # which is the fallback on every platform that named neither.
    base = os.getenv("LOCALAPPDATA") or os.getenv("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / _DIRECTORY / kind


class DiskCache:
    """Text kept on disk under a key, one JSON file each, expiring by age.

    The file name is a hash of the key, so any key becomes a name every filesystem
    takes; the key itself is stored *inside* the entry and checked on the way out,
    a hash being no promise that one page's text is not standing in for another's.
    """

    def __init__(
        self, directory: Path, *, ttl: float = DEFAULT_TTL, max_bytes: int = MAX_BYTES
    ) -> None:
        self.directory = directory
        self.ttl = ttl
        #: The most this directory may hold once the expired entries are out of
        #: it -- a bound on disk rather than on age, which :meth:`prune` enforces
        #: by deleting the oldest first (ADR-0052).
        self.max_bytes = max_bytes

    def get(self, key: str) -> str | None:
        """The text stored for ``key``, or None for a miss.

        A miss is everything that is not a fresh, readable entry naming this key.
        All of them mean "do the work", the direction a cache may be wrong in.
        """
        path = self._path(key)
        try:
            if time.time() - path.stat().st_mtime > self.ttl:
                return None
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # ValueError covers both ways a file can fail to be an entry: bytes
            # that are not UTF-8 (UnicodeDecodeError) and text that is not JSON.
            return None
        if not isinstance(entry, dict) or entry.get("key") != key:
            return None
        text = entry.get("value")
        return text if isinstance(text, str) else None

    def put(self, key: str, value: str) -> None:
        """Store ``value`` under ``key``, replacing whatever was there.

        Written to a temporary file and moved into place (``os.replace``, atomic
        on both platforms), so a reader never sees half an entry. The cleanup is
        suppressed rather than guarded: it runs inside the handler, and an
        ``unlink`` raising there would leave this module raising after all.
        """
        temporary = ""
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
            with os.fdopen(handle, "w", encoding="utf-8") as entry:
                json.dump({"key": key, "value": value}, entry)
            os.replace(temporary, self._path(key))
        except OSError:
            logger.debug("Could not cache an entry in %s", self.directory, exc_info=True)
            with suppress(OSError):
                # Empty only where ``mkstemp`` is what failed, and then there is
                # nothing on disk to take back.
                if temporary:
                    Path(temporary).unlink(missing_ok=True)

    def prune(self) -> int:
        """Delete what has expired and what no longer fits, and say how many went.

        Entries expire on the way out, so this changes no answer: it keeps the
        directory from being every page ever read. The half-written files a
        killed process leaves between ``mkstemp`` and ``os.replace`` go too, on
        the same cutoff -- nothing else ever looks at them, and the cutoff is
        what makes taking them safe, one a live run is writing being younger than
        the time to live. They are not entries, so they are reported at DEBUG
        rather than counted in the answer.

        Age is only half of it. What survives the cutoff is held to
        :attr:`max_bytes` as well, oldest first out (ADR-0052), so the size of
        this directory is bounded by something other than how often anybody
        shops. The two are asked in that order because expiry is free: an entry
        nobody may read again is no reason to delete one somebody may.
        """
        cutoff = time.time() - self.ttl
        removed = 0
        leftovers = 0
        live: list[tuple[float, int, Path]] = []
        # ``glob`` answers an empty iterator for a directory it cannot list, so
        # with the three calls below guarded this cannot raise -- which is what
        # lets ``open_cache`` call it unguarded.
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
        """Delete the oldest of ``live`` until the rest fits, and say how many went.

        Oldest first because that is the order they stop being worth keeping in: every
        entry here is still readable, so the only thing to choose between them by is
        which run is least likely to ask again. Sorted by modification time, which is
        also what the expiry above reads -- a cache with one clock rather than two.

        Each ``(mtime, size, path)`` comes from the single ``stat`` the caller already
        made: asking again here would be a second answer about a file another run may
        be replacing, and a size read twice is a budget that does not add up.
        """
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
        # DEBUG like everything else here: a cache tidying itself is nobody's
        # news, and the line a shopper reads about the cache is ``enrich``'s
        # count of how many pages came off disk.
        logger.debug(
            "Dropped %d cached entr%s from %s to stay under %d bytes",
            evicted,
            "y" if evicted == 1 else "ies",
            self.directory,
            self.max_bytes,
        )
        return evicted

    def _path(self, key: str) -> Path:
        return self.directory / f"{hashlib.sha256(key.encode('utf-8')).hexdigest()}.json"


def open_cache(kind: str, ttl: float) -> DiskCache | None:
    """The cache of one kind a run should use, or None for a run using none.

    ``ttl <= 0`` is how "do all of it fresh" is spelled -- one setting rather than
    a number and a switch that can disagree, and one for both kinds.
    """
    if ttl <= 0:
        return None
    cache = DiskCache(default_dir(kind), ttl=ttl)
    cache.prune()
    return cache


class RememberedAnswers:
    """A model server, with the answers it has already given handed back.

    A ``ChatModel`` wrapping a ``ChatModel``, so the pipeline just sees one that is
    sometimes very fast -- which is why the key holds everything deciding an
    answer: the messages, the schema and the run's fingerprint, built by the caller
    since this module has no business knowing what a provider is (ADR-0044). Only
    an answer is stored; a failure is a state of the world, not a fact about this
    question.
    """

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
                # A miss like any other: it should not happen, the schema being
                # part of the key, and it costs a model call rather than a run.
                logger.debug("A remembered answer could not be read back")
            else:
                logger.info("Reused a remembered %s answer", schema.__name__)
                return remembered

        answer = self.model.answer(messages, schema)
        self.cache.put(key, answer.model_dump_json())
        return answer

    def close(self) -> None:
        """Let go of what the model underneath holds open.

        Passed through: this wrapper holds nothing itself, a cache being a
        directory, while behind it is a client with a connection pool.
        """
        release(self.model)

    def _key(self, messages: Sequence[Message], schema: type[SchemaT]) -> str:
        """Everything this question is: the request, the schema, and the run.

        The schema goes in whole rather than by class name, so a field added to
        ``ExtractedProduct`` (ADR-0004) is a different question rather than the
        same one with a stale answer.
        """
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
    """``model``, answering off disk where it may, or ``model`` itself where not.

    Two decisions turn it off, neither a failure. ``ttl <= 0`` is the shopper
    asking for a live run. ``deterministic`` false is a *sampled* run: a model
    asked for a different answer each time has none to remember, and replaying one
    sample would change a run's result, which this may never do (ADR-0044).
    """
    if not deterministic:
        return model
    cache = open_cache(ANSWERS, ttl)
    return model if cache is None else RememberedAnswers(model, cache, fingerprint)
