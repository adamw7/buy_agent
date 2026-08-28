"""The HTTP layer: routing, status codes, the event stream and the static app."""

from __future__ import annotations

import json
import logging
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest

from buy_agent.agent import ModelUnavailableError
from buy_agent.models import Product, RankedProduct
from buy_agent.providers import OLLAMA, VLLM
from buy_agent.search import SearchError
from buy_agent.server import (
    _CROSS_SITE,
    _KEEPALIVE_SECONDS,
    _LOOPBACK_HOSTS,
    _SECURITY_HEADERS,
    BuyAgentHandler,
    _hostname,
    _relay,
    allowed_hosts_for,
    build_parser,
    create_server,
    main,
)

RANKED = [
    RankedProduct(
        product=Product(name="Sony WH-1000XM5", price=328.0, rating=4.7), score=0.9, rank=1
    ),
    RankedProduct(product=Product(name="Anker Q30", price=79.0), score=0.7, rank=2),
]


class StubAgent:
    """Stands in for BuyAgent: logs a line, then answers with whatever it was given."""

    captured: dict[str, Any] = {}
    result: Any = RANKED
    #: Seconds to spend saying nothing, standing in for the extraction step.
    delay: float = 0.0

    def __init__(self, config):
        StubAgent.captured["config"] = config

    def run(self, request, *, sort_by="score"):
        StubAgent.captured["request"] = request
        StubAgent.captured["sort_by"] = sort_by
        logging.getLogger("buy_agent.stub").info("Searching for %s", request)
        time.sleep(StubAgent.delay)
        # A second line on the far side of the delay: with two runs overlapping,
        # this is the one produced while both of them have a queue attached.
        logging.getLogger("buy_agent.stub").info("Ranking what came back for %s", request)
        if isinstance(StubAgent.result, BaseException):
            raise StubAgent.result
        return StubAgent.result


@pytest.fixture
def server(tmp_path: Path) -> Iterator[str]:
    """A live server on a loopback port, with the agent stubbed out.

    Nothing here reaches the network or Ollama: only the socket is real, because
    routing and status codes are exactly what these tests are about.
    """
    StubAgent.captured = {}
    StubAgent.result = RANKED
    StubAgent.delay = 0.0
    with serving(tmp_path) as base:
        yield base


