"""Fixtures for the live tests: a real Ollama, a fabricated web."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from typing import TYPE_CHECKING, NoReturn

import pytest

from buy_agent.agent import BuyAgent
from buy_agent.config import AgentConfig
from buy_agent.providers import OLLAMA
from benchmark.corpus import REQUEST, settings
from benchmark.runner import serving_the_corpus
from integration import (
    LIVE_TIMEOUT_SECONDS,
    MODEL_ENV_VAR,
    REQUIRE_ENV_VAR,
    TINY_MODEL,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from typing import Any

    from buy_agent.chat import Chain
    from buy_agent.models import ProductList, RankedProduct
    from buy_agent.search import SearchResult


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Give every live test the longer budget a real model needs."""
    for item in items:
        item.add_marker(pytest.mark.timeout(LIVE_TIMEOUT_SECONDS))


@dataclass(frozen=True, slots=True)
class LiveRun:
    """One end-to-end run, plus what the model said before Python judged it."""

    ranked: list[RankedProduct]
    extracted: ProductList
    pages: tuple[SearchResult, ...]


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
    """The shipped defaults, on the tiny model."""
    return settings(model=tiny_model, base_url=base_url)


@pytest.fixture(scope="session", autouse=True)
def fake_web() -> Iterator[list[SearchResult]]:
    """Hand every agent in this session the corpus instead of the web."""
    with serving_the_corpus() as served:
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
    """One real run of the whole pipeline, shared by every test that reads it."""
    agent = BuyAgent(live_config)
    recorded = Recording(agent.extraction_chain)
    agent.extraction_chain = recorded
    ranked = agent.run(REQUEST)

    seen = recorded.seen
    assert seen, "the extraction chain was never invoked"
    assert fake_web, "the agent never fetched the pages it was given"
    return LiveRun(ranked=ranked, extracted=seen[-1], pages=tuple(fake_web))


@pytest.fixture(scope="session")
def unreachable_base_url() -> str:
    """A loopback address with nothing behind it, for the transport error paths."""
    return _unreachable_base_url()
