"""What a run can reuse from the last one, kept on disk.

Two things, on the same rules and under the same time to live. The **pages**
:mod:`buy_agent.fetch` read (ADR-0040): a run opens ten of them, the ten a search
returns are the same ten an hour later, and re-fetching costs the wait, another
ten requests at shops that rate-limit, and a fresh chance for one of them to
answer 403 -- which blanks figures grounding would otherwise have backed. And the
**answers** a model server gave (ADR-0044): that is the rest of the minute, and a
run whose pages all came off disk is asking the same server the same question it
answered last time.

Two things about *what* is stored are load-bearing:

- A page is stored as its **visible text**, not as the condensed excerpt.
  ``page_chars`` and ``opinion_chars`` decide which lines of it survive into the
  prompt, so storing the excerpt would replay a stale one after either budget
  moved. Condensing is cheap and runs every time; fetching is what is skipped.
- Both are stored **whole**, exactly as the live thing produced them, so a cached
  run extracts from the text a fresh one would have and reports the answer a
  fresh one would have got. Nothing here trims.

Every operation is best-effort. A cache that cannot be read, written or created
is a slower run and never a failed one, so nothing here raises: an unwritable
directory, a half-written entry and a disk that filled up all read as a miss.
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

from buy_agent.chat import UnreadableAnswerError, read_answer

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from buy_agent.chat import ChatModel, Message, SchemaT

logger = logging.getLogger(__name__)

#: How long a stored page stays usable, in seconds. A day: prices move slower
#: than that, and a shopper comparing two runs an afternoon apart is comparing
#: the same pages rather than wondering which figures moved underneath them.
DEFAULT_TTL = 86_400.0

#: Under the directory each platform keeps disposable things in, because that is
#: what this is: deleting the whole of it costs one slow run.
_DIRECTORY = "buy-agent"

#: The two kinds of entry, each in its own directory under the root. Separate
#: because they are pruned, counted and reasoned about separately, and because a
#: page's key is a URL while an answer's is a whole request: one directory holding
#: both would make "how much of this run came off disk" two questions with one
#: answer.
PAGES = "pages"
ANSWERS = "answers"


def default_dir(kind: str) -> Path:
    """Where entries of one kind live.

    ``$BUY_AGENT_CACHE_DIR`` wins outright and holds both kinds, one directory
    each, which is how a run is pointed at a scratch directory or at a volume in
    the container. It is the one setting here with no flag and no form field, for
    the reason ``$VLLM_API_KEY`` is: a path on the server's disk is not a
    browser's to choose.
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

    The file name is a hash of the key, so a key of any length and any character
    becomes a name every filesystem takes. The key itself is stored *inside* the
    entry and checked on the way out: a hash is not a promise, and an entry that
    does not name the key asked for is a miss rather than one page's text -- or
    one prompt's answer -- quietly standing in for another's.

    A key is a URL for the pages and a whole rendered request for the answers,
    which is why it is stored rather than trusted: the second is long, and long is
    exactly where "the name is the hash" stops being an argument on its own.
    """

    def __init__(self, directory: Path, *, ttl: float = DEFAULT_TTL) -> None:
        self.directory = directory
        self.ttl = ttl

    def get(self, key: str) -> str | None:
        """The text stored for ``key``, or None for a miss.

        A miss is everything that is not a fresh, readable entry naming this key:
        no file, a file older than the time to live, one that is not JSON, one
        holding something other than an entry. All of them mean "do the work",
        which is the answer a cache is allowed to be wrong in the direction of.
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

        Written to a temporary file and moved into place, so a reader never sees
        half an entry and two runs storing the same page cannot interleave into
        one broken file. ``os.replace`` is the atomic move on both platforms.

        The cleanup is suppressed rather than guarded, because it runs *inside*
        the handler: an ``unlink`` that raised there would leave this raising
        after all, which is the one thing this module may not do.
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
        """Delete every entry past its time to live, and say how many went.

        Entries expire on the way out, so this changes no answer -- what it does
        is keep the directory from being every page ever read. Once per run, over
        a directory holding a run's worth of files at a time, which is cheaper
        than the first HTTP request that follows it.
        """
        cutoff = time.time() - self.ttl
        removed = 0
        # ``glob`` answers an empty iterator for a directory that is missing or
        # cannot be listed rather than raising, so the listing needs no guard of
        # its own -- and with the two calls below guarded, this cannot raise at
        # all, which is what lets ``open_cache`` call it without one either.
        for path in self.directory.glob("*.json"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:  # a file another run is replacing right now
                continue
        return removed

    def _path(self, key: str) -> Path:
        return self.directory / f"{hashlib.sha256(key.encode('utf-8')).hexdigest()}.json"


def open_cache(kind: str, ttl: float) -> DiskCache | None:
    """The cache of one kind a run should use, or None for a run using none.

    ``ttl <= 0`` is how "do all of it fresh" is spelled, on the command line and
    in the form alike -- one setting rather than a number and a switch that can
    disagree about whether the cache is on, and one setting for both kinds rather
    than two that can disagree about how old is too old.
    """
    if ttl <= 0:
        return None
    cache = DiskCache(default_dir(kind), ttl=ttl)
    cache.prune()
    return cache


class RememberedAnswers:
    """A model server, with the answers it has already given handed back.

    A :class:`~buy_agent.chat.ChatModel` wrapping a ``ChatModel``, so everything
    above it asks its one question and cannot tell: the pipeline sees a model that
    is sometimes very fast. That is the only way this is allowed to work, and it
    is why the key below has to hold *everything* that decides an answer.

    The key is the rendered messages, the schema decoding is constrained to, and
    the run's own fingerprint -- which is the caller's to build, this module having
    no business knowing what a provider is. A reworded prompt, a widened budget, a
    different model or a different server all change it, so all of them miss, and
    the one thing that hits is the same question asked of the same server twice.

    Only an answer is stored. A failure is not an answer, and a model that could
    not be reached is a state of the world rather than a fact about this question.
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
                # An entry that will not read back as the schema asked for is a
                # miss like any other. It should not happen -- the schema itself
                # is in the key -- and it costs a model call rather than a run.
                logger.debug("A remembered answer could not be read back")
            else:
                logger.info("Reused a remembered %s answer", schema.__name__)
                return remembered

        answer = self.model.answer(messages, schema)
        self.cache.put(key, answer.model_dump_json())
        return answer

    def _key(self, messages: Sequence[Message], schema: type[SchemaT]) -> str:
        """Everything this question is: the request, the schema, and the run.

        The schema goes in as the JSON schema itself rather than as the class's
        name, so a field added to ``ExtractedProduct`` -- which changes both the
        decoding grammar and the shape of the answer (ADR-0004) -- is a different
        question, and not the same one with a stale answer.
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

    Two things turn it off, and both are somebody's decision rather than a
    failure. ``ttl <= 0`` is the shopper asking for a live run, the same setting
    that reads every page off the web. ``deterministic`` false is the run being
    *sampled*: a model asked for a different answer each time has no answer to
    remember, and replaying one sample would be this cache changing a run's
    result, which is the one thing it may never do (ADR-0044).
    """
    if not deterministic:
        return model
    cache = open_cache(ANSWERS, ttl)
    return model if cache is None else RememberedAnswers(model, cache, fingerprint)
