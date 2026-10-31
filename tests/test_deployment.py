"""What a server reachable from more than one machine adds (ADR-0092): its token, its
fixed addresses, its counts and caps, its metrics and how it stops."""

from __future__ import annotations

import base64
import signal
import threading

import pytest

import buy_agent.deployment as deployment_module
from buy_agent.deployment import (
    DEFAULT_MAX_RUNS, OUTCOMES, Policy, Terminated, Traffic, authorised, fixed_address, request_id,
    same_address, stopped_by_sigterm,
)
from buy_agent.version import current

TOKEN = "a token long enough to keep"


def basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


@pytest.mark.parametrize(
    "header",
    [f"Bearer {TOKEN}", f"bearer   {TOKEN}  ", basic("", TOKEN), basic("anyone", TOKEN)],
    ids=["bearer", "bearer-spaced", "basic", "basic-named"],
)
def test_the_token_is_taken_as_a_bearer_or_as_a_basic_password(header: str) -> None:
    """A browser asks its reader for a name and a password and sends both: the name is
    whatever they typed, so only the password is read."""
    assert authorised(header, TOKEN)


@pytest.mark.parametrize(
    "header",
    [
        "", "Bearer", "Bearer wrong", f"Token {TOKEN}", basic(TOKEN, "wrong"), "Basic not base64!",
        "Basic " + base64.b64encode(b"\xff\xfe:x").decode(),
    ],
    ids=["none", "empty", "wrong", "scheme", "name-not-password", "garbled", "not-utf8"],
)
def test_anything_else_is_refused(header: str) -> None:
    assert not authorised(header, TOKEN)


def test_a_server_with_no_token_asks_for_none() -> None:
    assert authorised("", "")


def test_a_request_id_is_kept_when_it_looks_like_one() -> None:
    assert request_id(" abc-123.x:y_z ") == "abc-123.x:y_z"


@pytest.mark.parametrize("given", ["", "has space", "x" * 129, "new\nline"])
def test_a_request_id_is_made_up_when_none_will_do(given: str) -> None:
    made = request_id(given)

    assert made != given.strip()
    assert len(made) == 32 and made.isalnum()


def test_an_address_is_the_same_whatever_its_trailing_slash() -> None:
    assert same_address(" http://ollama:11434/ ", "http://ollama:11434")
    assert not same_address("http://10.0.0.5:11434", "http://ollama:11434")


def test_a_fixed_address_is_named_or_said_to_be_none() -> None:
    assert "only the Ollama it was started with, at http://o:1." in fixed_address(
        "Ollama", "http://o:1"
    )
    assert "it was started with none." in fixed_address("merchant", "")


def test_a_policy_keeps_its_token_out_of_its_repr() -> None:
    """A policy logged, or shown in a traceback, must not carry the secret with it."""
    assert TOKEN not in repr(Policy(token=TOKEN))
    assert Policy().max_runs == DEFAULT_MAX_RUNS


# -- traffic -------------------------------------------------------------------


def test_runs_past_the_cap_are_refused_and_counted() -> None:
    traffic = Traffic(max_runs=2)

    assert traffic.admit_run() is None
    assert traffic.admit_run() is None
    refusal = traffic.admit_run()

    assert refusal and "already running 2 searches, as many as it takes at once" in refusal
    assert traffic.ended["refused"] == 1
    traffic.end_run("finished", 1.5)
    assert traffic.admit_run() is None, "a run that ended makes room"


def test_one_run_is_named_as_one() -> None:
    traffic = Traffic(max_runs=1)
    traffic.admit_run()

    assert "already running 1 search, as many" in (traffic.admit_run() or "")


def test_a_draining_server_takes_no_more_runs() -> None:
    traffic = Traffic()

    assert traffic.drain(0) == 0
    assert "shutting down" in (traffic.admit_run() or "")
    assert traffic.ended["refused"] == 0, "a stop is not the cap"


def test_a_drain_answers_what_was_still_being_answered() -> None:
    traffic = Traffic()
    traffic.begin()

    assert traffic.drain(0) == 1


def test_a_drain_ends_when_the_last_request_is_answered() -> None:
    """Woken by the answer, not by a poll: the server's thread ends it, and the drain
    returns then rather than at its deadline."""
    traffic = Traffic()
    traffic.begin()
    answered = threading.Thread(target=traffic.end, args=(200,))
    answered.start()

    assert traffic.drain(30) == 0
    answered.join()


def test_connections_past_the_cap_are_refused(monkeypatch) -> None:
    monkeypatch.setattr(deployment_module, "MAX_CONNECTIONS", 1)
    traffic = Traffic()

    assert traffic.connect()
    assert not traffic.connect()
    traffic.disconnect()
    assert traffic.connect()


def test_the_metrics_are_prometheus_text() -> None:
    traffic = Traffic()
    traffic.begin()
    traffic.end(200)
    traffic.begin()
    traffic.end(503)
    traffic.begin()
    traffic.end(0)
    traffic.admit_run()
    traffic.end_run("finished", 2.5)

    lines = traffic.metrics().splitlines()

    assert f'buy_agent_build_info{{version="{current().label}"}} 1' in lines
    assert 'buy_agent_requests_total{code="2xx"} 1' in lines
    assert 'buy_agent_requests_total{code="5xx"} 1' in lines
    assert not any('code="0xx"' in line for line in lines), "no answer is no status"
    for outcome in OUTCOMES:
        assert any(line.startswith(f'buy_agent_runs_total{{outcome="{outcome}"}}') for line in lines)
    assert "buy_agent_run_seconds_sum 2.500" in lines
    assert "buy_agent_run_seconds_count 1" in lines
    assert "buy_agent_runs_in_flight 0" in lines
    assert all(line.startswith(("# HELP ", "# TYPE ", "buy_agent_")) for line in lines)


def test_a_label_value_is_escaped(monkeypatch) -> None:
    class Odd:
        label = 'a"b\\c\nd'

    monkeypatch.setattr(deployment_module, "current", Odd)

    assert 'version="a\\"b\\\\c\\nd"' in Traffic().metrics()


# -- stopping ------------------------------------------------------------------


def test_sigterm_raises_while_inside_and_is_put_back_after() -> None:
    before = signal.getsignal(signal.SIGTERM)

    with stopped_by_sigterm():
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        with pytest.raises(Terminated):
            handler(signal.SIGTERM, None)  # type: ignore[operator]

    assert signal.getsignal(signal.SIGTERM) == before


def test_a_handler_installed_outside_python_is_put_back_as_the_default(monkeypatch) -> None:
    """``signal.signal`` answers ``None`` for a handler it did not install, and refuses
    ``None`` back: the default is what that was."""
    installed: list[object] = []

    def install(signum: int, handler: object) -> object:
        installed.append(handler)
        return None

    monkeypatch.setattr(deployment_module.signal, "signal", install)

    with stopped_by_sigterm():
        pass

    assert installed[-1] == signal.SIG_DFL


def test_only_the_main_thread_takes_the_signal() -> None:
    """``signal.signal`` raises anywhere else, and a server served from a thread -- a
    test's, the benchmark's -- is stopped by whoever started it instead."""
    before = signal.getsignal(signal.SIGTERM)
    seen: list[object] = []

    def serve() -> None:
        with stopped_by_sigterm():
            seen.append(signal.getsignal(signal.SIGTERM))

    worker = threading.Thread(target=serve)
    worker.start()
    worker.join()

    assert seen == [before]
