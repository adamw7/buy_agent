"""A local HTTP server for the Angular UI in ``ui/`` (ADR-0010, ADR-0011, ADR-0034,
ADR-0035, ADR-0033, ADR-0018, ADR-0065)."""

from __future__ import annotations

import argparse
import errno
import json
import logging
import mimetypes
import queue
import re
import socket
import sys
import threading
import time
from contextvars import ContextVar
from functools import partial
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, unquote, urlparse

from buy_agent.agent import BuyAgent, Checkpoint, every_step_passes
from buy_agent.api import (
    ApiError,
    bounds_payload,
    defaults_payload,
    installed_models,
    parse_options,
    pay_now,
    rank_again,
    run_search,
    screenshot,
    sources_payload,
)
from buy_agent.config import DEFAULT_BACKEND, DEFAULT_PROVIDER, DEFAULT_RAIL
from buy_agent.logging_setup import configure_logging
from buy_agent.providers import PROVIDERS, provider_for
from buy_agent.screenshots import INSTALL, Camera, available
from buy_agent.search import backend_for
from buy_agent.rails import rail_for

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from buy_agent.api import AgentFactory

logger = logging.getLogger(__name__)

#: Where ``ng build`` leaves the app, relative to the repository root.
DEFAULT_UI_DIR = Path(__file__).resolve().parent.parent / "ui" / "dist" / "ui" / "browser"

#: The default bind: loopback, on the port the ``Dockerfile`` and ``scripts/start.ps1``
#: are held to by a convention test.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000

#: Seconds of silence before an SSE ``ping``, so proxies keep a quiet stream open.
_KEEPALIVE_SECONDS = 15.0

_MAX_BODY_BYTES = 64 * 1024

#: How long a browser may cache a screenshot.
_SCREENSHOT_CACHE = "private, max-age=3600"

#: A content-hashed build file (``main-AC2JNJ6W.js``), cacheable for good; anything
#: else, ``index.html`` above all, is revalidated on every load.
_HASHED_ASSET = re.compile(r"-[A-Z0-9]{8}\.[a-z0-9]+$")
_IMMUTABLE = "public, max-age=31536000, immutable"
_REVALIDATE = "no-cache"

#: How long one blocking read or write on a connection may take (ADR-0034).
_REQUEST_TIMEOUT = 30.0

#: Bindable ports. Checked at the door: out of range, ``bind`` raises ``OverflowError``,
#: which ``main``'s ``OSError`` handler misses.
_PORTS = (0, 65535)

#: Host names that mean "this machine".
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

#: The ``Sec-Fetch-Site`` value meaning a page on another site made the request.
_CROSS_SITE = "cross-site"

#: Headers on every response.
_SECURITY_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    # No opener access, and no powerful features: the page uses none.
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()"),
    (
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
        "form-action 'self'; base-uri 'self'; object-src 'none'; "
        "frame-ancestors 'none'",
    ),
)

#: Content types for what ``ng build`` emits.
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


#: What to run to get a page.
_UNBUILT_COMMAND = "npm install && npm run build"

#: The sentence a client that did not ask for HTML gets.
_UNBUILT = "The UI is not built. {remedy}"

#: The same answer for a browser.
_UNBUILT_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>buy_agent -- the UI is not built</title>
<style>
body {{ margin: 0; padding: 48px 20px; font: 15px/1.55 system-ui, sans-serif; }}
main {{ max-width: 620px; margin: 0 auto; }}
h1 {{ font-size: 20px; font-family: ui-monospace, monospace; }}
code, pre {{ font-family: ui-monospace, monospace; }}
pre {{ padding: 12px 14px; border-radius: 9px; background: rgb(128 128 128 / 14%);
      overflow-x: auto; }}
