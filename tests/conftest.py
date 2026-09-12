"""Shared fakes, and where to read this project back off its own disk. No test in
this suite touches the network, Ollama or the developer's own cache directory, and
none of them leaves a logger set."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from buy_agent import mandates
from buy_agent.logging_setup import _NOISY_LIBRARIES, _TRACE_LIBRARIES
from buy_agent.models import (
    ExtractedProduct,
    Opinion,
    Product,
    ProductList,
    RankedProduct,
    ScoreParts,
    SearchQuery,
)
from buy_agent.search import SearchResult

if TYPE_CHECKING:
    from collections.abc import Iterator


#: This repository as it is written, which is not always the tree the suite is
#: running in. ``mutmut run`` copies everything to ``mutants/`` and runs there
#: against a package carrying every mutant of every module at once --
#: ``logger.info(None)`` beside the line it was made from, an inverted branch
#: beside the branch. Two files here are made of rules read off that source rather
#: than exercised -- ``tests/test_conventions.py`` and ``tests/test_architecture.py``
#: -- and a rule read off the source is a rule about the code as *written*, so
#: they read the package from here and answer the same on a Saturday as on any
#: other day. Everything else the copy carries it carries unchanged, so the docs,
#: the workflows, the skills and the TypeScript are read where they sit.
SOURCE_ROOT = Path(__file__).resolve().parents[1]
if SOURCE_ROOT.name == "mutants":
    SOURCE_ROOT = SOURCE_ROOT.parent


#: Skips a test that cannot run without the optional AP2 SDK, the way
#: ``tests/test_start_script.py`` skips what cannot run without a PowerShell.
#: Paying is an optional feature and its SDK is an optional install (two
#: commands, and somebody else's git repository), so a checkout set up with
#: ``requirements-dev.txt`` alone has to come back green: seventy-three
#: *failures* say this project is broken, where seventy-three skips say one
#: feature was not installed. What the marker must never become is a way of not
#: noticing the SDK is missing where it is meant to be there -- ``ci.yml`` and
#: ``mutation.yml`` each install it in a step of their own, so on the runs that
#: matter nothing here is skipped and the coverage floor still has to be met.
#:
#: It covers the whole signing stack and not only the ``ap2`` package: the SDK
#: imports ``jwcrypto`` and ``cryptography``, the two files install together, and
#: a test that generates a key needs them whether or not it names ``ap2`` itself.
#: Four such tests were left unmarked and failed on the very checkout the marker
#: exists for.
#:
#: The other way round costs nothing on the runs that matter and everything on
#: the one this exists for: a test marked here that would have passed anyway is
#: one the dev-only checkout never runs, and nothing goes red to say so. So the
#: marker goes as close to what needs the SDK as pytest allows -- on the
#: ``pytest.param`` where one case of a parametrised test reaches the signing
#: stack and another only fakes the import it is about.
#:
#: Asked once, at import: ``mandates.available()`` defers the ``ap2`` import, so
#: this costs one attempted import for the whole session.
needs_ap2 = pytest.mark.skipif(
    not mandates.available(),
    reason=f"the optional AP2 SDK is not installed -- add it with:  {mandates.INSTALL}",
)


@pytest.fixture(scope="session")
def _scratch_cache(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("cache")


@pytest.fixture(autouse=True)
def cache_somewhere_disposable(
    _scratch_cache: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Point every test's cache at a scratch directory of its own.

    ``autouse``, so "nothing in this suite reads or writes the machine's own
    cache" is a property of the suite rather than of each test remembering: a
    test that builds a real ``BuyAgent`` gets a model that remembers its answers
    on disk (ADR-0044). One directory *per test* rather than one for the suite,
    because two tests asking one model the same question are two tests, and the
    second reading the first's answer would pass without ever reaching the model
    it is about. Nothing is created until something writes.
    """
    named = re.sub(r"[^\w.-]", "_", request.node.name)
    monkeypatch.setenv("BUY_AGENT_CACHE_DIR", str(_scratch_cache / named))


