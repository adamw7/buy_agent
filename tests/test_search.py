"""The DuckDuckGo wrapper, with the network stubbed out.

``buy_agent.search.DDGS`` is replaced wholesale rather than having its ``text``
method patched: the name ``ddgs`` exports is a wrapper that constructs a
different class, so a patched method on it would never be called.
"""

from __future__ import annotations

import logging

import pytest
from ddgs.exceptions import DDGSException

from buy_agent.search import _NO_RESULTS, _RETRY_WAIT, SearchError, SearchResult, search_web


def stub_ddgs(monkeypatch, *, results=None, error: Exception | None = None) -> dict:
    """Point search_web at a fake backend and return the call it records."""
    seen: dict = {}

    class FakeDDGS:
        def text(self, query: str, **kwargs) -> list[dict]:
            seen.update({"query": query, **kwargs})
            if error is not None:
                raise error
            return results or []

    monkeypatch.setattr("buy_agent.search.DDGS", FakeDDGS)
    return seen


def test_raw_results_are_mapped_onto_search_result(monkeypatch) -> None:
    stub_ddgs(
        monkeypatch,
        results=[{"title": "Sony XM5", "href": "https://shop/x", "body": "$328"}],
    )

    results = search_web("headphones")

    assert results[0].title == "Sony XM5"
    assert results[0].url == "https://shop/x"
    assert results[0].snippet == "$328"


def test_missing_fields_become_empty_strings(monkeypatch) -> None:
    stub_ddgs(monkeypatch, results=[{}])

    result = search_web("headphones")[0]

    assert (result.title, result.url, result.snippet) == ("", "", "")


def test_search_arguments_reach_the_backend(monkeypatch) -> None:
    seen = stub_ddgs(monkeypatch)

    search_web("laptops", max_results=4, region="pl-pl")

    assert seen == {"query": "laptops", "max_results": 4, "region": "pl-pl"}


def test_backend_failures_become_search_error(monkeypatch) -> None:
    stub_ddgs(monkeypatch, error=DDGSException("rate limit"))

    with pytest.raises(SearchError, match="rate limit"):
        search_web("headphones")


def test_prompt_block_shows_title_url_and_snippet(monkeypatch) -> None:
    stub_ddgs(monkeypatch, results=[{"title": "T", "href": "U", "body": "S"}])

    block = search_web("x")[0].as_prompt_block()

    assert block == "TITLE: T\nURL: U\nSNIPPET: S"


def test_the_prompt_block_includes_the_fetched_page_text() -> None:
    block = SearchResult(
        title="T", url="U", snippet="S", content="JBL Live 780NC\n$149"
    ).as_prompt_block()

    assert block == "TITLE: T\nURL: U\nSNIPPET: S\nPAGE:\nJBL Live 780NC\n$149"


def test_a_fresh_result_carries_no_page_content() -> None:
    """content is filled in later by fetch.enrich, if at all."""
    assert SearchResult(title="T").content == ""


def test_finding_nothing_is_an_empty_list_not_a_failure(monkeypatch) -> None:
    stub_ddgs(monkeypatch, results=[])

    assert search_web("something nobody sells") == []


def test_the_way_ddgs_itself_spells_finding_nothing_is_not_a_failure(monkeypatch) -> None:
    """ddgs raises rather than returning [], and that is not a backend failure.

    ``_search_sync`` ends in ``raise DDGSException(err or "No results found.")``,
    so a query that reached every engine and matched nothing arrives as an
    exception like any other. Read as one it became ``SearchError`` -- "the web
    search backend could not be reached", a 502 in the browser -- for a search
    that worked. The empty-list case above is the fake being kinder than the
    real thing, which is why it could not catch this on its own.
    """
    stub_ddgs(monkeypatch, error=DDGSException(_NO_RESULTS))

    assert search_web("something nobody sells") == []


def test_a_real_backend_failure_is_still_a_failure(monkeypatch) -> None:
    """The message is the discriminator, so anything else keeps raising."""
    stub_ddgs(monkeypatch, error=DDGSException("No results found. (engine timed out)"))

    with pytest.raises(SearchError):
        search_web("headphones")


def test_the_search_and_its_result_count_are_logged(monkeypatch, caplog) -> None:
    stub_ddgs(monkeypatch, results=[{"title": "A"}])

    with caplog.at_level(logging.INFO, logger="buy_agent.search"):
        search_web("headphones")

    assert "headphones" in caplog.text
    assert "Search returned 1 results" in caplog.text


