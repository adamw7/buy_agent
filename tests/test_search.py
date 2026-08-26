"""The DuckDuckGo wrapper, with the network stubbed out.

``buy_agent.search.DDGS`` is replaced wholesale rather than having its ``text``
method patched: the name ``ddgs`` exports is a wrapper that constructs a
different class, so a patched method on it would never be called.
"""

from __future__ import annotations

import logging

import pytest
from ddgs.exceptions import DDGSException

from buy_agent.search import SearchError, SearchResult, search_web


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
