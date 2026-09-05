"""Fixtures for the live tests: a real Ollama, a fabricated web.

One session-scoped run does the expensive part. A CPU-only model answers in
seconds rather than milliseconds, so a fixture per test would be a fixture per
inference, and the nightly job has five minutes for the lot -- pull included.
:func:`live_run` therefore runs the pipeline once and every test asserts
something different about the same answer.

The model is the only thing not faked, and the fake stops at the *transport*:
:func:`benchmark.runner.serving_the_corpus` hands ``search_web`` the fabricated
pages and lets ``enrich`` read their text instead of fetching a URL -- but what
it does with that text is :func:`buy_agent.fetch.condense`, the real one, on the
real ``page_chars`` and ``opinion_chars`` budgets. So the prompt the model sees
here is shaped the way a production prompt is shaped: newline-separated lines
that quote a figure or pass judgement, and nothing else.

That matters more than it looks. Handing the model tidy prose the fetch layer
would never have produced tests it on input it will never meet, and -- since
``build_haystack`` runs over the same condensed text -- it also lets a quote be
"verified" against a sentence :mod:`buy_agent.fetch` would have thrown away.
Both halves of ADR-0025 are about text this project produces, so the live run
produces it.

The corpus, the request and the widths come from :mod:`benchmark.corpus` rather
than living here, because ``integration/test_benchmark.py`` scores this same run
against the answer key beside it (ADR-0036). One corpus and one model call for
both questions: whether the promises held, and how well the model did.
"""

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
from integration import MODEL_ENV_VAR, REQUIRE_ENV_VAR, TINY_MODEL

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from typing import Any

    from buy_agent.chat import Chain
    from buy_agent.models import ProductList, RankedProduct
    from buy_agent.search import SearchResult


@dataclass(frozen=True, slots=True)
class LiveRun:
    """One end-to-end run, plus what the model said before Python judged it.

    ``extracted`` is the extraction chain's own answer, kept so a test can tell
    the two failures apart: a model that read nothing off the pages, and a model
    that read plenty and had it all thrown out by grounding.

    ``pages`` is what the agent was actually given -- the results as ``enrich``
    left them, condensed. Not :data:`benchmark.corpus.PAGES`, whose ``content``
    is still empty:
    grounding runs over the condensed text, so a test that rebuilt its haystack
    out of the un-enriched results would be checking a different corpus from the
    one the pipeline checked against.
    """

    ranked: list[RankedProduct]
    extracted: ProductList
    pages: tuple[SearchResult, ...]


def _unreachable_base_url() -> str:
    """A loopback URL nothing is listening on.

    A port the kernel has just handed out and nothing has bound since, which is
    the closest thing to a reserved one available: closing it does not hold it,
    so this is a small race rather than a guarantee. What it buys over a
    hard-coded number is that no other test here, and no service on the runner,
    is already sitting on it.
    """
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
    """The model tag to test against, once it is known to be pulled.

    Missing Ollama is a skip on a developer's machine and a failure on the
    nightly runner: locally these tests are opt-in, and a scheduled run that
    quietly skipped every one of them would be a green job reporting nothing.
    ``.github/workflows/integration.yml`` is what sets :data:`REQUIRE_ENV_VAR`.
    """
    tag = os.getenv(MODEL_ENV_VAR, TINY_MODEL)
    try:
        probe = AgentConfig(base_url=base_url)
        installed = [model.name for model in probe.model_server.installed(probe)]
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
    run is here to find out -- which needs a prompt wide enough for the question
    to arise. Ten condensed pages put the extraction prompt at ~9.5k characters,
    near enough 2.4k tokens: comfortable on the 8192 this asks for, and about
    1.7k left for thinking and JSON on Ollama's 4096 default, which is where a
    thinking model runs out. It is not the ~4.3k a ten-result run of real pages
    reaches -- fabricated pages are thinner than fetched ones -- but it is the
    same order, where three pages of prose were not.

    ``search_results`` is therefore the shipped default rather than a number
    chosen here. ``num_products`` is below the seven distinct products the pages
    name, so ``deduplicate``'s limit is a cap that has to bite rather than a
    ceiling nothing reaches.

    All of which is :func:`benchmark.corpus.settings`, which the benchmark also
    scores against: only the model and the server are this file's to say.
    """
    return settings(model=tiny_model, base_url=base_url)


@pytest.fixture(scope="session", autouse=True)
def fake_web() -> Iterator[list[SearchResult]]:
    """Hand every agent in this session the corpus instead of the web.

    ``autouse``, so that "nothing here reaches DuckDuckGo" is a property of the
    package rather than of each test remembering to ask for it. The tests that
    point an agent at a stopped Ollama call ``run()`` too, and today they are
    safe only because ``_refine_query`` re-raises ``ModelUnavailableError``
    before the search -- one change to that and an unrelated failure would start
    going out over the network.

    The patching is :func:`benchmark.runner.serving_the_corpus`, which is also
    what a scripted benchmark run uses: two copies of it would be two ideas of
    what the model was shown, and the answer key can only be about one of them.
    It yields the *condensed* results, which :func:`live_run` keeps -- the
    pipeline grounds against that text, so the tests have to read it and not the
    raw page.

    ``monkeypatch`` is function-scoped, so this holds the corpus open for the
    whole session and puts the two names back at the end of it.
    """
    with serving_the_corpus() as served:
        yield served


class Recording:
    """The extraction chain, with what it answered kept on the way past.

    A wrapper and not a replacement: ``invoke`` is the whole of a chain's surface,
    so delegating it leaves the run underneath the one ``BuyAgent`` would have
    made on its own -- the same prompt, the same schema, the same model.
    """

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
