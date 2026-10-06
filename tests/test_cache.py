"""The cache: what it stores, what it refuses to answer with, and where."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest

from buy_agent.cache import (
    ANSWERS,
    DEFAULT_TTL,
    PAGES,
    DiskCache,
    RememberedAnswers,
    default_dir,
    open_cache,
)
from buy_agent.chat import UnreadableAnswerError
from buy_agent.models import SearchQuery
from tests.conftest import FakeLLM

URL = "https://example.com/headphones"

#: What a root named by one of the platform variables comes out as.
ROOTED = "/root/buy-agent/pages"


@pytest.fixture
def cache(tmp_path: Path) -> DiskCache:
    return DiskCache(tmp_path / "pages", ttl=DEFAULT_TTL)


# -- storing and reading back --------------------------------------------------


# -- the ways an entry stops counting ------------------------------------------


def test_an_entry_past_its_time_to_live_is_a_miss(tmp_path: Path) -> None:
    cache = DiskCache(tmp_path, ttl=0.0001)
    cache.put(URL, "stale")
    _age(cache, URL, seconds=60)

    assert cache.get(URL) is None


def test_an_entry_exactly_its_time_to_live_old_is_fresh_to_reader_and_pruner_alike(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two sides of one line: were they to disagree there, a prune would delete an
    entry a read in the same second would have answered with."""
    cache = DiskCache(tmp_path, ttl=3600)
    cache.put(URL, "on the line")
    written = 1_800_000_000  # whole seconds, so the subtraction is exact
    os.utime(_entry(cache, URL), (written, written))
    monkeypatch.setattr("buy_agent.cache.time.time", lambda: written + 3600.0)

    assert cache.get(URL) == "on the line"
    assert cache.prune() == 0
    assert cache._path(URL).exists()


@pytest.mark.parametrize(
    "written",
    [
        pytest.param(b"{not json", id="not json"),
        # The other ``ValueError`` a file can raise on the way to being an entry.
        pytest.param(b"\xff\xfe not text", id="not utf-8"),
        pytest.param(b'["a list"]', id="json, but not an entry"),
        # A hash is not a promise.
        pytest.param(
            json.dumps({"key": "https://elsewhere.example", "value": "else"}).encode(),
            id="another url's entry",
        ),
        pytest.param(json.dumps({"key": URL, "value": 42}).encode(), id="a value that is not text"),
    ],
)
def test_anything_that_is_not_this_url_s_entry_is_a_miss(
    cache: DiskCache, written: bytes
) -> None:
    """Every way a file can fail to be this URL's entry means "fetch it", which is
    the direction a cache is allowed to be wrong in."""
    cache.put(URL, "text")
    _entry(cache, URL).write_bytes(written)

    assert cache.get(URL) is None


# -- a cache that cannot be used is never a failed run --------------------------


