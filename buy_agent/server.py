"""A local HTTP server for the Angular UI in ``ui/`` (ADR-0010, ADR-0011, ADR-0034,
ADR-0035, ADR-0033, ADR-0018, ADR-0065)."""

from __future__ import annotations

import argparse
import errno
import json
import logging
import mimetypes
import queue
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

#: Where the server listens when nothing says otherwise: this machine only, on the port
#: the ``Dockerfile``'s ``EXPOSE`` and the URL ``scripts/start.ps1`` opens a browser at
#: are both held to by a convention test.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000

#: How long the SSE loop waits for a log line before sending a ``ping``, so a quiet
#: stream is not timed out by a browser or a proxy.
_KEEPALIVE_SECONDS = 15.0

_MAX_BODY_BYTES = 64 * 1024

#: How long a browser may keep a picture of a page before asking for it again. An hour:
#: long enough that a second run of the same search draws its cards at once, and short
#: enough that nobody is shown a price banner a day old.
_SCREENSHOT_CACHE = "private, max-age=3600"

#: How long one blocking read or write on a connection may take (ADR-0034).
_REQUEST_TIMEOUT = 30.0

#: The ports a socket can actually be bound to. Held at the door for the reason
#: ``config.LIMITS`` holds the run's numbers there: out of range, ``bind`` raises an
#: ``OverflowError``, which is not the ``OSError`` ``main`` answers -- so a mistyped
#: port left the traceback the rest of this file exists to avoid.
_PORTS = (0, 65535)

#: Host names that mean "this machine".
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

#: The ``Sec-Fetch-Site`` value meaning a page on another site made the request.
_CROSS_SITE = "cross-site"

#: Headers on every response.
_SECURITY_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
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
    """The body a failure nothing planned for is answered with, wherever it lands."""
    return {"error": f"Unexpected failure: {exc}"}


def _no_such_endpoint(path: str) -> dict[str, Any]:
    """The body a path under ``/api`` that routes nowhere is answered with."""
    return {"error": f"No such endpoint: {path}"}


class _Stopped(Exception):
    """Raised inside a run whose reader has gone, to end it at a step boundary (ADR-0009,
    ADR-0034)."""


def _stop_when(stopped: threading.Event) -> Checkpoint:
    """A checkpoint that ends a run at the first step boundary after ``stopped``."""

    def checkpoint(step: str) -> None:
        if stopped.is_set():
            raise _Stopped(step)

    return checkpoint


#: Where the lines of the run being watched go, or ``None`` for a run nobody is streaming.
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
                    # The CLI's own clock and format: without it a four-minute
                    # extraction reads exactly like a four-second one.
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
    # Progress is logged at INFO, which a logger left at its default drops before any
    # handler sees it.
    if package_logger.getEffectiveLevel() > logging.INFO:
        package_logger.setLevel(logging.INFO)


