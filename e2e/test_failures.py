"""What the page does when the run does not work.

A failure arrives as its own SSE event rather than as a log line -- `failure` and
not `error`, so that a browser's own transport errors stay distinguishable from
the agent's -- and the panel's Download log button exists for exactly this case:
a successful run is on the screen in front of you, a failed one is a bug report.
"""

from __future__ import annotations

from e2e.conftest import TIMEOUT, run_a_search
from e2e.stub import RATE_LIMITED, Script


def test_a_failed_run_says_what_went_wrong(page: object, serve) -> None:
    page.goto(serve(Script(fails=RATE_LIMITED)), wait_until="networkidle")
    run_a_search(page)

    banner = page.locator("p.banner[role=alert]")
    assert "rate limited" in banner.inner_text()
    assert page.locator("section.results").count() == 0
    assert page.locator("app-progress-log .pill.working").count() == 0
    assert page.locator("button[type=submit]").count() == 1, "the form is usable again"


def test_a_failed_run_hands_over_the_log_as_a_bug_report(page: object, serve, tmp_path) -> None:
    """The transcript is the panel plus the one line the panel never had: a failure
    is not a log record, so it is only ever on the screen as the banner."""
    page.goto(serve(Script(fails=RATE_LIMITED)), wait_until="networkidle")
    run_a_search(page)

    with page.expect_download(timeout=TIMEOUT) as caught:
        page.click("app-progress-log .save")
    download = caught.value
    saved = tmp_path / download.suggested_filename
    download.save_as(saved)
    transcript = saved.read_text(encoding="utf-8")

    assert saved.suffix == ".txt"
    assert "Fetching 10 result page(s)" in transcript, "every line the panel showed"
    assert "rate limited" in transcript, "and the failure it could not show"
    # Whole logger names, where the panel's fixed column trims them.
    assert "buy_agent.search" in transcript


def test_a_run_that_worked_offers_no_download(page: object, serve) -> None:
    page.goto(serve(), wait_until="networkidle")
    run_a_search(page)

    assert page.locator("app-progress-log .save").count() == 0


def test_the_page_recovers_from_a_failure(page: object, serve) -> None:
    """A failed run leaves nothing behind that stops the next one: the banner goes
    when the next run starts, and the panel begins again."""
    base = serve(Script(fails=RATE_LIMITED))
    page.goto(base, wait_until="networkidle")
    run_a_search(page)
    assert page.locator("p.banner[role=alert]").count() == 1

    page.fill("input[name=request]", "another go")
    page.click("button[type=submit]")
    page.wait_for_selector("button.secondary:has-text('Stop')")
    assert page.locator("p.banner[role=alert]").count() == 0
