"""The browser seam: a picture of a page, taken in a browser one thread owns (ADR-0065).

No test here starts a browser, whether or not Playwright is installed: the camera is handed
a stand-in through ``launch``, and :class:`~buy_agent.screenshots.Chromium` is handed a
``playwright`` module of this file's own through ``sys.modules``.
"""

from __future__ import annotations

import logging
import queue
import sys
import threading
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from buy_agent import screenshots
from buy_agent.screenshots import (
    INSTALL,
    QUALITY,
    SCALE,
    USER_AGENT,
    VIEWPORT,
    Camera,
    Chromium,
    ScreenshotError,
)

SHOP = "https://audiosite.example/xm5"
ROUNDUP = "https://reviews.example/best-headphones"


class StandIn:
    """A browser that answers every address with a picture naming it, unless told not to."""

    def __init__(
        self,
        failures: dict[str, Exception] | None = None,
        *,
        hold: threading.Event | None = None,
        close_fails: bool = False,
    ) -> None:
        self.failures = failures or {}
        self.hold = hold
        self.close_fails = close_fails
        self.shot: list[str] = []
        self.closed = threading.Event()

    def shoot(self, url: str) -> bytes:
        self.shot.append(url)
        if self.hold is not None:
            self.hold.wait(5)
        if url in self.failures:
            raise self.failures[url]
        return f"jpeg of {url}".encode()

    def close(self) -> None:
        self.closed.set()
        if self.close_fails:
            raise RuntimeError("Target page, context or browser has been closed")


class Launcher:
    """Hands the camera the next browser it is to launch, and remembers each one."""

    def __init__(self, *browsers: StandIn | Exception) -> None:
        self.queued = list(browsers)
        self.launched: list[StandIn] = []

    def __call__(self) -> StandIn:
        browser = self.queued.pop(0) if self.queued else StandIn()
        if isinstance(browser, Exception):
            raise browser
        self.launched.append(browser)
        return browser


@pytest.fixture
def cameras() -> Any:
    """Cameras built by the test, each let go of afterwards whatever the test did."""
    built: list[Camera] = []

    def build(launch: Launcher, **settings: float) -> Camera:
        camera = Camera(launch=launch, **settings)
        built.append(camera)
        return camera

    yield build
    for camera in built:
        camera.close()


# -- the camera ------------------------------------------------------------------


def test_a_picture_is_taken_in_a_browser_launched_for_it(cameras) -> None:
    launch = Launcher()
    camera = cameras(launch)

    assert camera.shoot(SHOP) == f"jpeg of {SHOP}".encode()
    assert launch.launched[0].shot == [SHOP]


def test_one_browser_takes_every_picture_while_somebody_is_asking(cameras) -> None:
    """Ten cards are ten pictures and one browser: a launch per card would be most of the
    wait, and ten at once most of the machine."""
    launch = Launcher()
    camera = cameras(launch)

    camera.shoot(SHOP)
    camera.shoot(ROUNDUP)

    assert len(launch.launched) == 1
    assert launch.launched[0].shot == [SHOP, ROUNDUP]


def test_the_browser_is_let_go_once_nobody_has_asked_for_a_while(cameras) -> None:
    """A browser is a few hundred megabytes, and a minute after a run nobody is looking
    at its cards -- so it is closed, and the next picture launches another."""
    launch = Launcher()
    camera = cameras(launch, idle=0.01)

    camera.shoot(SHOP)
    assert launch.launched[0].closed.wait(5), "the browser outlived the idle wait"

    camera.shoot(ROUNDUP)
    assert len(launch.launched) == 2


def test_a_picture_asked_for_as_the_worker_gives_up_is_still_taken(cameras) -> None:
    """The race the lock is there for: a job queued in the moment between the wait running
    out and the worker deciding to leave is taken, not stranded on a queue nobody reads."""

    class Late(queue.Queue):
        """Says it is empty the first time it is waited on, holding a job all the same."""

        missed = False

        def get(self, block: bool = True, timeout: float | None = None) -> Any:
            if timeout is not None and not self.missed:
                self.missed = True
                raise queue.Empty
            return super().get(block, timeout)

    launch = Launcher()
    camera = cameras(launch)
    # pylint: disable-next=protected-access
    camera._jobs = Late()

    assert camera.shoot(SHOP) == f"jpeg of {SHOP}".encode()


def test_a_page_that_will_not_be_photographed_fails_only_its_own_picture(cameras) -> None:
    launch = Launcher(StandIn({SHOP: ScreenshotError(f"{SHOP} answered 403")}))
    camera = cameras(launch)

    with pytest.raises(ScreenshotError, match="answered 403"):
        camera.shoot(SHOP)

    assert camera.shoot(ROUNDUP) == f"jpeg of {ROUNDUP}".encode()
    assert len(launch.launched) == 1, "a page's failure is not the browser's"


