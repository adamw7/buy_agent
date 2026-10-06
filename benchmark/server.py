"""The benchmark's own page on :8100: pick models and cases, run them, read the
standings (ADR-0070). The handler is the shop's, with the benchmark's routes."""

from __future__ import annotations

import argparse
import logging
import threading
import time
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

from buy_agent.agent import BuyAgent, ModelUnavailableError
from buy_agent.api import ApiError
from buy_agent.config import DEFAULT_PROVIDER
from buy_agent.logging_setup import configure_logging
from buy_agent.providers import PROVIDERS, provider_for, provider_options
from buy_agent.server import (
    BuyAgentHandler,
    allowed_hosts_for,
    bind,
    browsable_url,
    port_number,
    serve_until_interrupted,
)
from benchmark.board import Board
from benchmark.cases import CASES, SCRIPTS
from benchmark.compare import (
    Contender,
    case_payload,
    metrics_payload,
    run_case,
    seconds_label,
    standings_payload,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from http.server import ThreadingHTTPServer

    from benchmark.cases import Case
    from benchmark.compare import CaseRun

logger = logging.getLogger(__name__)

#: The files beside this module; no build step.
WEB_DIR = Path(__file__).resolve().parent / "web"

#: A port no model server nor the shop's page defaults to.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8100

#: By the step ``BuyAgent.run`` announces (ADR-0034).
DOING: dict[str, str] = {
    "query": "refining the query",
    "search": "searching",
    "fetch": "reading the pages",
    "extract": "reading out the products",
    "rank": "ranking",
}


class Stopped(Exception):
    """Ends a comparison at the next step, after Stop."""


@dataclass
class Job:
    """One comparison: who over what, how far it has got, and how it ended."""

    contenders: tuple[Contender, ...]
    cases: tuple[Case, ...]
    started: float
    stop: threading.Event = field(default_factory=threading.Event)
    done: int = 0
    skipped: int = 0
    current: tuple[Contender, Case, str] | None = None
    #: What could not be asked, and the server's own remedy.
    problems: list[str] = field(default_factory=list)
    #: ``running``, then ``finished``, ``stopped`` or ``failed``.
    outcome: str = "running"
    ended: float | None = None

    @property
    def total(self) -> int:
        return len(self.contenders) * len(self.cases)

    def checkpoint(self, step: str) -> None:
        if self.current is not None:
            contender, case, _ = self.current
            self.current = (contender, case, step)
        if self.stop.is_set():
            raise Stopped(step)


class Bench:
    """One comparison at a time, run on a thread of its own, and the board it adds to."""

    def __init__(
        self,
        board: Board | None = None,
        *,
        run: Callable[..., CaseRun] = run_case,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.board = board or Board()
        self._run = run
        self._clock = clock
        self._lock = threading.Lock()
        self._job: Job | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._job is not None and self._job.outcome == "running"

    def start(self, contenders: Sequence[Contender], cases: Sequence[Case]) -> dict[str, Any]:
        """Start a comparison; 409 while another runs."""
        with self._lock:
            if self.running:
                raise ApiError(
                    "A comparison is already running: stop it, or wait for it to finish.",
                    409,
                )
            job = Job(tuple(contenders), tuple(cases), started=self._clock())
            self._job = job
            self._thread = threading.Thread(
                target=self._work, args=(job,), name="benchmark", daemon=True
            )
            self._thread.start()
        return self.state()

    def stop(self, _data: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if self._job is not None and self.running:
            self._job.stop.set()
        return self.state()

    def clear(self, _data: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Forget every kept run; 409 while a comparison would put its runs back."""
        if self.running:
            raise ApiError("A comparison is running; stop it before clearing the board.", 409)
        self.board.clear()
        return self.state()

    def wait(self, timeout: float | None = None) -> bool:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        return not self.running

    def state(self) -> dict[str, Any]:
        job = self._job
        cases = list(CASES.values())
        return {
            "running": self.running,
            "stopping": job is not None and self.running and job.stop.is_set(),
            "status": "" if job is None else self._status(job),
            "done": 0 if job is None else job.done + job.skipped,
            "total": 0 if job is None else job.total,
            "problems": [] if job is None else list(job.problems),
            "board": str(self.board.path),
            **standings_payload(self.board.runs(), cases),
        }

    def _status(self, job: Job) -> str:
        took = seconds_label((job.ended or self._clock()) - job.started)
        counted = f"{job.done + job.skipped} of {job.total} runs"
        if job.outcome == "running":
            if job.stop.is_set():
                return (
                    f"Stopping after the step under way ({counted} done): a question a "
                    "model is answering cannot be taken back."
                )
            if job.current is None:
                return f"Starting ({counted} done)."
            contender, case, step = job.current
            return (
                f"Running {contender.label} on {case.name}: {DOING.get(step, step)}. "
                f"{counted} done, {took} so far."
            )
        skipped = f", {job.skipped} skipped" if job.skipped else ""
        if job.outcome == "finished":
            return f"Finished {job.done} of {job.total} runs{skipped} in {took}."
        if job.outcome == "stopped":
            return f"Stopped after {job.done} of {job.total} runs{skipped}, in {took}."
        return f"The comparison failed after {job.done} of {job.total} runs."

    def _work(self, job: Job) -> None:
        """Contender by contender, so a server loads each model once."""
        try:
            for contender in job.contenders:
                for index, case in enumerate(job.cases):
                    job.current = (contender, case, "query")
                    job.checkpoint("query")
                    try:
                        run = self._run(contender, case, checkpoint=job.checkpoint)
                    except ModelUnavailableError as exc:
                        # Not a result; its other cases would fail the same way.
                        job.problems.append(f"{contender.label}: {exc}")
                        job.skipped += len(job.cases) - index
                        logger.warning("Skipped %s: %s", contender.label, exc)
                        break
                    self.board.add(run)
                    job.done += 1
                    logger.info(
                        "%s on %s: %s in %s",
                        contender.label,
                        case.name,
                        "failed" if run.failure else f"{run.score:.3f}",
                        seconds_label(run.model_seconds),
                    )
            job.outcome = "finished"
        except Stopped:
            job.outcome = "stopped"
        # Reported on the page rather than lost with the thread.
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception("The comparison failed")
            job.problems.append(f"Unexpected failure: {exc}")
            job.outcome = "failed"
        finally:
            job.current = None
            job.ended = self._clock()


def config_payload() -> dict[str, Any]:
    return {
        "provider": DEFAULT_PROVIDER,
        "provider_options": provider_options(),
        "cases": [case_payload(case) for case in CASES.values()],
        "scripts": [{"name": name, "means": means} for name, means in SCRIPTS.items()],
        "metrics": metrics_payload(),
    }


def _names(data: Mapping[str, Any], key: str) -> list[str]:
    value = data.get(key) or []
    if not isinstance(value, list) or not all(isinstance(name, str) for name in value):
        raise ApiError(f"{key} must be a list of names.", field=key)
    return list(dict.fromkeys(name.strip() for name in value if name.strip()))


def read_plan(data: Mapping[str, Any]) -> tuple[list[Contender], list[Case]]:
    """Who to run over what; an ``ApiError`` names the field (ADR-0033)."""
    provider = str(data.get("provider") or "").strip() or DEFAULT_PROVIDER
    if provider not in PROVIDERS:
        raise ApiError(
            f"provider must be one of {', '.join(PROVIDERS)}; got {provider!r}.",
            field="provider",
        )
    base_url = str(data.get("base_url") or "").strip()
    models = _names(data, "models")
    scripts = _names(data, "scripts")
    named = _names(data, "cases")

    if unknown := [script for script in scripts if script not in SCRIPTS]:
        raise ApiError(f"No such reference answer: {', '.join(unknown)}.", field="scripts")
    if unknown := [name for name in named if name not in CASES]:
        raise ApiError(f"No such case: {', '.join(unknown)}.", field="cases")
    if not named:
        raise ApiError("Pick at least one case to run.", field="cases")
    if not models and not scripts:
        raise ApiError("Pick at least one model, or a reference answer, to run.", field="models")

    contenders = [Contender.scripted(script) for script in scripts] + [
        Contender.served(provider, model, base_url) for model in models
    ]
    return contenders, [CASES[name] for name in named]


class BenchmarkHandler(BuyAgentHandler):
    """The shipped server's handler, admitting and answering as it does, with the
    benchmark's routes in place of the shop's."""

    #: Nothing is streamed: the page asks for the state instead.
    stream_path = None

    def __init__(self, *args: Any, bench: Bench, **kwargs: Any) -> None:
        # Set before the base class, whose ``__init__`` answers the request.
        self.bench = bench
        super().__init__(*args, ui_dir=WEB_DIR, agent_factory=BuyAgent, **kwargs)

    def get_routes(self) -> dict[str, Callable[[dict[str, str]], dict[str, Any] | bytes]]:
        return {
            "/api/config": lambda _: config_payload(),
            # The shop's own listing, its guard against asking itself included.
            "/api/models": self._models,
            "/api/state": lambda _: self.bench.state(),
        }

    def post_routes(self) -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
        bench = self.bench
        return {"/api/run": self._start, "/api/stop": bench.stop, "/api/clear": bench.clear}

    def _start(self, data: dict[str, Any]) -> dict[str, Any]:
        contenders, cases = read_plan(data)
        for contender in contenders:
            if contender.provider:
                self.refuse_own_address(PROVIDERS[contender.provider].label, contender.base_url)
        return self.bench.start(contenders, cases)


_LOOPBACK = allowed_hosts_for(DEFAULT_HOST)


def create_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    bench: Bench | None = None,
    allowed_hosts: frozenset[str] | None = _LOOPBACK,
) -> ThreadingHTTPServer:
    """Build the page's server without starting it (ADR-0018)."""
    handler = partial(BenchmarkHandler, bench=bench or Bench(), allowed_hosts=allowed_hosts)
    return bind(host, port, handler)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m benchmark.server",
        description="Serve the benchmark's page: compare local models on this agent's cases.",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"Interface to bind (default: {DEFAULT_HOST}, this machine only).",
    )
    parser.add_argument(
        "--port",
        type=port_number,
        default=DEFAULT_PORT,
        help=f"Port to bind (default: {DEFAULT_PORT}; 0 takes whichever one is free).",
    )
    parser.add_argument(
        "--allowed-host",
        action="append",
        default=[],
        metavar="HOST",
        help="Extra Host header to answer, repeatable, as the shop's server takes it.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Show each run's log.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(verbose=args.verbose)
    if not args.verbose:
        # The page is the output here, not each run's narration.
        logging.getLogger("buy_agent").setLevel(logging.WARNING)
    try:
        provider_for(DEFAULT_PROVIDER)
    except ValueError as exc:
        logger.error("%s", exc)
        return 1

    allowed = allowed_hosts_for(args.host, args.allowed_host)
    if allowed is None:
        logger.warning(
            "Bound to %s with no --allowed-host: this answers any Host header, and starts "
            "model runs for whoever reaches it.",
            args.host,
        )
    bench = Bench(Board())
    try:
        httpd = create_server(args.host, args.port, bench=bench, allowed_hosts=allowed)
    except OSError as exc:
        logger.error("Could not listen on %s:%s (%s).", args.host, args.port, exc)
        return 1

    host, port = httpd.server_address[:2]
    logger.info("Benchmark page on %s", browsable_url(str(host), port))
    logger.info("Runs are kept on the board at %s", bench.board.path)
    return serve_until_interrupted(httpd)


if __name__ == "__main__":
    raise SystemExit(main())
