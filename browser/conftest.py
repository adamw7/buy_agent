"""Fixtures for the browser tests: the build `ui/` makes, served by the real server with
the agent stood in for, and opened in a real Chromium."""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NoReturn

import pytest

from browser import REQUIRE_ENV_VAR
from buy_agent.models import Product
from buy_agent.ranking import rank_products
from buy_agent.screenshots import INSTALL
from buy_agent.server import DEFAULT_UI_DIR, create_server

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path
    from typing import Any

    from playwright.sync_api import Browser, ConsoleMessage, Page

    from buy_agent.models import RankedProduct

#: What a run finds, unless a test parametrizes ``answer``.
FOUND = (
    Product(
        name="Sony WH-1000XM5", price=328.0, currency="USD", rating=4.7, review_count=3200,
        url="https://shop.example/xm5",
    ),
    Product(name="Anker Q30", price=79.0, currency="USD", rating=4.4),
)

#: Long enough for a run over loopback on a loaded runner, short of the test's own cap.
_PATIENCE_MS = 15_000


def _absent(reason: str) -> NoReturn:
    """Skip, unless the environment says these tests were meant to run."""
    if os.getenv(REQUIRE_ENV_VAR):
        pytest.fail(reason)
    pytest.skip(reason)


@pytest.fixture(autouse=True)
def nothing_of_the_developers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A scratch cache and none of the paying variables, as the unit suite has."""
    monkeypatch.setenv("BUY_AGENT_CACHE_DIR", str(tmp_path / "cache"))
    for name in ("BUY_AGENT_AP2_KEY", "BUY_AGENT_AP2_MANDATE", "BUY_AGENT_MERCHANT_URL"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(scope="session")
def chromium() -> Iterator[Browser]:
    """One headless Chromium for the session, as ``screenshots.INSTALL`` installs it."""
    if not (DEFAULT_UI_DIR / "index.html").is_file():
        _absent(f"There is no build at {DEFAULT_UI_DIR}: cd ui; npm run build")
    try:
        # Deferred, as screenshots.py defers it: an optional install (ADR-0065).
        from playwright.sync_api import Error, sync_playwright
    except ImportError:
        _absent(f"Playwright is not installed: {INSTALL}")
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except Error as exc:
            _absent(f"Chromium will not start ({str(exc).splitlines()[0]}): {INSTALL}")
        yield browser
        browser.close()


@pytest.fixture
def answer() -> tuple[Product, ...] | Exception:
    """What a run answers with; a test parametrizes this to change it."""
    return FOUND


@pytest.fixture
def shop(answer: tuple[Product, ...] | Exception) -> Iterator[str]:
    """The real server on a loopback port, serving the build, with the agent stood in."""

    class Agent:
        """Stands in for ``BuyAgent``: says what it is doing, then answers."""

        def __init__(self, config: Any) -> None:
            self.config = config

        def run(self, request: str, *, sort_by: str = "score", **_: Any) -> list[RankedProduct]:
            logging.getLogger("buy_agent.agent").info("Searching for %s", request)
            if isinstance(answer, Exception):
                raise answer
            return rank_products(answer, sort_by=sort_by)

    httpd = create_server("127.0.0.1", 0, agent_factory=Agent)
    serving = threading.Thread(target=httpd.serve_forever, args=(0.01,), daemon=True)
    serving.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        serving.join()


@dataclass
class Visit:
    """One tab on the shop, and every error the page reported while it was open."""

    page: Page
    errors: list[str] = field(default_factory=list)

    def said(self, message: ConsoleMessage) -> None:
        # A blocked handler, script or stylesheet is reported here and nowhere else.
        if message.type == "error":
            self.errors.append(message.text)

    def threw(self, error: Exception) -> None:
        self.errors.append(f"uncaught: {error}")


@pytest.fixture
def visit(chromium: Browser, shop: str) -> Iterator[Visit]:
    """The page opened on the shop, listening from before its first byte."""
    context = chromium.new_context(base_url=shop)
    context.set_default_timeout(_PATIENCE_MS)
    seen = Visit(context.new_page())
    seen.page.on("console", seen.said)
    seen.page.on("pageerror", seen.threw)
    seen.page.goto("/")
    try:
        yield seen
    finally:
        context.close()
