"""What changes when the server is reachable from more than one machine (ADR-0092): who
may ask it, what it may be asked to reach, how much it takes on at once, how it stops,
and what it says about itself. No HTTP: the server reads the headers and hands them
here."""

from __future__ import annotations

import base64
import binascii
import hmac
import re
import signal
import threading
import uuid
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from buy_agent.api import Sealer
from buy_agent.version import current

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import FrameType

#: Runs at once unless told otherwise: a model server answers few at a time.
DEFAULT_MAX_RUNS = 4
#: The range the cap on runs is held to.
MAX_RUNS = (1, 64)
#: Open connections, a thread each, before another is closed unanswered.
MAX_CONNECTIONS = 256
#: How long a stopping server waits for what it is answering: under the thirty seconds
#: an orchestrator gives a container before it kills it.
DRAIN_SECONDS = 25.0
#: Shorter than this, a token is a password somebody can guess.
MIN_TOKEN = 16
#: How a run can end, as ``/metrics`` counts them; ``refused`` never began.
OUTCOMES = ("finished", "failed", "stopped", "refused")
#: A caller's ``X-Request-Id`` is kept when it looks like one, else one is made up.
_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")


@dataclass(frozen=True, slots=True)
class Policy:
    """How a server treats a request beyond what its page's own headers decide. The
    defaults are a laptop's: no token, any address, the client as the socket says, no
    access log."""

    #: Asked of every request but the probes; blank asks for none.
    token: str = field(default="", repr=False)
    #: Ask only the model server and the merchant this server was started with.
    fixed_addresses: bool = False
    #: Read the host and the client off the proxy's ``X-Forwarded-*`` headers.
    behind_proxy: bool = False
    access_log: bool = False
    max_runs: int = DEFAULT_MAX_RUNS
    sealer: Sealer = field(default_factory=Sealer.keyed_by)


def authorised(authorization: str, token: str) -> bool:
    """Whether an ``Authorization`` header carries ``token``: as a bearer, or as a Basic
    password, which a browser asks its reader for on the page's first answer and then
    sends with every request. No token asks for nothing."""
    if not token:
        return True
    scheme, _, credentials = authorization.strip().partition(" ")
    given = credentials.strip()
    if scheme.lower() == "basic":
        try:
            given = base64.b64decode(given, validate=True).decode("utf-8").partition(":")[2]
        except (binascii.Error, UnicodeDecodeError):
            return False
    elif scheme.lower() != "bearer":
        return False
    return hmac.compare_digest(given.encode("utf-8"), token.encode("utf-8"))


def request_id(given: str) -> str:
    """The caller's id for a request where it looks like one, else a new one."""
    given = given.strip()
    return given if _REQUEST_ID.fullmatch(given) else uuid.uuid4().hex


def same_address(given: str, own: str) -> bool:
    return given.strip().rstrip("/") == own.strip().rstrip("/")


def fixed_address(label: str, address: str) -> str:
    """Why an address was not asked: this server asks only the one it was started with."""
    where = f", at {address}" if address else ", and it was started with none"
    return (
        f"This server is reachable from other machines, so it asks only the {label} it "
        f"was started with{where}. Leave the address empty, or change it where the "
        f"server is deployed."
    )