@contextmanager
def serving(ui_dir: Path, allowed_hosts: frozenset[str] | None = _LOOPBACK_HOSTS) -> Iterator[str]:
    """Run a server on a loopback port for the duration of the block."""
    httpd = create_server(
        "127.0.0.1", 0, ui_dir=ui_dir, agent_factory=StubAgent, allowed_hosts=allowed_hosts
    )
    # 0.01 rather than the default 0.5s poll: otherwise shutdown costs half a
    # second per test.
    thread = threading.Thread(target=httpd.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    host, port = httpd.server_address[:2]
    try:
        yield f"http://{host}:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def get(url: str) -> tuple[int, Any]:
    return _call(urllib.request.Request(url))


def content_type(url: str) -> str:
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.headers.get("Content-Type", "")


def post(url: str, payload: Any) -> tuple[int, Any]:
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return _call(
        urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
    )


def _call(request: urllib.request.Request) -> tuple[int, Any]:
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, _decode(response)
    except urllib.error.HTTPError as error:
        return error.code, _decode(error)


def _decode(response) -> Any:
    raw = response.read()
    if "json" in response.headers.get("Content-Type", ""):
        return json.loads(raw)
    return raw.decode("utf-8", "replace")


def raw(base: str, request: bytes) -> str:
    """Send a request urllib refuses to build, and read the whole reply.

    The headers and the body are separate writes and so can arrive in separate
    segments; reading once would see the reply without its body about half the time.
    """
    parsed = urlparse(base)
    with socket.create_connection((parsed.hostname, parsed.port), timeout=10) as sock:
        sock.sendall(request)
        reply = b""
        while b"\r\n\r\n" not in reply:
            chunk = sock.recv(4096)
            if not chunk:
                return reply.decode("utf-8", "replace")
            reply += chunk
        head, _, body = reply.partition(b"\r\n\r\n")
        declared = re.search(rb"Content-Length: (\d+)", head, re.IGNORECASE)
        expected = int(declared.group(1)) if declared else 0
        while len(body) < expected:
            chunk = sock.recv(4096)
            if not chunk:
                break
            body += chunk
    return (head + b"\r\n\r\n" + body).decode("utf-8", "replace")


def ask(base: str, path: str = "/api/config", **headers: str) -> str:
    """Send a GET with exactly the headers given, and read the whole reply.

    Built by hand because these tests are about headers urllib insists on
    writing itself -- Host above all, which it derives from the URL.
    """
    sent = {"Host": urlparse(base).netloc, "Connection": "close"}
    sent |= {name.replace("_", "-"): value for name, value in headers.items()}
    lines = [f"GET {path} HTTP/1.1", *(f"{k}: {v}" for k, v in sent.items())]
    return raw(base, ("\r\n".join(lines) + "\r\n\r\n").encode())


def events(url: str) -> list[tuple[str, Any]]:
    """Read a Server-Sent Events response into ``(event, data)`` pairs."""
    collected: list[tuple[str, Any]] = []
    with urllib.request.urlopen(url, timeout=30) as response:
        assert response.headers["Content-Type"] == "text/event-stream"
        name = None
        for line in response:
            text = line.decode("utf-8").rstrip("\n")
            if text.startswith("event: "):
                name = text.removeprefix("event: ")
            elif text.startswith("data: ") and name:
                collected.append((name, json.loads(text.removeprefix("data: "))))
                name = None
    return collected


# -- the JSON API --------------------------------------------------------------


def test_config_serves_the_form_its_defaults(server: str) -> None:
    status, payload = get(f"{server}/api/config")
    assert status == 200
    assert payload["results"] == 10
    assert payload["sort_options"] == ["score", "price", "rating"]


def test_a_search_answers_with_ranked_products(server: str) -> None:
    status, payload = post(f"{server}/api/search", {"request": "headphones"})
    assert status == 200
    assert payload["count"] == 2
    assert payload["products"][0]["name"] == "Sony WH-1000XM5"
    assert payload["products"][0]["rank"] == 1
    assert StubAgent.captured["request"] == "headphones"


def test_search_options_reach_the_agents_config(server: str) -> None:
    post(
        f"{server}/api/search",
        {"request": "espresso machine", "model": "qwen2.5", "top": 5, "sort_by": "price"},
    )
    assert StubAgent.captured["config"].model == "qwen2.5"
    assert StubAgent.captured["config"].top_n == 5
    assert StubAgent.captured["sort_by"] == "price"


def test_an_empty_request_is_the_clients_mistake(server: str) -> None:
    StubAgent.result = ValueError("Nothing to shop for: the request is empty.")
    status, payload = post(f"{server}/api/search", {"request": "   "})
    assert status == 400
    assert "empty" in payload["error"]


def test_a_missing_ollama_is_reported_as_unavailable(server: str) -> None:
    StubAgent.result = ModelUnavailableError("Start it with:  ollama serve")
    status, payload = post(f"{server}/api/search", {"request": "headphones"})
    assert status == 503
    assert "ollama serve" in payload["error"]


def test_a_failed_search_is_reported_as_a_bad_gateway(server: str) -> None:
    StubAgent.result = SearchError("DuckDuckGo rate-limited the request")
    status, payload = post(f"{server}/api/search", {"request": "headphones"})
    assert status == 502
    assert "rate-limited" in payload["error"]


def test_a_bad_option_is_rejected_before_the_agent_runs(server: str) -> None:
    status, payload = post(f"{server}/api/search", {"request": "x", "sort_by": "cheapness"})
    assert status == 400
    assert "sort_by" in payload["error"]
    assert "request" not in StubAgent.captured


def test_a_body_that_is_not_json_is_rejected(server: str) -> None:
    status, payload = post(f"{server}/api/search", b"not json at all")
    assert status == 400
    assert "JSON" in payload["error"]


def test_a_body_that_is_not_an_object_is_rejected(server: str) -> None:
    status, payload = post(f"{server}/api/search", ["headphones"])
    assert status == 400


def test_unknown_api_paths_are_not_swallowed_by_the_app(server: str) -> None:
    """An /api typo must 404, not quietly return index.html."""
    assert get(f"{server}/api/nope")[0] == 404
    assert post(f"{server}/api/nope", {})[0] == 404


def test_head_does_not_start_a_search(server: str) -> None:
    """A monitor probing the stream endpoint must not spend a minute of Ollama."""
    request = urllib.request.Request(f"{server}/api/search/stream?request=x", method="HEAD")
    try:
        urllib.request.urlopen(request, timeout=10)
    except urllib.error.HTTPError as error:
        assert error.code == 405
    assert "request" not in StubAgent.captured


def test_models_reports_an_unreachable_ollama(server: str, monkeypatch) -> None:
    status, payload = get(f"{server}/api/models?base_url=http://127.0.0.1:1")
    assert status == 200
    assert payload["reachable"] is False
    assert payload["provider"] == "ollama", "the provider is what the address was asked as"


def test_models_asks_the_provider_the_request_named(server: str) -> None:
    """The address alone is not the question: the same URL is asked one way for
    Ollama and another for vLLM, and a vLLM asked Ollama's question answers 404."""
    status, payload = get(f"{server}/api/models?provider=vllm&base_url=http://127.0.0.1:1")

    assert status == 200
    assert (payload["provider"], payload["label"]) == ("vllm", "vLLM")
    assert payload["reachable"] is False


def test_models_falls_back_to_the_address_that_provider_serves_on(server: str) -> None:
    """A page that named a provider and no address wants that provider's own, not
    the other one's port -- 11434 answered by nothing is not "vLLM is down"."""
    status, payload = get(f"{server}/api/models?provider=vllm")

    assert status == 200
    assert payload["base_url"] == VLLM.base_url


def test_models_reports_a_provider_nothing_can_serve(server: str) -> None:
    """Rather than a 500: the pill above the form is already where a server that
    cannot be asked is explained, and this is one more reason it cannot be."""
    status, payload = get(f"{server}/api/models?provider=llama.cpp")

    assert status == 200
    assert payload["reachable"] is False
    assert "llama.cpp" in payload["detail"]


def test_a_head_request_answers_like_a_get_without_the_body(server: str) -> None:
    """Only the stream refuses HEAD; everything else answers headers and no body."""
    request = urllib.request.Request(f"{server}/api/config", method="HEAD")

    with urllib.request.urlopen(request, timeout=10) as response:
        assert response.status == 200
        assert "json" in response.headers["Content-Type"]
        assert int(response.headers["Content-Length"]) > 0
        assert response.read() == b""


def test_a_body_over_the_cap_is_refused_without_being_read(server: str) -> None:
    """The length is enough to refuse on; reading 100 KB to then reject it is not."""
    reply = raw(
        server,
        b"POST /api/search HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 100000\r\n\r\n{}",
    )

    assert "413" in reply.splitlines()[0]


def test_a_content_length_that_is_not_a_number_is_the_clients_mistake(server: str) -> None:
    reply = raw(
        server, b"POST /api/search HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: lots\r\n\r\n"
    )

    assert "400" in reply.splitlines()[0]
    assert "Content-Length is not a number" in reply


def test_a_request_with_no_body_is_read_as_an_empty_object(server: str) -> None:
    """A missing body is no options at all, rather than a body that failed to parse."""
    StubAgent.result = ValueError("Nothing to shop for: the request is empty.")

    reply = raw(
        server, b"POST /api/search HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 0\r\n\r\n"
    )

    assert "400" in reply.splitlines()[0]
    assert "Nothing to shop for" in reply
    assert "JSON" not in reply, "an absent body is not a malformed one"
    assert StubAgent.captured["request"] == ""


# -- who is allowed to ask -----------------------------------------------------


def test_a_page_on_another_site_cannot_start_a_run(server: str) -> None:
    """The reply it cannot read is not the point -- the run happening is.

    A cross-site POST needs no answer to be worth making: the browser refuses to
    show it, and ten pages have still been fetched and a model driven on somebody
    else's say-so. So it is refused before the agent is built, not after.
    """
    reply = raw(
        server,
        b"POST /api/search HTTP/1.1\r\nHost: 127.0.0.1\r\n"
        b"Origin: https://evil.example\r\nContent-Type: text/plain\r\n"
        b"Content-Length: 25\r\nConnection: close\r\n\r\n"
        b'{"request": "headphones"}',
    )

    assert "403" in reply.splitlines()[0]
    assert "captured" not in StubAgent.captured
    assert "request" not in StubAgent.captured


def test_a_cross_site_request_carrying_no_origin_is_still_refused(server: str) -> None:
    """An <img> or an <iframe> pointed here sends no Origin, only fetch metadata.

    Which is why both are read: Origin covers what fetch metadata a proxy stripped
    would not, and fetch metadata covers the requests that never carry an Origin.
    """
    reply = ask(server, "/api/search/stream?request=headphones", Sec_Fetch_Site="cross-site")

    assert "403" in reply.splitlines()[0]
    assert "request" not in StubAgent.captured


def test_the_dev_servers_proxy_is_not_a_foreign_site(server: str) -> None:
    """`npm start` serves the app on :4200 and proxies /api here, Origin and all.

    Loopback is the boundary being drawn, not the port: the attack is a page on
    the internet, and another port on this machine is the developer's own.
    """
    reply = ask(server, Origin="http://localhost:4200", Sec_Fetch_Site="same-origin")

    assert "200" in reply.splitlines()[0]
    assert OLLAMA.model in reply
    # Ports do not make a site, so a loopback page calling this one directly --
    # not through the proxy -- reports same-site. That is the developer, not a
    # stranger, and the loopback Origin is what says so.
    assert "200" in ask(
        server, Origin="http://127.0.0.1:4200", Sec_Fetch_Site="same-site"
    ).splitlines()[0]
    assert _CROSS_SITE == "cross-site"


def test_an_opaque_origin_is_refused(server: str) -> None:
    """A sandboxed iframe posts as "null"; the app is served from a real origin."""
    assert "403" in ask(server, Origin="null").splitlines()[0]


def test_a_client_that_is_not_a_browser_is_answered(server: str) -> None:
    """curl and the scripts POST /api/search was shaped for send none of this.

    Nothing here is an authentication check -- it is the browser's own account of
    where a request came from, which only a browser gives.
    """
    reply = ask(server)

    assert "200" in reply.splitlines()[0]
    assert OLLAMA.model in reply


def test_a_name_that_merely_resolves_here_is_not_answered(server: str) -> None:
    """DNS rebinding: evil.example re-points at 127.0.0.1 and is then same-origin.

    At that point every check above passes -- the browser genuinely believes the
    page and this server share an origin -- and the only thing left that tells
    them apart is the name the request was addressed to.
    """
    reply = ask(server, Host="evil.example")

    assert "403" in reply.splitlines()[0]
    assert OLLAMA.model not in reply, "the defaults leaked to a rebound name"


def test_head_is_guarded_like_the_others(server: str) -> None:
    """It answers by way of do_GET, so a guard only on GET would still run it."""
    reply = raw(
        server,
        b"HEAD /api/config HTTP/1.1\r\nHost: evil.example\r\nConnection: close\r\n\r\n",
    )

    assert "403" in reply.splitlines()[0]


def test_a_refused_request_ends_the_connection(server: str) -> None:
    """Nothing more is coming from a caller who was not meant to be asking."""
    reply = ask(server, Host="evil.example")

    assert "Connection: close" in reply


def test_every_response_says_what_the_page_may_do(server: str) -> None:
    """Including the JSON: the headers are cheap and the omission is the bug."""
    reply = ask(server)

    for name, value in _SECURITY_HEADERS:
        assert f"{name}: {value}" in reply


def test_the_stream_carries_the_security_headers_too(server: str) -> None:
    """It writes its own header block rather than going through _send_bytes."""
    with urllib.request.urlopen(f"{server}/api/search/stream?request=x", timeout=30) as response:
        headers = response.headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]


