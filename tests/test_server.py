"""The HTTP layer: routing, status codes, the event stream and the static app."""

from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from buy_agent.agent import OllamaUnavailableError
from buy_agent.models import Product, RankedProduct
from buy_agent.search import SearchError
from buy_agent.server import build_parser, create_server, main

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

    def __init__(self, config):
        StubAgent.captured["config"] = config

    def run(self, request, *, sort_by="score"):
        StubAgent.captured["request"] = request
        StubAgent.captured["sort_by"] = sort_by
        logging.getLogger("buy_agent.stub").info("Searching for %s", request)
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
    httpd = create_server("127.0.0.1", 0, ui_dir=tmp_path, agent_factory=StubAgent)
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
    StubAgent.result = OllamaUnavailableError("Start it with:  ollama serve")
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
    StubAgent.result = OllamaUnavailableError("Start it with:  ollama serve")
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


def test_two_streams_do_not_see_each_others_progress(server: str) -> None:
    """Log lines are routed by the thread that produced them."""
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
    httpd = create_server("127.0.0.1", 0, ui_dir=tmp_path, agent_factory=StubAgent)
    thread = threading.Thread(target=httpd.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    host, port = httpd.server_address[:2]
    base = f"http://{host}:{port}"
    try:
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
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


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
