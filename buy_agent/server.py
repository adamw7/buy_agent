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

Both are guarded by :meth:`BuyAgentHandler._admits`, because a server on
loopback is reachable from every page the same browser has open (ADR-0018).
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
from buy_agent.config import DEFAULT_PROVIDER, PROVIDER_DEFAULTS
from buy_agent.logging_setup import configure_logging

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from buy_agent.api import AgentFactory

logger = logging.getLogger(__name__)

#: Where ``ng build`` leaves the app, relative to the repository root.
DEFAULT_UI_DIR = Path(__file__).resolve().parent.parent / "ui" / "dist" / "ui" / "browser"

#: How long the SSE loop waits for a log line before sending a ``ping`` event to
#: keep browsers and proxies from timing out a quiet stream. Extraction is the
#: slow step and logs nothing while it runs, so quiet stretches are normal.
_KEEPALIVE_SECONDS = 15.0

_MAX_BODY_BYTES = 64 * 1024

#: Host names that mean "this machine". A ``Host`` header outside the allowed set
#: is a name that resolved here without being one of ours -- the shape of a DNS
#: rebinding attack, where a page on ``evil.example`` re-resolves to 127.0.0.1 and
#: is then same-origin with this server for as long as the browser believes it.
#: 0.0.0.0 is deliberately absent: it is an address to *bind*, meaning every
#: interface, and never a name a browser addresses a request to. Counting it here
#: would read ``--host 0.0.0.0`` -- the container's bind (ADR-0015) -- as a
#: loopback one and quietly refuse every name that actually reaches it.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

#: The ``Sec-Fetch-Site`` value meaning a page on another site made the request.
#: Browsers send this header on every request, including the ones that carry no
#: ``Origin`` at all -- an ``<img>``, an ``<iframe>``, a cross-site form post --
#: which is the gap it is here to close. Anything that is not a browser sends
#: nothing and is judged on ``Origin`` and ``Host`` alone.
#:
#: ``same-site`` is deliberately not here. A site is a registrable domain and not
#: a port, so a page on ``localhost:4200`` -- the Angular dev server, proxying
#: here -- calls this ``same-site``, and refusing that would refuse ``npm start``
#: to close a hole nobody on the internet can reach through.
_CROSS_SITE = "cross-site"

