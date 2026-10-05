"""The benchmark's page and the server behind it (ADR-0070)."""

from __future__ import annotations

import errno
import json
import logging
import os
import re
import socket
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

import pytest

import benchmark.server as server_module
from buy_agent.agent import ModelUnavailableError
from buy_agent.api import ApiError
from buy_agent.server import _SECURITY_HEADERS
from benchmark.board import Board
from benchmark.cases import CASES, HEADPHONES, LAPTOPS, SCRIPTS
from benchmark.compare import CaseRun, Contender, run_case, run_payload
from benchmark.query import QueryCheck, QueryVerdict
from benchmark.scoring import METRICS
from benchmark.server import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    DOING,
    WEB_DIR,
    Bench,
    Job,
    Stopped,
    _names,
    _port,
    build_parser,
    config_payload,
    create_server,
    main,
    read_plan,
)

PERFECT = Contender.scripted("perfect")
TINY = Contender(provider="ollama", model="tiny:1b", base_url="http://127.0.0.1:11434")
FULL = {name: (1, 1) for name in METRICS}


def kept(contender: Contender, case: str) -> CaseRun:
    """A finished run, made to order."""
    return CaseRun(
        contender=contender,
        case=case,
        fingerprint=CASES[case].fingerprint,
        finished="2026-10-02T09:05:30+00:00",
        query=QueryVerdict(query="q", checks=[QueryCheck(check="Keeps 'q'", passed=True)]),
        seconds={"query": 0.5, "extract": 1.5},
        counts=FULL,
    )


class Gate:
    """A run that waits to be let through, so a test can look at a comparison while it
    is under way -- with events, never a clock."""

    def __init__(self, *, fails: dict[str, Exception] | None = None) -> None:
        self.started = threading.Event()
        self.proceed = threading.Event()
        self.asked: list[tuple[str, str]] = []
        self.fails = fails or {}

    def __call__(self, contender: Contender, case: Any, *, checkpoint: Any) -> CaseRun:
        self.asked.append((contender.label, case.name))
        if contender.model in self.fails:
            raise self.fails[contender.model]
        self.started.set()
        assert self.proceed.wait(10), "the test never let the run through"
        checkpoint("extract")
        return kept(contender, case.name)


def ticking(step: float = 1.0):
    """A clock that moves ``step`` seconds every time it is read."""
    now = [0.0]

    def read() -> float:
        now[0] += step
        return now[0]

    return read


@pytest.fixture
def board(tmp_path: Path) -> Board:
    return Board(tmp_path / "board.json")


# -- one comparison at a time --------------------------------------------------


def test_a_comparison_runs_every_contender_over_every_case_in_turn(board: Board) -> None:
    """Contender by contender, so a server loads each model once."""
    gate = Gate()
    gate.proceed.set()
    bench = Bench(board, run=gate, clock=ticking())

    bench.start([PERFECT, TINY], [HEADPHONES, LAPTOPS])

    assert bench.wait(10)
    assert gate.asked == [
        ("perfect (scripted)", "headphones"),
        ("perfect (scripted)", "laptops"),
        ("tiny:1b", "headphones"),
        ("tiny:1b", "laptops"),
    ]
    state = bench.state()
    assert (state["running"], state["done"], state["total"]) == (False, 4, 4)
    assert re.fullmatch(r"Finished 4 of 4 runs in \d+\.\d s\.", state["status"])
    assert [row["label"] for row in state["standings"]] == ["perfect (scripted)", "tiny:1b"]
    assert len(board.runs()) == 4, "each run is kept as it finishes"


def test_a_comparison_says_where_it_has_got_to(board: Board) -> None:
    gate = Gate()
    bench = Bench(board, run=gate, clock=ticking(0.5))

    bench.start([TINY], [LAPTOPS])
    assert gate.started.wait(10)
    gate_state = bench.state()
    gate.proceed.set()
    bench.wait(10)

    assert gate_state["running"] is True
    assert gate_state["stopping"] is False
    assert gate_state["status"].startswith("Running tiny:1b on laptops: refining the query. ")
    assert "0 of 1 runs done" in gate_state["status"]


