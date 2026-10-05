"""Fixtures for the live tests: a real Ollama, a fabricated web."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

import pytest

from buy_agent.agent import BuyAgent
from buy_agent.chat import release
from buy_agent.config import AgentConfig
from buy_agent.providers import OLLAMA
from benchmark.cases import HEADPHONES
from benchmark.compare import (
    CaseRun,
    Contender,
    Stopwatch,
    describe,
    scored_run,
    summary_markdown,
    write_standings,
)
from benchmark.runner import Report, serving_the_corpus
from benchmark.scoring import score_run
from integration import (
    LIVE_TIMEOUT_SECONDS,
    MODEL_ENV_VAR,
    REQUIRE_ENV_VAR,
    SCORECARD_ENV_VAR,
    TINY_MODEL,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from typing import Any

    from buy_agent.chat import Chain
    from buy_agent.models import ProductList, RankedProduct
    from buy_agent.search import SearchResult

#: The live run scored as the benchmark scores one, once a test has asked for it, so
#: the session's summary can report it pass or fail (ADR-0072).
SCORED = pytest.StashKey[CaseRun]()


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Give every live test the longer budget a real model needs."""
    for item in items:
        item.add_marker(pytest.mark.timeout(LIVE_TIMEOUT_SECONDS))


def pytest_terminal_summary(
    terminalreporter: pytest.TerminalReporter, config: pytest.Config
) -> None:
    """The scorecard, pass or fail: in the job's log, on its summary page, and in the
    file the workflow keeps (ADR-0072). A green run used to print nothing at all."""
    run = config.stash.get(SCORED, None)
    if run is None:
        return
    terminalreporter.write_sep("=", "benchmark")
    terminalreporter.write_line(describe(run, HEADPHONES))
    if page := os.getenv("GITHUB_STEP_SUMMARY"):
        with Path(page).open("a", encoding="utf-8") as summary:
            summary.write(summary_markdown(run, HEADPHONES))
    if kept := os.getenv(SCORECARD_ENV_VAR):
        write_standings(Path(kept), [run], [HEADPHONES])


@dataclass(frozen=True, slots=True)
class LiveRun:
    """One end-to-end run, plus what the model said before Python judged it."""

    ranked: list[RankedProduct]
    extracted: ProductList
    pages: tuple[SearchResult, ...]
    #: The query the model refined the request into, and how long each question took.
    watched: Stopwatch


def _unreachable_base_url() -> str:
    """A loopback URL nothing is listening on."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}"


@pytest.fixture(scope="session")
def base_url() -> str:
    """Where Ollama is, honouring ``$OLLAMA_HOST`` as the rest of the project does."""
    return OLLAMA.base_url


@pytest.fixture(scope="session")
def tiny_model(base_url: str) -> str:
    """The model tag to test against, once it is known to be pulled."""
    tag = os.getenv(MODEL_ENV_VAR, TINY_MODEL)
    try:
        probe = AgentConfig(base_url=base_url)
        installed = [model.name for model in probe.model_server.installed(probe)]
    # Any transport failure means "not there", which is what this is asking.
    except Exception as exc:  # pylint: disable=broad-exception-caught
        _absent(f"No Ollama at {base_url} ({exc}). Start it with: ollama serve")
    if tag not in installed:
        _absent(f"Ollama at {base_url} has no {tag!r}. Pull it with: ollama pull {tag}")
    return tag


def _absent(reason: str) -> NoReturn:
    """Skip, unless the environment says these tests were meant to run."""
    if os.getenv(REQUIRE_ENV_VAR):
        pytest.fail(reason)
    pytest.skip(reason)


@pytest.fixture(scope="session")
def live_config(tiny_model: str, base_url: str) -> AgentConfig:
    """The shipped defaults, on the tiny model and the benchmark's headphones case."""
    return HEADPHONES.settings(model=tiny_model, base_url=base_url)


@pytest.fixture(scope="session", autouse=True)
def fake_web() -> Iterator[list[SearchResult]]:
    """Hand every agent in this session the corpus instead of the web."""
    with serving_the_corpus(HEADPHONES.pages, HEADPHONES.page_text) as served:
        yield served


class Recording:
    """The extraction chain, with what it answered kept on the way past."""

    def __init__(self, chain: Chain[ProductList]) -> None:
        self.chain = chain
        self.seen: list[ProductList] = []

    def invoke(self, payload: Mapping[str, Any]) -> ProductList:
        answer = self.chain.invoke(payload)
        self.seen.append(answer)
        return answer


@pytest.fixture(scope="session")
def live_run(live_config: AgentConfig, fake_web: list[SearchResult]) -> LiveRun:
    """One real run of the whole pipeline, timed, shared by every test that reads it."""
    watched = Stopwatch(live_config.model_server.chat_model(live_config))
    agent = BuyAgent(live_config, llm=watched)
    recorded = Recording(agent.extraction_chain)
    agent.extraction_chain = recorded
    try:
        ranked = agent.run(HEADPHONES.request)
    finally:
        # Handed in, so the agent leaves it open; nothing else asks it anything.
        release(watched.model)

    seen = recorded.seen
    assert seen, "the extraction chain was never invoked"
    assert fake_web, "the agent never fetched the pages it was given"
    return LiveRun(ranked=ranked, extracted=seen[-1], pages=tuple(fake_web), watched=watched)


@pytest.fixture(scope="session")
def benchmarked(
    live_run: LiveRun, live_config: AgentConfig, request: pytest.FixtureRequest
) -> CaseRun:
    """The live run, scored as ``python -m benchmark`` scores one -- the query and the
    time included -- and kept for the session's summary."""
    report = Report(
        scorecard=score_run(
            [entry.product for entry in live_run.ranked],
            live_run.pages,
            key=HEADPHONES.key,
            slots=live_config.num_products,
        ),
        ranked=live_run.ranked,
        pages=live_run.pages,
    )
    contender = Contender.served(live_config.provider, live_config.model, live_config.base_url)
    run = scored_run(contender, HEADPHONES, live_config, live_run.watched, report)
    request.config.stash[SCORED] = run
    return run


@pytest.fixture(scope="session")
def unreachable_base_url() -> str:
    """A loopback address with nothing behind it, for the transport error paths."""
    return _unreachable_base_url()