def test_a_chunked_body_does_not_desync_the_connection(server: str) -> None:
    """The fourth way, and the one that needs no Content-Length at all.

    ``BaseHTTPRequestHandler`` does not decode chunks, so a body framed by
    ``Transfer-Encoding`` was read as no body: the request ran with default
    options and the chunks stayed in the socket, to be parsed as whatever came
    next on a connection this server had just said it would keep. 411 is what
    says a length is required, and the connection ends rather than guessing.
    """
    parsed = urlparse(server)
    with socket.create_connection((parsed.hostname, parsed.port), timeout=10) as sock:
        sock.sendall(
            b"POST /api/search HTTP/1.1\r\nHost: 127.0.0.1\r\n"
            b"Transfer-Encoding: chunked\r\n\r\n"
            b"27\r\nGET /api/config HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n0\r\n\r\n"
        )
        reply = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            reply += chunk

    text = reply.decode("utf-8", "replace")
    assert "411" in text.splitlines()[0]
    assert "Connection: close" in text
    assert text.count("HTTP/1.1 ") == 1, "the smuggled request was answered"


def test_a_chunked_request_never_reaches_the_agent(server: str) -> None:
    """Refused before the run, not after: the options in it were never read."""
    StubAgent.captured.clear()

    reply = raw(
        server,
        b"POST /api/search HTTP/1.1\r\nHost: 127.0.0.1\r\n"
        b"Transfer-Encoding: chunked\r\n\r\n0\r\n\r\n",
    )

    assert "411" in reply.splitlines()[0]
    assert StubAgent.captured == {}


