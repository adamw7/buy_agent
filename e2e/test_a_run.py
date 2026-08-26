"""A search, from an empty form to ranked cards, in a browser.

What this is for is the seam neither suite can see across. The Python tests know
`api.py` sends a `price_label`; the jsdom tests know a card renders whatever
`price_label` it is handed. Only a browser can say that the label the server
wrote is the text on the page -- and only a browser runs the built bundle, the
Content-Security-Policy the server sends with it, and the `EventSource` a run's
progress arrives on.
"""

from __future__ import annotations

from e2e.conftest import TIMEOUT, run_a_search
from e2e.stub import CATALOGUE, Script


def test_the_page_boots_and_is_styled(page: object, serve) -> None:
    """A blank page and a page whose stylesheet never loaded look the same to jsdom.

    `optimization.styles.inlineCritical` is off in `ui/angular.json` because
    Angular's inliner defers the global stylesheet with an inline `onload` that
    `script-src 'self'` refuses to run, leaving the sheet at `media="print"` and
    the app unstyled. Nothing but a browser can see that it is back.
    """
    page.goto(serve(), wait_until="networkidle")

    assert page.title() == "buy_agent"
    assert page.locator("h1").inner_text() == "buy_agent"

    media = page.eval_on_selector_all("link[rel=stylesheet]", "els => els.map(e => e.media)")
    assert "print" not in media, f"the global stylesheet is still deferred: {media}"
    painted = page.eval_on_selector("body", "e => getComputedStyle(e).backgroundColor")
    assert painted != "rgba(0, 0, 0, 0)", "the page is on the browser's default background"
    assert page.eval_on_selector("form.card", "e => getComputedStyle(e).padding") != "0px"


def test_the_form_starts_on_the_servers_defaults(page: object, serve) -> None:
    """`GET /api/config` is the only place the form's starting values come from --
    the same ones `--help` prints."""
    page.goto(serve(), wait_until="networkidle")
    page.get_by_text("Settings").click()

    assert page.locator("input[name=model]").input_value() == "gemma4:12b"
    assert page.locator("input[name=results]").input_value() == "10"
    assert page.locator("input[name=top]").input_value() == "3"
    assert page.locator("input[name=numCtx]").input_value() == "8192"
    assert page.locator("select[name=thinking]").input_value() == "off"
    assert page.locator("select[name=sortBy] option").all_inner_texts() == [
        "score",
        "price",
        "rating",
    ]


def test_the_model_field_falls_back_to_a_text_box(page: object, serve) -> None:
    """Nothing answers on :11434 here, so `GET /api/models` comes back empty and
    unreachable -- a dropdown holding one unusable entry is worse than typing."""
    page.goto(serve(), wait_until="networkidle")

    assert page.locator("button.pill.ollama").inner_text().strip() == "Ollama unreachable"
    page.get_by_text("Settings").click()
    assert page.locator("input[name=model]").count() == 1
    assert page.locator("select[name=model]").count() == 0


def test_a_run_reports_itself_while_it_is_still_running(page: object, serve) -> None:
    """The reason a run is streamed rather than requested: log lines have to reach
    the page while the agent is still working, not in one flush at the end."""
    page.goto(serve(), wait_until="networkidle")
    page.fill("input[name=request]", "wireless noise cancelling headphones under $200")
    page.click("button[type=submit]")

    page.wait_for_selector("button.secondary:has-text('Stop')")
    page.wait_for_function(
        "() => document.querySelectorAll('app-progress-log p.line').length >= 2", timeout=TIMEOUT
    )
    assert page.locator("app-progress-log .pill.working").count() == 1
    assert page.locator("section.results").count() == 0, "the results cannot be there yet"

    page.wait_for_selector("section.results", timeout=TIMEOUT)
    assert page.locator("app-progress-log p.line").count() == 6
    assert page.locator("app-progress-log p.line.warn").count() == 1
    assert page.locator("button[type=submit]").count() == 1, "the form is idle again"


def test_the_highlighted_products_are_the_top_of_what_was_found(page: object, serve) -> None:
    """`top` is what the page highlights; the rest are behind a disclosure, because
    `count` is every product the run found and the heading is about both."""
    page.goto(serve(), wait_until="networkidle")
    run_a_search(page)

    highlighted = page.locator("section.results > app-product-card")
    assert highlighted.count() == 3
    assert "top 3 of 4" in page.locator("section.results h2").inner_text().lower()

    assert page.locator("details.also summary").inner_text().startswith("1 more")
    page.locator("details.also summary").click()
    assert page.locator("details.also app-product-card .rank").inner_text().strip() == "#4"


def test_a_card_says_what_python_said(page: object, serve) -> None:
    """Every figure on a card is a string `api.py` wrote. The browser decides
    nothing -- not the ranking, not the order, not the wording of an unknown price."""
    page.goto(serve(), wait_until="networkidle")
    run_a_search(page)

    quoted = page.locator("app-product-card", has_text="Bose QuietComfort Ultra")
    assert "329.00 USD" in quoted.inner_text()
    assert "4.7/5 (5,874 reviews)" in quoted.inner_text()
    assert quoted.locator("ul.opinions li").count() == 2
    assert quoted.locator(".figures .pill", has_text="Bose").count() >= 1
    assert quoted.locator(".figures .pill", has_text="example.com").count() >= 1

    link = quoted.locator("h3 a")
    assert link.get_attribute("target") == "_blank"
    assert "noopener" in (link.get_attribute("rel") or "")


