"""The DuckDuckGo wrapper, with the network stubbed out.

``buy_agent.search.DDGS`` is replaced wholesale rather than having its ``text``
method patched: the name ``ddgs`` exports is a wrapper that constructs a
different class, so a patched method on it would never be called.
"""

from __future__ import annotations

import pytest
from ddgs.exceptions import DDGSException

from buy_agent.search import SearchError, search_web


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


def test_the_tool_renders_results_for_the_model(monkeypatch) -> None:
    stub_ddgs(
        monkeypatch,
        results=[
            {"title": "A", "href": "https://a", "body": "cheap"},
            {"title": "B", "href": "https://b", "body": "dear"},
        ],
    )
    from buy_agent.search import search_products_tool

    rendered = search_products_tool.invoke({"query": "headphones", "max_results": 2})

    assert "TITLE: A" in rendered
    assert "TITLE: B" in rendered