def test_a_second_comparison_waits_for_the_first(board: Board) -> None:
    gate = Gate()
    bench = Bench(board, run=gate)
    bench.start([TINY], [LAPTOPS])
    assert gate.started.wait(10)

    with pytest.raises(ApiError, match="already running") as refused:
        bench.start([PERFECT], [LAPTOPS])

    assert refused.value.status == 409
    gate.proceed.set()
    bench.wait(10)


def test_stop_ends_a_comparison_at_its_next_step(board: Board) -> None:
    """A question a model is answering cannot be taken back, so the run under way ends at
    the step after it, and is not kept: it never finished."""
    gate = Gate()
    bench = Bench(board, run=gate, clock=ticking())
    bench.start([TINY, PERFECT], [HEADPHONES, LAPTOPS])
    assert gate.started.wait(10)

    stopping = bench.stop()
    gate.proceed.set()
    assert bench.wait(10)

    assert stopping["stopping"] is True
    assert stopping["status"].startswith("Stopping after the step under way (0 of 4 runs done)")
    state = bench.state()
    assert re.fullmatch(r"Stopped after 0 of 4 runs, in \d+\.\d s\.", state["status"])
    assert gate.asked == [("tiny:1b", "headphones")]
    assert board.runs() == []


def test_a_stop_with_nothing_running_changes_nothing(board: Board) -> None:
    bench = Bench(board)

    assert bench.stop()["status"] == ""
    assert bench.wait(0.1)


def test_a_model_that_cannot_be_asked_is_reported_and_its_cases_skipped(board: Board) -> None:
    """Its other cases would fail the same way; the next contender still runs."""
    gate = Gate(fails={"tiny:1b": ModelUnavailableError("Could not reach Ollama.")})
    gate.proceed.set()
    bench = Bench(board, run=gate, clock=ticking())

    bench.start([TINY, PERFECT], [HEADPHONES, LAPTOPS])
    bench.wait(10)
    state = bench.state()

    assert gate.asked == [
        ("tiny:1b", "headphones"),
        ("perfect (scripted)", "headphones"),
        ("perfect (scripted)", "laptops"),
    ]
    assert state["problems"] == ["tiny:1b: Could not reach Ollama."]
    assert (state["done"], state["total"]) == (4, 4)
    assert re.fullmatch(r"Finished 2 of 4 runs, 2 skipped in \d+\.\d s\.", state["status"])
    assert [row["label"] for row in state["standings"]] == ["perfect (scripted)"]


def test_a_failure_nobody_planned_for_ends_the_comparison_on_the_page(
    board: Board, caplog: pytest.LogCaptureFixture
) -> None:
    gate = Gate(fails={"tiny:1b": KeyError("boom")})
    bench = Bench(board, run=gate)

    with caplog.at_level(logging.ERROR, logger="benchmark.server"):
        bench.start([TINY], [HEADPHONES])
        bench.wait(10)

    state = bench.state()
    assert state["status"] == "The comparison failed after 0 of 1 runs."
    assert state["problems"] == ["Unexpected failure: 'boom'"]
    assert "The comparison failed" in caplog.text


def test_clearing_the_board_forgets_its_runs_but_not_while_a_comparison_adds_to_it(
    board: Board,
) -> None:
    board.add(kept(TINY, "headphones"))
    gate = Gate()
    bench = Bench(board, run=gate)
    bench.start([PERFECT], [LAPTOPS])
    assert gate.started.wait(10)

    with pytest.raises(ApiError, match="stop it before clearing") as refused:
        bench.clear()

    assert refused.value.status == 409
    gate.proceed.set()
    bench.wait(10)
    assert len(board.runs()) == 2

    assert bench.clear()["standings"] == []
    assert board.runs() == []