class BuyAgentHandler(BaseHTTPRequestHandler):
    """Routes ``/api`` to the agent and everything else to the built UI."""

    # ``close_connection`` is ``BaseHTTPRequestHandler``'s rather than this project's:
    # the base class sets it while handling a request and not in its ``__init__``, so
    # the eight places below that set it are answering the base class rather than
    # defining state of their own.
    # pylint: disable=attribute-defined-outside-init

    server_version = "buy_agent"
    protocol_version = "HTTP/1.1"
    #: Read by ``socketserver`` when the connection is set up -- see
    #: :data:`_REQUEST_TIMEOUT`, which is why there is one at all.
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
        #: None accepts every ``Host`` -- what an operator binding a public interface
        #: has already chosen.
        self.allowed_hosts = allowed_hosts
        #: None takes no pictures, which is every server but one bound to this machine
        #: with Playwright installed (ADR-0065).
        self.camera = camera
        super().__init__(*args, **kwargs)

    # -- who is allowed to ask --------------------------------------------------

    def _admits(self) -> bool:
        """Whether this request came from the page this server serves (ADR-0018)."""
        return self._origin_admits() and self._host_admits()

    def _origin_admits(self) -> bool:
        """Reject a request a page on another site made."""
        if self.headers.get("Sec-Fetch-Site", "").strip().lower() == _CROSS_SITE:
            return False
        origin = self.headers.get("Origin")
        # "null" is an opaque origin -- a sandboxed iframe, a data: document -- and is
        # never this app, which is served from a real one.
        if origin is None or origin == "null":
            return origin is None

        netloc = urlparse(origin).netloc.strip().lower()
        # An origin equal to the authority the request was addressed to is this server's
        # own page: the browser writes both headers and a page elsewhere cannot make
        # them agree.
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
        # Deliberately terse and not CORS-negotiable: there is nothing here another site
        # is meant to ask for.
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

    # The verb as it arrives on the wire, which is what the base class dispatches on.
    # pylint: disable-next=invalid-name
    def do_GET(self) -> None:
        if not self._admits():
            self._refuse()
            return
        url = urlparse(self.path)
        params = {key: values[-1] for key, values in parse_qs(url.query).items()}
        # Outside the guard below, and first: it writes its own response as it goes, so
        # there is no status left for a late failure -- and it has a ``failure`` event
        # for the ones it can still report.
        if url.path == "/api/search/stream":
            self._stream_search(params)
            return
        try:
            if url.path == "/api/config":
                self._send_json(200, defaults_payload(screenshots=self.camera is not None))
            elif url.path == "/api/models":
                self._send_json(200, self._models(params))
            elif url.path == "/api/sources":
                # 200 whatever it holds: what was asked is whether this parses, and that
                # question was answered (ADR-0033).
                self._send_json(200, sources_payload(params.get("sources", "")))
            elif url.path == "/api/bounds":
                # The second endpoint that runs nothing, and the only one that answers
                # with a value rather than a verdict: what the request itself asks for,
                # offered for the form to fill in and never applied (ADR-0059).
                self._send_json(200, bounds_payload(params.get("request", "")))
            elif url.path == "/api/screenshot":
                # The one endpoint that answers with something other than JSON, because
                # an ``<img>`` is what asks for it (ADR-0065).
                self._send_screenshot(params.get("url", ""))
            elif url.path.startswith("/api/"):
                self._send_json(404, _no_such_endpoint(url.path))
            else:
                self._serve_static(url.path)
        # A 500 beats a dropped connection: an exception out of a handler escapes to
        # socketserver, which closes the socket unanswered, and the page reads that as
        # the agent server being down.
        # pylint: disable-next=broad-exception-caught
        except Exception as exc:
            logger.exception("Unexpected failure answering %s", url.path)
            self._send_json(500, _unexpected(exc))

    # The verb as it arrives on the wire, which is what the base class dispatches on.
    # pylint: disable-next=invalid-name
    def do_POST(self) -> None:
        if not self._admits():
            self._refuse()
            return
        url = urlparse(self.path)
        # Both answer the same shape, and only one runs anything (ADR-0035).
        endpoints: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "/api/search": self._search,
            "/api/rank": rank_again,
            # Runs no pipeline either, and a POST for the reason a re-sort is: a query
            # string carries neither a run's products nor the approval.
            "/api/pay": pay_now,
        }
        run = endpoints.get(url.path)
        if run is None:
            self._send_json(404, _no_such_endpoint(url.path))
            return
        try:
            payload = self._read_json()
            self._send_json(200, run(payload))
        except ApiError as exc:
            self._send_json(exc.status, exc.payload())
        # A 500 beats a dropped connection, for the reason ``do_GET`` gives.
        # pylint: disable-next=broad-exception-caught
        except Exception as exc:
            logger.exception("Unexpected failure during a search")
            self._send_json(500, _unexpected(exc))

    # The verb as it arrives on the wire, which is what the base class dispatches on.
    # pylint: disable-next=invalid-name
    def do_HEAD(self) -> None:
        """Answer HEAD like GET, minus the body -- but never by running a search."""
        if not self._admits():
            self._refuse()
            return
        if urlparse(self.path).path == "/api/search/stream":
            self._send_json(405, {"error": "A search stream has to be asked for with GET."})
            return
        self.do_GET()

    # -- the agent -------------------------------------------------------------

    def _models(self, params: dict[str, str]) -> dict[str, Any]:
        """What the named server is serving, for the form's model picker."""
        provider = params.get("provider") or DEFAULT_PROVIDER
        base_url = params.get("base_url") or _default_base_url(provider)
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
                # Coarse and not instant: a model call in flight finishes first, nothing
                # here being able to cancel one.
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
            # The stream reports its failures and never crashes on one: the status line
            # is spent, so a ``failure`` event is all that is left to send.
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
        """A JPEG of the page a card links to, or the JSON saying why there is none."""
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
        self._send_bytes(200, body, content_type)

    def _send_unbuilt(self) -> None:
        """Say the app has not been built, in whatever the asker can read."""
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
        # A candidate outside the UI directory is someone walking out of it with '..';
        # fall through to the app rather than reading the filesystem.
        if relative and candidate.is_relative_to(root) and candidate.is_file():
            return candidate
        return index

    # -- plumbing --------------------------------------------------------------

    def _read_json(self) -> dict[str, Any]:
        # A body left unread stays in the socket, where the next request on a kept-alive
        # connection would be parsed out of the leftover bytes -- so every such path
        # ends the connection.
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
                # Say so, rather than letting the client discover it when its next
                # request on this connection is answered with a reset.
                self.send_header("Connection", "close")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        except OSError:
            # And nothing more goes over it: after a write that timed out the socket
            # refuses reads too, so leaving it open sends that failure to
            # ``socketserver`` -- the silent close every catch-all here avoids.
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

    # ``format`` shadows the builtin and is the base class's own parameter name.
    # pylint: disable-next=redefined-builtin
    def log_message(self, format: str, *args: Any) -> None:
        """Send request logging through logging, not straight to stderr."""
        logger.debug("%s - %s", self.address_string(), format % args)


