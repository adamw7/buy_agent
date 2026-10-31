"""A local HTTP server for the UI in ``ui/`` and its JSON API (ADR-0010, ADR-0011,
ADR-0018, ADR-0033, ADR-0034, ADR-0035, ADR-0065), deployable for more than one
machine (ADR-0092)."""

# The one stdlib server (ADR-0010) is one module: its routes, its stream, its static
# files and its command line. What a deployment adds beyond those is deployment.py's.
# pylint: disable=too-many-lines

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
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qs, unquote, urlparse

from buy_agent.agent import BuyAgent, Checkpoint, every_step_passes
from buy_agent.api import (
    ApiError,
    Sealer,
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
from buy_agent.config import (
    DEFAULT_BACKEND,
    DEFAULT_PROVIDER,
    DEFAULT_RAIL,
    ServerEnvironment,
    server_environment,
)
from buy_agent.deployment import (
    DEFAULT_MAX_RUNS,
    DRAIN_SECONDS,
    MAX_CONNECTIONS,
    MAX_RUNS,
    MIN_TOKEN,
    Policy,
    Terminated,
    Traffic,
    authorised,
    fixed_address,
    request_id,
    same_address,
    stopped_by_sigterm,
)
from buy_agent.logging_setup import configure_logging, wrap_help
from buy_agent.providers import PROVIDERS, provider_for
from buy_agent.rails import rail_for
from buy_agent.screenshots import INSTALL, Camera, available
from buy_agent.search import backend_for
from buy_agent.version import current

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from buy_agent.api import AgentFactory
    from buy_agent.config import AgentConfig

logger = logging.getLogger(__name__)
#: One line per request answered, when the server is asked for them (ADR-0092).
access_logger = logging.getLogger("buy_agent.access")

#: Where ``ng build`` leaves the app.
DEFAULT_UI_DIR = Path(__file__).resolve().parent.parent / "ui" / "dist" / "ui" / "browser"

#: Loopback, on the port the ``Dockerfile`` and ``scripts/start.ps1`` are held to.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000

#: Silence before an SSE ``ping``, so proxies keep a quiet stream open.
_KEEPALIVE_SECONDS = 15.0
#: An event stream's own headers; nginx holds a response back until it ends otherwise.
_STREAM_HEADERS = (
    ("Content-Type", "text/event-stream"),
    ("Cache-Control", "no-cache, no-transform"),
    ("X-Accel-Buffering", "no"),
)
_MAX_BODY_BYTES = 64 * 1024
_SCREENSHOT_CACHE = "private, max-age=3600"

#: A content-hashed build file (``main-AC2JNJ6W.js``) is cached for good; the rest,
#: ``index.html`` above all, is revalidated.
_HASHED_ASSET = re.compile(r"-[A-Z0-9]{8}\.[a-z0-9]+$")
_IMMUTABLE = "public, max-age=31536000, immutable"
_REVALIDATE = "no-cache"

#: How long one blocking read or write on a connection may take (ADR-0034).
_REQUEST_TIMEOUT = 30.0

#: Checked at the door: ``bind`` raises ``OverflowError``, which ``main`` would miss.
_PORTS = (0, 65535)

#: The paths a machine asks rather than a page: answered before any page's headers are
#: read, and the probes before any token is (ADR-0092).
_LIVENESS = "/healthz"
_READINESS = "/readyz"
_METRICS = "/metrics"
_PROBES = frozenset({_LIVENESS, _READINESS})
_METRICS_TYPE = "text/plain; version=0.0.4; charset=utf-8"
#: Seconds a refused run is told to wait.
_RETRY_AFTER = "30"
_CHALLENGE = 'Basic realm="buy_agent", charset="UTF-8"'
_LOG_FORMATS = ("text", "json")

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
#: A bind on every interface, which a loopback name reaches too.
_EVERYWHERE = frozenset({"0.0.0.0", "::", ""})
_SCHEME_PORTS = {"http": 80, "https": 443}
_CROSS_SITE = "cross-site"

_SECURITY_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
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

#: Spelt out: Windows' registry can serve ``.js`` as ``text/plain``.
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

_UNBUILT_COMMAND = "npm install && npm run build"
_UNBUILT = "The UI is not built. {remedy}"
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
p {{ color: #5c6470; }}
@media (prefers-color-scheme: dark) {{ p {{ color: #99a3b0; }} }}
</style>
</head>
<body>
<main>
<h1>buy_agent</h1>
<p>The API is answering, but the page has not been built yet.</p>
<p>{remedy}</p>
</main>
</body>
</html>
"""


def _unexpected(reference: str) -> dict[str, Any]:
    """What a client is told of a failure it did not cause: the log has the rest."""
    return {
        "error": f"Unexpected failure; the server's log has it under {reference}.",
        "reference": reference,
    }


def _no_such_endpoint(path: str) -> dict[str, Any]:
    return {"error": f"No such endpoint: {path}"}


class _Stopped(Exception):
    """Ends a run whose reader has gone, at a step boundary (ADR-0034)."""


def _stop_when(stopped: threading.Event) -> Checkpoint:
    def checkpoint(step: str) -> None:
        if stopped.is_set():
            raise _Stopped(step)

    return checkpoint


#: A laptop's policy, for a handler built without one: the benchmark's, which seals
#: nothing, and a test's.
_LAPTOP = Policy()


class _Busy(ApiError):
    """A run refused because the server has as many as it takes, or is stopping: a 503
    that says when to come back (ADR-0092)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, 503)


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
                    "time": time.strftime("%H:%M:%S", time.localtime(record.created)),
                    "level": record.levelname,
                    "logger": record.name,
                    "message": record.getMessage(),
                }
            )
        # A broken relay must not break the run it reports on.
        # pylint: disable-next=broad-exception-caught
        except Exception:
            self.handleError(record)

    def attach(self, sink: queue.Queue[Any]) -> None:
        _sink.set(sink)

    def detach(self) -> None:
        _sink.set(None)


_relay = _LogRelay()


def _install_relay() -> None:
    """Put the relay on the package logger (``addHandler`` dedupes)."""
    package_logger = logging.getLogger("buy_agent")
    package_logger.addHandler(_relay)
    # Progress is at INFO, which the default level drops.
    if package_logger.getEffectiveLevel() > logging.INFO:
        package_logger.setLevel(logging.INFO)


class BuyAgentHandler(BaseHTTPRequestHandler):
    """Routes ``/api`` to the agent and everything else to the built UI."""

    server_version = f"buy_agent/{current().label}"
    protocol_version = "HTTP/1.1"
    timeout = _REQUEST_TIMEOUT
    #: Set once a request line is read, so its answer can be counted and logged.
    request_id = ""

    def __init__(
        self,
        *args: Any,
        ui_dir: Path,
        agent_factory: AgentFactory,
        allowed_hosts: frozenset[str] | None = _LOOPBACK_HOSTS,
        camera: Camera | None = None,
        policy: Policy | None = None,
        **kwargs: Any,
    ) -> None:
        self.ui_dir = ui_dir
        self.agent_factory = agent_factory
        #: None accepts every ``Host``, as a public bind does.
        self.allowed_hosts = allowed_hosts
        #: None takes no pictures (ADR-0065).
        self.camera = camera
        self.policy = policy or _LAPTOP
        self._began: float | None = None
        self._status = 0
        super().__init__(*args, **kwargs)

    @property
    def traffic(self) -> Traffic:
        return cast("Server", self.server).traffic

    # -- who is allowed to ask (ADR-0018, ADR-0092) ------------------------------

    def _refused(self) -> bool:
        """Refuse a request not from this server's own page, or without the token the
        server asks for; say whether it was. The probes are a machine's to ask, and
        ``/metrics`` too, behind the token."""
        path = urlparse(self.path).path
        if path in _PROBES:
            return False
        if path != _METRICS and not (self._origin_admits() and self._host_admits()):
            self._refuse()
            return True
        if authorised(self.headers.get("Authorization", ""), self.policy.token):
            return False
        self._challenge()
        return True

    def _challenge(self) -> None:
        # Any body is unread, so the connection closes.
        self.close_connection = True
        logger.warning(
            "Refused a %s %s from %s without the access token",
            self.command, urlparse(self.path).path, self.address_string(),
        )
        self._send_json(
            401,
            {"error": "This server asks for its access token: send it as a bearer token, "
                      "or as the password when the browser asks."},
            headers=(("WWW-Authenticate", _CHALLENGE),),
        )

    def _origin_admits(self) -> bool:
        if self.headers.get("Sec-Fetch-Site", "").strip().lower() == _CROSS_SITE:
            return False
        origin = self.headers.get("Origin")
        # "null" (a sandboxed iframe, a data: document) is never this app.
        if origin is None or origin == "null":
            return origin is None
        try:
            netloc = urlparse(origin).netloc.strip().lower()
        except ValueError:
            # An unclosed IPv6 bracket: refused rather than dropped unanswered.
            return False
        # Origin equal to Host is our own page: another page cannot forge both.
        if netloc and netloc == self._host().strip().lower():
            return True
        return _hostname(netloc) in _LOOPBACK_HOSTS

    def _host_admits(self) -> bool:
        if self.allowed_hosts is None:
            return True
        return _hostname(self._host()) in self.allowed_hosts

    def _host(self) -> str:
        """The authority the browser addressed: the proxy's word for it behind one."""
        return self._forwarded("X-Forwarded-Host") or self.headers.get("Host", "")

    def _forwarded(self, name: str) -> str:
        """The first entry of a proxy's header, behind a proxy; blank anywhere else,
        where any client could have written it."""
        if not self.policy.behind_proxy:
            return ""
        return self.headers.get(name, "").partition(",")[0].strip()

    def address_string(self) -> str:
        return self._forwarded("X-Forwarded-For") or super().address_string()

    def _refuse(self) -> None:
        self.close_connection = True
        logger.warning(
            "Refused a %s %s from origin %r with host %r and fetch site %r",
            self.command,
            self.path,
            self.headers.get("Origin"),
            self.headers.get("Host"),
            self.headers.get("Sec-Fetch-Site"),
        )
        self._send_json(403, {"error": "This API only answers its own page."})

    # -- routing -------------------------------------------------------------------

    def handle_one_request(self) -> None:
        """One request, counted from its request line to its answer (ADR-0092)."""
        self._began = None
        self._status = 0
        try:
            super().handle_one_request()
        finally:
            if self._began is not None:
                self._answered()

    def _answered(self) -> None:
        self.traffic.end(self._status)
        if not self.policy.access_log:
            return
        path = urlparse(self.path).path
        took = round((time.monotonic() - (self._began or 0.0)) * 1000)
        access_logger.info(
            '%s "%s %s" %s %dms %s',
            self.address_string(), self.command, path, self._status or "-", took,
            self.request_id,
            extra={
                "request_id": self.request_id, "client": self.address_string(),
                "method": self.command, "path": path, "status": self._status,
                "duration_ms": took,
            },
        )

    def send_response(self, code: int, message: str | None = None) -> None:
        self._status = code
        super().send_response(code, message)

    def parse_request(self) -> bool:
        """Refuse, for every verb, a target that will not parse (``GET http://[x/``),
        which would otherwise raise before any catch-all and close unanswered."""
        if not super().parse_request():
            return False
        self.request_id = request_id(self.headers.get("X-Request-Id", ""))
        self._began = time.monotonic()
        self.traffic.begin()
        if self.traffic.draining:
            # Stopping: this answer is the connection's last.
            self.close_connection = True
        try:
            urlparse(self.path)
        except ValueError:
            # Any body is unread, so the connection closes.
            self.close_connection = True
            self._send_json(400, {"error": f"Not a path this server can route: {self.path!r}"})
            return False
        return True

    #: Where a search is streamed (ADR-0011); None for a handler with nothing to stream.
    stream_path: str | None = "/api/search/stream"

    def get_routes(self) -> dict[str, Callable[[dict[str, str]], dict[str, Any] | bytes]]:
        """Each ``GET`` endpoint: JSON, or a screenshot's JPEG (ADR-0065)."""
        return {
            "/api/config": lambda _: defaults_payload(screenshots=self.camera is not None),
            "/api/models": self._models,
            # 200 either way: the answer is the verdict (ADR-0033).
            "/api/sources": lambda params: sources_payload(params.get("sources", "")),
            # For the form to fill in and show (ADR-0094).
            "/api/bounds": lambda params: bounds_payload(params.get("request", "")),
            "/api/screenshot": lambda params: screenshot(params.get("url", ""), self.camera),
        }

    def post_routes(self) -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
        """Each ``POST`` endpoint; only a search runs anything (ADR-0035), and a re-sort
        or a payment takes only the products this server sealed (ADR-0092)."""
        return {
            "/api/search": self._search,
            "/api/rank": partial(rank_again, sealer=self.policy.sealer),
            "/api/pay": self._pay,
        }

    # The base class dispatches on the verb's name.
    # pylint: disable-next=invalid-name
    def do_GET(self) -> None:
        if self._refused():
            return
        url = urlparse(self.path)
        params = {key: values[-1] for key, values in parse_qs(url.query).items()}
        # Outside the guard: the stream spends its status line early.
        if url.path == self.stream_path:
            self._stream_search(params)
            return
        try:
            if self._answered_machine(url.path):
                return
            route = self.get_routes().get(url.path)
            if route is not None:
                self._send_answer(route(params))
            elif url.path.startswith("/api/"):
                self._send_json(404, _no_such_endpoint(url.path))
            else:
                self._serve_static(url.path)
        # Anything at all: see ``_send_failure``.
        # pylint: disable-next=broad-exception-caught
        except Exception as exc:
            self._send_failure(url.path, exc)

    # The base class dispatches on the verb's name.
    # pylint: disable-next=invalid-name
    def do_POST(self) -> None:
        if self._refused():
            return
        url = urlparse(self.path)
        run = self.post_routes().get(url.path)
        if run is None:
            # The body is unread, so the connection must close.
            self.close_connection = True
            self._send_json(404, _no_such_endpoint(url.path))
            return
        try:
            payload = self._read_json()
            self._send_json(200, run(payload))
        # Anything at all: see ``_send_failure``.
        # pylint: disable-next=broad-exception-caught
        except Exception as exc:
            self._send_failure(url.path, exc)

    def _send_failure(self, path: str, exc: Exception) -> None:
        """An ``ApiError`` with its status, anything else a 500 that names where in the
        log it is and nothing else of the server's: never a socket closed unanswered,
        which reads as a server that is down."""
        if isinstance(exc, ApiError):
            retry = (("Retry-After", _RETRY_AFTER),) if isinstance(exc, _Busy) else ()
            self._send_json(exc.status, exc.payload(), headers=retry)
            return
        logger.exception("Unexpected failure answering %s (%s)", path, self.request_id)
        self._send_json(500, _unexpected(self.request_id))

    # -- what a machine asks (ADR-0092) --------------------------------------------

    def _answered_machine(self, path: str) -> bool:
        """Answer a probe or ``/metrics``; say whether ``path`` was one."""
        if path == _LIVENESS:
            self._send_json(200, {"status": "ok", "version": current().label})
        elif path == _READINESS:
            self._send_json(*self._readiness())
        elif path == _METRICS:
            self._send_bytes(200, self.traffic.metrics().encode("utf-8"), _METRICS_TYPE)
        else:
            return False
        return True

    def _readiness(self) -> tuple[int, dict[str, Any]]:
        """Ready while not stopping and while the model server it was started with
        answers: a run started now would get as far as asking it."""
        if self.traffic.draining:
            return 503, {"ready": False, "reason": "This server is shutting down."}
        listed = installed_models(DEFAULT_PROVIDER, "")
        if not listed["reachable"]:
            return 503, {"ready": False, "reason": f"{listed['label']} is not answering."}
        return 200, {"ready": True}

    # The base class dispatches on the verb's name.
    # pylint: disable-next=invalid-name
    def do_HEAD(self) -> None:
        """Like GET minus the body, but never by running a search."""
        if self._refused():
            return
        if urlparse(self.path).path == self.stream_path:
            self._send_json(405, {"error": "A search stream has to be asked for with GET."})
            return
        self.do_GET()

    # -- the agent -----------------------------------------------------------------

    def _models(self, params: dict[str, str]) -> dict[str, Any]:
        provider = params.get("provider") or DEFAULT_PROVIDER
        server = PROVIDERS.get(provider)
        own = server.base_url if server else ""
        base_url = params.get("base_url") or own
        unaskable: Callable[[str], str] | None = None
        if self._answered_here(base_url):
            # Never asked: this page would answer, as a model server that is not running.
            unaskable = partial(_own_address, address=base_url)
        elif server and self.policy.fixed_addresses and not same_address(base_url, own):
            unaskable = partial(fixed_address, address=own)
        return installed_models(provider, base_url, unaskable=unaskable)

    def _answered_here(self, address: str) -> bool:
        """Whether ``address`` is this server's own (vLLM's and TensorRT-LLM's 8000 is)."""
        host, port = cast("tuple[Any, ...]", self.server.server_address)[:2]
        return _reaches(address, str(host), int(port))

    def refuse_own_address(self, label: str, address: str) -> None:
        """Refuse a model server at this page's own address, on its box (ADR-0033)."""
        if self._answered_here(address):
            raise ApiError(_own_address(label, address), field="base_url")

    def refuse_foreign_address(self, config: AgentConfig) -> None:
        """Refuse, where other machines reach this server, to ask a model server it was
        not started with: it would reach whatever address anybody typed (ADR-0092)."""
        own = config.model_server.base_url
        if self.policy.fixed_addresses and not same_address(config.base_url, own):
            raise ApiError(
                fixed_address(config.model_server.label, own), field="base_url"
            )

    def _search(
        self, data: dict[str, Any], *, checkpoint: Checkpoint = every_step_passes
    ) -> dict[str, Any]:
        config, sort_by = parse_options(data)
        self.refuse_own_address(config.model_server.label, config.base_url)
        self.refuse_foreign_address(config)
        request = str(data.get("request") or "")
        refusal = self.traffic.admit_run()
        if refusal:
            raise _Busy(refusal)
        began, outcome = time.monotonic(), "failed"
        try:
            answer = run_search(
                request, config, sort_by=sort_by, agent_factory=self.agent_factory,
                checkpoint=checkpoint, sealer=self.policy.sealer,
            )
            outcome = "finished"
            return answer
        except _Stopped:
            outcome = "stopped"
            raise
        finally:
            self.traffic.end_run(outcome, time.monotonic() - began)

    def _pay(self, data: dict[str, Any]) -> dict[str, Any]:
        """Pay as :func:`pay_now` does; where other machines reach this server, only
        through the merchant it was started with (ADR-0092)."""
        if self.policy.fixed_addresses:
            config, _sort_by = parse_options({**data, "pay": True})
            own = config.rail_used.endpoint
            if not same_address(config.merchant_url, own):
                raise ApiError(fixed_address("merchant", own), field="merchant_url")
        return pay_now(data, sealer=self.policy.sealer)

    def _stream_search(self, params: dict[str, str]) -> None:
        """Run a search on a worker thread, relaying its log lines (ADR-0034)."""
        self.close_connection = True
        try:
            self._send_head(200, _STREAM_HEADERS)
        except OSError:
            return
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
        """``log`` events for the run's progress, then ``result`` or ``failure``."""
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
            # The status line is spent, so this is a ``failure`` event.
            # pylint: disable-next=broad-exception-caught
            except Exception:
                logger.exception(
                    "Unexpected failure during a streamed search (%s)", self.request_id
                )
                outcome["error"] = (500, _unexpected(self.request_id))
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

    def _send_answer(self, answer: dict[str, Any] | bytes) -> None:
        if isinstance(answer, bytes):
            cache = (("Cache-Control", _SCREENSHOT_CACHE),)
            self._send_bytes(200, answer, "image/jpeg", headers=cache)
        else:
            self._send_json(200, answer)

    # -- static files --------------------------------------------------------------

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
            _UNBUILT_PAGE.format(remedy=escape(_unbuilt_remedy(self.ui_dir))).encode("utf-8"),
            "text/html; charset=utf-8",
        )

    def _resolve(self, path: str) -> Path | None:
        """A file inside the UI directory, else ``index.html``; None if unbuilt."""
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
        # A '..' walk out of the directory gets the app instead.
        if relative and candidate.is_relative_to(root) and candidate.is_file():
            return candidate
        return index

    # -- plumbing ------------------------------------------------------------------

    def _read_json(self) -> dict[str, Any]:
        # An unread body would be parsed as the next request, so each such path closes.
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            raise ApiError("Send a body with a Content-Length; chunked is not read here.", 411)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length < 0:
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
            self.close_connection = True
            raise ApiError("The request body did not arrive in time.", 408) from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
        # Nested past what the decoder will follow is a body nobody can read either.
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
            raise ApiError(f"Body is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ApiError("Body must be a JSON object.")
        return payload

    def _send_json(
        self, status: int, payload: dict[str, Any], *,
        headers: Sequence[tuple[str, str]] = (),
    ) -> None:
        body = json.dumps(payload).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8", headers=headers)

    def _send_head(self, status: int, headers: Sequence[tuple[str, str]]) -> None:
        """A status line, ``headers``, then the ones every answer carries."""
        self.send_response(status)
        for name, value in (*headers, *_SECURITY_HEADERS):
            self.send_header(name, value)
        if self.request_id:
            self.send_header("X-Request-Id", self.request_id)
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()

    def _send_bytes(
        self, status: int, body: bytes, content_type: str, *,
        headers: Sequence[tuple[str, str]] = (),
    ) -> None:
        try:
            length = (("Content-Type", content_type), ("Content-Length", str(len(body))))
            self._send_head(status, (*length, *headers))
            if self.command != "HEAD":
                self.wfile.write(body)
        except OSError:
            # Left open, socketserver would close a timed-out socket silently.
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
    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("%s - %s", self.address_string(), format % args)


def _workspace_for(ui_dir: Path) -> Path | None:
    """The Angular workspace whose build lands in ``ui_dir``."""
    workspace = ui_dir.parent.parent.parent
    return workspace if (workspace / "package.json").is_file() else None


def _unbuilt_remedy(ui_dir: Path) -> str:
    workspace = _workspace_for(ui_dir)
    if workspace is None:
        return (
            f"There is no Angular workspace above {ui_dir} to build, so point "
            f"--ui-dir at a build that exists -- ui/dist/ui/browser in a checkout."
        )
    return f"Run '{_UNBUILT_COMMAND}' in {workspace}, or point --ui-dir at a build elsewhere."


def browsable_url(host: str, port: int) -> str:
    """The address to type into a browser for a server bound to ``host``."""
    shown = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(host, host)
    if ":" in shown:
        shown = f"[{shown}]"
    return f"http://{shown}:{port}"


def _clashing_provider(port: int, exc: OSError) -> str:
    """Names the model server whose default address is ``port``, on ``EADDRINUSE``."""
    if exc.errno != errno.EADDRINUSE:
        return ""
    for server in PROVIDERS.values():
        listens = urlparse(server.base_url)
        if listens.port == port and _hostname(listens.netloc) in _LOOPBACK_HOSTS:
            return (
                f" That is also {server.label}'s own default address "
                f"({server.base_url}), so serve the UI somewhere else: --port {port + 1}"
            )
    return ""


def _reaches(address: str, host: str, port: int) -> bool:
    """Whether ``address`` lands on a server bound to ``host`` and ``port``."""
    try:
        parsed = urlparse(address)
        named = parsed.port or _SCHEME_PORTS.get(parsed.scheme)
    except ValueError:
        # Names nothing, least of all this server; the provider row refuses it.
        return False
    if named != port:
        return False
    target, bound = (parsed.hostname or "").lower(), host.lower()
    takes_loopback = bound in _EVERYWHERE or bound in _LOOPBACK_HOSTS
    return target == bound or (target in _LOOPBACK_HOSTS and takes_loopback)


def _own_address(label: str, address: str) -> str:
    return (
        f"{address} is this page's own address, not {label}'s: nothing else can listen "
        f"on that port while this server does. Set the {label} address to wherever "
        f"{label} is serving, or serve this page on another port."
    )


def _hostname(netloc: str) -> str:
    """The host out of a ``Host`` header or an origin's netloc, lowercased."""
    host = netloc.strip().lower()
    if host.startswith("["):
        return host[1:].partition("]")[0]
    return host.partition(":")[0]


def _bound_host(address: str) -> str:
    """The host of a typed address; unlike :func:`_hostname`, a bare IPv6 literal is
    not split at its colons (ADR-0018)."""
    host = address.strip().lower()
    if host.startswith("["):
        return _hostname(host)
    return host if host.count(":") > 1 else host.partition(":")[0]


def allowed_hosts_for(host: str, extra: Sequence[str] = ()) -> frozenset[str] | None:
    """Which ``Host`` headers a server bound to ``host`` should answer."""
    # A request with no ``Host`` arrives as a blank, so blanks are dropped.
    named = frozenset(filter(None, map(_bound_host, extra)))
    if _bound_host(host) not in _LOOPBACK_HOSTS:
        return named or None
    return _LOOPBACK_HOSTS | named


def _family_for(host: str) -> int:
    return socket.AF_INET6 if ":" in host else socket.AF_INET


class Server(ThreadingHTTPServer):
    """``ThreadingHTTPServer`` over whichever family its address needs, counting what it
    answers so it can cap it and stop without cutting it off (ADR-0092)."""

    #: Not joined on close, which waits on every thread for as long as each takes: a
    #: stop drains for a while instead (:meth:`drain`).
    block_on_close = False

    def __init__(
        self, server_address: tuple[str, int], handler: Any, traffic: Traffic | None = None
    ) -> None:
        # Read by the base class to open the socket.
        self.address_family = _family_for(server_address[0])
        self.traffic = traffic or Traffic()
        super().__init__(server_address, handler)

    def verify_request(self, request: Any, client_address: Any) -> bool:
        """Close a connection unanswered once as many are open as a server takes."""
        if self.traffic.connect():
            return True
        logger.debug("Closed a connection from %s: %d are open", client_address, MAX_CONNECTIONS)
        return False

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.traffic.disconnect()

    def drain(self, seconds: float = DRAIN_SECONDS) -> None:
        """Wait for the requests in flight to be answered, ``seconds`` at most; a second
        Ctrl-C stops waiting."""
        if self.traffic.requests:
            logger.info(
                "Finishing %d request(s) in flight, for %.0fs at most",
                self.traffic.requests, seconds,
            )
        try:
            unanswered = self.traffic.drain(seconds)
        except KeyboardInterrupt:
            unanswered = self.traffic.requests
        if unanswered:
            logger.warning("Stopped with %d request(s) still unanswered", unanswered)


def bind(
    host: str, port: int, handler: Any, *, max_runs: int = DEFAULT_MAX_RUNS
) -> Server:
    """A threading server for ``handler`` on ``host``; shared with the benchmark."""
    return Server((host, port), handler, Traffic(max_runs))


def create_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    ui_dir: Path | None = None,
    agent_factory: AgentFactory = BuyAgent,
    allowed_hosts: frozenset[str] | None = _LOOPBACK_HOSTS,
    camera: Camera | None = None,
    policy: Policy | None = None,
) -> Server:
    """Build the HTTP server without starting it."""
    policy = policy or Policy()
    handler = partial(
        BuyAgentHandler, ui_dir=ui_dir or DEFAULT_UI_DIR, agent_factory=agent_factory,
        allowed_hosts=allowed_hosts, camera=camera, policy=policy,
    )
    return bind(host, port, handler, max_runs=policy.max_runs)


def camera_for(host: str) -> Camera | None:
    """A camera for a loopback bind with Playwright installed, else None (ADR-0065):
    bound publicly, it would photograph internal pages for anyone."""
    if not available():
        logger.info(
            "Cards will have no screenshot of their page: that needs Playwright. %s", INSTALL
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


def whole_number(limits: tuple[int, int]) -> Callable[[str], int]:
    """A flag ``type`` taking a whole number within ``limits``, else a usage error."""
    minimum, maximum = limits

    def typed(text: str) -> int:
        try:
            number = int(text)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"must be a whole number; got {text!r}") from exc
        if not minimum <= number <= maximum:
            raise argparse.ArgumentTypeError(
                f"must be between {minimum} and {maximum}; got {number}"
            )
        return number

    return typed


#: ``--port`` as argparse takes it: a bindable port. Shared with the benchmark.
port_number = whole_number(_PORTS)

#: What Kubernetes sets ``$BUY_AGENT_PORT`` to in a pod beside a Service named
#: ``buy-agent``, unless the pod turns its service links off (ADR-0093).
_SERVICE_LINK = re.compile(r"tcp://\S+:\d+")


def listening_port(text: str) -> int:
    """The server's ``--port``: :data:`port_number`, naming where a Kubernetes service
    link came from, since a pod deployed as ``buy-agent`` is handed one unasked."""
    if _SERVICE_LINK.fullmatch(text.strip()):
        raise argparse.ArgumentTypeError(
            f"must be a whole number; got {text!r}, which Kubernetes sets for a Service "
            "named buy-agent: set enableServiceLinks: false on the pod"
        )
    return port_number(text)


class _Help(argparse.HelpFormatter):
    """Wraps help with :func:`~buy_agent.logging_setup.wrap_help`."""

    def _split_lines(self, text: str, width: int) -> list[str]:
        return wrap_help(text, width)


def log_format(text: str) -> str:
    """``--log-format`` as argparse takes it, so a misspelt variable is refused too:
    ``choices`` is never asked of a default."""
    if text not in _LOG_FORMATS:
        raise argparse.ArgumentTypeError(
            f"must be one of {', '.join(_LOG_FORMATS)}; got {text!r}"
        )
    return text


def build_parser(deployed: ServerEnvironment | None = None) -> argparse.ArgumentParser:
    """The server's flags, each defaulting to its ``$BUY_AGENT_*`` where that is set
    (ADR-0092): argparse reads a string default as if it were typed, refusals included."""
    env = deployed or server_environment()
    parser = argparse.ArgumentParser(
        prog="buy_agent.server",
        description="Serve the buy_agent UI and its JSON API on localhost.",
        formatter_class=_Help,
    )
    add = parser.add_argument
    add("--host", default=env.host or DEFAULT_HOST,
        help=f"Interface to bind (default: {DEFAULT_HOST}, this machine only, or "
             "$BUY_AGENT_HOST). Binding anywhere else answers any Host header unless "
             "--allowed-host names one, and asks only the model server and the merchant "
             "it was started with.")
    add("--port", type=listening_port, default=env.port or DEFAULT_PORT,
        help=f"Port to bind (default: {DEFAULT_PORT}, or $BUY_AGENT_PORT; 0 takes "
             "whichever one is free and says which at startup).")
    add("--ui-dir", type=Path, default=DEFAULT_UI_DIR,
        help="Directory holding the built Angular app (default: ui/dist/ui/browser).")
    add("--allowed-host", action="append", default=list(env.allowed_hosts), metavar="HOST",
        help="Extra Host header to answer, repeatable, so that a name pointed at this "
             "machine cannot pass itself off as this server. A loopback bind answers the "
             "loopback names as well; a public one answers these and nothing else, and "
             "any Host at all where none were named. $BUY_AGENT_ALLOWED_HOSTS names more, "
             "separated by commas.")
    add("--max-runs", type=whole_number(MAX_RUNS),
        default=env.max_runs or DEFAULT_MAX_RUNS, metavar="N",
        help=f"Searches to run at once; one more is refused with a 503 saying when to "
             f"come back (default: {DEFAULT_MAX_RUNS}, or $BUY_AGENT_MAX_RUNS).")
    add("--behind-proxy", action="store_true", default=env.behind_proxy,
        help="Take the host a browser addressed and the client's address from "
             "X-Forwarded-Host and X-Forwarded-For, which only a proxy that sets both "
             "itself can be trusted with; it also asks only the model server and the "
             "merchant it was started with (default: off, or $BUY_AGENT_BEHIND_PROXY).")
    add("--access-log", action="store_true", default=env.access_log,
        help="Log a line for every request answered: its client, path, status, time "
             "and request id (default: off, or $BUY_AGENT_ACCESS_LOG).")
    add("--log-format", type=log_format, default=env.log_format or "text",
        metavar="{text,json}",
        help="text for a person, json for a log collector, one object a line "
             "(default: text, or $BUY_AGENT_LOG_FORMAT).")
    add("-v", "--verbose", action="store_true", help="Debug logging.")
    add("--version", action="version", version=f"%(prog)s {current().label}")
    return parser


def main(argv: list[str] | None = None) -> int:
    deployed = server_environment()
    args = build_parser(deployed).parse_args(argv)
    configure_logging(verbose=args.verbose, json_lines=args.log_format == "json")
    _install_relay()
    try:
        provider_for(DEFAULT_PROVIDER)
        rail_for(DEFAULT_RAIL)
        backend_for(DEFAULT_BACKEND)
    except ValueError as exc:
        # A misspelt ``$BUY_AGENT_*`` now, not as a 500 per page load.
        logger.error("%s", exc)
        return 1
    if deployed.token and len(deployed.token) < MIN_TOKEN:
        logger.error(
            "$BUY_AGENT_API_TOKEN is %d characters long: give it %d at least, since "
            "anyone who guesses it can search and pay through this server.",
            len(deployed.token), MIN_TOKEN,
        )
        return 1

    allowed = allowed_hosts_for(args.host, args.allowed_host)
    if allowed is None:
        logger.warning(
            "Bound to %s with no --allowed-host: this answers any Host header, so "
            "a name resolved here by someone else is answered too. Name the host "
            "you reach it by to close that.",
            args.host,
        )
    # Other machines reach it either way: through the bind, or through the proxy.
    exposed = args.behind_proxy or _bound_host(args.host) not in _LOOPBACK_HOSTS
    if exposed and not deployed.token:
        logger.warning(
            "Reachable from other machines with no $BUY_AGENT_API_TOKEN: anyone who "
            "can reach it can search, and pay however it is set up to. Set one to ask "
            "for it."
        )
    policy = Policy(
        token=deployed.token,
        fixed_addresses=exposed,
        behind_proxy=args.behind_proxy,
        access_log=args.access_log,
        max_runs=args.max_runs,
        sealer=Sealer.keyed_by(deployed.token),
    )
    # Behind a proxy a loopback bind is anybody's, and so would be its camera (ADR-0065).
    camera = None if args.behind_proxy else camera_for(args.host)
    try:
        httpd = create_server(
            args.host, args.port, ui_dir=args.ui_dir, allowed_hosts=allowed, camera=camera,
            policy=policy,
        )
    except OSError as exc:
        logger.error(
            "Could not listen on %s:%s (%s).%s",
            args.host, args.port, exc, _clashing_provider(args.port, exc),
        )
        return 1

    host, port = httpd.server_address[:2]
    logger.info("buy_agent UI on %s", browsable_url(str(host), port))
    if not (args.ui_dir / "index.html").is_file():
        logger.warning(
            "No built UI at %s -- the API works, but the page will not. %s",
            args.ui_dir, _unbuilt_remedy(args.ui_dir),
        )
    try:
        return serve_until_interrupted(httpd)
    finally:
        if camera is not None:
            camera.close()
        logging.getLogger("buy_agent").removeHandler(_relay)


def serve_until_interrupted(httpd: Server) -> int:
    """Serve until Ctrl-C or SIGTERM, then finish what is in flight, for a while, and
    close: 130 for Ctrl-C, as every entry point answers one, and 0 for a stop that was
    asked for (ADR-0092)."""
    code = 0
    try:
        with stopped_by_sigterm():
            httpd.serve_forever()
    except KeyboardInterrupt:
        logger.warning("Interrupted.")
        code = 130
    except Terminated:
        logger.info("Asked to stop.")
    finally:
        httpd.server_close()
        httpd.drain()
    return code


if __name__ == "__main__":
    sys.exit(main())
