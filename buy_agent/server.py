"""A local HTTP server for the Angular UI in ``ui/``.

Stdlib only, on purpose: the agent's dependency list is already the interesting
part of this project, and a shopping run that takes a minute and serves one
person does not need a web framework under it.

Two ways to ask for the same thing:

``POST /api/search``
    Runs the pipeline and answers with the ranked products in one JSON response.
    Straightforward, and the shape scripts want.
``GET /api/search/stream``
    The same run as Server-Sent Events (``log`` lines, then ``result`` or
    ``failure``), relaying the agent's own log lines as they happen. A run takes
    tens of seconds, so the UI uses this one and shows the same progress the CLI
    prints.

Everything outside ``/api`` is the built Angular app, with unknown paths falling
back to ``index.html`` so the single-page app keeps its own routing.
"""

from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import queue
import sys
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, unquote, urlparse

from buy_agent.agent import BuyAgent
from buy_agent.api import (
    ApiError,
    defaults_payload,
    installed_models,
    parse_options,
    run_search,
)
from buy_agent.config import DEFAULT_BASE_URL
from buy_agent.logging_setup import configure_logging

if TYPE_CHECKING:
    from collections.abc import Iterator

    from buy_agent.api import AgentFactory

logger = logging.getLogger(__name__)

#: Where ``ng build`` leaves the app, relative to the repository root.
DEFAULT_UI_DIR = Path(__file__).resolve().parent.parent / "ui" / "dist" / "ui" / "browser"

#: How long the SSE loop waits for a log line before sending a ``ping`` event to
#: keep browsers and proxies from timing out a quiet stream. Extraction is the
#: slow step and logs nothing while it runs, so quiet stretches are normal.
_KEEPALIVE_SECONDS = 15.0

_MAX_BODY_BYTES = 64 * 1024

#: Content types for what ``ng build`` emits. Spelled out rather than left to
#: ``mimetypes``, which reads the registry on Windows and can answer ``text/plain``
#: for ``.js`` -- which a browser refuses to run as a module, leaving a blank page.
_CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".ico": "image/x-icon",
    ".jpg": "image/jpeg",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".txt": "text/plain; charset=utf-8",
    ".webmanifest": "application/manifest+json",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}


class _LogRelay(logging.Handler):
    """Fans ``buy_agent`` log records out to the run that produced them.

    Each streamed search runs in its own thread, so the thread id is enough to
    tell whose log line a record is -- two searches at once do not see each
    other's progress.
    """

    def __init__(self) -> None:
        super().__init__()
        self._queues: dict[int, queue.Queue[Any]] = {}
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        with self._lock:
            sink = self._queues.get(threading.get_ident())
        if sink is None:
            return
        try:
            sink.put(
                {
                    "level": record.levelname,
                    "logger": record.name,
                    "message": record.getMessage(),
                }
            )
        except Exception:  # noqa: BLE001 -- a broken relay must not break the run
            self.handleError(record)

    def attach(self, sink: queue.Queue[Any]) -> None:
        with self._lock:
            self._queues[threading.get_ident()] = sink

    def detach(self) -> None:
        with self._lock:
            self._queues.pop(threading.get_ident(), None)


_relay = _LogRelay()


def _install_relay() -> None:
    """Put the relay on the package logger. Idempotent -- ``addHandler`` dedupes."""
    package_logger = logging.getLogger("buy_agent")
    package_logger.addHandler(_relay)
    # The progress the browser is waiting for is logged at INFO, and a logger left
    # at its default would drop it before any handler sees it. ``configure_logging``
    # does this too, but the server is also importable without it.
    if package_logger.getEffectiveLevel() > logging.INFO:
        package_logger.setLevel(logging.INFO)


