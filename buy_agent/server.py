"""A local HTTP server for the Angular UI in ``ui/``.

Stdlib only, on purpose: the dependency list is already the interesting part of
this project, and a run that takes a minute and serves one person needs no
framework under it.

Two ways to ask for the same thing. ``POST /api/search`` runs the pipeline and
answers in one JSON response -- the shape scripts want. ``GET
/api/search/stream`` is the same run as Server-Sent Events (``log`` lines, then
``result`` or ``failure``), which is what the UI uses. Closing that stream ends
the run: the first frame that cannot be written says the reader has gone, and the
pipeline stops at its next step boundary (ADR-0034).

Two endpoints run no pipeline at all. ``POST /api/rank`` puts a finished run's
products in another order (ADR-0035), and ``GET /api/sources`` reads a Trusted
sources field the way a run would, so the form can refuse ``Marques Brownlee``
before opening a stream (ADR-0033).

Everything outside ``/api`` is the built Angular app, unknown paths falling back
to ``index.html`` so it keeps its own routing. Both are guarded by
:meth:`BuyAgentHandler._admits`: a server on loopback is reachable from every
page the same browser has open (ADR-0018).
"""

from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import queue
import sys
import threading
import time
from functools import partial
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, unquote, urlparse

from buy_agent.agent import BuyAgent, Checkpoint, every_step_passes
from buy_agent.api import (
    ApiError,
    defaults_payload,
    installed_models,
    parse_options,
    rank_again,
    run_search,
    sources_payload,
)
from buy_agent.config import DEFAULT_PROVIDER
from buy_agent.logging_setup import configure_logging
from buy_agent.providers import PROVIDERS, provider_for

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from buy_agent.api import AgentFactory

logger = logging.getLogger(__name__)

#: Where ``ng build`` leaves the app, relative to the repository root.
DEFAULT_UI_DIR = Path(__file__).resolve().parent.parent / "ui" / "dist" / "ui" / "browser"

#: How long the SSE loop waits for a log line before sending a ``ping``, so a
#: quiet stream is not timed out by a browser or a proxy. Extraction is the slow
#: step and logs nothing while it runs, so quiet stretches are normal.
_KEEPALIVE_SECONDS = 15.0

_MAX_BODY_BYTES = 64 * 1024

#: Host names that mean "this machine". A ``Host`` outside the allowed set is a
#: name that resolved here without being one of ours -- DNS rebinding. 0.0.0.0 is
#: deliberately absent: an address to *bind*, never one a browser addresses, so
#: counting it would read ``--host 0.0.0.0`` -- the container's bind (ADR-0015) --
#: as loopback and refuse every name that actually reaches it.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

#: The ``Sec-Fetch-Site`` value meaning a page on another site made the request.
#: Browsers send it even where there is no ``Origin`` at all -- an ``<img>``, a
#: cross-site form post -- which is the gap it closes; a non-browser sends nothing
#: and is judged on ``Origin`` and ``Host``. ``same-site`` is deliberately not
#: here: a site is a domain and not a port, so the Angular dev server on
#: ``localhost:4200`` counts as one, and refusing it would break ``npm start`` to
#: close a hole nobody on the internet can reach through.
_CROSS_SITE = "cross-site"

#: Headers on every response. The app loads everything from its own origin, so
#: the policy is ``'self'`` throughout -- with ``'unsafe-inline'`` for styles
#: only, which Angular's per-component ``<style>`` blocks need.
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


#: What to run to get a page. One command, quoted by every answer that names it
#: -- the page, the JSON and the warning ``main`` logs at startup -- so the three
#: cannot come to say different things.
_UNBUILT_COMMAND = "npm install && npm run build"

#: The sentence a client that did not ask for HTML gets.
_UNBUILT = (
    f"The UI is not built. Run '{_UNBUILT_COMMAND}' in {{workspace}}, or point "
    f"--ui-dir at a build elsewhere."
)