@pytest.fixture(autouse=True)
def pay_with_nothing_of_the_developers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset the two payment variables for every test in the suite.

    ``autouse`` for the reason the cache directory is, and more so: these name a
    signing key and a pre-signed open mandate, so a developer who has set either
    would otherwise have a suite that signs with their key and buys on their
    budget. Unset, every test that wants one points at a file it made itself.
    """
    monkeypatch.delenv("BUY_AGENT_AP2_KEY", raising=False)
    monkeypatch.delenv("BUY_AGENT_AP2_MANDATE", raising=False)
    monkeypatch.delenv("BUY_AGENT_MERCHANT_URL", raising=False)


#: Every logger ``configure_logging`` sets a level on, which is every logger a
#: test can leave changed for the ones after it. The root is on the list because
#: that function sets it itself rather than leaving it to ``basicConfig``, which
#: does nothing where a handler is already installed -- and under pytest one
#: always is.
_LEVELS_CONFIGURE_LOGGING_SETS = ("", "buy_agent", *_NOISY_LIBRARIES, *_TRACE_LIBRARIES)


@pytest.fixture(autouse=True)
def leave_every_logger_as_it_was() -> Iterator[None]:
    """Put back every level ``configure_logging`` sets, after every test.

    ``autouse`` for the reason the cache directory is: three entry points call
    that function -- the CLI, the server and the benchmark -- and a test that
    runs one of them is otherwise deciding how loud every later test is. What
    that costs is not a failure where it happened but a ``caplog`` assertion
    going quiet three files further on, or a branch that only runs at a level
    somebody else already set. A level and not the handlers: those are installed
    per-test where they matter, and ``caplog`` manages its own.
    """
    kept = [
        (logger, logger.level)
        for logger in map(logging.getLogger, _LEVELS_CONFIGURE_LOGGING_SETS)
    ]
    yield
    for logger, level in kept:
        logger.setLevel(level)


class FakeLLM:
    """Stands in for a model server: a canned object per requested schema.

    ``answer`` is the whole of :class:`buy_agent.chat.ChatModel`, so this is a
    class with one method. The schema it is asked for says which of the two
    chains is calling. Raising is supported so error paths can be exercised.
    """

    def __init__(
        self,
        *,
        query: SearchQuery | None = None,
        products: ProductList | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.query = query or SearchQuery(query="fake refined query")
        self.products = products or ProductList()
        self.raises = raises
        self.calls: list[Any] = []

    def answer(self, messages: Any, schema: type) -> Any:
        self.calls.append(messages)
        if self.raises is not None:
            raise self.raises
        return self.query if schema is SearchQuery else self.products


def said(*quotes: str, page: str | None = None) -> list[Opinion]:
    """Quotes as a grounded product carries them: words beside the page that
    printed them (ADR-0042). ``page`` is one link for all of them, which is what
    a product quoted off a single result has."""
    return [Opinion(text=quote, url=page) for quote in quotes]


def ranked_product(product: Product, *, score: float, rank: int) -> RankedProduct:
    """One finished ranking entry with the score a test wants it to have.

    ``rank_products`` is what builds these in a run, and its scores fall where
    the arithmetic puts them -- so a test about something else (a payload's
    shape, the report's wording, a rounding) says the score it needs and gets a
    breakdown that agrees with it: three shares blending to exactly that, and
    ``neutral`` naming whatever this product genuinely published nothing for.
    """
    assumed = [
        name
        for name, figure in (
            ("rating", product.rating),
            ("popularity", product.review_count),
            ("price", product.price),
        )
        if figure is None
    ]
    return RankedProduct(
        product=product,
        breakdown=ScoreParts(
            rating=score, popularity=score, price=score, total=score, neutral=assumed
        ),
        rank=rank,
    )


def payable_product(**extra: Any) -> Product:
    """A product a run really could pay for, and the one four files needed.

    Priced, in a currency, off a page that was searched: every one of the things
    ``payment._check`` refuses a product for missing, and every one of them
    something grounding would have had to leave standing. Written out once
    because four files were carrying the same seven lines, and a rule added to
    that check would have had to be answered in all four. ``extra`` overrides as
    well as adds, so a test about one field says that field and nothing else.
    """
    return Product(
        **{
            "name": "Sony WH-1000XM5",
            "price": 329.99,
            "currency": "USD",
            "seller": "AudioSite",
            "url": "https://audiosite.example/xm5",
        }
        | extra
    )


@pytest.fixture
def search_results() -> list[SearchResult]:
    return [
        SearchResult(
            title="Sony WH-1000XM5 review",
            url="https://example.com/sony",
            snippet="$328, rated 4.7 out of 5 from 12000 reviews.",
        ),
        SearchResult(
            title="Anker Soundcore Q30",
            url="https://example.com/anker",
            snippet="$79, rated 4.3 out of 5 from 90000 reviews.",
        ),
        SearchResult(
            title="Unknown Brand Buds land this week",
            url="https://example.com/unknown",
            snippet="The Unknown Brand Buds are out now. No word on pricing.",
        ),
    ]


@pytest.fixture
def extracted_products() -> ProductList:
    return ProductList(
        products=[
            ExtractedProduct(
                name="Sony WH-1000XM5",
                price=328.0,
                currency="usd",
                rating=4.7,
                review_count=12000,
                seller="Amazon",
                url="https://example.com/sony",
                notes="Best noise cancelling.",
            ),
            ExtractedProduct(
                name="Anker Soundcore Q30",
                price=79.0,
                currency="USD",
                rating=4.3,
                review_count=90000,
                seller="Anker",
                url="https://example.com/anker",
            ),
            ExtractedProduct(name="Unknown Brand Buds"),
        ]
    )