def test_a_negative_content_length_does_not_desync_the_connection(server: str) -> None:
    """The third way to declare a body this loop will never read.

    An over-long body and an unparseable length both close the connection for
    this reason; a negative one parses as an integer and used to slip past into
    "no body at all", leaving the bytes to be read as the next request line.
    """
    parsed = urlparse(server)
    with socket.create_connection((parsed.hostname, parsed.port), timeout=10) as sock:
        sock.sendall(
            b"POST /api/search HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: -1\r\n\r\n"
            b"GET /api/config HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"
        )
        reply = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            reply += chunk

    text = reply.decode("utf-8", "replace")
    assert "400" in text.splitlines()[0]
    assert "Connection: close" in text
    assert text.count("HTTP/1.1 ") == 1, "the smuggled request was answered"


def test_a_path_that_cannot_name_a_file_is_answered_not_dropped(tmp_path: Path) -> None:
    """An encoded NUL cannot name a file, and it is answered rather than dropped.

    The browser cannot tell a dropped socket from a server that died, so the path
    that cannot name a file is answered the way every other one is.

    The two platforms reach that answer down different lines. On POSIX
    ``resolve()`` raises ValueError on the NUL and ``_resolve`` catches it; on
    Windows ``ntpath.realpath`` hands a non-strict caller the path back
    unchanged, and it is ``is_file()`` -- which swallows the same ValueError --
    that sends it on to the app. Only the answer is common to both, so only the
    answer is asserted here; the branch itself is
    ``test_a_path_the_platform_refuses_to_resolve_is_answered_not_dropped``.
    """
    (tmp_path / "index.html").write_text("<app-root></app-root>", encoding="utf-8")

    with serving(tmp_path) as base:
        reply = ask(base, "/main%00.js")

    assert "200" in reply.splitlines()[0]
    assert "<app-root>" in reply