#: The same answer for a browser. No script and no other origin, so it is served
#: under the same CSP as the app; the inline ``style`` is what ``style-src``
#: already allows for Angular's own.
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
<p>The API is answering, but the page has not been built yet. Run this in
<code>{workspace}</code>:</p>
<pre>{command}</pre>
<p>Then reload. A build that lives somewhere else is named with
<code>--ui-dir</code>.</p>
</main>
</body>
</html>
"""


class _Stopped(Exception):
    """Raised inside a run whose reader has gone, to end it at a step boundary.

    Deliberately not one of the agent's three failure modes (ADR-0009): nothing
    failed, and nobody is left to answer with a status. Caught by the worker that
    raised it and going no further, which is why it lives here beside the stream
    that is the only thing able to want one (ADR-0034).
    """


def _stop_when(stopped: threading.Event) -> Checkpoint:
    """A checkpoint that ends a run at the first step boundary after ``stopped``.

    The exception carries the step that was about to start: "stopped before
    extract" and "stopped before rank" are the difference between saving a minute
    and saving nothing.
    """

    def checkpoint(step: str) -> None:
        if stopped.is_set():
            raise _Stopped(step)

    return checkpoint


class _LogRelay(logging.Handler):
    """Fans ``buy_agent`` log records out to the run that produced them.

    Each streamed search runs in its own thread, so the thread a record arrived on
    says whose it is -- kept in ``threading.local``, which is why nothing locks
    and why two searches do not see each other.
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
                    # The CLI's own clock and format: without it a run that spent
                    # four minutes in extraction looks exactly like one that spent
                    # four seconds, on screen and in a bug report's transcript.
                    "time": time.strftime("%H:%M:%S", time.localtime(record.created)),
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
    # The progress the browser waits for is logged at INFO, which a logger left at
    # its default drops before any handler sees it. ``configure_logging`` does this
    # too, but the server is importable without it.
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
        #: None accepts every ``Host`` -- what an operator binding a public
        #: interface has already chosen (see ``main``). It has to be asked for:
        #: the default is the guarded one, so no handler is unprotected by
        #: accident.
        self.allowed_hosts = allowed_hosts
        super().__init__(*args, **kwargs)

    # -- who is allowed to ask --------------------------------------------------

    def _admits(self) -> bool:
        """Whether this request came from the page this server serves (ADR-0018).

        There is no authentication and should not be -- it serves one person on
        their own machine -- but loopback is not a boundary a browser respects. A
        cross-site *write* needs no reply to be worth making: a page elsewhere can
        open an ``EventSource`` here and the run happens anyway. A cross-site
        *read* needs the origin to match, which DNS rebinding manufactures.

        So the fetch metadata and the ``Origin`` say who asked, the ``Host`` says
        which name they used, and both have to be ours.
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
        # headers and a page elsewhere cannot make them agree. That keeps a
        # deliberately public bind usable without loosening anything.
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
        # All three headers the checks read, logged as they arrived -- None where
        # one was not sent. Fetch metadata can refuse a request carrying no Origin
        # at all, so a line naming only the Origin sends the reader after the
        # wrong header.
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

    def do_GET(self) -> None:  # noqa: N802 -- BaseHTTPRequestHandler's spelling
        if not self._admits():
            self._refuse()
            return
        url = urlparse(self.path)
        params = {key: values[-1] for key, values in parse_qs(url.query).items()}
        # Outside the guard below, and first: it writes its own response as it
        # goes, so there is no status left to answer a late failure with -- and it
        # has a ``failure`` event for the ones it can still report.
        if url.path == "/api/search/stream":
            self._stream_search(params)
            return
        try:
            if url.path == "/api/config":
                self._send_json(200, defaults_payload())
            elif url.path == "/api/models":
                self._send_json(200, self._models(params))
            elif url.path == "/api/sources":
                # 200 whatever it holds: what was asked is whether this parses, and
                # that question was answered (ADR-0033).
                self._send_json(200, sources_payload(params.get("sources", "")))
            elif url.path.startswith("/api/"):
                self._send_json(404, {"error": f"No such endpoint: {url.path}"})
            else:
                self._serve_static(url.path)
        except Exception as exc:  # noqa: BLE001 -- a 500 beats a dropped connection
            # The reason ``do_POST`` has one of these: an exception here escapes to
            # socketserver, which closes the socket unanswered, and the page reads
            # that as the agent server being down -- "Could not reach the agent
            # server" for a server that is up and answered every other request.
            # ``$BUY_AGENT_PROVIDER=olama`` did exactly that to ``/api/config``,
            # where ``AgentConfig()`` raises and the sentence naming the servers
            # that do exist never reached anybody.
            logger.exception("Unexpected failure answering %s", url.path)
            self._send_json(500, {"error": f"Unexpected failure: {exc}"})

    def do_POST(self) -> None:  # noqa: N802 -- BaseHTTPRequestHandler's spelling
        if not self._admits():
            self._refuse()
            return
        url = urlparse(self.path)
        # Both answer the same shape, and only one runs anything: a re-sort is
        # the ranking a run ends with, asked for on its own (ADR-0035).
        endpoints: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "/api/search": self._search,
            "/api/rank": rank_again,
        }
        run = endpoints.get(url.path)
        if run is None:
            self._send_json(404, {"error": f"No such endpoint: {url.path}"})
            return
        try:
            payload = self._read_json()
            self._send_json(200, run(payload))
        except ApiError as exc:
            self._send_json(exc.status, exc.payload())
        except Exception as exc:  # noqa: BLE001 -- a 500 beats a dropped connection
            # Otherwise it escapes to socketserver, which closes the socket
            # unanswered: the browser sees a network error and cannot tell a failed
            # run from a server that went away. The stream answers such failures
            # with a ``failure`` event, and this endpoint has to match.
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

        The provider comes with the address, since a vLLM asked Ollama's question
        answers 404. An unknown name is not refused here: ``installed_models``
        reports it as an unreachable server with the reason, which is what the
        pill above the form shows.
        """
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
        """Run a search in a worker thread, relaying its log lines as they arrive.

        The work cannot happen on this thread: the response is written while the
        run is still going, which is the whole point of the stream.

        A reader who goes away takes the run with them (ADR-0034): the first frame
        that cannot be written -- a log line, or the keepalive ping at worst 15
        seconds later -- sets ``stopped``, and the run ends at its next step
        boundary rather than fetching ten more pages nobody will see.
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

        stopped = threading.Event()
        for event, data in self._search_events(params, stopped):
            if not self._send_event(event, data):
                # Coarse and not instant: a model call already in flight finishes
                # first, nothing here being able to cancel one. What it saves is
                # every step after that one.
                stopped.set()
                logger.info("Client disconnected; stopping the run at its next step")
                return

    def _search_events(
        self, params: dict[str, str], stopped: threading.Event
    ) -> Iterator[tuple[str, Any]]:
        """Yield ``log`` events for the run's progress, then ``result`` or ``failure``.

        Not ``error``: a browser's EventSource delivers its own transport errors
        under that name and then reconnects, which would silently restart the run.

        ``stopped`` is the caller's way of ending the run: set it and the worker
        raises :class:`_Stopped` out of the pipeline's next step boundary. Nothing
        is yielded for that -- whoever set it is the reader who has already gone.
        """
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
        """Say the app has not been built, in whatever the asker can read.

        This is the first thing anybody who ran ``python -m buy_agent.server``
        before ``npm run build`` sees, and they see it in a browser -- which
        renders ``application/json`` as its braces and quotes, so the one message
        standing between somebody and a working page arrived looking like a
        crash. A browser says so in ``Accept``, so it gets the sentence as a page
        and every other client gets the JSON it was already reading.

        The sentence itself is written once and shown both ways: what to run and
        where to run it is the same answer whoever asked.
        """
        workspace = _workspace_for(self.ui_dir)
        if "text/html" not in self.headers.get("Accept", ""):
            self._send_json(503, {"error": _UNBUILT.format(workspace=workspace)})
            return
        self._send_bytes(
            503,
            _UNBUILT_PAGE.format(
                command=escape(_UNBUILT_COMMAND),
                workspace=escape(str(workspace)),
            ).encode("utf-8"),
            "text/html; charset=utf-8",
        )

    def _resolve(self, path: str) -> Path | None:
        """Map a URL path to a file inside the UI directory, or to ``index.html``.

        None when there is nothing to serve, which nearly always means the app has
        not been built.
        """
        index = self.ui_dir / "index.html"
        if not index.is_file():
            return None

        root = self.ui_dir.resolve()
        relative = unquote(urlparse(path).path).lstrip("/")
        try:
            candidate = (self.ui_dir / relative).resolve()
        except (OSError, ValueError):
            # A percent-encoded NUL makes resolve() raise, and an exception here
            # escapes to socketserver, which drops the socket unanswered -- from
            # the browser, indistinguishable from the server having died.
            return index
        # A candidate outside the UI directory is someone walking out of it with
        # '..'; fall through to the app rather than reading the filesystem.
        if relative and candidate.is_relative_to(root) and candidate.is_file():
            return candidate
        return index

    # -- plumbing --------------------------------------------------------------

    def _read_json(self) -> dict[str, Any]:
        # Rejecting a body unread leaves it in the socket, where the next request
        # on a kept-alive connection would be parsed out of the leftover bytes --
        # so every such path ends the connection instead. Transfer-Encoding is the
        # same desync through another header: nothing here decodes chunks.
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

        Cheap here in a way it is not elsewhere: the app is served whole from one
        origin, so the policy describing it is the tightest there is.
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
                # Say so, rather than letting the client discover it when its next
                # request on this connection is answered with a reset.
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


def _workspace_for(ui_dir: Path) -> Path:
    """The Angular workspace whose build lands in ``ui_dir`` -- where npm is run.

    ``ng build`` writes ``<workspace>/dist/<project>/browser``, so the workspace is
    three levels up, and it is named only where it really is: a ``--ui-dir``
    pointing elsewhere has no knowable workspace above it, and inventing one sends
    the reader to "run npm install in ui/dist" -- a directory that exists only once
    the build has already succeeded.
    """
    workspace = ui_dir.parent.parent.parent
    return workspace if (workspace / "package.json").is_file() else ui_dir


def _browsable_url(host: str, port: int) -> str:
    """The address to type into a browser for a server bound to ``host``.

    A wildcard bind is an address to listen on rather than one to visit, so the
    URL names loopback instead; an IPv6 literal is bracketed, which is what an
    address bar needs and what ``http://::1:8000`` is missing.
    """
    shown = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(host, host)
    if ":" in shown:
        shown = f"[{shown}]"
    return f"http://{shown}:{port}"


def _clashing_provider(port: int) -> str:
    """The sentence naming the model server whose own default address is ``port``.

    vLLM's default is ``http://localhost:8000/v1`` and this server's default port
    is 8000, so the two collide on the machine most likely to run both -- and
    "Could not listen on 127.0.0.1:8000" says nothing about a model server.
    """
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
    """Where that provider listens when the request named no address.

    An unknown provider gets the empty string, which ``installed_models`` turns
    into the unreachable status carrying the reason -- the same answer a server
    that is simply down gets.
    """
    server = PROVIDERS.get(provider)
    return server.base_url if server else ""


def _hostname(netloc: str) -> str:
    """The host out of a ``Host`` header or an origin's netloc, lowercased.

    By hand because the value may be a bare authority rather than a URL:
    ``urlparse("localhost:8000").hostname`` is None, the port having been read as a
    scheme. Brackets come off, so ``[::1]:8000`` and ``::1`` are one host.
    """
    host = netloc.strip().lower()
    if host.startswith("["):
        return host[1:].partition("]")[0]
    return host.partition(":")[0]


def allowed_hosts_for(host: str, extra: Sequence[str] = ()) -> frozenset[str] | None:
    """Which ``Host`` headers a server bound to ``host`` should answer.

    Loopback binds get the loopback names plus anything the operator named. A
    public bind gets None -- every host accepted -- because the name reaching it
    is the operator's to know; ``--allowed-host`` turns the check back on.
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
        host: Interface to bind. Loopback by default -- this is not something to
            expose.
        port: Port to bind, or 0 to let the OS choose (what the tests do).
        ui_dir: Directory holding the built Angular app.
        agent_factory: Builds the agent from a config; the tests' injection seam.
        allowed_hosts: ``Host`` headers to answer. None answers any, which is what
            a public bind gets -- see :func:`allowed_hosts_for`.
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

    # Installed on the package logger so every module's output reaches a stream.
    _install_relay()

    try:
        provider_for(DEFAULT_PROVIDER)
    except ValueError as exc:
        # Every page load resolves this name -- the form's own defaults are an
        # ``AgentConfig`` -- so a misspelt ``$BUY_AGENT_PROVIDER`` is not worth a
        # server that starts and then answers 500 to everything it serves. Said
        # here, where the shell that set it is still on screen.
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

    try:
        httpd = create_server(
            args.host, args.port, ui_dir=args.ui_dir, allowed_hosts=allowed
        )
    except OSError as exc:
        logger.error(
            "Could not listen on %s:%s (%s).%s",
            args.host,
            args.port,
            exc,
            _clashing_provider(args.port),
        )
        return 1

    host, port = httpd.server_address[:2]
    logger.info("buy_agent UI on %s", _browsable_url(str(host), port))
    if not (args.ui_dir / "index.html").is_file():
        # The same command and the same workspace the 503 quotes: said at startup
        # to the shell that is still on screen, and again to whoever loads the
        # page -- one answer, not two that can come to differ.
        logger.warning(
            "No built UI at %s -- the API works, but the page will not. "
            "Build it with:  %s   (in %s)",
            args.ui_dir,
            _UNBUILT_COMMAND,
            _workspace_for(args.ui_dir),
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
