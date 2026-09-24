"""The HTTP layer: routing, status codes, the event stream and the static app."""

from __future__ import annotations

import errno
import json
import logging
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request
import queue
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import copy_context
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

import pytest

import buy_agent.server as server_module
from buy_agent.agent import ModelUnavailableError, every_step_passes
from buy_agent.models import Product, nothing_recorded
from tests.conftest import Photographer, needs_ap2, ranked_product
from buy_agent.providers import OLLAMA, VLLM
from buy_agent.screenshots import INSTALL, Camera, ScreenshotError
from buy_agent.search import SearchError
from buy_agent.server import (
    _CROSS_SITE,
    DEFAULT_UI_DIR,
    _KEEPALIVE_SECONDS,
    _LOOPBACK_HOSTS,
    _SECURITY_HEADERS,
    BuyAgentHandler,
    _bound_host,
    _family_for,
    _hostname,
    _relay,
    _workspace_for,
    allowed_hosts_for,
    build_parser,
    camera_for,
    create_server,
    main,
)

RANKED = [
    ranked_product(Product(name="Sony WH-1000XM5", price=328.0, rating=4.7), score=0.9, rank=1),
    ranked_product(Product(name="Anker Q30", price=79.0), score=0.7, rank=2),
]


class StubAgent:
    """Stands in for BuyAgent: logs a line, then answers with whatever it was given."""

    captured: dict[str, Any] = {}
    result: Any = RANKED
    #: Seconds to spend saying nothing, standing in for the extraction step.
    delay: float = 0.0

    def __init__(self, config):
        StubAgent.captured["config"] = config

    def run(
        self,
        request,
        *,
        sort_by="score",
        checkpoint=every_step_passes,
        record=nothing_recorded,
    ):
        StubAgent.captured["request"] = request
        StubAgent.captured["sort_by"] = sort_by
        logging.getLogger("buy_agent.stub").info("Searching for %s", request)
        time.sleep(StubAgent.delay)
        # A second line on the far side of the delay: with two runs overlapping,
        # this is the one produced while both of them have a queue attached.
        logging.getLogger("buy_agent.stub").info("Ranking what came back for %s", request)
        # Where the real pipeline asks whether anyone is still reading.
        checkpoint("extract")
        StubAgent.captured["reached"] = "extract"
        if isinstance(StubAgent.result, BaseException):
            raise StubAgent.result
        return StubAgent.result


@pytest.fixture
def server(tmp_path: Path) -> Iterator[str]:
    """A live server on a loopback port, with the agent stubbed out."""
    StubAgent.captured = {}
    StubAgent.result = RANKED
    StubAgent.delay = 0.0
    with serving(tmp_path) as base:
        yield base


def unbuilt_workspace(tmp_path: Path) -> Path:
    """A ``--ui-dir`` shaped like a real one: an Angular workspace with no build in it."""
    workspace = tmp_path / "ui"
    workspace.mkdir()
    (workspace / "package.json").write_text("{}", encoding="utf-8")
    build = workspace / "dist" / "ui" / "browser"
    build.parent.mkdir(parents=True)
    return build