def test_a_path_the_platform_refuses_to_resolve_is_answered_not_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same answer, with the failure provoked rather than hoped for.

    ``_resolve`` catches OSError and ValueError because an exception there
    escapes to socketserver, which drops the socket without a reply. Which
    inputs actually raise is the platform's business -- the encoded NUL above
    raises on POSIX and does not on Windows -- so the input that used to stand in
    for "resolve() will not answer" left the branch unrun on the platform this
    project is written on, and the test above passing said nothing about it.
    Making resolve() refuse outright is the only way to ask both platforms the
    same question.
    """
    (tmp_path / "index.html").write_text("<app-root></app-root>", encoding="utf-8")
    resolve = Path.resolve

    def refuse(self: Path, *args: Any, **kwargs: Any) -> Path:
        # The ui_dir resolve() on the line above the try has to keep working, or
        # the exception is raised somewhere this branch does not cover.
        if self.name == "main.js":
            raise OSError("the platform will not resolve this")
        return resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", refuse)

    with serving(tmp_path) as base:
        reply = ask(base, "/main.js")

    assert "200" in reply.splitlines()[0]
    assert "<app-root>" in reply


def test_a_server_told_to_answer_any_host_does(tmp_path: Path) -> None:
    """What a bind to a public interface gets, since its name is not ours to guess.

    The Origin and fetch-metadata checks still stand -- this turns off the one
    check that needs to know what this server is called.
    """
    (tmp_path / "index.html").write_text("<app-root></app-root>", encoding="utf-8")

    with serving(tmp_path, allowed_hosts=None) as base:
        assert "200" in ask(base, "/", Host="buy.lan").splitlines()[0]
        assert "403" in ask(base, "/", Host="buy.lan", Sec_Fetch_Site="cross-site").splitlines()[0]


def test_the_apps_own_page_is_answered_whatever_the_server_is_called(tmp_path: Path) -> None:
    """A public bind is reached by a name that is not loopback, and it still works.

    The container binds 0.0.0.0 (ADR-0015) and someone reaches it at buy.lan. Its
    page then sends an Origin no loopback rule would recognise -- so what admits
    it is that the Origin and the Host agree, which only this server's own page
    can manage: the browser writes both.
    """
    (tmp_path / "index.html").write_text("<app-root></app-root>", encoding="utf-8")

    with serving(tmp_path, allowed_hosts=frozenset({"buy.lan"})) as base:
        own = ask(base, "/api/config", Host="buy.lan:8000", Origin="http://buy.lan:8000")
        assert "200" in own.splitlines()[0]
        # The same page name, on a host this server does not answer to.
        borrowed = ask(base, "/api/config", Host="buy.lan:8000", Origin="http://evil.example")
        assert "403" in borrowed.splitlines()[0]


def test_the_hostname_of_an_authority_is_read_without_a_scheme() -> None:
    """urlparse reads 'localhost:8000' as a scheme and a path, hence by hand."""
    assert _hostname("localhost:8000") == "localhost"
    assert _hostname("127.0.0.1") == "127.0.0.1"
    assert _hostname("[::1]:8000") == "::1"
    assert _hostname("[::1]") == "::1"
    assert _hostname(" EVIL.example ") == "evil.example"
    assert _hostname("") == ""


def test_a_loopback_bind_answers_the_loopback_names_and_what_was_named() -> None:
    allowed = allowed_hosts_for("127.0.0.1", ["buy.local"])
    assert allowed is not None
    assert _LOOPBACK_HOSTS <= allowed
    assert "buy.local" in allowed
    assert "evil.example" not in allowed


def test_a_public_bind_answers_any_host_until_one_is_named() -> None:
    """The name that reaches a public interface is the operator's to know.

    Guessing it would refuse the container's own users (ADR-0015 binds 0.0.0.0),
    so the check is off by default there and --allowed-host is what turns it on.
    """
    assert allowed_hosts_for("192.168.1.5") is None
    assert allowed_hosts_for("192.168.1.5", ["buy.lan:8000"]) == frozenset({"buy.lan"})
    assert allowed_hosts_for("127.0.0.1", ["  "]) == _LOOPBACK_HOSTS


# -- the event stream ----------------------------------------------------------


def test_the_stream_relays_progress_then_the_result(server: str) -> None:
    stream = events(f"{server}/api/search/stream?request=headphones&top=2")
    kinds = [name for name, _ in stream]
    assert kinds[-1] == "result"
    assert "log" in kinds

    logs = [data for name, data in stream if name == "log"]
    assert any("Searching for headphones" in entry["message"] for entry in logs)
    assert {"level", "logger", "message"} == set(logs[0])

    _, result = stream[-1]
    assert result["count"] == 2
    assert result["products"][0]["name"] == "Sony WH-1000XM5"


def test_the_stream_passes_options_through_the_query_string(server: str) -> None:
    events(f"{server}/api/search/stream?request=espresso&model=qwen2.5&fetch=false")
    assert StubAgent.captured["config"].model == "qwen2.5"
    assert StubAgent.captured["config"].fetch_pages is False


def test_a_failure_ends_the_stream_with_a_failure_event(server: str) -> None:
    """Named 'failure', not 'error': EventSource reserves 'error' for the transport."""
    StubAgent.result = ModelUnavailableError("Start it with:  ollama serve")
    name, data = events(f"{server}/api/search/stream?request=headphones")[-1]
    assert name == "failure"
    assert data["status"] == 503
    assert "ollama serve" in data["error"]


def test_a_bad_option_ends_the_stream_before_the_agent_runs(server: str) -> None:
    name, data = events(f"{server}/api/search/stream?request=x&results=nope")[-1]
    assert (name, data["status"]) == ("failure", 400)
    assert "request" not in StubAgent.captured


def test_an_unexpected_failure_still_ends_the_stream(server: str) -> None:
    """The stream reports; it never leaves the browser waiting on a dead run."""
    StubAgent.result = RuntimeError("something nobody predicted")
    name, data = events(f"{server}/api/search/stream?request=headphones")[-1]
    assert (name, data["status"]) == ("failure", 500)
    assert "something nobody predicted" in data["error"]


def test_an_unexpected_failure_is_a_500_and_not_a_dropped_connection(server: str) -> None:
    """The one-shot endpoint answers what the stream answers.

    Letting the exception escape hands the socket to socketserver, which closes it
    without writing anything -- and a browser cannot tell that from the server
    going away mid-run.
    """
    StubAgent.result = RuntimeError("something nobody predicted")

    status, payload = post(f"{server}/api/search", {"request": "headphones"})

    assert status == 500
    assert "something nobody predicted" in payload["error"]


@pytest.mark.parametrize(
    ("length", "expected"),
    [(b"100000", "413"), (b"lots", "400")],
)
def test_a_rejected_body_ends_the_connection_rather_than_desyncing_it(
    server: str, length: bytes, expected: str
) -> None:
    """A body refused unread would otherwise be parsed as the next request.

    Both of these answer without reading the body, so on a kept-alive HTTP/1.1
    connection the leftover bytes become the next request line -- the client asks
    for /api/config and gets a 414 off its own JSON.
    """
    parsed = urlparse(server)
    with socket.create_connection((parsed.hostname, parsed.port), timeout=10) as sock:
        sock.sendall(
            b"POST /api/search HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: " + length + b"\r\n\r\n{}"
        )
        reply = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            reply += chunk

    text = reply.decode("utf-8", "replace")
    assert expected in text.splitlines()[0]
    assert "Connection: close" in text
    assert text.count("HTTP/1.1 ") == 1, "the leftover body was parsed as a request"


def test_two_streams_do_not_see_each_others_progress(server: str) -> None:
    """Log lines are routed by the thread that produced them.

    The delay is what makes this a test of the routing: without it the first run
    finishes and detaches before the second attaches, so there is only ever one
    queue to choose from and any rule at all would look right.
    """
    StubAgent.delay = 0.3
    collected: dict[str, list] = {}

    def run(request: str) -> None:
        collected[request] = events(f"{server}/api/search/stream?request={request}")

    threads = [
        threading.Thread(target=run, args=(request,)) for request in ("kettle", "toaster")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    for request, stream in collected.items():
        messages = [data["message"] for name, data in stream if name == "log"]
        assert any(request in message for message in messages)
        assert not any(other in message for message in messages for other in {"kettle", "toaster"} - {request})


def test_a_quiet_run_is_kept_alive_with_pings(server: str, monkeypatch) -> None:
    """Extraction is slow and logs nothing, so a real run goes quiet for a minute.

    Browsers and proxies time a silent response out and ``EventSource`` then
    reconnects, which would start the whole search again.
    """
    monkeypatch.setattr("buy_agent.server._KEEPALIVE_SECONDS", 0.05)
    StubAgent.delay = 0.4

    collected = events(f"{server}/api/search/stream?request=kettle")
    names = [name for name, _ in collected]

    assert ("ping", {}) in collected, "a quiet stream sent nothing for 0.4s"
    assert names[-1] == "result", "a ping is never the last word"
    assert "failure" not in names, "a quiet stretch is not a failure"


def test_the_keepalive_is_frequent_enough_to_be_worth_sending() -> None:
    """The mechanism is only useful if the interval beats what gives up on silence.

    Proxies commonly drop a silent response at 60s and browsers sooner, so the
    test that exercises the ping has to patch the interval -- which leaves the
    shipped value itself unpinned unless it is asserted here.
    """
    assert 0 < _KEEPALIVE_SECONDS <= 30


def test_a_stream_nobody_is_reading_is_abandoned(server: str, monkeypatch, caplog) -> None:
    """The run itself carries on -- it is blocked in Ollama -- but the writing stops."""
    monkeypatch.setattr(BuyAgentHandler, "_send_event", lambda self, event, data: False)

    with caplog.at_level(logging.INFO, logger="buy_agent.server"):
        with urllib.request.urlopen(
            f"{server}/api/search/stream?request=kettle", timeout=10
        ) as response:
            assert response.read() == b"", "nothing more is written to a dead socket"

    assert "Client disconnected" in caplog.text


def test_writing_to_a_closed_connection_is_reported_rather_than_raised() -> None:
    """``_send_event`` returning False is the only signal the loop has."""

    class BrokenPipe:
        def write(self, _data):
            raise BrokenPipeError("client went away")

        def flush(self):
            raise BrokenPipeError("client went away")

    handler = BuyAgentHandler.__new__(BuyAgentHandler)
    handler.wfile = BrokenPipe()

    assert handler._send_event("log", {"message": "hello"}) is False


# -- the built app -------------------------------------------------------------


def test_an_unbuilt_ui_says_how_to_build_it(server: str) -> None:
    status, payload = get(f"{server}/")
    assert status == 503
    assert "npm run build" in payload["error"]


def test_the_app_is_served_and_owns_its_own_routes(tmp_path: Path) -> None:
    """Including the content types: a .js served as text/plain is a blank page."""
    (tmp_path / "index.html").write_text("<app-root></app-root>", encoding="utf-8")
    (tmp_path / "main.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "styles.css").write_text("body{}", encoding="utf-8")
    with serving(tmp_path) as base:
        assert get(f"{base}/")[1] == "<app-root></app-root>"
        assert get(f"{base}/main.js")[1] == "console.log(1)"
        assert content_type(f"{base}/main.js").startswith("text/javascript")
        assert content_type(f"{base}/styles.css").startswith("text/css")
        # A deep link belongs to the app's router, not to the filesystem.
        assert get(f"{base}/results/3")[1] == "<app-root></app-root>"
        # Walking out of the UI directory gets the app, not the file -- encoded
        # or not, since the path is unquoted before it is checked.
        assert get(f"{base}/../../requirements.txt")[1] == "<app-root></app-root>"
        assert get(f"{base}/%2e%2e/%2e%2e/requirements.txt")[1] == "<app-root></app-root>"


def test_the_content_type_table_answers_and_not_the_platform(tmp_path: Path, monkeypatch) -> None:
    """On Windows ``mimetypes`` reads the registry and can call a .js text/plain.

    A module a browser is handed as text/plain is refused, leaving a blank page
    and no error -- so the table has to win even where the platform disagrees.
    The Linux runner agrees with the table, which is why this has to be forced.
    """
    monkeypatch.setattr(
        "buy_agent.server.mimetypes.guess_type", lambda *_a, **_k: ("text/plain", None)
    )
    (tmp_path / "index.html").write_text("<app-root></app-root>", encoding="utf-8")
    for name in ("main.js", "polyfills.mjs", "styles.css", "icon.svg", "font.woff2"):
        (tmp_path / name).write_text("x", encoding="utf-8")

    with serving(tmp_path) as base:
        assert content_type(f"{base}/main.js").startswith("text/javascript")
        assert content_type(f"{base}/polyfills.mjs").startswith("text/javascript")
        assert content_type(f"{base}/styles.css").startswith("text/css")
        assert content_type(f"{base}/icon.svg").startswith("image/svg+xml")
        assert content_type(f"{base}/font.woff2").startswith("font/woff2")


def test_an_extension_the_table_does_not_know_still_gets_served(tmp_path: Path) -> None:
    """The table covers what ng build emits; anything else falls back rather than 404s."""
    (tmp_path / "index.html").write_text("<app-root></app-root>", encoding="utf-8")
    # An extension mimetypes has no opinion on either, so the last resort answers.
    (tmp_path / "app.zzz").write_bytes(b"\x00\x01")

    with serving(tmp_path) as base:
        assert content_type(f"{base}/app.zzz") == "application/octet-stream"


# -- the command line ----------------------------------------------------------


def test_the_parser_defaults_to_loopback_port_8000() -> None:
    args = build_parser().parse_args([])
    assert (args.host, args.port) == ("127.0.0.1", 8000)
    assert args.ui_dir.name == "browser"


def test_a_port_that_cannot_be_bound_is_reported_not_raised(monkeypatch) -> None:
    def refuse(*args, **kwargs):
        raise OSError("Address already in use")

    monkeypatch.setattr("buy_agent.server.create_server", refuse)
    assert main(["--port", "8000"]) == 1


class FakeHttpd:
    """Stands in for the socket server, so main() can be driven without binding one."""

    server_address = ("127.0.0.1", 8000)

    def __init__(self, on_serve=None) -> None:
        self.on_serve = on_serve
        self.closed = False

    def serve_forever(self) -> None:
        if self.on_serve:
            raise self.on_serve

    def server_close(self) -> None:
        self.closed = True


def test_serving_until_the_socket_is_given_up_exits_zero(monkeypatch, tmp_path: Path) -> None:
    httpd = FakeHttpd()
    monkeypatch.setattr("buy_agent.server.create_server", lambda *a, **k: httpd)

    assert main(["--ui-dir", str(tmp_path)]) == 0
    assert httpd.closed, "the port has to be released on the way out"


def test_a_ctrl_c_stops_the_server_without_a_traceback(monkeypatch, tmp_path: Path) -> None:
    """Ctrl-C is how this server is stopped; 130 is the shell's word for that."""
    httpd = FakeHttpd(on_serve=KeyboardInterrupt())
    monkeypatch.setattr("buy_agent.server.create_server", lambda *a, **k: httpd)

    assert main(["--ui-dir", str(tmp_path)]) == 130
    assert httpd.closed