def test_a_browser_that_will_not_start_is_tried_again_for_the_next_picture(cameras) -> None:
    """The remedy is a command somebody runs while the server is up, so the camera does
    not give up on the first refusal and need a restart to notice it was answered."""
    launch = Launcher(ScreenshotError("Could not start a browser"))
    camera = cameras(launch)

    with pytest.raises(ScreenshotError, match="Could not start a browser"):
        camera.shoot(SHOP)

    assert camera.shoot(SHOP) == f"jpeg of {SHOP}".encode()


def test_a_browser_that_fails_unexpectedly_is_replaced(cameras, caplog) -> None:
    """Anything but a page's own failure may be a browser that has crashed, which is not
    one to go on photographing with."""
    broken = StandIn({SHOP: RuntimeError("Target crashed\nCall log: ...")})
    launch = Launcher(broken)
    camera = cameras(launch)

    with caplog.at_level(logging.WARNING, logger="buy_agent.screenshots"):
        with pytest.raises(ScreenshotError, match=rf"Could not photograph {SHOP}: Target crashed$"):
            camera.shoot(SHOP)

    assert broken.closed.is_set()
    assert "Target crashed" in caplog.text

    camera.shoot(ROUNDUP)
    assert len(launch.launched) == 2


def test_a_launch_that_fails_unexpectedly_is_one_picture_s_failure(cameras) -> None:
    """Playwright's driver can fail before there is a browser to let go of at all."""
    launch = Launcher(RuntimeError("Connection closed while reading from the driver"))
    camera = cameras(launch)

    with pytest.raises(ScreenshotError, match="Connection closed"):
        camera.shoot(SHOP)

    assert camera.shoot(SHOP) == f"jpeg of {SHOP}".encode()


def test_a_browser_that_will_not_even_close_is_let_go_anyway(cameras, caplog) -> None:
    stubborn = StandIn({SHOP: RuntimeError("Target crashed")}, close_fails=True)
    camera = cameras(Launcher(stubborn))

    with caplog.at_level(logging.DEBUG, logger="buy_agent.screenshots"):
        with pytest.raises(ScreenshotError):
            camera.shoot(SHOP)

    assert "Closing the screenshot browser failed" in caplog.text


def test_a_request_that_has_waited_too_long_is_told_so(cameras) -> None:
    """The queue is shared and a page can take a while: past the wait, the request is
    answered rather than left holding a connection the browser is also waiting on."""
    hold = threading.Event()
    camera = cameras(Launcher(StandIn(hold=hold)), wait=0.01)

    try:
        with pytest.raises(ScreenshotError, match=f"No picture of {SHOP}"):
            camera.shoot(SHOP)
    finally:
        hold.set()


def test_closing_a_camera_lets_its_browser_go_now(cameras) -> None:
    """The server's shutdown, which would otherwise leave the browser to the process
    exiting under it."""
    launch = Launcher()
    camera = cameras(launch)
    camera.shoot(SHOP)

    camera.close()

    assert launch.launched[0].closed.is_set()


def test_closing_a_camera_that_took_nothing_does_nothing(cameras) -> None:
    launch = Launcher()
    cameras(launch).close()

    assert launch.launched == []


# -- Playwright, and doing without it -----------------------------------------------


class FakeError(Exception):
    """``playwright.sync_api.Error``: what every one of its failures is."""


class FakeTimeout(FakeError):
    """``playwright.sync_api.TimeoutError``, which is one of those."""


class World:
    """What the fake browser was asked, and how it is to answer."""

    def __init__(self) -> None:
        self.status: int | None = 200
        self.goto_fails: Exception | None = None
        self.still_loading = False
        self.launch_fails: Exception | None = None
        self.context: dict[str, Any] = {}
        self.visits: list[tuple[str, dict[str, Any]]] = []
        self.pictures: list[dict[str, Any]] = []
        self.pages_closed = 0
        self.browser_closed = False
        self.stopped = False


class FakePage:
    def __init__(self, world: World) -> None:
        self.world = world

    def goto(self, url: str, **how: Any) -> Any:
        self.world.visits.append((url, how))
        if self.world.goto_fails:
            raise self.world.goto_fails
        return None if self.world.status is None else SimpleNamespace(status=self.world.status)

    def wait_for_load_state(self, state: str, *, timeout: float) -> None:
        assert state == "load"
        if self.world.still_loading:
            raise FakeTimeout(f"Timeout {timeout:.0f}ms exceeded.")

    def screenshot(self, **how: Any) -> bytes:
        self.world.pictures.append(how)
        return b"\xff\xd8 a jpeg"

    def close(self) -> None:
        self.world.pages_closed += 1


