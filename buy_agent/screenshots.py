"""A picture of the page a product card links to (ADR-0065).

The only module importing Playwright, and the only process the package starts: a
headless Chromium, launched on first use and closed when idle. Optional: a separate
install, used only by a loopback server.

Playwright's sync objects belong to their thread, so one worker owns the browser and
request threads queue jobs for it, one page at a time.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

#: How to install the library and the browser it downloads.
INSTALL = (
    "pip install -r requirements-screenshots.txt && "
    "python -m playwright install --only-shell chromium"
)

#: A laptop's window, for the desktop layout.
VIEWPORT = {"width": 1280, "height": 800}

#: A 640 x 400 picture: twice the card's size, for high-density screens.
SCALE = 0.5

#: JPEG quality; the text is not meant to be read.
QUALITY = 70

#: How long a page may take to produce a document.
LOAD_SECONDS = 15.0

#: Further time to finish loading before shooting whatever is drawn.
SETTLE_SECONDS = 3.0

#: How long an idle browser stays up.
IDLE_SECONDS = 60.0

#: How long a request waits, queued jobs included: ten pages with room to spare.
WAIT_SECONDS = 240.0

#: A desktop agent, since shops refuse "HeadlessChrome". Duplicated from ``fetch``: this
#: seam imports nothing from the package.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class ScreenshotError(Exception):
    """A picture that could not be taken, and why."""


class Browser(Protocol):
    """What the camera needs of a browser."""

    def shoot(self, url: str) -> bytes: ...

    def close(self) -> None: ...


def _sync_api() -> Any:
    """Playwright's synchronous API, imported now rather than at module import."""
    try:
        # Deferred: the library is optional, and importing it is the check.
        # pylint: disable-next=import-outside-toplevel
        from playwright import sync_api
    except ImportError as exc:
        raise ScreenshotError(
            f"Screenshots need Playwright, which is not installed. Install it with:  {INSTALL}"
        ) from exc
    return sync_api


def available() -> bool:
    """Whether Playwright is installed (the browser is found out at launch)."""
    try:
        _sync_api()
    except ScreenshotError:
        return False
    return True


def _first_line(exc: Exception) -> str:
    """A Playwright error's first line, without its call log."""
    return (str(exc).strip().splitlines() or [type(exc).__name__])[0]


class Chromium:
    """Playwright's headless Chromium, for the one thread that launched it."""

    def __init__(self) -> None:
        api = _sync_api()
        self._error: type[Exception] = api.Error
        self._timeout: type[Exception] = api.TimeoutError
        self._playwright = api.sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.launch()
            self._context = self._browser.new_context(
                viewport=VIEWPORT, device_scale_factor=SCALE, user_agent=USER_AGENT
            )
        except self._error as exc:
            self._playwright.stop()
            raise ScreenshotError(
                f"Could not start a browser to take screenshots with ({_first_line(exc)}). "
                f"If it is not installed:  {INSTALL}"
            ) from exc

    def shoot(self, url: str) -> bytes:
        page = self._context.new_page()
        try:
            response = page.goto(
                url, wait_until="domcontentloaded", timeout=LOAD_SECONDS * 1000
            )
            # An error page would misrepresent a link that works in a real browser.
            if response is not None and response.status >= 400:
                raise ScreenshotError(f"{url} answered {response.status}")
            try:
                page.wait_for_load_state("load", timeout=SETTLE_SECONDS * 1000)
            except self._timeout:
                logger.debug("%s was still loading; photographing what it has drawn", url)
            picture: bytes = page.screenshot(type="jpeg", quality=QUALITY)
            return picture
        except self._error as exc:
            raise ScreenshotError(f"Could not photograph {url}: {_first_line(exc)}") from exc
        finally:
            page.close()

    def close(self) -> None:
        try:
            self._browser.close()
        finally:
            self._playwright.stop()


class _Job:
    """One picture asked for, and the request thread waiting on it."""

    def __init__(self, url: str) -> None:
        self.url = url
        self._settled = threading.Event()
        self._picture = b""
        self._error: ScreenshotError | None = None

    def done(self, picture: bytes) -> None:
        self._picture = picture
        self._settled.set()

    def fail(self, error: ScreenshotError) -> None:
        self._error = error
        self._settled.set()

    def wait(self, seconds: float) -> bytes:
        if not self._settled.wait(seconds):
            raise ScreenshotError(f"No picture of {self.url} after {seconds:.0f} seconds")
        if self._error is not None:
            raise self._error
        return self._picture


class Camera:
    """Takes pictures of pages, one at a time, in a browser of its own.

    ``launch`` is the test seam: no test starts a real browser.
    """

    def __init__(
        self,
        *,
        launch: Callable[[], Browser] = Chromium,
        idle: float = IDLE_SECONDS,
        wait: float = WAIT_SECONDS,
    ) -> None:
        self._launch = launch
        self._idle = idle
        self._wait = wait
        self._jobs: queue.Queue[_Job | None] = queue.Queue()
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None

    def shoot(self, url: str) -> bytes:
        """A JPEG of the page at ``url``, after any queued ahead of it."""
        job = _Job(url)
        with self._lock:
            self._jobs.put(job)
            if self._worker is None:
                self._worker = threading.Thread(
                    target=self._work, name="buy_agent-camera", daemon=True
                )
                self._worker.start()
        return job.wait(self._wait)

    def close(self) -> None:
        """Close the browser now, on server shutdown."""
        with self._lock:
            worker = self._worker
            if worker is None:
                return
            self._jobs.put(None)
        worker.join(timeout=self._wait)

    def _work(self) -> None:
        browser: Browser | None = None
        try:
            while (job := self._next()) is not None:
                browser = self._take(job, browser)
        finally:
            with self._lock:
                # Also on an unplanned exit, so the next job starts a new worker.
                if self._worker is threading.current_thread():
                    self._worker = None
            if browser is not None:
                _let_go(browser)

    def _next(self) -> _Job | None:
        """The next job, or ``None`` once idle.

        Decided under :meth:`shoot`'s lock, so no job lands on a queue nobody reads.
        """
        try:
            return self._jobs.get(timeout=self._idle)
        except queue.Empty:
            with self._lock:
                try:
                    return self._jobs.get_nowait()
                except queue.Empty:
                    self._worker = None
                    return None

    def _take(self, job: _Job, browser: Browser | None) -> Browser | None:
        """Take one picture, answering the job; return the browser to keep using."""
        try:
            if browser is None:
                browser = self._launch()
            job.done(browser.shoot(job.url))
        except ScreenshotError as exc:
            logger.debug("%s", exc)
            job.fail(exc)
        # An unexpected failure: discard this browser; the next job launches another.
        # pylint: disable-next=broad-exception-caught
        except Exception as exc:
            logger.warning("The screenshot browser failed: %s", _first_line(exc))
            job.fail(ScreenshotError(f"Could not photograph {job.url}: {_first_line(exc)}"))
            if browser is not None:
                _let_go(browser)
            return None
        return browser


def _let_go(browser: Browser) -> None:
    """Close a browser, whatever state it is in."""
    try:
        browser.close()
    # A crashed browser has nothing to close; log and move on.
    # pylint: disable-next=broad-exception-caught
    except Exception as exc:
        logger.debug("Closing the screenshot browser failed: %s", _first_line(exc))