class BuyAgentHandler(BaseHTTPRequestHandler):
    """Routes ``/api`` to the agent and everything else to the built UI."""

    server_version = "buy_agent"
    protocol_version = "HTTP/1.1"

    def __init__(
        self, *args: Any, ui_dir: Path, agent_factory: AgentFactory, **kwargs: Any
    ) -> None:
        self.ui_dir = ui_dir
        self.agent_factory = agent_factory
        super().__init__(*args, **kwargs)

    # -- routing ---------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 -- BaseHTTPRequestHandler's spelling
        url = urlparse(self.path)
        params = {key: values[-1] for key, values in parse_qs(url.query).items()}
        if url.path == "/api/config":
            self._send_json(200, defaults_payload())
        elif url.path == "/api/models":
            self._send_json(200, installed_models(params.get("base_url") or DEFAULT_BASE_URL))
        elif url.path == "/api/search/stream":
            self._stream_search(params)
        elif url.path.startswith("/api/"):
            self._send_json(404, {"error": f"No such endpoint: {url.path}"})
        else:
            self._serve_static(url.path)

    def do_POST(self) -> None:  # noqa: N802 -- BaseHTTPRequestHandler's spelling
        url = urlparse(self.path)
        if url.path != "/api/search":
            self._send_json(404, {"error": f"No such endpoint: {url.path}"})
            return
        try:
            payload = self._read_json()
            self._send_json(200, self._search(payload))
        except ApiError as exc:
            self._send_json(exc.status, exc.payload())

    def do_HEAD(self) -> None:  # noqa: N802 -- BaseHTTPRequestHandler's spelling
        """Answer HEAD like GET, minus the body -- but never by running a search."""
        if urlparse(self.path).path == "/api/search/stream":
            self._send_json(405, {"error": "A search stream has to be asked for with GET."})
            return
        self.do_GET()

    # -- the agent -------------------------------------------------------------

    def _search(self, data: dict[str, Any]) -> dict[str, Any]:
        config, sort_by = parse_options(data)
        request = str(data.get("request") or "")
        return run_search(
            request, config, sort_by=sort_by, agent_factory=self.agent_factory
        )

    def _stream_search(self, params: dict[str, str]) -> None:
        """Run a search in a worker thread, relaying its log lines as they arrive.

        The work cannot happen on this thread: the response has to be written
        while the run is still going, which is the whole point of the stream.
        """
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "close")
            self.end_headers()
        except OSError:
            return

        # No keep-alive on a stream whose length nobody knows in advance.
        self.close_connection = True

        for event, data in self._search_events(params):
            if not self._send_event(event, data):
                # The run itself carries on to the end: it is blocked inside an
                # HTTP call to Ollama, and there is nothing here to cancel it with.
                logger.info("Client disconnected; abandoning the stream")
                return

    def _search_events(self, params: dict[str, str]) -> Iterator[tuple[str, Any]]:
        """Yield ``log`` events for the run's progress, then ``result`` or ``failure``.

        The failure event is not called ``error`` because a browser's EventSource
        delivers its own transport errors under that name, and a run that failed
        on the server is a different thing from a connection that dropped.
        """
        _install_relay()
        sink: queue.Queue[Any] = queue.Queue()
        done = object()
        outcome: dict[str, Any] = {}

        def work() -> None:
            _relay.attach(sink)
            try:
                outcome["result"] = self._search(params)
            except ApiError as exc:
                outcome["error"] = (exc.status, exc.payload())
            except Exception as exc:  # noqa: BLE001 -- the stream reports, never crashes
                logger.exception("Unexpected failure during a streamed search")
                outcome["error"] = (500, {"error": f"Unexpected failure: {exc}"})
            finally:
                _relay.detach()
                sink.put(done)

        worker = threading.Thread(target=work, name="buy_agent-search", daemon=True)
        worker.start()

        while True:
            try:
                item = sink.get(timeout=_KEEPALIVE_SECONDS)
            except queue.Empty:
                yield "ping", {}
                continue
            if item is done:
                break
            yield "log", item

        worker.join(timeout=5.0)
        if "result" in outcome:
            yield "result", outcome["result"]
        else:
            status, payload = outcome.get("error", (500, {"error": "The search ended."}))
            yield "failure", {**payload, "status": status}

    # -- static files ----------------------------------------------------------

    def _serve_static(self, path: str) -> None:
        target = self._resolve(path)
        if target is None:
            self._send_json(
                503,
                {
                    "error": (
                        f"The UI is not built. Run 'npm install && npm run build' in "
                        f"{self.ui_dir.parent.parent} , or point --ui-dir at the build."
                    )
                },
            )
            return

        body = target.read_bytes()
        content_type = _CONTENT_TYPES.get(target.suffix.lower()) or (
            mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        )
        self._send_bytes(200, body, content_type)

    def _resolve(self, path: str) -> Path | None:
        """Map a URL path to a file inside the UI directory, or to ``index.html``.

        Returns None when there is nothing to serve at all, which nearly always
        means the app has not been built yet.
        """
        index = self.ui_dir / "index.html"
        if not index.is_file():
            return None

        relative = unquote(urlparse(path).path).lstrip("/")
        candidate = (self.ui_dir / relative).resolve()
        # A candidate outside the UI directory is someone walking out of it with
        # '..'; fall through to the app rather than reading the filesystem.
        inside = candidate == self.ui_dir.resolve() or self.ui_dir.resolve() in candidate.parents
        if relative and inside and candidate.is_file():
            return candidate
        return index

    # -- plumbing --------------------------------------------------------------

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as exc:
            raise ApiError("Content-Length is not a number.") from exc
        if length > _MAX_BODY_BYTES:
            raise ApiError("Request body is too large.", 413)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError(f"Body is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ApiError("Body must be a JSON object.")
        return payload

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        except OSError:
            logger.debug("Client went away before the response was written")

    def _send_event(self, event: str, data: Any) -> bool:
        """Write one SSE frame. False means the client is gone."""
        frame = f"event: {event}\ndata: {json.dumps(data)}\n\n"
        try:
            self.wfile.write(frame.encode("utf-8"))
            self.wfile.flush()
        except OSError:
            return False
        return True

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Send request logging through logging, not straight to stderr."""
        logger.debug("%s - %s", self.address_string(), format % args)


def create_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    *,
    ui_dir: Path | None = None,
    agent_factory: AgentFactory = BuyAgent,
) -> ThreadingHTTPServer:
    """Build the HTTP server without starting it.

    Args:
        host: Interface to bind. Loopback by default -- this serves an agent that
            runs commands against a local Ollama, not something to expose.
        port: Port to bind, or 0 to let the OS choose (what the tests do).
        ui_dir: Directory holding the built Angular app.
        agent_factory: Builds the agent from a config; the tests' injection seam.
    """
    handler = partial(
        BuyAgentHandler,
        ui_dir=ui_dir or DEFAULT_UI_DIR,
        agent_factory=agent_factory,
    )
    return ThreadingHTTPServer((host, port), handler)  # type: ignore[arg-type]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="buy_agent.server",
        description="Serve the buy_agent UI and its JSON API on localhost.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Interface to bind.")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind.")
    parser.add_argument(
        "--ui-dir",
        type=Path,
        default=DEFAULT_UI_DIR,
        help="Directory holding the built Angular app (default: ui/dist/ui/browser).",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(verbose=args.verbose)

    # The relay is what lets a stream show the run's progress; it is installed on
    # the package logger so every module's output reaches the browser.
    _install_relay()

    try:
        httpd = create_server(args.host, args.port, ui_dir=args.ui_dir)
    except OSError as exc:
        logger.error("Could not listen on %s:%s (%s)", args.host, args.port, exc)
        return 1

    host, port = httpd.server_address[:2]
    logger.info("buy_agent UI on http://%s:%s", host, port)
    if not (args.ui_dir / "index.html").is_file():
        logger.warning(
            "No built UI at %s -- the API works, but the page will not. "
            "Build it with:  npm install && npm run build   (in ui/)",
            args.ui_dir,
        )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.warning("Interrupted.")
        return 130
    finally:
        httpd.server_close()
        logging.getLogger("buy_agent").removeHandler(_relay)
    return 0


if __name__ == "__main__":
    sys.exit(main())