def test_a_job_notes_the_step_about_to_start_and_stops_there_when_asked() -> None:
    job = Job((TINY,), (HEADPHONES,), started=0.0)

    job.checkpoint("search")
    assert job.current is None, "nothing under way yet"

    job.current = (TINY, HEADPHONES, "query")
    job.checkpoint("fetch")
    assert job.current == (TINY, HEADPHONES, "fetch")

    job.stop.set()
    with pytest.raises(Stopped, match="extract"):
        job.checkpoint("extract")
    assert job.total == 1


def test_a_job_that_has_not_reached_its_first_run_says_it_is_starting(board: Board) -> None:
    bench = Bench(board, clock=ticking())
    bench._job = Job((TINY,), (HEADPHONES,), started=0.0)

    assert bench.state()["status"] == "Starting (0 of 1 runs done)."


def test_every_step_the_agent_announces_has_words_for_the_page() -> None:
    """``BuyAgent.run`` calls its checkpoint before these four (ADR-0034); the query is
    the comparison's own name for the time before the first of them."""
    assert set(DOING) == {"query", "search", "fetch", "extract", "rank"}


# -- what a request may ask for ------------------------------------------------


def test_a_plan_names_its_contenders_scripts_first_and_its_cases_in_order() -> None:
    contenders, cases = read_plan(
        {
            "provider": "ollama",
            "base_url": " http://127.0.0.1:11555 ",
            "models": ["tiny:1b", "tiny:1b", " "],
            "scripts": ["sloppy"],
            "cases": ["espresso", "headphones"],
        }
    )

    assert contenders == [
        Contender.scripted("sloppy"),
        Contender.served("ollama", "tiny:1b", "http://127.0.0.1:11555"),
    ]
    assert [case.name for case in cases] == ["espresso", "headphones"]


def test_a_plan_with_no_server_named_asks_the_default_one() -> None:
    contenders, _ = read_plan({"models": ["tiny:1b"], "cases": ["laptops"]})

    assert contenders == [Contender.served(server_module.DEFAULT_PROVIDER, "tiny:1b")]


@pytest.mark.parametrize(
    ("plan", "field", "said"),
    [
        ({"provider": "kobold"}, "provider", "provider must be one of"),
        (
            {"scripts": ["lucky"], "cases": ["laptops"]},
            "scripts",
            "No such reference answer: lucky",
        ),
        ({"scripts": ["perfect"], "cases": ["kettles"]}, "cases", "No such case: kettles"),
        ({"scripts": ["perfect"]}, "cases", "Pick at least one case"),
        ({"cases": ["laptops"]}, "models", "Pick at least one model, or a reference answer"),
        ({"models": "tiny:1b", "cases": ["laptops"]}, "models", "models must be a list of names"),
        ({"models": [1], "cases": ["laptops"]}, "models", "models must be a list of names"),
    ],
    ids=["provider", "script", "case", "no case", "no contender", "not a list", "not names"],
)
def test_a_plan_a_run_could_not_start_from_is_refused_on_its_field(
    plan: dict[str, Any], field: str, said: str
) -> None:
    with pytest.raises(ApiError, match=said) as refused:
        read_plan(plan)

    assert (refused.value.status, refused.value.field) == (400, field)


def test_a_list_of_names_is_read_once_each_in_order() -> None:
    named = {"cases": [" laptops", "espresso", "laptops"]}

    assert _names(named, "cases") == ["laptops", "espresso"]
    assert _names({}, "cases") == []
    assert _names({"cases": None}, "cases") == []


def test_the_page_is_drawn_from_the_tables_it_offers() -> None:
    drawn = config_payload()

    assert drawn["provider"] == server_module.DEFAULT_PROVIDER
    assert [row["name"] for row in drawn["provider_options"]] == ["ollama", "vllm", "litellm"]
    assert [case["name"] for case in drawn["cases"]] == list(CASES)
    assert [script["name"] for script in drawn["scripts"]] == list(SCRIPTS)
    assert [metric["name"] for metric in drawn["metrics"]] == list(METRICS)


# -- over HTTP -----------------------------------------------------------------