class Traffic:
    """What one server is answering and has answered: its drain, its cap on runs and
    ``/metrics`` all read this."""

    def __init__(self, max_runs: int = DEFAULT_MAX_RUNS) -> None:
        self.max_runs = max_runs
        self._changed = threading.Condition()
        self.connections = 0
        self.requests = 0
        self.runs = 0
        self.draining = False
        self.answered: Counter[str] = Counter()
        self.ended: Counter[str] = Counter()
        self.run_seconds = 0.0

    def connect(self) -> bool:
        """Count a connection in, unless as many are open as a server takes."""
        with self._changed:
            if self.connections >= MAX_CONNECTIONS:
                return False
            self.connections += 1
            return True

    def disconnect(self) -> None:
        with self._changed:
            self.connections -= 1

    def begin(self) -> None:
        with self._changed:
            self.requests += 1

    def end(self, status: int) -> None:
        """One request answered with ``status``; 0 if it never got as far as one."""
        with self._changed:
            self.requests -= 1
            if status:
                self.answered[f"{status // 100}xx"] += 1
            self._changed.notify_all()

    def admit_run(self) -> str | None:
        """Why another run cannot start now, or ``None`` having counted it in."""
        with self._changed:
            if self.draining:
                return "This server is shutting down; search again in a moment."
            if self.runs >= self.max_runs:
                self.ended["refused"] += 1
                searches = "search" if self.runs == 1 else "searches"
                return (
                    f"This server is already running {self.runs} {searches}, as many as "
                    f"it takes at once. Try again in a minute."
                )
            self.runs += 1
            return None

    def end_run(self, outcome: str, seconds: float) -> None:
        with self._changed:
            self.runs -= 1
            self.ended[outcome] += 1
            self.run_seconds += seconds

    def drain(self, seconds: float) -> int:
        """Take no more runs and wait up to ``seconds`` for the requests in flight;
        answer how many were still being answered."""
        with self._changed:
            self.draining = True
            self._changed.wait_for(lambda: self.requests == 0, timeout=seconds)
            return self.requests

    def metrics(self) -> str:
        """Prometheus's text format, a sample a line."""
        with self._changed:
            answered = sorted(self.answered.items())
            ran = sum(self.ended[outcome] for outcome in OUTCOMES if outcome != "refused")
            lines = [
                "# HELP buy_agent_build_info The build answering.",
                "# TYPE buy_agent_build_info gauge",
                f'buy_agent_build_info{{version="{_label_value(current().label)}"}} 1',
                "# HELP buy_agent_requests_total Requests answered, by status class.",
                "# TYPE buy_agent_requests_total counter",
                *(f'buy_agent_requests_total{{code="{code}"}} {n}' for code, n in answered),
                "# HELP buy_agent_requests_in_flight Requests being answered.",
                "# TYPE buy_agent_requests_in_flight gauge",
                f"buy_agent_requests_in_flight {self.requests}",
                "# HELP buy_agent_runs_total Runs by how they ended; refused never began.",
                "# TYPE buy_agent_runs_total counter",
                *(
                    f'buy_agent_runs_total{{outcome="{outcome}"}} {self.ended[outcome]}'
                    for outcome in OUTCOMES
                ),
                "# HELP buy_agent_runs_in_flight Runs going now.",
                "# TYPE buy_agent_runs_in_flight gauge",
                f"buy_agent_runs_in_flight {self.runs}",
                "# HELP buy_agent_run_seconds How long the runs that began took.",
                "# TYPE buy_agent_run_seconds summary",
                f"buy_agent_run_seconds_sum {self.run_seconds:.3f}",
                f"buy_agent_run_seconds_count {ran}",
                "# HELP buy_agent_connections_open Connections open, a thread each.",
                "# TYPE buy_agent_connections_open gauge",
                f"buy_agent_connections_open {self.connections}",
            ]
        return "\n".join(lines) + "\n"


def _label_value(text: str) -> str:
    """A Prometheus label value: its backslashes, quotes and newlines escaped."""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class Terminated(Exception):
    """SIGTERM, raised in the main thread out of whatever it was doing."""


def _terminate(signum: int, _frame: FrameType | None) -> None:
    raise Terminated(signum)


@contextmanager
def stopped_by_sigterm() -> Iterator[None]:
    """While inside, SIGTERM raises :class:`Terminated`: what ``docker stop`` sends, and
    which a process with no handler of its own ignores when it is a container's first."""
    # Only the main thread can be handed a signal.
    installed = threading.current_thread() is threading.main_thread()
    previous = signal.signal(signal.SIGTERM, _terminate) if installed else None
    try:
        yield
    finally:
        if installed:
            signal.signal(signal.SIGTERM, previous if previous is not None else signal.SIG_DFL)