def test_the_default_is_ten_results_from_the_us(monkeypatch) -> None:
    seen = stub_ddgs(monkeypatch)

    search_web("headphones")

    assert seen == {"query": "headphones", "max_results": 10, "region": "us-en"}


def test_an_unexpected_error_is_not_disguised_as_a_search_failure(monkeypatch) -> None:
    """Only backend failures become SearchError; a bug must not look like one."""
    stub_ddgs(monkeypatch, error=TypeError("bug in the wrapper"))

    with pytest.raises(TypeError, match="bug in the wrapper"):
        search_web("headphones")


def stub_sequence(monkeypatch, *answers) -> list[str]:
    """Point ``search_web`` at a backend giving each answer in turn.

    The retry is about the second search, so the stub has to be able to fail once and
    work afterwards -- which one that raises forever, or answers forever, cannot say.
    """
    remaining = list(answers)
    asked: list[str] = []

    class FakeDDGS:
        def text(self, query: str, **_: object) -> list[dict]:
            asked.append(query)
            answer = remaining.pop(0) if len(remaining) > 1 else remaining[0]
            if isinstance(answer, Exception):
                raise answer
            return answer

    monkeypatch.setattr("buy_agent.search.DDGS", FakeDDGS)
    return asked


def test_a_failed_search_is_asked_once_more(monkeypatch) -> None:
    """One search is the whole of a run's input, so the one that failed is worth
    asking twice -- a lost page leaves nine, a lost search leaves nothing (ADR-0053)."""
    asked = stub_sequence(
        monkeypatch,
        DDGSException("rate limit"),
        [{"title": "Sony XM5", "href": "https://shop/x", "body": "$328"}],
    )
    waits: list[float] = []

    results = search_web("headphones", wait=waits.append)

    assert [result.title for result in results] == ["Sony XM5"]
    assert waits == [_RETRY_WAIT]
    assert len(asked) == 2


def test_a_search_that_fails_twice_is_the_failure_it_always_was(monkeypatch) -> None:
    """The second failure is final, and carries its own message: a backend that is
    down stays down, and the sentence the shopper reads is the last thing it said."""
    asked = stub_sequence(
        monkeypatch, DDGSException("rate limit"), DDGSException("still rate limited")
    )

    with pytest.raises(SearchError, match="still rate limited"):
        search_web("headphones", wait=lambda _: None)

    assert len(asked) == 2


def test_a_search_is_asked_once_where_there_is_nothing_to_wait_by(monkeypatch) -> None:
    """The default: a step handed no clock does not wait, and the failure is immediate."""
    asked = stub_sequence(monkeypatch, DDGSException("rate limit"))

    with pytest.raises(SearchError, match="rate limit"):
        search_web("headphones")

    assert len(asked) == 1


def test_a_search_that_matched_nothing_is_never_asked_again(monkeypatch) -> None:
    """It worked. Asking again would match nothing twice and cost the wait to find
    out -- which is the difference between an answer and a failure."""
    asked = stub_sequence(monkeypatch, DDGSException(_NO_RESULTS))
    waits: list[float] = []

    assert search_web("headphones", wait=waits.append) == []
    assert (waits, len(asked)) == ([], 1)


def test_a_search_that_matched_nothing_on_the_second_try_is_still_an_answer(
    monkeypatch,
) -> None:
    """The no-results check is inside the loop, so it is asked of both attempts.

    A backend that failed and then had nothing is a search that worked: reported as
    a ``SearchError`` it would be "DuckDuckGo is unreachable" over a running one.
    """
    asked = stub_sequence(monkeypatch, DDGSException("rate limit"), DDGSException(_NO_RESULTS))

    assert search_web("headphones", wait=lambda _: None) == []
    assert len(asked) == 2


def test_the_retry_says_what_it_is_waiting_for(monkeypatch, caplog) -> None:
    """At WARNING: this is the failure the run would have ended on, and the line is
    what tells a run that took two seconds longer from one that nearly stopped."""
    stub_sequence(monkeypatch, DDGSException("rate limit"), [])

    with caplog.at_level(logging.WARNING):
        search_web("headphones", wait=lambda _: None)

    assert "asking again in 2s" in caplog.text
