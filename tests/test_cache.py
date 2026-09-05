"""The page cache: what it stores, what it refuses to answer with, and where.

Nothing here touches the network -- the cache never does. It is a directory, a
clock and a hash, and the point of most of these tests is that every way of it
going wrong comes back as a miss rather than as a failed run.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from buy_agent.cache import DEFAULT_TTL, PageCache, default_dir, open_cache

URL = "https://example.com/headphones"

#: What a root named by one of the platform variables comes out as.
ROOTED = "/root/buy-agent/pages"


@pytest.fixture
def cache(tmp_path: Path) -> PageCache:
    return PageCache(tmp_path / "pages", ttl=DEFAULT_TTL)


# -- storing and reading back --------------------------------------------------


def test_a_stored_page_comes_back(cache: PageCache) -> None:
    cache.put(URL, "Sony WH-1000XM5 -- $328")

    assert cache.get(URL) == "Sony WH-1000XM5 -- $328"


def test_a_page_nobody_stored_is_a_miss(cache: PageCache) -> None:
    assert cache.get(URL) is None


def test_storing_again_replaces_what_was_there(cache: PageCache) -> None:
    """Prices move; the newer read is the one worth keeping."""
    cache.put(URL, "$328")
    cache.put(URL, "$299")

    assert cache.get(URL) == "$299"


def test_two_urls_do_not_share_an_entry(cache: PageCache) -> None:
    cache.put(URL, "sony")
    cache.put("https://example.com/anker", "anker")

    assert cache.get(URL) == "sony"
    assert cache.get("https://example.com/anker") == "anker"


def test_a_url_no_filesystem_would_take_as_a_name_is_still_storable(
    cache: PageCache,
) -> None:
    """Which is why the file is named by a hash of the URL and not by the URL."""
    awkward = "https://example.com/a?b=c&d=../../e:f*g|h" + "z" * 500
    cache.put(awkward, "kept")

    assert cache.get(awkward) == "kept"


def test_the_directory_is_made_on_the_first_write(tmp_path: Path) -> None:
    directory = tmp_path / "not" / "there" / "yet"
    PageCache(directory, ttl=DEFAULT_TTL).put(URL, "text")

    assert directory.is_dir()


def test_an_empty_page_is_stored_as_itself(cache: PageCache) -> None:
    """`get` answers None for a miss, so a stored empty string has to come back as
    an empty string rather than as "nothing stored" -- otherwise the two are one
    answer and the caller cannot tell them apart."""
    cache.put(URL, "")

    assert cache.get(URL) == ""


# -- the ways an entry stops counting ------------------------------------------


def test_an_entry_past_its_time_to_live_is_a_miss(tmp_path: Path) -> None:
    cache = PageCache(tmp_path, ttl=0.0001)
    cache.put(URL, "stale")
    _age(cache, URL, seconds=60)

    assert cache.get(URL) is None


def test_an_entry_inside_its_time_to_live_is_a_hit(tmp_path: Path) -> None:
    cache = PageCache(tmp_path, ttl=3600)
    cache.put(URL, "fresh")
    _age(cache, URL, seconds=60)

    assert cache.get(URL) == "fresh"


@pytest.mark.parametrize(
    "written",
    [
        pytest.param(b"{not json", id="not json"),
        # The other ``ValueError`` a file can raise on the way to being an entry.
        pytest.param(b"\xff\xfe not text", id="not utf-8"),
        pytest.param(b'["a list"]', id="json, but not an entry"),
        # A hash is not a promise. Answering with this would let one page's text
        # stand in for another's, which is the one thing a cache must never do.
        pytest.param(
            json.dumps({"url": "https://elsewhere.example", "text": "else"}).encode(),
            id="another url's entry",
        ),
        pytest.param(json.dumps({"url": URL, "text": 42}).encode(), id="text that is not text"),
    ],
)
def test_anything_that_is_not_this_url_s_entry_is_a_miss(
    cache: PageCache, written: bytes
) -> None:
    """Every way a file can fail to be this URL's entry means "fetch it", which is
    the direction a cache is allowed to be wrong in."""
    cache.put(URL, "text")
    _entry(cache, URL).write_bytes(written)

    assert cache.get(URL) is None


# -- a cache that cannot be used is never a failed run --------------------------


def test_a_directory_that_cannot_be_created_is_not_an_error(tmp_path: Path) -> None:
    """A file where the directory should be. The run goes on without a cache."""
    blocked = tmp_path / "file"
    blocked.write_text("not a directory", encoding="utf-8")
    cache = PageCache(blocked / "pages", ttl=DEFAULT_TTL)

    cache.put(URL, "text")  # no raise

    assert cache.get(URL) is None


def test_a_write_that_fails_leaves_nothing_behind(
    cache: PageCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The half-written file is removed rather than left to be read as an entry."""
    monkeypatch.setattr(
        "buy_agent.cache.os.replace", _raising(OSError("no space left on device"))
    )

    cache.put(URL, "text")  # no raise

    assert cache.get(URL) is None
    assert list(cache.directory.iterdir()) == []