@contextmanager
def serving(
    ui_dir: Path,
    allowed_hosts: frozenset[str] | None = _LOOPBACK_HOSTS,
    camera: Any = None,
) -> Iterator[str]:
    """Run a server on a loopback port for the duration of the block."""
    httpd = create_server(
        "127.0.0.1",
        0,
        ui_dir=ui_dir,
        agent_factory=StubAgent,
        allowed_hosts=allowed_hosts,
        camera=camera,
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
    """Send a request urllib refuses to build, and read the whole reply."""
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


def smuggled(base: str, request: bytes, expected: str, note: str) -> None:
    """Send a request whose body this server will never read, and check it stopped."""
    parsed = urlparse(base)
    with socket.create_connection((parsed.hostname, parsed.port), timeout=10) as sock:
        sock.sendall(request)
        text = read_all(sock).decode("utf-8", "replace")

    assert expected in text.splitlines()[0]
    assert "Connection: close" in text
    assert text.count("HTTP/1.1 ") == 1, note


def ask(base: str, path: str = "/api/config", **headers: str) -> str:
    """Send a GET with exactly the headers given, and read the whole reply."""
    sent = {"Host": urlparse(base).netloc, "Connection": "close"}
    sent |= {name.replace("_", "-"): value for name, value in headers.items()}
    lines = [f"GET {path} HTTP/1.1", *(f"{k}: {v}" for k, v in sent.items())]
    return raw(base, ("\r\n".join(lines) + "\r\n\r\n").encode())


def until(ready, timeout: float = 5.0) -> bool:
    """Wait for something a worker thread does, without sleeping a fixed guess."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ready():
            return True
        time.sleep(0.01)
    return False


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


def test_config_ships_the_ranges_the_form_holds_its_numbers_to(server: str) -> None:
    """So a field can refuse 51 products without a second copy of the bounds."""
    status, payload = get(f"{server}/api/config")

    assert status == 200
    assert payload["limits"]["results"] == {"min": 1, "max": 50}


def test_sources_says_what_is_wrong_with_a_field_without_running_anything(
    server: str,
) -> None:
    """The one endpoint that starts nothing: it reads the field the way a run
    would and answers, so the page never opens a stream to be told (ADR-0033)."""
    status, payload = get(f"{server}/api/sources?sources=Marques+Brownlee")

    assert status == 200
    assert payload["sources"] == "Marques Brownlee"
    assert "does not name a source" in payload["error"]
    assert "request" not in StubAgent.captured


def test_sources_answers_200_for_a_field_that_names_sources(server: str) -> None:
    """Asking whether this parses is a question that was answered either way."""
    status, payload = get(f"{server}/api/sources?sources=rtings.com+@mkbhd")

    assert status == 200
    assert payload == {"sources": "rtings.com @mkbhd", "error": ""}


def test_sources_with_nothing_to_check_is_the_whole_web(server: str) -> None:
    status, payload = get(f"{server}/api/sources")

    assert status == 200
    assert payload == {"sources": "", "error": ""}


def test_bounds_answers_what_the_request_asks_for_in_words(server: str) -> None:
    """The second endpoint that starts nothing, and the only one that answers with a
    value rather than a verdict (ADR-0059)."""
    status, payload = get(f"{server}/api/bounds?request=headphones+under+%24200")

    assert status == 200
    assert payload["request"] == "headphones under $200"
    assert [bound["bound"] for bound in payload["noticed"]] == ["max_price"]
    assert "request" not in StubAgent.captured


def test_bounds_with_nothing_to_read_answers_nothing(server: str) -> None:
    status, payload = get(f"{server}/api/bounds")

    assert status == 200
    assert payload == {"request": "", "noticed": []}


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


def test_reordering_a_finished_run_never_reaches_the_agent(server: str) -> None:
    """The point of the endpoint: the browser posts back what it is already holding and
    Python reorders it, so nothing searches, fetches or extracts (ADR-0035)."""
    found = post(f"{server}/api/search", {"request": "headphones", "top": 1})[1]
    StubAgent.captured.clear()

    status, payload = post(
        f"{server}/api/rank",
        {
            "request": found["request"],
            "products": found["products"],
            "sort_by": "price",
            "top": found["top_n"],
        },
    )

    assert status == 200
    assert [p["name"] for p in payload["products"]] == ["Anker Q30", "Sony WH-1000XM5"]
    assert payload["sort_by"] == "price"
    assert payload["top_n"] == 1
    assert StubAgent.captured == {}, "no run was started to reorder what was there"


def test_a_reorder_of_something_that_is_not_a_run_is_refused(server: str) -> None:
    status, payload = post(f"{server}/api/rank", {"products": ["Sony"]})

    assert status == 400
    assert payload["field"] == "products"


def test_a_get_that_raises_is_answered_rather_than_dropped(server: str, monkeypatch) -> None:
    """The reason ``do_POST`` has a catch-all, on the half that had none."""

    def explode(**_whatever_the_server_has) -> dict:
        raise ValueError("Unknown provider 'olama'; expected one of ollama, vllm.")

    monkeypatch.setattr(server_module, "defaults_payload", explode)

    status, payload = get(f"{server}/api/config")

    assert status == 500
    assert "olama" in payload["error"], "the reason is what makes a 500 worth reading"


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


def test_a_post_with_no_length_header_at_all_is_read_as_an_empty_object(
    server: str,
) -> None:
    """The header absent, rather than present and zero -- which is what a client that
    meant to send nothing actually sends."""
    StubAgent.result = ValueError("Nothing to shop for: the request is empty.")

    reply = raw(server, b"POST /api/search HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")

    assert "400" in reply.splitlines()[0]
    assert "Nothing to shop for" in reply


# -- who is allowed to ask -----------------------------------------------------


def test_a_page_on_another_site_cannot_start_a_run(server: str) -> None:
    """The reply it cannot read is not the point -- the run happening is."""
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
    """An <img> or an <iframe> pointed here sends no Origin, only fetch metadata."""
    reply = ask(server, "/api/search/stream?request=headphones", Sec_Fetch_Site="cross-site")

    assert "403" in reply.splitlines()[0]
    assert "request" not in StubAgent.captured


def test_a_refusal_names_the_header_that_decided_it(server: str, caplog) -> None:
    """Origin is the value a reader reaches for, and on this path there is none."""
    with caplog.at_level(logging.WARNING, logger="buy_agent.server"):
        assert "403" in ask(server, Sec_Fetch_Site="cross-site").splitlines()[0]

    assert "origin None" in caplog.text
    assert "fetch site 'cross-site'" in caplog.text


def test_a_refusal_names_the_host_when_the_host_is_what_decided_it(
    server: str, caplog
) -> None:
    """The other header a refusal can turn on, and the one a reader least expects."""
    with caplog.at_level(logging.WARNING, logger="buy_agent.server"):
        assert "403" in ask(server, Host="attacker.example").splitlines()[0]

    assert "host 'attacker.example'" in caplog.text


def test_a_refusal_says_what_was_asked_for(server: str, caplog) -> None:
    """One line per refused request, and a run of them is a scan."""
    with caplog.at_level(logging.WARNING, logger="buy_agent.server"):
        ask(server, "/api/models?provider=ollama", Sec_Fetch_Site="cross-site")

    assert "Refused a GET /api/models?provider=ollama" in caplog.text


def test_the_dev_servers_proxy_is_not_a_foreign_site(server: str) -> None:
    """`npm start` serves the app on :4200 and proxies /api here, Origin and all."""
    reply = ask(server, Origin="http://localhost:4200", Sec_Fetch_Site="same-origin")

    assert "200" in reply.splitlines()[0]
    assert OLLAMA.model in reply
    # Ports do not make a site, so a loopback page calling this one directly -- not
    # through the proxy -- reports same-site.
    assert "200" in ask(
        server, Origin="http://127.0.0.1:4200", Sec_Fetch_Site="same-site"
    ).splitlines()[0]
    assert _CROSS_SITE == "cross-site"


def test_an_opaque_origin_is_refused(server: str) -> None:
    """A sandboxed iframe posts as "null"; the app is served from a real origin."""
    assert "403" in ask(server, Origin="null").splitlines()[0]


def test_a_client_that_is_not_a_browser_is_answered(server: str) -> None:
    """curl and the scripts POST /api/search was shaped for send none of this."""
    reply = ask(server)

    assert "200" in reply.splitlines()[0]
    assert OLLAMA.model in reply


def test_a_name_that_merely_resolves_here_is_not_answered(server: str) -> None:
    """DNS rebinding: evil.example re-points at 127.0.0.1 and is then same-origin."""
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
    """The fourth way, and the one that needs no Content-Length at all."""
    smuggled(
        server,
        b"POST /api/search HTTP/1.1\r\nHost: 127.0.0.1\r\n"
        b"Transfer-Encoding: chunked\r\n\r\n"
        b"27\r\nGET /api/config HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n0\r\n\r\n",
        "411",
        "the smuggled request was answered",
    )


def test_a_chunked_request_never_reaches_the_agent(server: str) -> None:
    """Refused before the run, not after: the options in it were never read."""
    StubAgent.captured.clear()

    reply = raw(
        server,
        b"POST /api/search HTTP/1.1\r\nHost: 127.0.0.1\r\n"
        b"Transfer-Encoding: chunked\r\n\r\n0\r\n\r\n",
    )

    assert "411" in reply.splitlines()[0]
    assert "Send a body with a Content-Length; chunked is not read here." in reply, (
        "411 alone is a client author guessing which of the two framings to use"
    )
    assert StubAgent.captured == {}


def test_a_negative_content_length_does_not_desync_the_connection(server: str) -> None:
    """The third way to declare a body this loop will never read."""
    smuggled(
        server,
        b"POST /api/search HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: -1\r\n\r\n"
        b"GET /api/config HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n",
        "400",
        "the smuggled request was answered",
    )


def read_all(sock: socket.socket) -> bytes:
    """Read a reply to the end, so closing the socket sends a FIN and not a reset."""
    reply = b""
    while chunk := sock.recv(4096):
        reply += chunk
    return reply


def wait_until(settled: Callable[[], bool], limit: float = 5.0) -> bool:
    """Poll until the condition holds, or give up."""
    deadline = time.monotonic() + limit
    while not settled() and time.monotonic() < deadline:
        time.sleep(0.02)
    return settled()


def test_a_handler_does_not_wait_on_a_stalled_client_for_ever(
    server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A body announced and never sent parks the thread reading it."""
    monkeypatch.setattr(BuyAgentHandler, "timeout", 0.5)
    before = threading.active_count()
    parsed = urlparse(server)

    with socket.create_connection((parsed.hostname, parsed.port), timeout=10) as sock:
        sock.sendall(
            b"POST /api/search HTTP/1.1\r\nHost: 127.0.0.1\r\n"
            b"Content-Length: 500\r\n\r\n{"
        )
        assert wait_until(lambda: threading.active_count() > before), (
            "nothing is holding the connection"
        )
        reply = sock.recv(4096).decode("utf-8", "replace")

    # Answered rather than dropped, and answered as itself: without the clause
    # that names it, the timeout reached ``do_POST``'s catch-all and was logged
    # as an "Unexpected failure during a search" over a traceback.
    assert "408" in reply.splitlines()[0]
    assert "Connection: close" in reply

    assert wait_until(lambda: threading.active_count() == before), (
        "the handler thread was never reclaimed"
    )


def test_a_client_that_is_merely_slow_still_gets_its_answer(
    server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The timeout bounds one blocking read, not a request and not a run."""
    monkeypatch.setattr(BuyAgentHandler, "timeout", 0.5)
    body = json.dumps({"products": [{"name": "Sony WH-1000XM5"}]}).encode()
    parsed = urlparse(server)

    with socket.create_connection((parsed.hostname, parsed.port), timeout=10) as sock:
        sock.sendall(
            b"POST /api/rank HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n"
        )
        # Longer than half the timeout above, so a wait per read is what is being
        # measured rather than a wait per request.
        time.sleep(0.3)
        sock.sendall(body)
        reply = read_all(sock).decode("utf-8", "replace")

    assert "200" in reply.splitlines()[0]


def test_a_path_that_cannot_name_a_file_is_answered_not_dropped(tmp_path: Path) -> None:
    """An encoded NUL cannot name a file, and it is answered rather than dropped."""
    (tmp_path / "index.html").write_text("<app-root></app-root>", encoding="utf-8")

    with serving(tmp_path) as base:
        reply = ask(base, "/main%00.js")

    assert "200" in reply.splitlines()[0]
    assert "<app-root>" in reply


def test_a_path_the_platform_refuses_to_resolve_is_answered_not_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same answer, with the failure provoked rather than hoped for."""
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
    """What a bind to a public interface gets, since its name is not ours to guess."""
    (tmp_path / "index.html").write_text("<app-root></app-root>", encoding="utf-8")

    with serving(tmp_path, allowed_hosts=None) as base:
        assert "200" in ask(base, "/", Host="buy.lan").splitlines()[0]
        assert "403" in ask(base, "/", Host="buy.lan", Sec_Fetch_Site="cross-site").splitlines()[0]


def test_the_apps_own_page_is_answered_whatever_the_server_is_called(tmp_path: Path) -> None:
    """A public bind is reached by a name that is not loopback, and it still works."""
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
    """The name that reaches a public interface is the operator's to know."""
    assert allowed_hosts_for("192.168.1.5") is None
    assert allowed_hosts_for("192.168.1.5", ["buy.lan:8000"]) == frozenset({"buy.lan"})
    assert allowed_hosts_for("127.0.0.1", ["  "]) == _LOOPBACK_HOSTS


def test_an_address_typed_at_the_command_line_is_read_without_its_brackets() -> None:
    """``--host`` is not a ``Host`` header: an address bar brackets an IPv6 literal and a
    command line does not, so the colons in a bare ``::1`` are the address rather than a
    port separator."""
    assert _bound_host("127.0.0.1") == "127.0.0.1"
    assert _bound_host("buy.lan:8000") == "buy.lan"
    assert _bound_host("::1") == "::1"
    assert _bound_host("::") == "::"
    assert _bound_host("[::1]") == "::1"
    assert _bound_host(" LOCALHOST ") == "localhost"
    assert _bound_host("") == ""


def test_the_ipv6_loopback_is_a_loopback_bind_like_any_other() -> None:
    """Read as a ``Host`` header it split at the first colon and named nothing, so the
    one address ``_LOOPBACK_HOSTS`` spells out was the one bind classed as public --
    which turns the ``Host`` check off and says so at startup (ADR-0018)."""
    assert allowed_hosts_for("::1") == _LOOPBACK_HOSTS
    assert allowed_hosts_for("::") is None, "every interface is every interface"


def test_a_named_host_that_names_nothing_is_dropped_rather_than_allowed() -> None:
    """An entry that cannot be read comes to ``""``, which is also what a request sending
    no ``Host`` at all arrives as -- so allowing it inverted the flag: the host somebody
    named was refused and the one nobody named was let in."""
    assert allowed_hosts_for("0.0.0.0", ["::1"]) == frozenset({"::1"})
    assert allowed_hosts_for("0.0.0.0", [":8000"]) is None
    assert "" not in (allowed_hosts_for("127.0.0.1", [":8000"]) or frozenset())


def test_a_request_sending_no_host_is_refused_by_a_server_that_names_one(
    tmp_path: Path,
) -> None:
    """The other half of that, over the wire: urllib will not build a request without a
    ``Host``, so this one is spoken by hand."""
    (tmp_path / "index.html").write_text("<app-root></app-root>", encoding="utf-8")

    with serving(tmp_path, allowed_hosts=allowed_hosts_for("0.0.0.0", ["::1"])) as base:
        assert "403" in raw(base, b"GET /api/config HTTP/1.0\r\n\r\n").splitlines()[0]


def test_an_ipv6_address_is_bound_on_the_family_it_needs() -> None:
    """``ThreadingHTTPServer`` is ``AF_INET`` and nothing else, so every IPv6 bind failed
    outright -- including the ``::1`` ``_browsable_url`` is written to print."""
    assert _family_for("127.0.0.1") is socket.AF_INET
    assert _family_for("0.0.0.0") is socket.AF_INET
    assert _family_for("::1") is socket.AF_INET6
    assert _family_for("::") is socket.AF_INET6


def test_the_server_it_builds_is_bound_on_that_family(tmp_path: Path) -> None:
    """Read off the socket the server opened, not off the class: the family is chosen per
    instance, where the base class holds one for every server there is."""
    built = create_server("127.0.0.1", 0, ui_dir=tmp_path)
    try:
        assert built.socket.family is socket.AF_INET
    finally:
        built.server_close()


# -- the event stream ----------------------------------------------------------


def test_the_stream_relays_progress_then_the_result(server: str) -> None:
    stream = events(f"{server}/api/search/stream?request=headphones&top=2")
    kinds = [name for name, _ in stream]
    assert kinds[-1] == "result"
    assert "log" in kinds

    logs = [data for name, data in stream if name == "log"]
    assert any("Searching for headphones" in entry["message"] for entry in logs)
    assert {"time", "level", "logger", "message"} == set(logs[0])


def test_every_relayed_line_is_timed(server: str) -> None:
    """The panel is showing the CLI's own lines, and the CLI times them."""
    stream = events(f"{server}/api/search/stream?request=headphones")

    logs = [data for name, data in stream if name == "log"]
    assert logs
    for entry in logs:
        assert re.fullmatch(r"\d{2}:\d{2}:\d{2}", entry["time"]), entry

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


def test_a_paying_rail_with_no_address_is_refused_at_the_box_it_came_from(
    server: str,
) -> None:
    """The form can make this one: pick the rail, leave the address empty."""
    name, data = events(
        f"{server}/api/search/stream?request=headphones&pay=true&rail=http"
    )[-1]

    assert (name, data["status"]) == ("failure", 400)
    assert data["field"] == "merchant_url"
    assert "needs an address" in data["error"]
    assert "request" not in StubAgent.captured, "nothing was run for a setting like this"


def test_an_unexpected_failure_still_ends_the_stream(server: str) -> None:
    """The stream reports; it never leaves the browser waiting on a dead run."""
    StubAgent.result = RuntimeError("something nobody predicted")
    name, data = events(f"{server}/api/search/stream?request=headphones")[-1]
    assert (name, data["status"]) == ("failure", 500)
    assert "something nobody predicted" in data["error"]


def test_an_unexpected_failure_is_a_500_and_not_a_dropped_connection(server: str) -> None:
    """The one-shot endpoint answers what the stream answers."""
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
    """A body refused unread would otherwise be parsed as the next request."""
    smuggled(
        server,
        b"POST /api/search HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: "
        + length
        + b"\r\n\r\n{}",
        expected,
        "the leftover body was parsed as a request",
    )


def test_two_streams_do_not_see_each_others_progress(server: str) -> None:
    """Log lines are routed by the context the run is being watched through, and a worker
    thread begins in one of its own."""
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
    """Extraction is slow and logs nothing, so a real run goes quiet for a minute."""
    monkeypatch.setattr("buy_agent.server._KEEPALIVE_SECONDS", 0.05)
    StubAgent.delay = 0.4

    collected = events(f"{server}/api/search/stream?request=kettle")
    names = [name for name, _ in collected]

    assert ("ping", {}) in collected, "a quiet stream sent nothing for 0.4s"
    assert names[-1] == "result", "a ping is never the last word"
    assert "failure" not in names, "a quiet stretch is not a failure"
    # And the stream goes on relaying afterwards.
    after_the_first_ping = names[names.index("ping") :]
    assert "log" in after_the_first_ping, "the progress stopped at the first ping"


def test_the_keepalive_is_frequent_enough_to_be_worth_sending() -> None:
    """The mechanism is only useful if the interval beats what gives up on silence."""
    assert 0 < _KEEPALIVE_SECONDS <= 30


def test_a_stream_nobody_is_reading_is_abandoned(server: str, monkeypatch, caplog) -> None:
    """A frame that cannot be written is the only notice the loop gets."""
    monkeypatch.setattr(BuyAgentHandler, "_send_event", lambda self, event, data: False)

    with caplog.at_level(logging.INFO, logger="buy_agent.server"):
        with urllib.request.urlopen(
            f"{server}/api/search/stream?request=kettle", timeout=10
        ) as response:
            assert response.read() == b"", "nothing more is written to a dead socket"

    assert "Client disconnected" in caplog.text


def test_a_stream_nobody_is_reading_stops_the_run(server: str, monkeypatch, caplog) -> None:
    """Stop means stop, at the next step boundary the pipeline reaches (ADR-0034)."""
    monkeypatch.setattr(BuyAgentHandler, "_send_event", lambda self, event, data: False)
    StubAgent.delay = 0.3

    with caplog.at_level(logging.INFO, logger="buy_agent"):
        with urllib.request.urlopen(
            f"{server}/api/search/stream?request=kettle", timeout=10
        ) as response:
            response.read()
        assert until(lambda: "Run stopped before extract" in caplog.text), caplog.text

    assert "reached" not in StubAgent.captured, "the step after the boundary still ran"


def test_a_run_nobody_stopped_passes_every_boundary(server: str) -> None:
    """The other side of the same check: a reader who stays gets the whole run."""
    collected = events(f"{server}/api/search/stream?request=kettle")

    assert [name for name, _ in collected][-1] == "result"
    assert StubAgent.captured["reached"] == "extract"


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


def test_an_unbuilt_ui_says_how_to_build_it(tmp_path: Path) -> None:
    with serving(unbuilt_workspace(tmp_path)) as server:
        status, payload = get(f"{server}/")

    assert status == 503
    assert "npm run build" in payload["error"]


def test_an_unbuilt_ui_says_it_to_a_browser_as_a_page(tmp_path: Path) -> None:
    """The one client that matters here, and the one that cannot read JSON."""
    with serving(unbuilt_workspace(tmp_path)) as server:
        status, page = _call(
            urllib.request.Request(f"{server}/", headers={"Accept": "text/html,*/*;q=0.8"})
        )

    assert status == 503
    assert page.startswith("<!doctype html>")
    assert "npm install &amp;&amp; npm run build" in page
    # No script and nothing from another origin: the CSP that goes out with it is
    # the app's own, and a page it refuses to draw says nothing to anybody.
    assert "<script" not in page


def test_the_directory_to_run_npm_in_is_the_workspace() -> None:
    """Not the build's own parent, which is where this message used to send people."""
    workspace = _workspace_for(DEFAULT_UI_DIR)

    assert workspace is not None
    assert (workspace / "package.json").is_file(), "the directory npm install needs"
    assert (workspace / "angular.json").is_file(), "...and npm run build"


def test_a_ui_dir_with_no_workspace_above_it_is_not_told_to_build(tmp_path: Path) -> None:
    """A remedy nobody can follow is worse than none."""
    assert _workspace_for(tmp_path) is None

    with serving(tmp_path) as server:
        status, payload = get(f"{server}/")

    assert status == 503
    said = payload["error"]
    assert str(tmp_path) in said
    assert "--ui-dir" in said
    assert "npm" not in said, f"nothing to run npm in, so nothing said about npm: {said}"


def test_the_page_for_a_ui_dir_with_no_workspace_drops_the_command_too(
    tmp_path: Path,
) -> None:
    """The browser's half of the same: no command block, because there is no command."""
    with serving(tmp_path) as server:
        status, page = _call(
            urllib.request.Request(f"{server}/", headers={"Accept": "text/html"})
        )

    assert status == 503
    assert "npm" not in page
    assert "<pre>" not in page
    assert escape(str(tmp_path)) in page


def test_a_ui_dir_a_reader_names_is_escaped_into_the_page(tmp_path: Path) -> None:
    """The path in that sentence is somebody's argument, and this is HTML."""
    # Left uncreated on purpose: Windows refuses a filename holding '<' or '>',
    # and an unbuilt --ui-dir is a path that need not be there anyway -- which is
    # the whole of what this page is for. The characters have to be these ones,
    # so the directory is the half that gives.
    awkward = tmp_path / "a<b>&c"

    with serving(awkward) as server:
        _, page = _call(urllib.request.Request(f"{server}/", headers={"Accept": "text/html"}))

    assert "a<b>&c" not in page
    assert "a&lt;b&gt;&amp;c" in page


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
    """On Windows ``mimetypes`` reads the registry and can call a .js text/plain."""
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


def _refusing(reason: int):
    """A ``create_server`` that fails the way a real bind fails: with an errno."""

    def refuse(*args, **kwargs):
        raise OSError(reason, os.strerror(reason))

    return refuse


def test_a_port_that_cannot_be_bound_is_reported_not_raised(monkeypatch) -> None:
    monkeypatch.setattr("buy_agent.server.create_server", _refusing(errno.EADDRINUSE))
    assert main(["--port", "8000"]) == 1


def test_the_port_a_model_server_also_wants_is_named_in_the_refusal(
    monkeypatch, caplog
) -> None:
    """vLLM's own default is http://localhost:8000/v1 and this server's default
    port is 8000, so the two collide on exactly the machine that runs both --
    and "Address already in use" says nothing about a model server."""
    monkeypatch.setattr("buy_agent.server.create_server", _refusing(errno.EADDRINUSE))

    with caplog.at_level(logging.ERROR, logger="buy_agent.server"):
        main(["--port", "8000"])

    assert "vLLM" in caplog.text
    assert "--port 8001" in caplog.text


def test_a_port_nobody_else_claims_is_refused_without_the_aside(monkeypatch, caplog) -> None:
    monkeypatch.setattr("buy_agent.server.create_server", _refusing(errno.EADDRINUSE))

    with caplog.at_level(logging.ERROR, logger="buy_agent.server"):
        main(["--port", "8123"])

    assert "vLLM" not in caplog.text


def test_a_bind_that_failed_for_another_reason_is_not_told_to_change_port(
    monkeypatch, caplog
) -> None:
    """The aside is a remedy for one failure and was printed for every one. A --host
    that names nothing fails the same bind with the same port in the message, and
    "serve the UI somewhere else: --port 8001" sent the reader to change the half
    that was right -- the reading providers._answered_by already makes about a hint
    naming a model when the address is what is wrong."""
    monkeypatch.setattr("buy_agent.server.create_server", _refusing(errno.EADDRNOTAVAIL))

    with caplog.at_level(logging.ERROR, logger="buy_agent.server"):
        assert main(["--port", "8000"]) == 1

    assert "vLLM" not in caplog.text
    assert "--port 8001" not in caplog.text


@pytest.mark.parametrize(
    ("given", "says"),
    [
        ("99999", "between 0 and 65535"),
        ("-1", "between 0 and 65535"),
        ("0.5", "a whole number"),
    ],
)
def test_a_port_no_socket_could_take_is_a_usage_error(given: str, says: str, capsys) -> None:
    """The rule ``__main__._bounded`` holds for every number the agent takes. Left
    unbounded, the number went as far as ``socket.bind``, whose ``OverflowError`` is
    not the ``OSError`` main() reports a refused bind with -- so a mistyped port was
    the one thing this file's catch-alls exist to prevent: a traceback."""
    with pytest.raises(SystemExit) as exit_code:
        main(["--port", given])

    assert exit_code.value.code == 2
    assert says in capsys.readouterr().err


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
        main(["--ui-dir", str(unbuilt_workspace(tmp_path))])

    assert "npm run build" in caplog.text


def test_a_public_bind_with_no_named_host_is_warned_about(
    monkeypatch, tmp_path: Path, caplog
) -> None:
    """Binding 0.0.0.0 turns the rebinding check off, so it is said out loud."""
    monkeypatch.setattr("buy_agent.server.create_server", lambda *a, **k: FakeHttpd())

    with caplog.at_level(logging.WARNING, logger="buy_agent.server"):
        main(["--host", "0.0.0.0", "--ui-dir", str(tmp_path)])

    assert "--allowed-host" in caplog.text


def test_naming_the_host_turns_the_check_back_on(monkeypatch, tmp_path: Path, caplog) -> None:
    captured: dict[str, Any] = {}

    def remember(*args: Any, **kwargs: Any) -> FakeHttpd:
        captured.update(kwargs)
        return FakeHttpd()

    monkeypatch.setattr("buy_agent.server.create_server", remember)

    with caplog.at_level(logging.WARNING, logger="buy_agent.server"):
        main(["--host", "0.0.0.0", "--allowed-host", "buy.lan", "--ui-dir", str(tmp_path)])

    assert captured["allowed_hosts"] == frozenset({"buy.lan"})
    assert "--allowed-host" not in caplog.text


@pytest.mark.parametrize(
    ("bind", "expected"),
    [
        ("127.0.0.1", "http://127.0.0.1:8000"),
        # An address to listen on rather than one to visit, and a browser given it
        # has nowhere to go; ::1 needs the brackets an address bar reads it by.
        ("0.0.0.0", "http://127.0.0.1:8000"),
        ("::", "http://[::1]:8000"),
        ("::1", "http://[::1]:8000"),
    ],
)
def test_the_url_it_prints_is_one_a_browser_can_open(
    monkeypatch, tmp_path: Path, caplog, bind: str, expected: str
) -> None:
    class Bound(FakeHttpd):
        server_address = (bind, 8000)

    monkeypatch.setattr("buy_agent.server.create_server", lambda *a, **k: Bound())

    with caplog.at_level(logging.INFO, logger="buy_agent.server"):
        main(["--host", bind, "--ui-dir", str(tmp_path)])

    assert expected in caplog.text


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


def inherit(context) -> None:
    """What ``fetch._as_the_caller`` does, for a pool built here to stand in for
    the one ``enrich`` builds."""
    for variable, value in context.items():
        variable.set(value)


def test_a_line_from_a_thread_the_run_started_still_reaches_the_stream() -> None:
    """The relay follows the run, not the thread that happened to log."""
    sink: queue.Queue[Any] = queue.Queue()
    package_logger = logging.getLogger("buy_agent")
    # As a streamed run installs it: progress is logged at INFO, which a logger
    # left at its default drops before any handler sees it.
    server_module._install_relay()

    def run() -> None:
        _relay.attach(sink)
        try:
            # A pool whose workers start in this thread's context, which is what
            # ``enrich`` does and ``tests/test_fetch.py`` holds it to.
            with ThreadPoolExecutor(
                max_workers=2, initializer=inherit, initargs=(copy_context(),)
            ) as pool:
                list(
                    pool.map(
                        lambda url: logging.getLogger("buy_agent.fetch").info(
                            "%s asked to be tried again; waiting %.1fs", url, 3.0
                        ),
                        ["https://a.example", "https://b.example"],
                    )
                )
        finally:
            _relay.detach()

    worker = threading.Thread(target=run)
    worker.start()
    worker.join(timeout=10)
    package_logger.removeHandler(_relay)

    relayed = []
    while not sink.empty():
        relayed.append(sink.get()["message"])
    assert sorted(relayed) == [
        "https://a.example asked to be tried again; waiting 3.0s",
        "https://b.example asked to be tried again; waiting 3.0s",
    ]


def test_a_thread_outside_the_run_is_not_one_of_its_lines() -> None:
    """The other half: a context is what a line belongs to, and a thread that never took
    one carries none."""
    sink: queue.Queue[Any] = queue.Queue()
    package_logger = logging.getLogger("buy_agent")
    # As a streamed run installs it: progress is logged at INFO, which a logger
    # left at its default drops before any handler sees it.
    server_module._install_relay()

    def run() -> None:
        _relay.attach(sink)
        try:
            stranger = threading.Thread(
                target=logging.getLogger("buy_agent.stub").info, args=("somebody else",)
            )
            stranger.start()
            stranger.join(timeout=10)
        finally:
            _relay.detach()

    worker = threading.Thread(target=run)
    worker.start()
    worker.join(timeout=10)
    package_logger.removeHandler(_relay)

    assert sink.empty()


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


def test_the_server_refuses_to_start_on_a_provider_nothing_can_serve(
    monkeypatch, caplog
) -> None:
    """Fail where the shell that set it is still on screen."""
    monkeypatch.setattr(server_module, "DEFAULT_PROVIDER", "olama")

    with caplog.at_level(logging.ERROR):
        assert main([]) == 1

    assert "olama" in caplog.text
    assert "ollama, vllm" in caplog.text, "the servers that do exist are the whole message"


def test_the_server_refuses_to_start_on_a_rail_nothing_can_pay_through(
    monkeypatch, caplog
) -> None:
    """The rail's half of the check above, and there for the same reason."""
    monkeypatch.setattr(server_module, "DEFAULT_RAIL", "dryrun")

    with caplog.at_level(logging.ERROR):
        assert main([]) == 1

    assert "dryrun" in caplog.text
    assert "dry-run, http" in caplog.text, "the rails that do exist are the whole message"


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


# -- paying --------------------------------------------------------------------


PAYABLE_PRODUCT = {
    "name": "Sony WH-1000XM5",
    "price": 329.99,
    "currency": "USD",
    "seller": "AudioSite",
    "url": "https://audiosite.example/xm5",
}

PAY_BODY = {
    "products": [PAYABLE_PRODUCT],
    "rank": 1,
    "approved": {"title": "Sony WH-1000XM5", "price": 329.99, "currency": "USD"},
}


@needs_ap2
def test_paying_answers_a_receipt(server: str) -> None:
    status, body = post(f"{server}/api/pay", PAY_BODY)

    assert status == 200
    assert body["receipt"]["title"] == "Sony WH-1000XM5"


def test_paying_runs_no_pipeline(server: str) -> None:
    """The same line `POST /api/rank` sits on (ADR-0035): the products travel in
    the body because the browser is already holding them."""
    post(f"{server}/api/pay", PAY_BODY)

    assert "reached" not in StubAgent.captured


def test_an_approval_that_does_not_match_is_a_409(server: str) -> None:
    body = {**PAY_BODY, "approved": {**PAY_BODY["approved"], "price": 1.0}}

    status, answer = post(f"{server}/api/pay", body)

    assert status == 409
    assert answer["field"] == "approved"


def test_an_unpayable_product_is_a_400_naming_the_field(server: str) -> None:
    body = {
        "products": [{**PAYABLE_PRODUCT, "price": None, "currency": None}],
        "approved": PAY_BODY["approved"],
    }

    status, answer = post(f"{server}/api/pay", body)

    assert status == 400
    assert answer["field"] == "products"


def test_paying_is_guarded_like_every_other_method(server: str) -> None:
    """`_refused` runs at the top of `do_POST`, so a new endpoint added under it
    is guarded by being there -- this is what says it still is."""
    request = urllib.request.Request(
        f"{server}/api/pay",
        data=json.dumps(PAY_BODY).encode(),
        headers={"Content-Type": "application/json", "Sec-Fetch-Site": "cross-site"},
        method="POST",
    )

    status, _body = _call(request)

    assert status == 403


def test_the_form_is_told_what_this_server_can_pay_through(server: str) -> None:
    _status, body = get(f"{server}/api/config")

    assert body["pay"] is False
    assert [row["name"] for row in body["rail_options"]] == ["dry-run", "http"]


# -- a picture of the page a card links to (ADR-0065) ---------------------------

SHOP = "https://audiosite.example/xm5"


def picture(url: str, **headers: str) -> tuple[int, dict[str, str], bytes]:
    """A GET read as bytes: a JPEG decoded as text is not the JPEG that was sent."""
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def screenshot_of(base: str, address: str) -> str:
    return f"{base}/api/screenshot?{urlencode({'url': address})}"


def test_a_card_is_sent_a_picture_of_its_page(tmp_path: Path) -> None:
    """An ``<img>`` asks for it, so what comes back is the JPEG itself -- with every
    header the rest of this server's answers carry, and a lifetime a browser keeps it for
    so the next run of the same search draws its cards at once."""
    camera = Photographer()
    with serving(tmp_path, camera=camera) as base:
        status, headers, body = picture(screenshot_of(base, SHOP))

    assert status == 200
    assert headers["Content-Type"] == "image/jpeg"
    assert headers["Cache-Control"] == "private, max-age=3600"
    assert body == f"jpeg of {SHOP}".encode()
    assert camera.asked == [SHOP]
    for name, value in _SECURITY_HEADERS:
        assert headers[name] == value


def test_the_form_is_told_this_server_takes_screenshots(tmp_path: Path) -> None:
    with serving(tmp_path, camera=Photographer()) as base:
        _status, with_camera = get(f"{base}/api/config")
    with serving(tmp_path) as base:
        _status, without = get(f"{base}/api/config")

    assert with_camera["screenshots"] is True
    assert without["screenshots"] is False


def test_a_server_with_no_camera_answers_in_json(server: str) -> None:
    status, answer = get(screenshot_of(server, SHOP))

    assert status == 404
    assert "no screenshots" in answer["error"]


def test_a_picture_of_something_that_is_not_a_web_page_is_refused(tmp_path: Path) -> None:
    camera = Photographer()
    with serving(tmp_path, camera=camera) as base:
        status, answer = get(screenshot_of(base, "file:///etc/passwd"))

    assert status == 400
    assert "Not a web page" in answer["error"]
    assert camera.asked == []


def test_a_page_that_would_not_be_photographed_is_a_502(tmp_path: Path) -> None:
    camera = Photographer(ScreenshotError(f"Could not photograph {SHOP}: Timeout 15000ms"))
    with serving(tmp_path, camera=camera) as base:
        status, answer = get(screenshot_of(base, SHOP))

    assert status == 502
    assert "Timeout" in answer["error"]


def test_a_picture_is_guarded_like_every_other_request(tmp_path: Path) -> None:
    """``_refused`` runs at the top of ``do_GET``: a page on another site cannot point
    this server's browser anywhere, which is the rule that makes a camera safe to have."""
    camera = Photographer()
    with serving(tmp_path, camera=camera) as base:
        status, _headers, _body = picture(screenshot_of(base, SHOP), **{"Sec-Fetch-Site": "cross-site"})

    assert status == 403
    assert camera.asked == []


def test_a_server_without_playwright_has_no_camera_and_says_how_to_get_one(
    monkeypatch, caplog
) -> None:
    monkeypatch.setattr(server_module, "available", lambda: False)

    with caplog.at_level(logging.INFO, logger="buy_agent.server"):
        assert camera_for("127.0.0.1") is None

    assert INSTALL in caplog.text


@pytest.mark.parametrize("bind", ["0.0.0.0", "192.168.1.20", "::"])
def test_a_server_the_network_can_reach_takes_no_pictures(monkeypatch, caplog, bind: str) -> None:
    """Bound anywhere but here, anybody who can reach the port could have the browser
    photograph the router's page and hand the picture back."""
    monkeypatch.setattr(server_module, "available", lambda: True)

    with caplog.at_level(logging.WARNING, logger="buy_agent.server"):
        assert camera_for(bind) is None

    assert "Screenshots are off" in caplog.text


@pytest.mark.parametrize("bind", ["127.0.0.1", "localhost", "::1"])
def test_a_server_bound_to_this_machine_has_a_camera(monkeypatch, bind: str) -> None:
    monkeypatch.setattr(server_module, "available", lambda: True)

    camera = camera_for(bind)

    assert isinstance(camera, Camera)


def test_the_camera_is_handed_to_the_server_and_let_go_on_the_way_out(
    monkeypatch, tmp_path: Path
) -> None:
    """Closed with the socket, rather than left to the process exiting under a browser."""
    camera = Photographer()
    captured: dict[str, Any] = {}

    def remember(*args: Any, **kwargs: Any) -> FakeHttpd:
        captured.update(kwargs)
        return FakeHttpd()

    monkeypatch.setattr(server_module, "camera_for", lambda host: camera)
    monkeypatch.setattr(server_module, "create_server", remember)

    assert main(["--ui-dir", str(tmp_path)]) == 0
    assert captured["camera"] is camera
    assert camera.closed
