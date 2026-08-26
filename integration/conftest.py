"""Fixtures for the live tests: a real Ollama, a fabricated web.

One session-scoped run does the expensive part. A CPU-only model answers in
seconds rather than milliseconds, so a fixture per test would be a fixture per
inference, and the nightly job has five minutes for the lot -- pull included.
:func:`live_run` therefore runs the pipeline once and every test asserts
something different about the same answer.

The two chains are the only things not faked. ``search_web`` and ``enrich`` are
monkeypatched to hand back :data:`PAGES`, so what the model reads is fixed text
this file owns: an assertion about a real shop's listing would be a nightly
failure about the shop.
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from typing import TYPE_CHECKING, NoReturn

import pytest
from langchain_core.runnables import RunnableLambda

from buy_agent import agent as agent_module
from buy_agent.agent import BuyAgent, list_models
from buy_agent.config import DEFAULT_BASE_URL, AgentConfig
from buy_agent.search import SearchResult
from integration import MODEL_ENV_VAR, REQUIRE_ENV_VAR, TINY_MODEL

if TYPE_CHECKING:
    from collections.abc import Iterator

    from buy_agent.models import ProductList, RankedProduct

#: The web these tests search: three pages, each printing a price, a rating, a
#: review count and a sentence somebody could be quoted on. Fabricated, and
#: written the way review pages are -- "$328", "4.7 out of 5", "12,480 reviews"
#: -- because that phrasing is what ``verification`` looks for, and a page that
#: worded it differently would test the fixture rather than the pipeline.
#:
#: The third is a listicle, which is the mistake this pipeline exists to catch:
#: a small model reports its headline as a product, and ``clean_products`` is
#: what stops "9 Best Noise Cancelling Headphones Under $400" reaching the top 3.
PAGES: tuple[SearchResult, ...] = (
    SearchResult(
        title="Sony WH-1000XM5 review: still the one to beat | AudioSite",
        url="https://audiosite.example/sony-wh-1000xm5-review",
        snippet="The Sony WH-1000XM5 sells for $328 and is rated 4.7 out of 5.",
        content=(
            "The Sony WH-1000XM5 costs $328 at most shops. "
            "Rated 4.7 out of 5 from 12,480 reviews. "
            "The noise cancelling is still the best we have tested. "
            "The case no longer folds flat, which is a real annoyance in a bag."
        ),
    ),
    SearchResult(
        title="Anker Soundcore Space Q45 - price and review",
        url="https://headphonebarn.example/anker-space-q45",
        snippet="Anker Soundcore Space Q45, $99, rated 4.4 out of 5.",
        content=(
            "The Anker Soundcore Space Q45 is $99. "
            "Rated 4.4 out of 5 from 31,200 reviews. "
            "Battery life is enormous and the price is hard to argue with. "
            "The app is cluttered and the treble is dull out of the box."
        ),
    ),
    SearchResult(
        title="9 Best Noise Cancelling Headphones Under $400 | GearRoundup",
        url="https://gearroundup.example/best-noise-cancelling",
        snippet="Our picks: the Sony WH-1000XM5 at $328, the Sennheiser Accentum at $179.",
        content=(
            "The Sony WH-1000XM5 remains our overall pick at $328. "
            "The Sennheiser Accentum is $179 and rated 4.2 out of 5 from 3,400 reviews. "
            "Comfort is excellent on a long flight. "
            "Call quality is merely acceptable."
        ),
    ),
)

#: What the shopper typed. Deliberately vague, so query refinement has something
#: to do: a request that already reads like a query proves nothing about it.
REQUEST = "comfortable noise cancelling headphones for flights, under $350"


@dataclass(frozen=True, slots=True)
class LiveRun:
    """One end-to-end run, plus what the model said before Python judged it.

    ``extracted`` is the extraction chain's own answer, kept so a test can tell
    the two failures apart: a model that read nothing off the pages, and a model
    that read plenty and had it all thrown out by grounding.
    """

    ranked: list[RankedProduct]
    extracted: ProductList
    pages: tuple[SearchResult, ...]


def _unreachable_base_url() -> str:
    """A loopback URL nothing is listening on.

    Bound and closed rather than picked out of the air: a port the kernel just
    handed out is one no other test in this repository, and no service on the
    runner, is sitting on.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}"