#: Headers on every response. The app loads its own scripts, styles and fonts and
#: talks to its own origin, so the policy can be as tight as ``'self'`` -- with
#: ``'unsafe-inline'`` for styles only, which is what Angular's per-component
#: ``<style>`` blocks need.
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

    Each streamed search runs in its own thread and logs from it, so the thread
    a record arrived on says whose it is -- which is what ``threading.local``
    keeps, and why nothing here locks: a thread touches only its own slot. Two
    searches at once do not see each other's progress.
    """

    def __init__(self) -> None:
        super().__init__()
        self._local = threading.local()

    def emit(self, record: logging.LogRecord) -> None:
        sink: queue.Queue[Any] | None = getattr(self._local, "sink", None)
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
        self._local.sink = sink

    def detach(self) -> None:
        self._local.sink = None


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
        self,
        *args: Any,
        ui_dir: Path,
        agent_factory: AgentFactory,
        allowed_hosts: frozenset[str] | None = _LOOPBACK_HOSTS,
        **kwargs: Any,
    ) -> None:
        self.ui_dir = ui_dir
        self.agent_factory = agent_factory
        #: None means every ``Host`` is accepted -- what an operator who bound a
        #: public interface has already chosen (see ``main``). It has to be asked
        #: for: the default here is the guarded one, so a handler built without
        #: the argument is not the unprotected one by accident.
        self.allowed_hosts = allowed_hosts
        super().__init__(*args, **kwargs)

    # -- who is allowed to ask --------------------------------------------------

    def _admits(self) -> bool:
        """Whether this request came from the page this server serves.

        There is no authentication here and there should not be: it serves one
        person on their own machine. But loopback is not a boundary a browser
        respects -- any page the user has open can reach 127.0.0.1 -- and both of
        the things that follow from that have to be shut off explicitly.

        A cross-site *write* needs no reply to be worth making: a page can post a
        form or open an ``EventSource`` at this server, and although the browser
        refuses to show it the answer, the run happens anyway -- ten pages
        fetched, a model driven, on someone else's say-so. A cross-site *read*
        needs the origin to match, which DNS rebinding manufactures: the attacker
        re-points their own name at 127.0.0.1, at which point the browser
        believes their page and this server are the same origin and hands over
        every answer.

        So: the fetch metadata and the ``Origin`` say who asked, and the ``Host``
        says which name they used to get here. Both have to be ours.
        """
        return self._origin_admits() and self._host_admits()

    def _origin_admits(self) -> bool:
        """Reject a request a page on another site made."""
        if self.headers.get("Sec-Fetch-Site", "").strip().lower() == _CROSS_SITE:
            return False
        origin = self.headers.get("Origin")
        # "null" is an opaque origin -- a sandboxed iframe, a data: document --
        # and is never this app, which is served from a real one.
        if origin is None or origin == "null":
            return origin is None

        netloc = urlparse(origin).netloc.strip().lower()
        # An origin equal to the authority the request was addressed to is this
        # server's own page, whatever that authority is: the browser writes both
        # headers, and a page elsewhere cannot make them agree. That is what keeps
        # a deliberately public bind -- the container reached at buy.lan:8000 --
        # usable without loosening anything for a page on the internet.
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
        # Deliberately terse and deliberately not CORS-negotiable: there is
        # nothing here another site is meant to be able to ask for.
        self.close_connection = True
        logger.warning(
            "Refused a %s %s from origin %r with host %r",
            self.command,
            self.path,
            self.headers.get("Origin", ""),
            self.headers.get("Host", ""),
        )
        self._send_json(403, {"error": "This API only answers its own page."})

    # -- routing ---------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 -- BaseHTTPRequestHandler's spelling
        if not self._admits():
            self._refuse()
            return
        url = urlparse(self.path)
        params = {key: values[-1] for key, values in parse_qs(url.query).items()}
        if url.path == "/api/config":
            self._send_json(200, defaults_payload())
        elif url.path == "/api/models":
            self._send_json(200, self._models(params))
        elif url.path == "/api/search/stream":
            self._stream_search(params)
        elif url.path.startswith("/api/"):
            self._send_json(404, {"error": f"No such endpoint: {url.path}"})
        else:
            self._serve_static(url.path)

    def do_POST(self) -> None:  # noqa: N802 -- BaseHTTPRequestHandler's spelling
        if not self._admits():
            self._refuse()
            return
        url = urlparse(self.path)
        if url.path != "/api/search":
            self._send_json(404, {"error": f"No such endpoint: {url.path}"})
            return
        try:
            payload = self._read_json()
            self._send_json(200, self._search(payload))
        except ApiError as exc:
            self._send_json(exc.status, exc.payload())
        except Exception as exc:  # noqa: BLE001 -- a 500 beats a dropped connection
            # Without this the exception escapes to socketserver, which closes the
            # socket without writing anything: the browser sees a network error and
            # cannot tell a failed run from a server that went away. The stream
            # already answers unexpected failures with a ``failure`` event, and the
            # one-shot endpoint has to match it.
            logger.exception("Unexpected failure during a search")
            self._send_json(500, {"error": f"Unexpected failure: {exc}"})

    def do_HEAD(self) -> None:  # noqa: N802 -- BaseHTTPRequestHandler's spelling
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
        """What the named server is serving, for the form's model picker.

        The provider comes with the address because the two belong together: the
        same URL is asked in two different ways, and a vLLM asked Ollama's
        question answers 404. An unknown name is not refused here --
        ``installed_models`` reports it as an unreachable server with the reason,
        which is what the pill above the form is already built to show.
        """
        provider = params.get("provider") or DEFAULT_PROVIDER
        base_url = params.get("base_url") or _default_base_url(provider)
        return installed_models(provider, base_url)

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
            self._send_security_headers()
            self.end_headers()
        except OSError:
            return

        # No keep-alive on a stream whose length nobody knows in advance.
        self.close_connection = True

        for event, data in self._search_events(params):
            if not self._send_event(event, data):
                # The run itself carries on to the end: it is blocked inside an
                # HTTP call to the model server, and there is nothing here to
                # cancel it with.
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
        content_type = (
            _CONTENT_TYPES.get(target.suffix.lower())
            or mimetypes.guess_type(target.name)[0]
            or "application/octet-stream"
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

        root = self.ui_dir.resolve()
        relative = unquote(urlparse(path).path).lstrip("/")
        try:
            candidate = (self.ui_dir / relative).resolve()
        except (OSError, ValueError):
            # A percent-encoded NUL makes resolve() raise rather than answer, and
            # an exception here escapes to socketserver, which drops the socket
            # without a reply -- indistinguishable, from the browser, from the
            # server having died. It is a path that cannot name a file: say so
            # the way every other unservable path is answered.
            return index
        # A candidate outside the UI directory is someone walking out of it with
        # '..'; fall through to the app rather than reading the filesystem.
        if relative and candidate.is_relative_to(root) and candidate.is_file():
            return candidate
        return index

    # -- plumbing --------------------------------------------------------------

    def _read_json(self) -> dict[str, Any]:
        # Rejecting a body without reading it leaves it in the socket, where the
        # next request on a kept-alive connection would be parsed out of the
        # leftover bytes -- so those two paths end the connection instead.
        # A body framed by Transfer-Encoding rather than by a length is the same
        # desync through a different header: this handler does not decode chunks,
        # so the body would be left in the socket for the next request on this
        # connection to be parsed out of. Nothing here speaks chunked, and 411 is
        # what says so.
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            raise ApiError("Send a body with a Content-Length; chunked is not read here.", 411)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length < 0:
                # A negative length declares a body that is never read, leaving
                # it in the socket to be parsed as the next request. That is the
                # same desync as an unparseable one, reached through a number
                # that is technically an integer, so it is answered the same way.
                raise ValueError(length)
        except ValueError as exc:
            self.close_connection = True
            raise ApiError("Content-Length is not a number.") from exc
        if length > _MAX_BODY_BYTES:
            self.close_connection = True
            raise ApiError("Request body is too large.", 413)
        if length == 0:
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

    def _send_security_headers(self) -> None:
        """Say what the page is allowed to do, on every response.

        Cheap here in a way it is not elsewhere: the app is served whole from this
        one origin and asks nothing of any other, so the policy that describes it
        is the tightest one there is.
        """
        for name, value in _SECURITY_HEADERS:
            self.send_header(name, value)

    def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self._send_security_headers()
            if self.close_connection:
                # Say so, rather than letting the client discover it by having its
                # next request on this connection answered with a reset.
                self.send_header("Connection", "close")
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


def _default_base_url(provider: str) -> str:
    """Where that provider listens when the request named no address.

    A provider nothing serves has no address either, and the empty string it gets
    is what ``installed_models`` turns into the unreachable status carrying the
    reason -- the same answer the browser gets for a server that is simply down.
    """
    _model, base_url = PROVIDER_DEFAULTS.get(provider, ("", ""))
    return base_url


def _hostname(netloc: str) -> str:
    """The host out of a ``Host`` header or an origin's netloc, lowercased.

    Written by hand because the value may be a bare authority rather than a URL:
    ``urlparse("localhost:8000").hostname`` is None, the port having been read as
    a scheme. Strips the brackets IPv6 literals are written in, so ``[::1]:8000``
    and ``::1`` are the one host they are.
    """
    host = netloc.strip().lower()
    if host.startswith("["):
        return host[1:].partition("]")[0]
    return host.partition(":")[0]


def allowed_hosts_for(host: str, extra: Sequence[str] = ()) -> frozenset[str] | None:
    """Which ``Host`` headers a server bound to ``host`` should answer.

    Loopback binds get the loopback names plus anything the operator named. A
    bind to a public interface gets None -- every host accepted -- because the
    name that reaches it is the operator's to know and not ours to guess; naming
    it with ``--allowed-host`` is what turns the check back on.
    """
    named = frozenset(_hostname(entry) for entry in extra if entry.strip())
    if _hostname(host) not in _LOOPBACK_HOSTS:
        return named or None
    return _LOOPBACK_HOSTS | named


def create_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    *,
    ui_dir: Path | None = None,
    agent_factory: AgentFactory = BuyAgent,
    allowed_hosts: frozenset[str] | None = _LOOPBACK_HOSTS,
) -> ThreadingHTTPServer:
    """Build the HTTP server without starting it.

    Args:
        host: Interface to bind. Loopback by default -- this serves an agent that
            runs prompts against a local model server, not something to expose.
        port: Port to bind, or 0 to let the OS choose (what the tests do).
        ui_dir: Directory holding the built Angular app.
        agent_factory: Builds the agent from a config; the tests' injection seam.
        allowed_hosts: ``Host`` headers to answer. None answers any, which is
            what a bind to a public interface gets -- see :func:`allowed_hosts_for`.
    """
    handler = partial(
        BuyAgentHandler,
        ui_dir=ui_dir or DEFAULT_UI_DIR,
        agent_factory=agent_factory,
        allowed_hosts=allowed_hosts,
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
    parser.add_argument(
        "--allowed-host",
        action="append",
        default=[],
        metavar="HOST",
        help=(
            "Extra Host header to answer, repeatable. The loopback names are "
            "always answered; anything else is refused, so that a name pointed "
            "at this machine cannot pass itself off as this server."
        ),
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(verbose=args.verbose)

    # The relay is what lets a stream show the run's progress; it is installed on
    # the package logger so every module's output reaches the browser.
    _install_relay()

    allowed = allowed_hosts_for(args.host, args.allowed_host)
    if allowed is None:
        logger.warning(
            "Bound to %s with no --allowed-host: this answers any Host header, so "
            "a name resolved here by someone else is answered too. Name the host "
            "you reach it by to close that.",
            args.host,
        )

    try:
        httpd = create_server(
            args.host, args.port, ui_dir=args.ui_dir, allowed_hosts=allowed
        )
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
