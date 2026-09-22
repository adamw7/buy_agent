"""A picture of the page a product links to, for the card that links to it (ADR-0065).

The one module that imports Playwright, and the one place the package starts a process of
its own: a headless Chromium, launched on the first picture anybody asks for and let go
once nobody has asked for one in a while. It is optional twice over -- the library is an
install of its own, and only a server bound to this machine asks for a camera at all --
so a checkout without it runs, serves and passes its suite exactly as before.

Playwright's synchronous objects belong to the thread that made them, so one worker thread
owns the browser and every request thread hands it a job and waits. One browser, one page
at a time: ten pictures a run is not worth ten browsers, and the order the jobs arrive in
is the order the cards are drawn in, the top of the report first.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

#: What to type when screenshots are wanted and not installed: the library, and then the
#: one browser it drives, which it downloads rather than finding on the machine.
INSTALL = (
    "pip install -r requirements-screenshots.txt && "
    "python -m playwright install --only-shell chromium"
)

#: The window a page is laid out in -- a laptop's, so a shop draws its desktop layout
#: rather than the one it keeps for a phone.
VIEWPORT = {"width": 1280, "height": 800}

#: Half a pixel per CSS pixel, which makes that window a 640 x 400 picture: twice what
#: the card draws, so it is sharp on a high-density screen, and a fifth of the bytes a
#: full-size one takes. Chromium lays the page out at the full width either way.
SCALE = 0.5

#: JPEG quality: a page's text at that size is a texture, not something to read.
QUALITY = 70

#: How long a page may take to have a document at all. A result page that has not
#: answered in this long is one nobody wanted a picture of.
LOAD_SECONDS = 15.0

#: How much longer it is given to finish loading -- the images, the fonts -- before the
#: picture is taken of whatever it has drawn. Shops load trackers for a long time.
SETTLE_SECONDS = 3.0

#: How long the browser stays up with nobody asking. A run's cards ask within seconds of
#: each other; a minute later nobody is looking at them any more.
IDLE_SECONDS = 60.0

#: How long a request waits for its picture, the jobs ahead of it included: ten pages at
#: :data:`LOAD_SECONDS` and :data:`SETTLE_SECONDS` each, with room to spare.
WAIT_SECONDS = 240.0

#: A browser-ish agent, for the reason ``fetch.USER_AGENT`` is one: headless Chromium
#: says so in its own, and the shops that answer python-httpx with a 403 answer
#: "HeadlessChrome" the same way. Written again rather than imported, the fetcher being a
#: step of the pipeline and this a seam that knows nothing of the package above it.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class ScreenshotError(Exception):
    """A picture that could not be taken, and why -- a page that would not load, or a
    browser that would not start."""


class Browser(Protocol):
    """What the camera needs of a browser: a picture of one address, and letting go."""

    def shoot(self, url: str) -> bytes: ...

    def close(self) -> None: ...


def _sync_api() -> Any:
    """Playwright's synchronous API, imported now rather than at module import."""
    try:
        # Deferred, and the reason this module can be imported where the library is not
        # installed: whether it imports is the question being asked.
        # pylint: disable-next=import-outside-toplevel
        from playwright import sync_api
    except ImportError as exc:
        raise ScreenshotError(
            f"Screenshots need Playwright, which is not installed. Install it with:  {INSTALL}"
        ) from exc
    return sync_api


def available() -> bool:
    """Is Playwright installed? Whether its browser is gets found out by launching it."""
    try:
        _sync_api()
    except ScreenshotError:
        return False
    return True


def _first_line(exc: Exception) -> str:
    """The sentence out of a Playwright error, which goes on to print a call log."""
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
            # A shop's "access denied" is a page too, and a picture of it next to the
            # product would say the link is broken when it opens fine in a real browser.
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
    """Takes pictures of pages, one at a time, in a browser nobody else touches.

    ``launch`` is the seam the suite hands a stand-in through, the way ``BuyAgent`` is
    handed its model: nothing here is worth a real browser to test, and a real browser is
    exactly what a test may not start.
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
        """A JPEG of the page at ``url``, taken now or once the pictures ahead of it are."""
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
        """Let the browser go now rather than after :data:`IDLE_SECONDS` -- the server's
        shutdown, which would otherwise leave it to the process exiting."""
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
                # Said again here for an exit nobody planned, so the next picture asked
                # for starts a worker rather than queueing behind one that is gone.
                if self._worker is threading.current_thread():
                    self._worker = None
            if browser is not None:
                _let_go(browser)

    def _next(self) -> _Job | None:
        """The next picture asked for, or ``None`` once nobody has asked for a while.

        Whether nobody has is decided under the lock :meth:`shoot` queues under, so a job
        arriving at that moment either lands before the decision and is taken, or after
        it and starts a worker of its own -- never on a queue nobody is reading.
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
        """Take one picture, answering the job either way. The browser to keep using."""
        try:
            if browser is None:
                browser = self._launch()
            job.done(browser.shoot(job.url))
        except ScreenshotError as exc:
            logger.debug("%s", exc)
            job.fail(exc)
        # Anything else is a browser that failed in a way nothing here expected, which is
        # not one to go on photographing with: the next job launches a fresh one.
        # pylint: disable-next=broad-exception-caught
        except Exception as exc:
            logger.warning("The screenshot browser failed: %s", _first_line(exc))
            job.fail(ScreenshotError(f"Could not photograph {job.url}: {_first_line(exc)}"))
            if browser is not None:
                _let_go(browser)
            return None
        return browser


def _let_go(browser: Browser) -> None:
    """Close a browser, whatever state it is in: it is being let go either way."""
    try:
        browser.close()
    # A browser that crashed has nothing left to close, and saying so is all there is to do.
    # pylint: disable-next=broad-exception-caught
    except Exception as exc:
        logger.debug("Closing the screenshot browser failed: %s", _first_line(exc))
