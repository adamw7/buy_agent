"""The browser suite's fixtures: a real Chromium, against a real server.

This directory is deliberately outside ``pytest.ini``'s ``testpaths``, so
``python -m pytest`` never collects it. These tests need two things that suite
does not -- a built UI under ``ui/dist/ui/browser`` and a browser to point at it
-- and a default run that fails for want of either would be a worse default than
a suite you have to ask for by name:

```powershell
pip install -r requirements-e2e.txt
python -m playwright install chromium
cd ui; npm run build
python -m pytest e2e
```

Missing any of that, the whole suite skips with the command that fixes it.
``$BUY_AGENT_E2E_CHROMIUM`` points at a Chromium that Playwright did not install
itself, which is how the browser already on a machine gets used instead.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from buy_agent.server import DEFAULT_UI_DIR, create_server
from e2e.stub import Script, agent_factory

#: Long enough for a paced stub run (about a second) plus a cold first paint on a
#: loaded CI runner, short enough that a genuinely stuck page fails the job rather
#: than sitting out its timeout.
TIMEOUT = 30_000

#: A phone that is still current, the widest phone worth worrying about, and the
#: desktop the screenshots in docs/ are taken at. 320px is where a fixed-width
#: pill first carries the page off the side of the screen.
WIDTHS = (320, 390, 1280)

#: Where a page that failed a test is photographed. A browser test that fails on
#: a runner otherwise reports a selector and a timeout, which says where the test
#: stopped looking and nothing about what the page was showing instead.
SHOTS = Path(__file__).resolve().parent.parent / "e2e-screenshots"

_FAILED = pytest.StashKey[bool]()


def pytest_report_header() -> str:
    return f"e2e: serving {DEFAULT_UI_DIR}"


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: object):
    report = yield
    if report.when == "call":
        item.stash[_FAILED] = report.failed
    return report


@pytest.fixture(scope="session")
def browser() -> Iterator[object]:
    """One Chromium for the whole suite; starting one costs more than a test does.

    Playwright is imported here rather than at the top of the file on purpose: a
    machine without it should see every test in this directory skip with the
    command that installs it, not a collection error.
    """
    api = pytest.importorskip(
        "playwright.sync_api",
        reason="the browser suite needs Playwright: pip install -r requirements-e2e.txt",
    )
    if not (DEFAULT_UI_DIR / "index.html").is_file():
        pytest.skip(f"no built UI at {DEFAULT_UI_DIR}: run `npm run build` in ui/")

    executable = os.environ.get("BUY_AGENT_E2E_CHROMIUM")
    with api.sync_playwright() as playwright:
        try:
            instance = playwright.chromium.launch(executable_path=executable or None)
        except api.Error as exc:  # pragma: no cover - a machine with Playwright but no browser
            pytest.skip(
                f"no Chromium to drive ({exc}): run `python -m playwright install chromium`"
            )
        try:
            yield instance
        finally:
            instance.close()


@pytest.fixture
def page(browser: object, request: pytest.FixtureRequest) -> Iterator[object]:
    """A fresh page, whose console has to stay clean.

    An exception in the browser is not something a test has to think to check
    for: it is the failure the two suites in this repository cannot see at all,
    and any test that provokes one has found something whatever else it asserted.
    """
    yield from _page(browser, request, width=1280)


@pytest.fixture
def phone(browser: object, request: pytest.FixtureRequest) -> Iterator[Callable[[int], object]]:
    """Pages at whatever width a test asks for, all of them console-clean."""
    opened: list[Iterator[object]] = []

    def open_at(width: int) -> object:
        pages = _page(browser, request, width=width)
        opened.append(pages)
        return next(pages)

    yield open_at
    for pages in opened:
        next(pages, None)


def _page(browser: object, request: pytest.FixtureRequest, *, width: int) -> Iterator[object]:
    page = browser.new_page(viewport={"width": width, "height": 900})
    complaints: list[str] = []
    page.on("pageerror", lambda error: complaints.append(f"pageerror: {error}"))
    page.on(
        "console",
        lambda message: complaints.append(f"console.{message.type}: {message.text}")
        if message.type == "error"
        else None,
    )
    page.set_default_timeout(TIMEOUT)
    try:
        yield page
    finally:
        if request.node.stash.get(_FAILED, False):
            _photograph(page, f"{request.node.name}-{width}")
        page.close()
        assert not complaints, f"the browser complained: {complaints}"


def _photograph(page: object, name: str) -> None:
    """Save what the page was showing when a test failed, for a run nobody watched."""
    SHOTS.mkdir(exist_ok=True)
    keep = "".join(
        character if character.isalnum() or character in "-_" else "-" for character in name
    )
    try:
        page.screenshot(path=str(SHOTS / f"{keep}.png"), full_page=True)
    except Exception as exc:  # pragma: no cover - a page that is already gone
        print(f"could not photograph {keep}: {exc}")


@pytest.fixture
def serve() -> Iterator[Callable[..., str]]:
    """Start servers on loopback ports, each running a script of its own.

    The agent is the only stub: this is ``create_server`` with the real handler,
    the real API and the built app behind it, on a real socket.
    """
    with _servers() as start:
        yield start


@contextmanager
def _servers() -> Iterator[Callable[..., str]]:
    running: list[tuple[object, threading.Thread]] = []

    def start(script: Script | None = None, *, ui_dir: Path = DEFAULT_UI_DIR) -> str:
        httpd = create_server(
            "127.0.0.1", 0, ui_dir=ui_dir, agent_factory=agent_factory(script or Script())
        )
        # 0.01 rather than the default 0.5s poll, as in tests/test_server.py:
        # otherwise shutting down costs half a second per server.
        thread = threading.Thread(target=httpd.serve_forever, args=(0.01,), daemon=True)
        thread.start()
        running.append((httpd, thread))
        host, port = httpd.server_address[:2]
        return f"http://{host}:{port}"

    try:
        yield start
    finally:
        for httpd, thread in running:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)


def sideways(page: object) -> int:
    """How far the page scrolls horizontally -- 0 on a layout that fits."""
    return page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )


def past_the_edge(page: object) -> list[dict[str, object]]:
    """Every element sticking out past the right edge, for when ``sideways`` is not 0."""
    return page.evaluate(
        """() => {
          const edge = document.documentElement.clientWidth;
          return [...document.querySelectorAll('body *')]
            .map((element) => ({
              tag: element.tagName.toLowerCase(),
              className: typeof element.className === 'string' ? element.className : '',
              right: Math.round(element.getBoundingClientRect().right),
            }))
            .filter((box) => box.right > edge + 1)
            .slice(0, 5);
        }"""
    )


def run_a_search(page: object, request: str = "wireless headphones") -> None:
    """Ask for something and wait for the run to finish, however it ends.

    Started *and* then finished, rather than "the results are on the page": a
    second run leaves the first one's results up while it works, so waiting only
    for those would hand the test a page that is one log line into the new run.
    """
    page.fill("input[name=request]", request)
    page.click("button[type=submit]")
    page.wait_for_selector("button.secondary:has-text('Stop')", timeout=TIMEOUT)
    page.wait_for_selector("button[type=submit]", timeout=TIMEOUT)
    page.wait_for_selector("section.results, p.banner", timeout=TIMEOUT)
