"""Layout rules that only a browser laying the page out can check.

jsdom has no layout: it will tell you a pill is in the document and never that it
is 40px wider than the phone the document is on. Both of the rules here were
broken when this suite was written -- `.pill` is `white-space: nowrap` so that a
rating never breaks in two, and two of the pills hold text that is not a figure:
an example chip is a whole shopping request, and a seller and a host are whatever
the page called itself.
"""

from __future__ import annotations

import pytest

from e2e.conftest import WIDTHS, past_the_edge, run_a_search, sideways
from e2e.stub import AWKWARD, Script


@pytest.mark.parametrize("width", WIDTHS)
def test_the_idle_page_never_scrolls_sideways(width: int, phone, serve) -> None:
    page = phone(width)
    page.goto(serve(), wait_until="networkidle")
    page.get_by_text("Settings").click()

    assert sideways(page) == 0, f"at {width}px: {past_the_edge(page)}"


@pytest.mark.parametrize("width", WIDTHS)
def test_a_page_of_results_never_scrolls_sideways(width: int, phone, serve) -> None:
    """With the most awkward content a page can hand a card: a title that is a
    sentence, a seller and a host that never break at a space, and a quote that is
    one unbroken word."""
    page = phone(width)
    page.goto(serve(Script(products=list(AWKWARD))), wait_until="networkidle")
    run_a_search(page)

    assert sideways(page) == 0, f"at {width}px: {past_the_edge(page)}"


def test_a_long_name_and_a_long_quote_stay_inside_their_card(phone, serve) -> None:
    """Folding is what keeps them in: a card that clipped them instead would pass
    the rule above and still be missing the end of a product's name."""
    page = phone(320)
    page.goto(serve(Script(products=list(AWKWARD))), wait_until="networkidle")
    run_a_search(page)

    card = page.locator("app-product-card").first
    edges = page.evaluate(
        """() => {
          const card = document.querySelector('app-product-card');
          const inside = card.getBoundingClientRect();
          return [...card.querySelectorAll('h3, .figures .pill, .opinions li, .notes')]
            .map((element) => Math.round(element.getBoundingClientRect().right - inside.right));
        }"""
    )
    assert edges and max(edges) <= 0, f"something is sticking out of the card: {edges}"
    assert card.locator("h3").inner_text().endswith("Headphones"), "the name is not cut short"


def test_the_form_folds_to_one_column_on_a_phone(phone, serve) -> None:
    """The settings grid is `auto-fit, minmax(190px, 1fr)`, which is one column on
    a phone and several on a desktop; a phone that got two of them would be showing
    93px-wide number fields."""
    page = phone(390)
    page.goto(serve(), wait_until="networkidle")
    page.get_by_text("Settings").click()

    columns = page.eval_on_selector(
        ".advanced .grid", "e => getComputedStyle(e).gridTemplateColumns"
    )
    assert len(columns.split()) == 1, columns
    assert page.eval_on_selector(".actions button", "e => e.getBoundingClientRect().width") > 200


def test_the_progress_panel_scrolls_rather_than_growing(page: object, serve) -> None:
    """The log is a fixed panel with its own scroller: a run of a hundred lines
    must not push the results off the bottom of the page."""
    page.goto(serve(), wait_until="networkidle")
    run_a_search(page)

    panel = page.eval_on_selector(
        "app-progress-log .scroller", "e => getComputedStyle(e).overflowY"
    )
    assert panel in ("auto", "scroll")