def test_an_unbuilt_ui_is_warned_about_at_startup(monkeypatch, tmp_path: Path, caplog) -> None:
    """The API still works, so this is a warning and not a refusal to start."""
    monkeypatch.setattr("buy_agent.server.create_server", lambda *a, **k: FakeHttpd())

    with caplog.at_level(logging.WARNING, logger="buy_agent.server"):
        main(["--ui-dir", str(tmp_path)])

    assert "npm run build" in caplog.text


def test_a_public_bind_with_no_named_host_is_warned_about(
    monkeypatch, tmp_path: Path, caplog
) -> None:
    """Binding 0.0.0.0 turns the rebinding check off, so it is said out loud."""
    monkeypatch.setattr("buy_agent.server.create_server", lambda *a, **k: FakeHttpd())

    with caplog.at_level(logging.WARNING, logger="buy_agent.server"):
        main(["--host", "0.0.0.0", "--ui-dir", str(tmp_path)])  # noqa: S104

    assert "--allowed-host" in caplog.text


def test_naming_the_host_turns_the_check_back_on(monkeypatch, tmp_path: Path, caplog) -> None:
    captured: dict[str, Any] = {}

    def remember(*args: Any, **kwargs: Any) -> FakeHttpd:
        captured.update(kwargs)
        return FakeHttpd()

    monkeypatch.setattr("buy_agent.server.create_server", remember)

    with caplog.at_level(logging.WARNING, logger="buy_agent.server"):
        main(["--host", "0.0.0.0", "--allowed-host", "buy.lan", "--ui-dir", str(tmp_path)])  # noqa: S104

    assert captured["allowed_hosts"] == frozenset({"buy.lan"})
    assert "--allowed-host" not in caplog.text