@contextmanager
def serving(bench: Bench, host: str = "127.0.0.1") -> Iterator[str]:
    """The page's server on a loopback port for the duration of the block."""
    httpd = create_server(host, 0, bench=bench)
    thread = threading.Thread(target=httpd.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    bound, port = httpd.server_address[:2]
    try:
        yield f"http://[{bound}]:{port}" if ":" in bound else f"http://{bound}:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


@pytest.fixture
def page(board: Board) -> Iterator[tuple[str, Bench]]:
    bench = Bench(board)
    with serving(bench) as base:
        yield base, bench


def call(
    url: str, payload: Any = None, *, method: str | None = None, **headers: str
) -> tuple[int, Any, Any]:
    """One request: its status, its body (JSON where it says so), its headers."""
    body = payload
    if payload is not None and not isinstance(payload, bytes):
        body = json.dumps(payload).encode()
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw, status, sent = response.read(), response.status, response.headers
    except urllib.error.HTTPError as error:
        raw, status, sent = error.read(), error.code, error.headers
    if raw and "json" in sent.get("Content-Type", ""):
        return status, json.loads(raw), sent
    return status, raw.decode("utf-8"), sent


def test_the_page_is_served_with_the_shops_security_headers(page: tuple[str, Bench]) -> None:
    """The same policy the shop's page is held to (ADR-0018): the page runs no inline
    script, and a browser would refuse one."""
    base, _ = page
    status, body, sent = call(f"{base}/")

    assert status == 200
    assert "<h1>Local model benchmark</h1>" in body
    assert sent["Content-Type"] == "text/html; charset=utf-8"
    for name, value in _SECURITY_HEADERS:
        assert sent[name] == value


@pytest.mark.parametrize(
    ("path", "kind"),
    [("/app.js", "text/javascript; charset=utf-8"), ("/style.css", "text/css; charset=utf-8")],
)
def test_the_pages_own_files_are_served_as_what_they_are(
    page: tuple[str, Bench], path: str, kind: str
) -> None:
    base, _ = page
    status, _body, sent = call(f"{base}{path}")

    assert (status, sent["Content-Type"]) == (200, kind)


def test_a_head_request_is_answered_without_a_body(page: tuple[str, Bench]) -> None:
    base, _ = page
    status, body, sent = call(f"{base}/api/config", method="HEAD")

    assert status == 200
    assert body == ""
    assert int(sent["Content-Length"]) > 0


def test_the_config_is_what_the_page_is_drawn_from(page: tuple[str, Bench]) -> None:
    base, _ = page

    assert call(f"{base}/api/config")[:2] == (200, json.loads(json.dumps(config_payload())))


def test_an_unknown_endpoint_is_a_404_either_way(page: tuple[str, Bench]) -> None:
    base, _ = page

    assert call(f"{base}/api/nothing")[:2] == (404, {"error": "No such endpoint: /api/nothing"})
    assert call(f"{base}/api/nothing", {})[:2] == (404, {"error": "No such endpoint: /api/nothing"})


def test_a_request_from_another_site_is_refused(page: tuple[str, Bench]) -> None:
    """A page elsewhere must not start model runs on this machine (ADR-0018)."""
    base, bench = page
    status, body, _ = call(
        f"{base}/api/run",
        {"scripts": ["perfect"], "cases": ["laptops"]},
        **{"Sec-Fetch-Site": "cross-site"},
    )

    assert (status, body) == (403, {"error": "This API only answers its own page."})
    assert bench.state()["status"] == ""


@pytest.mark.parametrize(
    "headers",
    [{"Origin": "https://evil.example"}, {"Host": "rebound.example"}],
    ids=["foreign origin", "rebound host"],
)
def test_a_look_from_another_site_is_refused_too(
    page: tuple[str, Bench], headers: dict[str, str]
) -> None:
    """What the board holds is nobody else's to read, through a page elsewhere or a name
    rebound to this machine."""
    base, _ = page

    assert call(f"{base}/api/state", **headers)[:2] == (
        403,
        {"error": "This API only answers its own page."},
    )


def test_a_comparison_is_started_watched_and_read_over_http(page: tuple[str, Bench]) -> None:
    base, bench = page

    plan = {"scripts": ["perfect", "sloppy"], "cases": ["laptops"]}
    status, started, _ = call(f"{base}/api/run", plan)
    bench.wait(10)
    _, state, _ = call(f"{base}/api/state")

    assert status == 200
    assert started["total"] == 2
    assert state["running"] is False
    assert [row["score_label"] for row in state["standings"]] == ["1.000", "0.600"]
    assert [case["name"] for case in state["cases"]] == list(CASES), "every case is a column"
    assert state["board"] == str(bench.board.path)


def test_a_plan_the_server_refuses_names_its_field(page: tuple[str, Bench]) -> None:
    base, _ = page

    assert call(f"{base}/api/run", {"cases": ["laptops"]})[:2] == (
        400,
        {"error": "Pick at least one model, or a reference answer, to run.", "field": "models"},
    )


def test_a_body_that_is_not_json_is_refused(page: tuple[str, Bench]) -> None:
    base, _ = page
    status, body, _ = call(f"{base}/api/run", b"{not json")

    assert status == 400
    assert body["error"].startswith("Body is not valid JSON")


def test_a_model_server_at_the_pages_own_address_is_refused_before_it_runs(
    page: tuple[str, Bench],
) -> None:
    """Asking itself for a model would answer with this page."""
    base, bench = page
    status, body, _ = call(
        f"{base}/api/run",
        {"provider": "vllm", "base_url": base, "models": ["m"], "cases": ["laptops"]},
    )

    assert status == 400
    assert body["field"] == "base_url"
    assert body["error"].startswith(f"{base} is this page's own address, not vLLM's.")
    assert bench.state()["status"] == ""


def test_the_models_listing_is_the_shops_own(
    page: tuple[str, Bench], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same answer, the same remedy (ADR-0032), and the same refusal to ask itself."""
    base, _ = page

    def get(_url: str, **_kwargs: Any) -> Any:
        raise ConnectionRefusedError("[Errno 111] Connection refused")

    monkeypatch.setattr("buy_agent.providers.httpx.get", get)
    query = urlencode({"provider": "ollama", "base_url": "http://127.0.0.1:9"})
    _, unreachable, _ = call(f"{base}/api/models?{query}")
    _, itself, _ = call(f"{base}/api/models?{urlencode({'provider': 'vllm', 'base_url': base})}")

    assert unreachable["reachable"] is False
    assert unreachable["hint"].startswith("Could not reach Ollama at http://127.0.0.1:9")
    assert itself["reachable"] is False
    assert "this page's own address" in itself["hint"]


def test_stop_and_clear_are_answered_with_the_state(page: tuple[str, Bench]) -> None:
    base, bench = page
    bench.board.add(kept(TINY, "headphones"))

    assert call(f"{base}/api/stop", {})[1]["running"] is False
    assert call(f"{base}/api/clear", {})[1]["standings"] == []


def test_a_failure_nobody_planned_for_is_a_500_not_a_dropped_connection(
    page: tuple[str, Bench], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    base, bench = page

    def broken(*_args: Any) -> dict:
        raise RuntimeError("the board is on fire")

    monkeypatch.setattr(bench, "state", broken)
    monkeypatch.setattr(bench, "stop", broken)
    with caplog.at_level(logging.ERROR, logger="benchmark.server"):
        got = call(f"{base}/api/state")[:2]
        posted = call(f"{base}/api/stop", {})[:2]

    expected = (500, {"error": "Unexpected failure: the board is on fire"})
    assert (got, posted) == (expected, expected)


def test_a_target_that_will_not_parse_is_refused_not_dropped(page: tuple[str, Bench]) -> None:
    """The shipped handler's refusal, inherited: each ``do_*`` here parses the target
    before its catch-all too."""
    parsed = urlparse(page[0])
    with socket.create_connection((parsed.hostname, parsed.port), timeout=10) as sock:
        sock.sendall(b"GET http://[x/ HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        reply = sock.recv(4096).decode("utf-8", "replace")

    assert reply.startswith("HTTP/1.1 400"), reply or "the connection closed unanswered"


def ipv6_loopback() -> bool:
    """Whether this machine can bind ``::1``: built in is not the same as configured."""
    if not socket.has_ipv6:
        return False
    try:
        with socket.socket(socket.AF_INET6) as probe:
            probe.bind(("::1", 0))
    except OSError:
        return False
    return True


@pytest.mark.skipif(not ipv6_loopback(), reason="this machine cannot bind ::1")
def test_the_server_binds_an_ipv6_address_too(board: Board) -> None:
    with serving(Bench(board), host="::1") as base:
        assert urlparse(base).hostname == "::1"
        assert call(f"{base}/api/config")[0] == 200


# -- the page's own files ------------------------------------------------------


class _Ids(HTMLParser):
    """Every ``id`` the page declares, and what it loads."""

    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.loaded: list[str] = []
        self.handlers: list[str] = []
        self.inline_scripts = 0
        self._in_script = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        named = dict(attrs)
        if "id" in named and named["id"]:
            self.ids.add(named["id"])
        self.handlers += [name for name, _ in attrs if name.startswith("on")]
        if tag == "script":
            self._in_script = True
            if named.get("src"):
                self.loaded.append(named["src"] or "")
        if tag == "link" and named.get("rel") == "stylesheet":
            self.loaded.append(named.get("href") or "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._in_script = False

    def handle_data(self, data: str) -> None:
        if self._in_script and data.strip():
            self.inline_scripts += 1


@pytest.fixture(scope="module")
def markup() -> _Ids:
    parsed = _Ids()
    parsed.feed((WEB_DIR / "index.html").read_text(encoding="utf-8"))
    return parsed


@pytest.fixture(scope="module")
def script() -> str:
    return (WEB_DIR / "app.js").read_text(encoding="utf-8")


def test_the_page_loads_only_its_own_files(markup: _Ids) -> None:
    """``script-src 'self'``: no inline script and no handler attribute, which the CSP
    refuses and only a browser would show (ADR-0066's rule, for this page)."""
    assert sorted(markup.loaded) == ["app.js", "style.css"]
    assert all((WEB_DIR / name).is_file() for name in markup.loaded)
    assert markup.inline_scripts == 0
    assert markup.handlers == []


def test_every_element_the_script_looks_up_is_on_the_page(markup: _Ids, script: str) -> None:
    looked_up = set(re.findall(r"byId\('([\w-]+)'\)", script))

    assert looked_up, "the script looks nothing up; this rule has outlived it"
    assert looked_up <= markup.ids, sorted(looked_up - markup.ids)


def test_the_script_writes_text_and_never_markup(script: str) -> None:
    """Model names and quotes are model output, so they reach the page as text."""
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert sink not in script, sink


def test_the_script_reads_only_what_the_server_sends(script: str, board: Board) -> None:
    """The page decides nothing (ADR-0012), so every field it reads is one a payload
    carries -- the rule ``agent.types.ts`` is held to, for a page with no types."""
    board.add(kept(TINY, "headphones"))
    state = Bench(board).state()
    row = state["standings"][0]
    run = row["runs"][0]
    config = config_payload()
    listing = {"provider", "label", "base_url", "reachable", "models", "detail", "hint"}
    read_as = {
        "state": state,
        "row": row,
        "cell": row["cells"][0],
        "run": run,
        "product": run_payload(run_case(PERFECT, LAPTOPS))["products"][0],
        "metric": {**run["metrics"][0], **config["metrics"][0]} if run["metrics"] else {},
        "check": run["query"]["checks"][0],
        "entry": config["cases"][0],
        "option": config["provider_options"][0],
        "script": config["scripts"][0],
        "answer": dict.fromkeys(listing),
        "model": {"name": "", "completion": True},
    }

    for variable, payload in read_as.items():
        read = set(re.findall(rf"\b{variable}\.(\w+)", script))
        assert read, f"the script never reads a {variable}; this rule has outlived it"
        assert read <= set(payload), f"{variable}: {sorted(read - set(payload))}"



# -- python -m benchmark.server ------------------------------------------------


def test_a_port_is_a_whole_number_a_socket_can_take() -> None:
    assert _port("8100") == 8100
    with pytest.raises(Exception, match="must be a whole number"):
        _port("eighty")
    with pytest.raises(Exception, match="must be between 0 and 65535"):
        _port("70000")


def test_the_page_binds_this_machine_on_a_port_of_its_own_by_default() -> None:
    """Not the shop's 8000, which is also vLLM's, nor LiteLLM's 4000."""
    args = build_parser().parse_args([])

    assert (args.host, args.port) == (DEFAULT_HOST, DEFAULT_PORT) == ("127.0.0.1", 8100)
    assert args.allowed_host == []


class FakeHttpd:
    """Stands in for the server ``main`` builds, so starting it serves nothing."""

    def __init__(self, interrupted: bool = False) -> None:
        self.server_address = ("127.0.0.1", 8100)
        self.interrupted = interrupted
        self.closed = False

    def serve_forever(self) -> None:
        if self.interrupted:
            raise KeyboardInterrupt

    def server_close(self) -> None:
        self.closed = True


@pytest.fixture
def built(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    made: dict[str, Any] = {}

    def create(host: str, port: int, *, bench: Bench, allowed_hosts: Any) -> FakeHttpd:
        made.update(host=host, port=port, bench=bench, allowed_hosts=allowed_hosts)
        made["httpd"] = FakeHttpd(made.get("interrupted", False))
        return made["httpd"]

    monkeypatch.setattr(server_module, "create_server", create)
    return made


def test_main_serves_the_page_until_it_is_stopped(
    built: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="benchmark.server"):
        assert main(["--port", "0"]) == 0

    assert built["httpd"].closed
    assert built["allowed_hosts"] == frozenset({"localhost", "127.0.0.1", "::1"})
    assert "Benchmark page on http://127.0.0.1:8100" in caplog.text
    assert f"Runs are kept on the board at {built['bench'].board.path}" in caplog.text
    assert logging.getLogger("buy_agent").level == logging.WARNING


def test_main_answers_ctrl_c_as_every_entry_point_does(built: dict[str, Any]) -> None:
    built["interrupted"] = True

    assert main([]) == 130
    assert built["httpd"].closed


def test_main_warns_before_answering_any_host_on_a_public_bind(
    built: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="benchmark.server"):
        main(["--host", "0.0.0.0", "-v"])

    assert built["allowed_hosts"] is None
    assert "answers any Host header" in caplog.text
    assert logging.getLogger("buy_agent").level != logging.WARNING


def test_main_refuses_a_provider_nothing_serves(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(server_module, "DEFAULT_PROVIDER", "kobold")

    with caplog.at_level(logging.ERROR, logger="benchmark.server"):
        assert main([]) == 1

    assert "Unknown provider 'kobold'" in caplog.text


def test_main_says_when_its_port_is_taken(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Raised rather than provoked: Windows lets a second socket take a port the first
    is listening on (ADR-0020)."""

    def taken(*_args: Any, **_kwargs: Any) -> None:
        raise OSError(errno.EADDRINUSE, os.strerror(errno.EADDRINUSE))

    monkeypatch.setattr(server_module, "create_server", taken)
    with caplog.at_level(logging.ERROR, logger="benchmark.server"):
        assert main(["--port", "8100"]) == 1

    assert "Could not listen on 127.0.0.1:8100" in caplog.text


def test_a_board_can_be_named_for_the_bench(tmp_path: Path) -> None:
    named = Board(tmp_path / "elsewhere.json")

    assert Bench(named).board is named
    assert Bench().board.path.name == "board.json"