def _workspace_for(ui_dir: Path) -> Path | None:
    """The Angular workspace whose build lands in ``ui_dir`` -- where npm is run."""
    workspace = ui_dir.parent.parent.parent
    return workspace if (workspace / "package.json").is_file() else None


def _unbuilt_remedy(ui_dir: Path) -> str:
    """What to do about a missing build, for whoever is looking at this ``--ui-dir``."""
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
    """The same two remedies for a browser, which can show the command as a block."""
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
    """The sentence naming the model server whose own default address is ``port``.

    Only for the failure it is a remedy for. "Serve the UI somewhere else" answers a
    port already taken and nothing else: a host that does not resolve fails the same
    bind with the same port in the message, and was answered with a new port that
    would not have helped -- the address being what is wrong, which is the reading
    ``providers._answered_by`` already makes about a hint naming a model.
    """
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


def _default_base_url(provider: str) -> str:
    """Where that provider listens when the request named no address."""
    server = PROVIDERS.get(provider)
    return server.base_url if server else ""


def _hostname(netloc: str) -> str:
    """The host out of a ``Host`` header or an origin's netloc, lowercased."""
    host = netloc.strip().lower()
    if host.startswith("["):
        return host[1:].partition("]")[0]
    return host.partition(":")[0]


def _bound_host(address: str) -> str:
    """The host out of an address somebody typed at the command line, lowercased.

    Not the same reading as :func:`_hostname`, which is handed what a browser wrote: an
    address bar brackets an IPv6 literal and ``--host`` does not, so the colons in a bare
    ``::1`` are the address rather than a port separator. Split at the first of them it
    named nothing at all -- which is what ``_LOOPBACK_HOSTS`` failed to match, so a bind
    to IPv6 loopback read as a public interface and turned the ``Host`` check off
    (ADR-0018), and an ``--allowed-host`` naming one allowed ``""``: the very thing a
    request with no ``Host`` header at all comes to.
    """
    host = address.strip().lower()
    if host.startswith("["):
        return _hostname(host)
    # One colon is ``host:port``; more than one is an IPv6 literal written bare, which
    # carries no port for the same reason it needs the brackets when it does.
    return host if host.count(":") > 1 else host.partition(":")[0]


def allowed_hosts_for(host: str, extra: Sequence[str] = ()) -> frozenset[str] | None:
    """Which ``Host`` headers a server bound to ``host`` should answer."""
    # Whatever names nothing is dropped rather than admitted: a blank is what a request
    # sending no ``Host`` arrives as, and an entry that cannot be read is not the host
    # somebody meant to name.
    named = frozenset(filter(None, map(_bound_host, extra)))
    if _bound_host(host) not in _LOOPBACK_HOSTS:
        return named or None
    return _LOOPBACK_HOSTS | named


def _family_for(host: str) -> int:
    """Which socket family an address has to be bound on.

    A colon in a bind address is an IPv6 literal -- ``--host`` carries no port, the port
    being ``--port``. Told apart here rather than left to the base class, which is
    ``AF_INET`` and nothing else: every IPv6 bind failed outright, ``::1`` included,
    which is the address :data:`_LOOPBACK_HOSTS` names and :func:`_browsable_url` is
    written to print.
    """
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
    """The camera a server bound to ``host`` takes its screenshots with, if it may take
    any (ADR-0065).

    Only on this machine's own addresses. The admission checks keep a page on another
    site out, and nothing keeps out a program on the network asking for itself: bound
    anywhere else, this would photograph any address -- the router's page, a service
    behind the firewall -- for whoever can reach the port, and hand the picture back.
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
    """``--port`` as argparse takes it: a number a socket could be bound to.

    The rule ``__main__._bounded`` holds for every number the agent takes -- an
    out-of-range one is a usage error rather than a failure further in. Here "further
    in" is ``socket.bind``, whose ``OverflowError`` goes straight past the ``OSError``
    ``main`` reports a refused bind with.
    """
    minimum, maximum = _PORTS
    try:
        port = int(text)
    # Read here rather than left to argparse, which names the *converter* in its own
    # message -- "invalid _port value", where a reader wants "a whole number".
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
    # Both name their default, the way every other flag in this project does: the port
    # is the address somebody is about to type, and the host is the difference between a
    # server only this machine can reach and one the network can -- which also turns the
    # ``Host`` check off (ADR-0018).
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
        # Every page load resolves all three names, the form's defaults being an
        # ``AgentConfig``, so a misspelt ``$BUY_AGENT_PROVIDER``, ``$BUY_AGENT_RAIL``
        # or ``$BUY_AGENT_BACKEND`` is said here rather than as a 500 per page.
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
        # The same remedy the 503 quotes: said at startup to the shell still on screen,
        # and again to whoever loads the page.
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