def test_a_write_that_fails_says_so_for_the_reader_who_asked(
    cache: DiskCache, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Not an error -- the run goes on and simply fetches again next time -- but a cache
    that never fills is worth being able to see, at DEBUG and naming where."""
    monkeypatch.setattr("buy_agent.cache.os.replace", _raising(OSError("disk full")))

    with caplog.at_level(logging.DEBUG, logger="buy_agent.cache"):
        cache.put(URL, "text")

    assert f"Could not cache an entry in {cache.directory}" in caplog.text
    assert "disk full" in caplog.text, "with the failure itself attached"


def test_an_entry_is_written_beside_itself_and_renamed_into_place(
    cache: DiskCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In the cache's own directory, so the rename is atomic, and as a ``.tmp``, so a
    run killed mid-write leaves a file ``prune`` knows to clear."""
    renamed: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def replace(source: str, destination: str) -> None:
        renamed.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr("buy_agent.cache.os.replace", replace)

    cache.put(URL, "text")

    [(written, entry)] = renamed
    assert (written.parent, written.suffix) == (cache.directory, ".tmp")
    assert entry == cache._path(URL)


# -- pruning -------------------------------------------------------------------


def test_pruning_leaves_a_temporary_file_another_run_is_writing(tmp_path: Path) -> None:
    """The cutoff is what makes taking the leftovers safe."""
    cache = DiskCache(tmp_path, ttl=3600)
    in_flight = tmp_path / "being-written.tmp"
    in_flight.write_text("half an entry", encoding="utf-8")

    assert cache.prune() == 0
    assert in_flight.exists()


def test_pruning_counts_the_entries_and_reports_the_leftovers(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Two kinds of file, and only one of them is an entry."""
    cache = DiskCache(tmp_path, ttl=3600)
    cache.put(URL, "old")
    _age(cache, URL, seconds=7200)
    orphan = tmp_path / "leftover.tmp"
    orphan.write_text("half an entry", encoding="utf-8")
    stale = orphan.stat().st_mtime - 7200
    os.utime(orphan, (stale, stale))

    with caplog.at_level("DEBUG", logger="buy_agent.cache"):
        assert cache.prune() == 1

    assert "1 abandoned temporary file(s)" in caplog.text


def test_pruning_steps_over_an_entry_it_cannot_remove(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another run replacing the same page at the same moment, or a file somebody
    else owns. Neither is this run's to fail over, and the rest still go.

    The stubborn one is whichever the sweep reaches first: chosen by name, the rest
    went only where the filesystem happened to list it last."""
    cache = DiskCache(tmp_path, ttl=3600)
    for url in (URL, "https://example.com/other", "https://example.com/third"):
        cache.put(url, "stale")
        _age(cache, url, seconds=7200)
    real_unlink = Path.unlink
    stubborn: list[Path] = []

    def unlink(self: Path, **kwargs: object):
        if not stubborn:
            stubborn.append(self)
            raise OSError("held open")
        return real_unlink(self, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)

    assert cache.prune() == 2
    assert [path.name for path in tmp_path.glob("*.json")] == [stubborn[0].name]


# -- opening one for a run -----------------------------------------------------


def test_any_time_to_live_at_all_is_a_cache() -> None:
    """The other side of that line, which is at nothing and not at a second."""
    assert open_cache(PAGES, 1) is not None


def test_opening_a_cache_gives_one_at_the_default_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BUY_AGENT_CACHE_DIR", str(tmp_path))

    cache = open_cache(PAGES, 3600)

    assert cache is not None
    assert cache.directory == tmp_path / PAGES
    assert cache.ttl == 3600


# -- where it lives ------------------------------------------------------------


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        # Named outright, which is what the container and a scratch run use.
        pytest.param({"BUY_AGENT_CACHE_DIR": "/named"}, "/named/pages", id="named outright"),
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

    assert default_dir(PAGES) == Path(expected)


def _entry(cache: DiskCache, url: str) -> Path:
    """The file holding this URL, so a test can break it in one specific way."""
    entry = cache._path(url)
    assert cache.get(url) is not None, "the entry has to be readable to be broken"
    return entry


def _age(cache: DiskCache, url: str, *, seconds: float) -> None:
    """Backdate an entry, which is how freshness is tested without waiting."""
    entry = cache._path(url)
    stamp = entry.stat().st_mtime - seconds
    os.utime(entry, (stamp, stamp))


def _raising(exc: Exception):
    """A stand-in that fails however it is called."""

    def fail(*_args: object, **_kwargs: object):
        raise exc

    return fail


# -- remembering what a model answered -----------------------------------------


def _remembering(tmp_path: Path, server: FakeLLM, **fingerprint: object):
    """``server``, remembering into ``tmp_path``."""
    cache = DiskCache(tmp_path / ANSWERS, ttl=DEFAULT_TTL)
    return RememberedAnswers(server, cache, {"model": "gemma4:12b", **fingerprint})


ASKED = [{"role": "user", "content": "headphones"}]


def test_the_order_a_fingerprint_is_written_in_is_not_part_of_the_question(
    tmp_path: Path,
) -> None:
    """A key is read back by a later process, which owes this one no dict order."""
    model = FakeLLM(query=SearchQuery(query="wireless headphones"))
    cache = DiskCache(tmp_path / ANSWERS, ttl=DEFAULT_TTL)
    RememberedAnswers(model, cache, {"model": "gemma4:12b", "provider": "ollama"}).answer(
        ASKED, SearchQuery
    )

    RememberedAnswers(model, cache, {"provider": "ollama", "model": "gemma4:12b"}).answer(
        ASKED, SearchQuery
    )

    assert len(model.calls) == 1


class Reworded(SearchQuery):
    """The same schema with a field described differently, which is a different
    decoding grammar and so a different question (ADR-0004)."""


Reworded.model_fields["query"].description = "something else entirely"
Reworded.model_rebuild(force=True)


def test_an_answer_that_will_not_read_back_is_a_miss(tmp_path: Path) -> None:
    """It should not happen -- the schema is in the key -- and it costs a model
    call rather than a run."""
    model = FakeLLM(query=SearchQuery(query="wireless headphones"))
    remembering = _remembering(tmp_path, model)
    remembering.answer(ASKED, SearchQuery)
    remembering.cache.put(remembering._key(ASKED, SearchQuery), '{"not": "a query"}')

    assert remembering.answer(ASKED, SearchQuery).query == "wireless headphones"
    assert len(model.calls) == 2


def test_a_model_that_failed_left_nothing_to_remember(tmp_path: Path) -> None:
    """A failure is not an answer: a stopped server is a state of the world, and
    storing one would answer the next run with it."""
    model = FakeLLM(raises=UnreadableAnswerError("Invalid JSON answer: nothing at all"))
    remembering = _remembering(tmp_path, model)
    for _ in range(2):
        with pytest.raises(UnreadableAnswerError):
            remembering.answer(ASKED, SearchQuery)

    assert len(model.calls) == 2


# -- how big it may get --------------------------------------------------------


def _filled(directory: Path, *keys: str) -> DiskCache:
    """A cache holding one entry per key, all the same size as each other."""
    cache = DiskCache(directory, ttl=3600)
    for key in keys:
        cache.put(key, "x" * 10)
    return cache


def test_expiry_is_asked_before_the_cap(tmp_path: Path) -> None:
    """In that order because expiry is free: an entry nobody may read again is no
    reason to delete one somebody may."""
    cache = _filled(tmp_path, "https://old", "https://new")
    _age(cache, "https://old", seconds=7200)
    one = _entry(cache, "https://new").stat().st_size

    tight = DiskCache(tmp_path, ttl=3600, max_bytes=one)

    assert tight.prune() == 1
    assert cache.get("https://new") == "x" * 10, "the cap had nothing left to do"


def test_an_entry_that_vanishes_mid_eviction_is_not_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another run may be replacing the very file this one chose, and nothing in
    this module raises. The budget is still met: the next-oldest goes instead."""
    cache = _filled(tmp_path, "https://a", "https://b", "https://c")
    _age(cache, "https://a", seconds=200)
    _age(cache, "https://b", seconds=100)
    oldest = _entry(cache, "https://a")
    one = _entry(cache, "https://c").stat().st_size
    unlink = Path.unlink

    def flaky(self: Path, *args: object, **kwargs: object) -> None:
        if self == oldest:
            raise OSError("another run got there first")
        unlink(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "unlink", flaky)
    tight = DiskCache(tmp_path, ttl=3600, max_bytes=one * 2)

    assert tight.prune() == 1
    assert cache.get("https://b") is None, "the one that could be taken was"
    assert cache.get("https://a") == "x" * 10


@pytest.mark.parametrize(
    ("cap_in_entries", "said"),
    [(2, "Dropped 1 cached entry"), (1, "Dropped 2 cached entries")],
)
def test_an_eviction_says_how_many_went(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, cap_in_entries: int, said: str
) -> None:
    """At DEBUG like the rest of this module: a cache tidying itself is nobody's
    news, and the line a shopper reads about it is ``enrich``'s count of how many
    pages came off disk."""
    cache = _filled(tmp_path, "https://a", "https://b", "https://c")
    one = _entry(cache, "https://c").stat().st_size

    with caplog.at_level(logging.DEBUG):
        DiskCache(tmp_path, ttl=3600, max_bytes=one * cap_in_entries).prune()

    assert said in caplog.text


def test_a_cache_that_cannot_be_tidied_is_not_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every candidate refusing to go leaves the directory over its cap, and the run
    carries on regardless: nothing in this module raises, and a cache that is too big
    is still a cache (ADR-0052)."""
    cache = _filled(tmp_path, "https://a", "https://b")
    one = _entry(cache, "https://b").stat().st_size
    monkeypatch.setattr(Path, "unlink", _raising(OSError("read-only filesystem")))

    tight = DiskCache(tmp_path, ttl=3600, max_bytes=one)

    assert tight.prune() == 0  # no raise
    assert cache.get("https://a") == "x" * 10