def playwright_module(world: World) -> ModuleType:
    """A ``playwright`` package whose ``sync_api`` drives :class:`World`."""

    class Browser:
        def new_context(self, **settings: Any) -> Any:
            world.context = settings
            return SimpleNamespace(new_page=lambda: FakePage(world))

        def close(self) -> None:
            world.browser_closed = True

    def launch() -> Browser:
        if world.launch_fails:
            raise world.launch_fails
        return Browser()

    def stop() -> None:
        world.stopped = True

    started = SimpleNamespace(chromium=SimpleNamespace(launch=launch), stop=stop)
    package = ModuleType("playwright")
    setattr(
        package,
        "sync_api",
        SimpleNamespace(
            Error=FakeError,
            TimeoutError=FakeTimeout,
            sync_playwright=lambda: SimpleNamespace(start=lambda: started),
        ),
    )
    return package


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> World:
    """A Playwright of this file's own, installed where the seam imports it from."""
    made = World()
    monkeypatch.setitem(sys.modules, "playwright", playwright_module(made))
    return made


@pytest.fixture
def no_playwright(monkeypatch: pytest.MonkeyPatch) -> None:
    """A checkout that never installed it: ``None`` in ``sys.modules`` refuses the import."""
    monkeypatch.setitem(sys.modules, "playwright", None)


def test_playwright_is_reported_as_available_when_it_imports(world: World) -> None:
    assert screenshots.available() is True


def test_a_missing_playwright_is_reported_rather_than_raised(no_playwright: None) -> None:
    """Asked at every server's startup, where most checkouts will never have it."""
    assert screenshots.available() is False


def test_a_missing_playwright_is_one_command_and_not_an_import_error(
    no_playwright: None,
) -> None:
    with pytest.raises(ScreenshotError) as refused:
        Chromium()

    assert INSTALL in str(refused.value)


def test_a_page_is_photographed_at_the_size_the_card_draws_it(world: World) -> None:
    """A laptop's window, laid out at full width and taken at half the pixels -- the
    desktop layout a shopper would see, in a fifth of the bytes."""
    browser = Chromium()

    assert browser.shoot(SHOP) == b"\xff\xd8 a jpeg"
    assert world.context == {
        "viewport": VIEWPORT,
        "device_scale_factor": SCALE,
        "user_agent": USER_AGENT,
    }
    assert world.pictures == [{"type": "jpeg", "quality": QUALITY}]
    assert world.visits[0][0] == SHOP
    assert world.visits[0][1]["wait_until"] == "domcontentloaded"
    assert world.pages_closed == 1


def test_the_agent_is_not_the_one_headless_chromium_announces() -> None:
    """Shops that turn away python-httpx turn away "HeadlessChrome" the same way."""
    assert "Headless" not in USER_AGENT
    assert "Chrome/" in USER_AGENT


def test_a_page_still_loading_is_photographed_as_far_as_it_got(world: World) -> None:
    """Shops load trackers for a long time; what is on the screen by then is the page."""
    world.still_loading = True

    assert Chromium().shoot(SHOP) == b"\xff\xd8 a jpeg"


def test_a_page_that_answered_with_nothing_to_report_is_still_photographed(
    world: World,
) -> None:
    """``goto`` answers ``None`` for a navigation that fetched nothing -- an anchor on the
    page already open -- and that is no failure."""
    world.status = None

    assert Chromium().shoot(SHOP) == b"\xff\xd8 a jpeg"


def test_a_page_that_answered_with_an_error_is_not_photographed(world: World) -> None:
    """A shop's "access denied" beside its product says the link is broken, when it
    opens fine in the shopper's own browser."""
    world.status = 403

    with pytest.raises(ScreenshotError, match="answered 403"):
        Chromium().shoot(SHOP)
    assert world.pictures == []
    assert world.pages_closed == 1


def test_a_page_that_would_not_load_says_why_in_one_line(world: World) -> None:
    """Playwright's own message goes on to print a call log, which is not a sentence."""
    world.goto_fails = FakeError("Page.goto: net::ERR_NAME_NOT_RESOLVED\nCall log:\n  - ...")

    with pytest.raises(ScreenshotError) as refused:
        Chromium().shoot(SHOP)

    assert str(refused.value) == f"Could not photograph {SHOP}: Page.goto: net::ERR_NAME_NOT_RESOLVED"
    assert world.pages_closed == 1


def test_a_failure_with_nothing_to_say_is_named_by_its_kind(world: World) -> None:
    world.goto_fails = FakeTimeout("")

    with pytest.raises(ScreenshotError, match="FakeTimeout$"):
        Chromium().shoot(SHOP)


def test_a_browser_that_is_not_installed_says_how_to_install_it(world: World) -> None:
    """The library installs without the browser it drives, which is a second command
    nobody remembers -- so the failure carries both."""
    world.launch_fails = FakeError("BrowserType.launch: Executable doesn't exist at C:\\x\n...")

    with pytest.raises(ScreenshotError) as refused:
        Chromium()

    assert "Executable doesn't exist" in str(refused.value)
    assert INSTALL in str(refused.value)
    assert world.stopped, "Playwright's own process has to go with the browser that did not"


def test_letting_a_browser_go_closes_it_and_stops_playwright(world: World) -> None:
    browser = Chromium()

    browser.close()

    assert world.browser_closed
    assert world.stopped