def test_a_grounded_away_figure_reads_as_unknown(page: object, serve) -> None:
    """Grounding blanks what the sources did not back, and a blank is not a gap on
    the page: "price unknown" and "unrated" are `Product`'s own labels."""
    page.goto(serve(), wait_until="networkidle")
    run_a_search(page)
    page.locator("details.also summary").click()

    blanked = page.locator("app-product-card", has_text="Sennheiser Momentum 4")
    assert "price unknown" in blanked.inner_text()
    assert "unrated" in blanked.inner_text()
    assert blanked.locator(".price.unknown").count() == 1


def test_sorting_is_the_servers_answer_and_not_a_re_sort(page: object, serve) -> None:
    """`sort_by` is a request parameter: the browser asks for a different ranking
    rather than reordering the one it has."""
    asked: list[str] = []
    page.on(
        "request",
        lambda request: asked.append(request.url) if "/api/search/stream" in request.url else None,
    )
    page.goto(serve(), wait_until="networkidle")
    page.get_by_text("Settings").click()
    page.select_option("select[name=sortBy]", "price")
    run_a_search(page, "espresso machine")

    assert asked and "sort_by=price" in asked[-1]
    assert "ranked by price" in page.locator("section.results h2").inner_text().lower()
    prices = [
        float(text.replace(" USD", "").replace(",", ""))
        for text in page.locator("section.results > app-product-card .price").all_inner_texts()
        if text.strip() and "unknown" not in text
    ]
    assert prices == sorted(prices)


def test_the_advanced_settings_are_remembered_and_the_request_is_not(
    page: object, serve
) -> None:
    """What to shop for is a new question every time; which model to ask is not."""
    base = serve()
    page.goto(base, wait_until="networkidle")
    page.get_by_text("Settings").click()
    page.fill("input[name=region]", "pl-pl")
    page.fill("input[name=temperature]", "0.4")
    run_a_search(page, "cordless drill")

    page.reload(wait_until="networkidle")
    page.get_by_text("Settings").click()
    assert page.locator("input[name=region]").input_value() == "pl-pl"
    assert page.locator("input[name=temperature]").input_value() == "0.4"
    assert page.locator("input[name=request]").input_value() == ""


def test_stopping_a_run_leaves_the_form_idle(page: object, serve) -> None:
    """Stop unsubscribes, which closes the stream. Nothing should still be working."""
    page.goto(serve(), wait_until="networkidle")
    page.fill("input[name=request]", "gaming laptop")
    page.click("button[type=submit]")
    page.wait_for_selector("button.secondary:has-text('Stop')")
    page.click("button.secondary:has-text('Stop')")

    page.wait_for_selector("button[type=submit]")
    page.wait_for_timeout(int(1.5 * 1000))
    assert page.locator("app-progress-log .pill.working").count() == 0
    assert page.locator("section.results").count() == 0


def test_an_example_chip_fills_the_request(page: object, serve) -> None:
    page.goto(serve(), wait_until="networkidle")
    example = page.locator("button.pill.example").first.inner_text().strip()
    page.locator("button.pill.example").first.click()

    page.wait_for_function(
        "want => document.querySelector('input[name=request]').value === want", arg=example
    )


def test_enter_in_the_request_field_starts_a_run(page: object, serve) -> None:
    """The form is a form: the keyboard alone has to be able to run a search."""
    page.goto(serve(), wait_until="networkidle")
    page.click("input[name=request]")
    page.keyboard.type("kettle")
    page.keyboard.press("Enter")

    page.wait_for_selector("section.results", timeout=TIMEOUT)
    assert page.locator("section.results > app-product-card").count() == 3


def test_two_runs_in_a_row_do_not_pool_their_log_lines(page: object, serve) -> None:
    """`_LogRelay` routes records by the thread that produced them, and the panel
    starts each run over: the second run's progress is the second run's alone."""
    page.goto(serve(), wait_until="networkidle")
    run_a_search(page, "wireless headphones")
    first = page.locator("app-progress-log p.line").count()
    run_a_search(page, "running shoes")

    assert page.locator("app-progress-log p.line").count() == first
    assert "running shoes" in page.locator("app-progress-log p.line").first.inner_text()


def test_an_answer_with_nothing_in_it_explains_itself(page: object, serve) -> None:
    """A run that finds nothing is not an error, and the page says which of the two
    it was in the shopper's own words."""
    page.goto(serve(Script(products=[])), wait_until="networkidle")
    run_a_search(page, "left-handed screwdriver")

    quiet = page.locator("p.banner.quiet")
    assert "left-handed screwdriver" in quiet.inner_text()
    assert page.locator("app-product-card").count() == 0


def test_every_product_the_run_found_reaches_the_page(page: object, serve) -> None:
    """Not a rule about layout: the disclosure holds the remainder, so the two
    together have to be everything `rank_products` returned."""
    page.goto(serve(), wait_until="networkidle")
    run_a_search(page)
    page.locator("details.also summary").click()

    names = page.locator("app-product-card h3").all_inner_texts()
    assert sorted(names) == sorted(product.name for product in CATALOGUE)
