"""The build, opened in Chromium as a shopper opens it (ADR-0087). jsdom runs the
components and never the build, so these are the things only a browser shows: what the
CSP refuses, a stylesheet or script served as the wrong type, and the event stream."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from playwright.sync_api import expect

from browser.conftest import FOUND
from buy_agent.ranking import rank_products
from buy_agent.search import SearchError

if TYPE_CHECKING:
    from browser.conftest import Visit


def search(visit: Visit, request: str) -> None:
    visit.page.locator("input[name=request]").fill(request)
    visit.page.get_by_role("button", name="Find products").click()


def test_the_page_starts_and_is_styled_under_its_own_policy(visit: Visit) -> None:
    """An inline ``onload`` (``inlineCritical``) or a stylesheet served as ``text/plain``
    leaves the page in the browser's own serif, and the policy says so only here."""
    expect(visit.page.get_by_role("button", name="Find products")).to_be_visible()

    token = visit.page.evaluate(
        "getComputedStyle(document.documentElement).getPropertyValue('--text')"
    )
    assert token.strip(), "styles.css never applied: its tokens are undeclared"
    assert visit.errors == []


def test_a_search_streams_its_progress_and_its_products_onto_the_page(visit: Visit) -> None:
    """Through a real ``EventSource``: a ``log`` line, then the ``result``, drawn in the
    order the run ranked it (ADR-0012)."""
    search(visit, "wireless headphones")

    cards = visit.page.locator("app-product-card")
    expect(cards).to_have_count(len(FOUND))
    ranked = [entry.product.name for entry in rank_products(FOUND)]
    assert cards.locator("h3").all_inner_texts() == ranked
    expect(visit.page.get_by_role("log")).to_contain_text("Searching for wireless headphones")
    assert visit.errors == []


@pytest.mark.parametrize("answer", [SearchError("The web search answered nothing at all.")])
def test_a_failed_search_is_told_on_the_page(visit: Visit) -> None:
    """A ``failure`` event, never ``error``, which an ``EventSource`` reconnects on."""
    search(visit, "wireless headphones")

    expect(visit.page.locator(".banner.failed")).to_have_text(
        "The web search answered nothing at all."
    )
    expect(visit.page.locator("app-product-card")).to_have_count(0)
    assert visit.errors == []