@pytest.fixture(scope="session")
def base_url() -> str:
    """Where Ollama is, honouring ``$OLLAMA_HOST`` as the rest of the project does."""
    return DEFAULT_BASE_URL


@pytest.fixture(scope="session")
def tiny_model(base_url: str) -> str:
    """The model tag to test against, once it is known to be pulled.

    Missing Ollama is a skip on a developer's machine and a failure on the
    nightly runner: locally these tests are opt-in, and a scheduled run that
    quietly skipped every one of them would be a green job reporting nothing.
    ``.github/workflows/integration.yml`` is what sets :data:`REQUIRE_ENV_VAR`.
    """
    tag = os.getenv(MODEL_ENV_VAR, TINY_MODEL)
    try:
        installed = list_models(base_url)
    except Exception as exc:  # noqa: BLE001 -- any transport failure means "not there"
        _absent(f"No Ollama at {base_url} ({exc}). Start it with: ollama serve")
    if tag not in installed:
        _absent(f"Ollama at {base_url} has no {tag!r}. Pull it with: ollama pull {tag}")
    return tag


def _absent(reason: str) -> NoReturn:
    """Skip, unless the environment says these tests were meant to run.

    ``NoReturn`` because both branches raise: the caller reads ``installed``
    straight after, and it is bound only where this was not reached.
    """
    if os.getenv(REQUIRE_ENV_VAR):
        pytest.fail(reason)
    pytest.skip(reason)


@pytest.fixture(scope="session")
def live_config(tiny_model: str, base_url: str) -> AgentConfig:
    """The shipped defaults, on the tiny model.

    Only the model, the server and the widths move. ``temperature``, ``num_ctx``
    and ``reasoning`` are left exactly as the agent ships them (ADR-0019),
    because whether those defaults still make a small model answer with JSON
    instead of thinking until the context runs out is one of the things a live
    run is here to find out.
    """
    return AgentConfig(
        model=tiny_model,
        base_url=base_url,
        search_results=len(PAGES),
        num_products=5,
        top_n=3,
    )


@pytest.fixture(scope="session")
def fake_web() -> Iterator[None]:
    """Hand every agent in this session :data:`PAGES` instead of the web.

    ``monkeypatch`` is function-scoped, so the patching is done by hand: these
    two names are replaced for the whole session and put back at the end of it.
    """

    def search(query: str, *, max_results: int = 10, region: str = "us-en") -> list:
        return list(PAGES[:max_results])

    def enrich(results: list, **_: object) -> list:
        return results

    original = agent_module.search_web, agent_module.enrich
    agent_module.search_web, agent_module.enrich = search, enrich
    yield
    agent_module.search_web, agent_module.enrich = original


@pytest.fixture(scope="session")
def live_run(live_config: AgentConfig, fake_web: None) -> LiveRun:
    """One real run of the whole pipeline, shared by every test that reads it.

    The extraction chain is wrapped rather than replaced: the appended step
    records what came back and hands it straight on, so the run underneath is
    the one ``BuyAgent`` would have made on its own.
    """
    seen: list[ProductList] = []

    def record(products: ProductList) -> ProductList:
        seen.append(products)
        return products

    agent = BuyAgent(live_config)
    agent.extraction_chain = agent.extraction_chain | RunnableLambda(record)
    ranked = agent.run(REQUEST)

    assert seen, "the extraction chain was never invoked"
    return LiveRun(ranked=ranked, extracted=seen[-1], pages=PAGES)


@pytest.fixture(scope="session")
def unreachable_base_url() -> str:
    """A loopback address with nothing behind it, for the transport error paths."""
    return _unreachable_base_url()