def test_a_temporary_file_that_cannot_be_removed_is_not_an_error(
    cache: PageCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both halves of the failure path fail. There is still nothing to do about
    it and still nothing worth failing a run for."""
    monkeypatch.setattr("buy_agent.cache.os.replace", _raising(OSError("nope")))
    monkeypatch.setattr(Path, "unlink", _raising(OSError("nor that")))

    cache.put(URL, "text")  # no raise

    assert cache.get(URL) is None


# -- pruning -------------------------------------------------------------------


def test_pruning_drops_the_stale_and_keeps_the_fresh(tmp_path: Path) -> None:
    cache = PageCache(tmp_path, ttl=3600)
    cache.put(URL, "old")
    cache.put("https://example.com/new", "new")
    _age(cache, URL, seconds=7200)

    assert cache.prune() == 1
    assert cache.get(URL) is None
    assert cache.get("https://example.com/new") == "new"


def test_pruning_a_directory_that_is_not_there_removes_nothing(tmp_path: Path) -> None:
    assert PageCache(tmp_path / "absent", ttl=3600).prune() == 0


def test_pruning_steps_over_an_entry_it_cannot_remove(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another run replacing the same page at the same moment, or a file somebody
    else owns. Neither is this run's to fail over, and the rest still go."""
    cache = PageCache(tmp_path, ttl=3600)
    cache.put(URL, "stubborn")
    cache.put("https://example.com/other", "also stale")
    _age(cache, URL, seconds=7200)
    _age(cache, "https://example.com/other", seconds=7200)
    stubborn = cache._path(URL)
    real_unlink = Path.unlink

    def unlink(self: Path, **kwargs: object):
        if self == stubborn:
            raise OSError("held open")
        return real_unlink(self, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)

    assert cache.prune() == 1


# -- opening one for a run -----------------------------------------------------


def test_a_time_to_live_of_zero_means_no_cache_at_all() -> None:
    """One setting rather than a number and a switch that can disagree."""
    assert open_cache(0) is None
    assert open_cache(-1) is None


def test_opening_a_cache_gives_one_at_the_default_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BUY_AGENT_CACHE_DIR", str(tmp_path / "pages"))

    cache = open_cache(3600)

    assert cache is not None
    assert cache.directory == tmp_path / "pages"
    assert cache.ttl == 3600


def test_opening_a_cache_prunes_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Once per run, over a run's worth of files -- cheaper than the first
    request that follows it, and the only thing keeping the directory bounded."""
    monkeypatch.setenv("BUY_AGENT_CACHE_DIR", str(tmp_path))
    stale = PageCache(tmp_path, ttl=3600)
    stale.put(URL, "old")
    _age(stale, URL, seconds=7200)

    assert open_cache(3600) is not None
    assert stale.get(URL) is None


# -- where it lives ------------------------------------------------------------


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        # Named outright, which is what the container and a scratch run use.
        pytest.param({"BUY_AGENT_CACHE_DIR": "/named"}, "/named", id="named outright"),
        # An unset variable and one exported empty mean the same thing; read as a
        # path, the second puts the cache in the working directory.
        pytest.param({"BUY_AGENT_CACHE_DIR": "", "XDG_CACHE_HOME": "/root"}, ROOTED, id="empty"),
        # Windows names one and everything else the other, so both are read here
        # and neither of these is a platform test.
        pytest.param({"LOCALAPPDATA": "/root"}, ROOTED, id="windows"),
        pytest.param({"XDG_CACHE_HOME": "/root"}, ROOTED, id="xdg"),
        # Naming neither is every platform's fallback.
        pytest.param({}, "/home/.cache/buy-agent/pages", id="neither"),
    ],
)
def test_where_the_cache_lives(
    environment: dict[str, str], expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("BUY_AGENT_CACHE_DIR", "LOCALAPPDATA", "XDG_CACHE_HOME"):
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: Path("/home")))

    assert default_dir() == Path(expected)


def _entry(cache: PageCache, url: str) -> Path:
    """The file holding this URL, so a test can break it in one specific way.

    Asked of the cache rather than worked out here: what the name of an entry is
    is the cache's own business, and a second copy of the rule would only ever
    test itself.
    """
    entry = cache._path(url)
    assert cache.get(url) is not None, "the entry has to be readable to be broken"
    return entry


def _age(cache: PageCache, url: str, *, seconds: float) -> None:
    """Backdate an entry, which is how freshness is tested without waiting."""
    entry = cache._path(url)
    stamp = entry.stat().st_mtime - seconds
    os.utime(entry, (stamp, stamp))


def _raising(exc: Exception):
    """A stand-in that fails however it is called."""

    def fail(*_args: object, **_kwargs: object):
        raise exc

    return fail