p {{ color: #5c6470; }}
@media (prefers-color-scheme: dark) {{ p {{ color: #99a3b0; }} }}
</style>
</head>
<body>
<main>
<h1>buy_agent</h1>
<p>The API is answering, but the page has not been built yet.</p>
{remedy}
</main>
</body>
</html>
"""


def _unexpected(exc: Exception) -> dict[str, Any]:
    """The body of an unplanned 500."""
    return {"error": f"Unexpected failure: {exc}"}


def _no_such_endpoint(path: str) -> dict[str, Any]:
    """The body of an unknown ``/api`` path."""
    return {"error": f"No such endpoint: {path}"}


class _Stopped(Exception):
    """Ends a run whose reader has gone, at a step boundary (ADR-0009, ADR-0034)."""


def _stop_when(stopped: threading.Event) -> Checkpoint:
    """A checkpoint that ends a run at the first step boundary after ``stopped``."""

    def checkpoint(step: str) -> None:
        if stopped.is_set():
            raise _Stopped(step)

    return checkpoint


#: Where the watched run's log lines go; ``None`` when nobody is streaming.
_sink: ContextVar[queue.Queue[Any] | None] = ContextVar("buy_agent_stream", default=None)


class _LogRelay(logging.Handler):
    """Fans ``buy_agent`` log records out to the run that produced them."""

    def emit(self, record: logging.LogRecord) -> None:
        sink = _sink.get()
        if sink is None:
            return
        try:
            sink.put(
                {
                    # The CLI's clock: gaps between lines show slow steps.
                    "time": time.strftime("%H:%M:%S", time.localtime(record.created)),
                    "level": record.levelname,
                    "logger": record.name,
                    "message": record.getMessage(),
                }
            )
        # A broken relay must not break the run it is only reporting on.
        # pylint: disable-next=broad-exception-caught
        except Exception:
            self.handleError(record)

    def attach(self, sink: queue.Queue[Any]) -> None:
        _sink.set(sink)

    def detach(self) -> None:
        _sink.set(None)


_relay = _LogRelay()


def _install_relay() -> None:
    """Put the relay on the package logger. Idempotent -- ``addHandler`` dedupes."""
    package_logger = logging.getLogger("buy_agent")
    package_logger.addHandler(_relay)
    # Progress is at INFO, which the default level drops.
    if package_logger.getEffectiveLevel() > logging.INFO:
        package_logger.setLevel(logging.INFO)


class BuyAgentHandler(BaseHTTPRequestHandler):
    """Routes ``/api`` to the agent and everything else to the built UI."""

    # ``close_connection`` belongs to the base class, which sets it outside ``__init__``.
    # pylint: disable=attribute-defined-outside-init

    server_version = "buy_agent"
    protocol_version = "HTTP/1.1"
    #: Read by ``socketserver`` at connection setup (see :data:`_REQUEST_TIMEOUT`).
    timeout = _REQUEST_TIMEOUT

    def __init__(
        self,
        *args: Any,
        ui_dir: Path,
        agent_factory: AgentFactory,
        allowed_hosts: frozenset[str] | None = _LOOPBACK_HOSTS,
        camera: Camera | None = None,
        **kwargs: Any,
    ) -> None:
        self.ui_dir = ui_dir
        self.agent_factory = agent_factory
        #: None accepts every ``Host``, as a public bind does.
        self.allowed_hosts = allowed_hosts
        #: None takes no pictures (ADR-0065).
        self.camera = camera
        super().__init__(*args, **kwargs)

    # -- who is allowed to ask --------------------------------------------------

    def _refused(self) -> bool:
        """Refuse a request not from this server's own page; say whether it was
        (ADR-0018)."""
        if self._origin_admits() and self._host_admits():
            return False
        self._refuse()
        return True

    def _origin_admits(self) -> bool:
        """Reject a request a page on another site made."""
        if self.headers.get("Sec-Fetch-Site", "").strip().lower() == _CROSS_SITE:
            return False
        origin = self.headers.get("Origin")
        # "null" (a sandboxed iframe, a data: document) is never this app.
        if origin is None or origin == "null":
            return origin is None

        netloc = urlparse(origin).netloc.strip().lower()
        # Origin equal to Host is our own page: another page cannot forge both.
        if netloc and netloc == self.headers.get("Host", "").strip().lower():
            return True
        return _hostname(netloc) in _LOOPBACK_HOSTS

    def _host_admits(self) -> bool:
        """Reject a name that resolved here without being one of ours."""
        if self.allowed_hosts is None:
            return True
        return _hostname(self.headers.get("Host", "")) in self.allowed_hosts

    def _refuse(self) -> None:
        """Answer a request from somewhere else, without doing any of its work."""
        # Terse and not CORS-negotiable: nothing here is for other sites.
        self.close_connection = True
        # All three headers the checks read, as they arrived.
        logger.warning(
            "Refused a %s %s from origin %r with host %r and fetch site %r",
            self.command,
            self.path,
            self.headers.get("Origin"),
            self.headers.get("Host"),
            self.headers.get("Sec-Fetch-Site"),
        )
        self._send_json(403, {"error": "This API only answers its own page."})

    # -- routing ---------------------------------------------------------------

    # The base class dispatches on the verb's name.
    # pylint: disable-next=invalid-name
    def do_GET(self) -> None:
        if self._refused():
            return
        url = urlparse(self.path)
        params = {key: values[-1] for key, values in parse_qs(url.query).items()}
        # Outside the guard: the stream spends its status line early and reports
        # failures as ``failure`` events.
        if url.path == "/api/search/stream":
            self._stream_search(params)
            return
        try:
            if url.path == "/api/config":
                self._send_json(200, defaults_payload(screenshots=self.camera is not None))
            elif url.path == "/api/models":
                self._send_json(200, self._models(params))
            elif url.path == "/api/sources":
                # 200 either way: the answer is the verdict (ADR-0033).
                self._send_json(200, sources_payload(params.get("sources", "")))
            elif url.path == "/api/bounds":
                # Offered to the form, never applied (ADR-0059).
                self._send_json(200, bounds_payload(params.get("request", "")))
            elif url.path == "/api/screenshot":
                # A JPEG, for an ``<img>`` (ADR-0065).
                self._send_screenshot(params.get("url", ""))
            elif url.path.startswith("/api/"):
                self._send_json(404, _no_such_endpoint(url.path))
            else:
                self._serve_static(url.path)
        # A 500 beats socketserver closing the socket unanswered, which reads as a
        # server that is down.
        # pylint: disable-next=broad-exception-caught
        except Exception as exc:
            logger.exception("Unexpected failure answering %s", url.path)
            self._send_json(500, _unexpected(exc))

    # The base class dispatches on the verb's name.
    # pylint: disable-next=invalid-name
    def do_POST(self) -> None:
        if self._refused():
            return
        url = urlparse(self.path)
        # Same shape; only a search runs anything (ADR-0035).
        endpoints: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "/api/search": self._search,
            "/api/rank": rank_again,
            # A POST: a query string cannot carry the products or the approval.
            "/api/pay": pay_now,
        }
        run = endpoints.get(url.path)
        if run is None:
            # The body is unread, so the connection must close (as in ``_read_json``).
            self.close_connection = True
            self._send_json(404, _no_such_endpoint(url.path))
            return
        try:
            payload = self._read_json()
            self._send_json(200, run(payload))
        except ApiError as exc:
            self._send_json(exc.status, exc.payload())
        # As in ``do_GET``.
        # pylint: disable-next=broad-exception-caught
        except Exception as exc:
            logger.exception("Unexpected failure during a search")
            self._send_json(500, _unexpected(exc))

    # The base class dispatches on the verb's name.
    # pylint: disable-next=invalid-name
    def do_HEAD(self) -> None:
        """Answer HEAD like GET, minus the body -- but never by running a search."""
        if self._refused():
            return
        if urlparse(self.path).path == "/api/search/stream":
            self._send_json(405, {"error": "A search stream has to be asked for with GET."})
            return
        self.do_GET()

    # -- the agent -------------------------------------------------------------

    def _models(self, params: dict[str, str]) -> dict[str, Any]:
        """The named server's models, for the form's picker."""
        provider = params.get("provider") or DEFAULT_PROVIDER
        server = PROVIDERS.get(provider)
        base_url = params.get("base_url") or (server.base_url if server else "")
        return installed_models(provider, base_url)

    def _search(
        self, data: dict[str, Any], *, checkpoint: Checkpoint = every_step_passes
    ) -> dict[str, Any]:
        config, sort_by = parse_options(data)
        request = str(data.get("request") or "")
        return run_search(
            request,
            config,
            sort_by=sort_by,
            agent_factory=self.agent_factory,
            checkpoint=checkpoint,
        )

    def _stream_search(self, params: dict[str, str]) -> None:
        """Run a search in a worker thread, relaying its log lines as they arrive
        (ADR-0034)."""
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "close")
            self._send_security_headers()
            self.end_headers()
        except OSError:
            return

        # No keep-alive on a stream whose length nobody knows in advance.
        self.close_connection = True

        stopped = threading.Event()
        for event, data in self._search_events(params, stopped):
            if not self._send_event(event, data):
                # Takes effect at the next step; a model call cannot be cancelled.
                stopped.set()
                logger.info("Client disconnected; stopping the run at its next step")
                return

    def _search_events(
        self, params: dict[str, str], stopped: threading.Event
    ) -> Iterator[tuple[str, Any]]:
        """Yield ``log`` events for the run's progress, then ``result`` or ``failure``
        (ADR-0011)."""
        _install_relay()
        sink: queue.Queue[Any] = queue.Queue()
        done = object()
        outcome: dict[str, Any] = {}

        def work() -> None:
            _relay.attach(sink)
            try:
                outcome["result"] = self._search(params, checkpoint=_stop_when(stopped))
            except _Stopped as where:
                logger.info("Run stopped before %s: nobody is reading it", where)
            except ApiError as exc:
                outcome["error"] = (exc.status, exc.payload())
            # The status line is spent, so report as a ``failure`` event.
            # pylint: disable-next=broad-exception-caught
            except Exception as exc:
                logger.exception("Unexpected failure during a streamed search")
                outcome["error"] = (500, _unexpected(exc))
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

    def _send_screenshot(self, address: str) -> None:
        """A JPEG of a card's page, or JSON saying why not."""
        try:
            picture = screenshot(address, self.camera)
        except ApiError as exc:
            self._send_json(exc.status, exc.payload())
            return
        self._send_bytes(
            200, picture, "image/jpeg", headers=(("Cache-Control", _SCREENSHOT_CACHE),)
        )

    # -- static files ----------------------------------------------------------

    def _serve_static(self, path: str) -> None:
        target = self._resolve(path)
        if target is None:
            self._send_unbuilt()
            return

        body = target.read_bytes()
        content_type = (
            _CONTENT_TYPES.get(target.suffix.lower())
            or mimetypes.guess_type(target.name)[0]
            or "application/octet-stream"
        )
        cache = _IMMUTABLE if _HASHED_ASSET.search(target.name) else _REVALIDATE
        self._send_bytes(200, body, content_type, headers=(("Cache-Control", cache),))

    def _send_unbuilt(self) -> None:
        """Say the app is not built, as HTML or JSON per ``Accept``."""
        if "text/html" not in self.headers.get("Accept", ""):
            remedy = _unbuilt_remedy(self.ui_dir)
            self._send_json(503, {"error": _UNBUILT.format(remedy=remedy)})
            return
        self._send_bytes(
            503,
            _UNBUILT_PAGE.format(remedy=_unbuilt_remedy_html(self.ui_dir)).encode("utf-8"),
            "text/html; charset=utf-8",
        )

    def _resolve(self, path: str) -> Path | None:
        """Map a URL path to a file inside the UI directory, or to ``index.html``."""
        index = self.ui_dir / "index.html"
        if not index.is_file():
            return None

        root = self.ui_dir.resolve()
        relative = unquote(urlparse(path).path).lstrip("/")
        try:
            candidate = (self.ui_dir / relative).resolve()
        except (OSError, ValueError):
            # A percent-encoded NUL makes resolve() raise.
            return index
        # Outside the UI directory (a '..' walk): serve the app instead.
        if relative and candidate.is_relative_to(root) and candidate.is_file():
            return candidate
        return index

    # -- plumbing --------------------------------------------------------------

    def _read_json(self) -> dict[str, Any]:
        # An unread body would be parsed as the next request, so every such path closes
        # the connection.
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            raise ApiError("Send a body with a Content-Length; chunked is not read here.", 411)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length < 0:
                # The same desync through a number that is technically an integer.
                raise ValueError(length)
        except ValueError as exc:
            self.close_connection = True
            raise ApiError("Content-Length is not a number.") from exc
        if length > _MAX_BODY_BYTES:
            self.close_connection = True
            raise ApiError("Request body is too large.", 413)
        if length == 0:
            return {}
        try:
            raw = self.rfile.read(length)
        except TimeoutError as exc:
            # A body announced and never sent: what :data:`_REQUEST_TIMEOUT` ends.
            self.close_connection = True
            raise ApiError("The request body did not arrive in time.", 408) from exc
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

    def _send_security_headers(self) -> None:
        """Say what the page is allowed to do, on every response."""
        for name, value in _SECURITY_HEADERS:
            self.send_header(name, value)

    def _send_bytes(
        self,
        status: int,
        body: bytes,
        content_type: str,
        *,
        headers: Sequence[tuple[str, str]] = (),
    ) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for name, value in headers:
                self.send_header(name, value)
            self._send_security_headers()
            if self.close_connection:
                # Tell the client rather than let its next request meet a reset.
                self.send_header("Connection", "close")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        except OSError:
            # A timed-out socket refuses reads too; left open, socketserver would close it
            # silently.
            self.close_connection = True
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

    # ``format`` is the base class's parameter name.
    # pylint: disable-next=redefined-builtin
    def log_message(self, format: str, *args: Any) -> None:
        """Send request logging through logging, not straight to stderr."""
        logger.debug("%s - %s", self.address_string(), format % args)


def _workspace_for(ui_dir: Path) -> Path | None:
    """The Angular workspace whose build lands in ``ui_dir`` -- where npm is run."""
    workspace = ui_dir.parent.parent.parent
    return workspace if (workspace / "package.json").is_file() else None


def _unbuilt_remedy(ui_dir: Path) -> str:
    """The remedy for a missing build at this ``--ui-dir``."""
    workspace = _workspace_for(ui_dir)
    if workspace is None:
        return (
            f"There is no Angular workspace above {ui_dir} to build, so point "
            f"--ui-dir at a build that exists -- ui/dist/ui/browser in a checkout."
        )
    return (
        f"Run '{_UNBUILT_COMMAND}' in {workspace}, or point --ui-dir at a build "
        f"elsewhere."
    )


def _unbuilt_remedy_html(ui_dir: Path) -> str:
    """The same remedy as HTML."""
    workspace = _workspace_for(ui_dir)
    if workspace is None:
        return (
            f"<p>There is no Angular workspace above <code>{escape(str(ui_dir))}</code>,"
            " so there is nothing here to build. Point <code>--ui-dir</code> at a"
            " build that exists -- <code>ui/dist/ui/browser</code> in a checkout.</p>"
        )
    return (
        f"<p>Run this in <code>{escape(str(workspace))}</code>:</p>"
        f"<pre>{escape(_UNBUILT_COMMAND)}</pre>"
        "<p>Then reload. A build that lives somewhere else is named with"
        " <code>--ui-dir</code>.</p>"
    )


def _browsable_url(host: str, port: int) -> str:
    """The address to type into a browser for a server bound to ``host``."""
    shown = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(host, host)
    if ":" in shown:
        shown = f"[{shown}]"
    return f"http://{shown}:{port}"


def _clashing_provider(port: int, exc: OSError) -> str:
    """The sentence naming the model server whose default address is ``port`` -- only
    for ``EADDRINUSE``, not a bad host."""
    if exc.errno != errno.EADDRINUSE:
        return ""
    for server in PROVIDERS.values():
        listens = urlparse(server.base_url)
        if listens.port == port and _hostname(listens.netloc) in _LOOPBACK_HOSTS:
            return (
                f" That is also {server.label}'s own default address "
                f"({server.base_url}), so serve the UI somewhere else: "
                f"--port {port + 1}"
            )
    return ""


def _hostname(netloc: str) -> str:
    """The host out of a ``Host`` header or an origin's netloc, lowercased."""
    host = netloc.strip().lower()
    if host.startswith("["):
        return host[1:].partition("]")[0]
    return host.partition(":")[0]


def _bound_host(address: str) -> str:
    """The host of a typed address, lowercased.

    Unlike :func:`_hostname`, a bare IPv6 literal (``::1``) is not split at its colons
    (ADR-0018).
    """
    host = address.strip().lower()
    if host.startswith("["):
        return _hostname(host)
    # One colon is ``host:port``; more is a bare IPv6 literal, with no port.
    return host if host.count(":") > 1 else host.partition(":")[0]


def allowed_hosts_for(host: str, extra: Sequence[str] = ()) -> frozenset[str] | None:
    """Which ``Host`` headers a server bound to ``host`` should answer."""
    # Drop blanks: a request with no ``Host`` arrives as one.
    named = frozenset(filter(None, map(_bound_host, extra)))
    if _bound_host(host) not in _LOOPBACK_HOSTS:
        return named or None
    return _LOOPBACK_HOSTS | named


def _family_for(host: str) -> int:
    """The socket family for a bind address: a colon means IPv6 (the base class is
    ``AF_INET`` only)."""
    return socket.AF_INET6 if ":" in host else socket.AF_INET


class _HTTPServer(ThreadingHTTPServer):
    """``ThreadingHTTPServer`` over whichever family the address it is given needs."""

    def __init__(self, server_address: tuple[str, int], handler: Any) -> None:
        # Set before the base class, which reads it to open the socket.
        self.address_family = _family_for(server_address[0])
        super().__init__(server_address, handler)


def create_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    ui_dir: Path | None = None,
    agent_factory: AgentFactory = BuyAgent,
    allowed_hosts: frozenset[str] | None = _LOOPBACK_HOSTS,
    camera: Camera | None = None,
) -> ThreadingHTTPServer:
    """Build the HTTP server without starting it."""
    handler = partial(
        BuyAgentHandler,
        ui_dir=ui_dir or DEFAULT_UI_DIR,
        agent_factory=agent_factory,
        allowed_hosts=allowed_hosts,
        camera=camera,
    )
    return _HTTPServer((host, port), handler)


def camera_for(host: str) -> Camera | None:
    """A camera for a loopback bind with Playwright installed, else ``None`` (ADR-0065).

    Bound publicly, it would photograph internal pages for anyone on the network.
    """
    if not available():
        logger.info(
            "Cards will have no screenshot of their page: that needs Playwright. %s",
            INSTALL,
        )
        return None
    if _bound_host(host) not in _LOOPBACK_HOSTS:
        logger.warning(
            "Screenshots are off: bound to %s, this server would photograph any address "
            "for whoever can reach it.",
            host,
        )
        return None
    return Camera()


def _port(text: str) -> int:
    """``--port`` as argparse takes it: a bindable port, else a usage error."""
    minimum, maximum = _PORTS
    try:
        port = int(text)
    # argparse would say "invalid _port value".
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"must be a whole number; got {text!r}") from exc
    if not minimum <= port <= maximum:
        raise argparse.ArgumentTypeError(
            f"must be between {minimum} and {maximum}; got {port}"
        )
    return port


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="buy_agent.server",
        description="Serve the buy_agent UI and its JSON API on localhost.",
    )
    # Both name their default, like every flag here (ADR-0018).
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"Interface to bind (default: {DEFAULT_HOST}, this machine only). Binding "
        "anywhere else answers any Host header unless --allowed-host names one.",
    )
    parser.add_argument(
        "--port",
        type=_port,
        default=DEFAULT_PORT,
        help=f"Port to bind (default: {DEFAULT_PORT}; 0 takes whichever one is free "
        "and says which at startup).",
    )
    parser.add_argument(
        "--ui-dir",
        type=Path,
        default=DEFAULT_UI_DIR,
        help="Directory holding the built Angular app (default: ui/dist/ui/browser).",
    )
    parser.add_argument(
        "--allowed-host",
        action="append",
        default=[],
        metavar="HOST",
        help=(
            "Extra Host header to answer, repeatable, so that a name pointed at "
            "this machine cannot pass itself off as this server. A loopback bind "
            "answers the loopback names as well; a public one answers these and "
            "nothing else, and any Host at all where none were named."
        ),
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(verbose=args.verbose)

    # Installed on the package logger so every module's output reaches a stream.
    _install_relay()

    try:
        provider_for(DEFAULT_PROVIDER)
        rail_for(DEFAULT_RAIL)
        backend_for(DEFAULT_BACKEND)
    except ValueError as exc:
        # Refuse a misspelt ``$BUY_AGENT_PROVIDER``, ``$BUY_AGENT_RAIL`` or
        # ``$BUY_AGENT_BACKEND`` now, not as a 500 per page load.
        logger.error("%s", exc)
        return 1

    allowed = allowed_hosts_for(args.host, args.allowed_host)
    if allowed is None:
        logger.warning(
            "Bound to %s with no --allowed-host: this answers any Host header, so "
            "a name resolved here by someone else is answered too. Name the host "
            "you reach it by to close that.",
            args.host,
        )

    camera = camera_for(args.host)
    try:
        httpd = create_server(
            args.host, args.port, ui_dir=args.ui_dir, allowed_hosts=allowed, camera=camera
        )
    except OSError as exc:
        logger.error(
            "Could not listen on %s:%s (%s).%s",
            args.host,
            args.port,
            exc,
            _clashing_provider(args.port, exc),
        )
        return 1

    host, port = httpd.server_address[:2]
    logger.info("buy_agent UI on %s", _browsable_url(str(host), port))
    if not (args.ui_dir / "index.html").is_file():
        # The remedy the 503 page quotes.

        logger.warning(
            "No built UI at %s -- the API works, but the page will not. %s",
            args.ui_dir,
            _unbuilt_remedy(args.ui_dir),
        )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.warning("Interrupted.")
        return 130
    finally:
        httpd.server_close()
        if camera is not None:
            camera.close()
        logging.getLogger("buy_agent").removeHandler(_relay)
    return 0


if __name__ == "__main__":
    sys.exit(main())