def test_a_built_ui_is_not_warned_about(monkeypatch, tmp_path: Path, caplog) -> None:
    monkeypatch.setattr("buy_agent.server.create_server", lambda *a, **k: FakeHttpd())
    (tmp_path / "index.html").write_text("<app-root></app-root>", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="buy_agent.server"):
        main(["--ui-dir", str(tmp_path)])

    assert "npm run build" not in caplog.text


def test_the_log_relay_is_taken_off_the_package_logger_on_the_way_out(
    monkeypatch, tmp_path: Path
) -> None:
    """It is installed for the streams' benefit; leaving it on leaks every later run."""
    monkeypatch.setattr("buy_agent.server.create_server", lambda *a, **k: FakeHttpd())
    package_logger = logging.getLogger("buy_agent")

    main(["--ui-dir", str(tmp_path)])

    assert _relay not in package_logger.handlers


def test_a_relay_whose_reader_has_gone_does_not_break_the_run(monkeypatch, caplog) -> None:
    """The stream is a bystander: a broken relay must not take the search down."""
    handled: list[str] = []

    class RefusingQueue:
        def put(self, _item):
            raise RuntimeError("nobody is reading this")

    monkeypatch.setattr(
        type(_relay), "handleError", lambda self, record: handled.append(record.getMessage())
    )
    package_logger = logging.getLogger("buy_agent")
    package_logger.addHandler(_relay)
    _relay.attach(RefusingQueue())
    try:
        with caplog.at_level(logging.INFO, logger="buy_agent"):
            logging.getLogger("buy_agent.stub").info("still working")
    finally:
        _relay.detach()
        package_logger.removeHandler(_relay)

    assert handled == ["still working"], "the failure was reported, not raised"


def test_a_body_nobody_is_left_to_read_is_not_an_error() -> None:
    """The client closing mid-response is ordinary, not something to raise over."""

    class BrokenPipe:
        def write(self, _data):
            raise BrokenPipeError("client went away")

    handler = BuyAgentHandler.__new__(BuyAgentHandler)
    handler.command = "GET"
    handler.close_connection = False
    handler.wfile = BrokenPipe()
    handler.send_response = lambda *_a: None
    handler.send_header = lambda *_a: None
    handler.end_headers = lambda: None

    handler._send_bytes(200, b"body", "text/plain")


def test_a_client_that_left_before_the_headers_never_starts_a_search() -> None:
    """A run takes a minute; there is no point spending one on a dead connection."""

    def gone(*_args):
        raise BrokenPipeError("client went away")

    def never(_config):
        raise AssertionError("the agent was built for a client that had already gone")

    handler = BuyAgentHandler.__new__(BuyAgentHandler)
    handler.agent_factory = never
    handler.send_response = gone

    handler._stream_search({"request": "kettle"})
