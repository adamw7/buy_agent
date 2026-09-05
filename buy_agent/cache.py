"""A disk cache for the pages :mod:`buy_agent.fetch` reads.

A run opens ten pages, and most of a minute of it is that plus the extraction
over what they said. Neither is interesting the second time: the pages a search
returns are the same pages an hour later, and re-fetching them costs the wait,
another ten requests at shops that rate-limit, and a fresh chance for one of them
to answer 403 -- which blanks figures grounding would otherwise have backed. So
what a page said is kept for a while (ADR-0040).

Two things about *what* is stored are load-bearing:

- It is the page's **visible text**, not the condensed excerpt. ``page_chars``
  and ``opinion_chars`` decide which lines of it survive into the prompt, so
  storing the excerpt would replay a stale one after either budget moved.
  Condensing is cheap and runs every time; fetching is what is skipped.
- It is stored **whole**, exactly as a live fetch produced it, so a cached run
  extracts from the text a fresh one would have. Nothing here trims: the ceiling
  is :data:`buy_agent.fetch._MAX_PAGE_BYTES`, already applied by the fetch.

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

logger = logging.getLogger(__name__)

#: How long a stored page stays usable, in seconds. A day: prices move slower
#: than that, and a shopper comparing two runs an afternoon apart is comparing
#: the same pages rather than wondering which figures moved underneath them.
DEFAULT_TTL = 86_400.0

#: Where entries go when ``$BUY_AGENT_CACHE_DIR`` does not say. Under the
#: directory each platform keeps disposable things in, because that is what this
#: is: deleting the whole of it costs one slow run.
_DIRECTORY = ("buy-agent", "pages")


def default_dir() -> Path:
    """Where the cache lives.

    ``$BUY_AGENT_CACHE_DIR`` wins outright, which is how a run is pointed at a
    scratch directory or at a volume in the container. It is the one setting here
    with no flag and no form field, for the reason ``$VLLM_API_KEY`` is: a path on
    the server's disk is not a browser's to choose.
    """
    named = os.getenv("BUY_AGENT_CACHE_DIR")
    if named:
        return Path(named)
    # LOCALAPPDATA on Windows, XDG_CACHE_HOME where it is set, and ~/.cache --
    # which is the fallback on every platform that named neither.
    base = os.getenv("LOCALAPPDATA") or os.getenv("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root.joinpath(*_DIRECTORY)


class PageCache:
    """Page text kept on disk, one JSON file per URL, expiring by age.

    The file name is a hash of the URL, so a URL of any length and any character
    becomes a name every filesystem takes. The URL itself is stored *inside* the
    entry and checked on the way out: a hash is not a promise, and an entry that
    does not name the URL asked for is a miss rather than another page's text
    quietly standing in for this one.
    """

    def __init__(self, directory: Path, *, ttl: float = DEFAULT_TTL) -> None:
        self.directory = directory
        self.ttl = ttl

    def get(self, url: str) -> str | None:
        """The text stored for ``url``, or None for a miss.

        A miss is everything that is not a fresh, readable entry naming this URL:
        no file, a file older than the time to live, one that is not JSON, one
        holding something other than an entry. All of them mean "fetch it", which
        is the answer a cache is allowed to be wrong in the direction of.
        """
        path = self._path(url)
        try:
            if time.time() - path.stat().st_mtime > self.ttl:
                return None
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # ValueError covers both ways a file can fail to be an entry: bytes
            # that are not UTF-8 (UnicodeDecodeError) and text that is not JSON.
            return None
        if not isinstance(entry, dict) or entry.get("url") != url:
            return None
        text = entry.get("text")
        return text if isinstance(text, str) else None

    def put(self, url: str, text: str) -> None:
        """Store the text of ``url``, replacing whatever was there.

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
                json.dump({"url": url, "text": text}, entry)
            os.replace(temporary, self._path(url))
        except OSError:
            logger.debug("Could not cache %s", url, exc_info=True)
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

    def _path(self, url: str) -> Path:
        return self.directory / f"{hashlib.sha256(url.encode('utf-8')).hexdigest()}.json"


def open_cache(ttl: float) -> PageCache | None:
    """The cache a run should use, or None for a run that should not use one.

    ``ttl <= 0`` is how "fetch everything fresh" is spelled, on the command line
    and in the form alike -- one setting rather than a number and a switch that
    can disagree about whether the cache is on.
    """
    if ttl <= 0:
        return None
    cache = PageCache(default_dir(), ttl=ttl)
    cache.prune()
    return cache
