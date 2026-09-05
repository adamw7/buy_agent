"""Shared fakes. No test in this suite touches the network, Ollama or the
developer's own cache directory."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import pytest

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
    from pathlib import Path


@pytest.fixture(scope="session")
def _scratch_cache(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("cache")


@pytest.fixture(autouse=True)
def cache_somewhere_disposable(
    _scratch_cache: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Point every test's cache at a scratch directory of its own.

    ``autouse``, so "nothing in this suite reads or writes the machine's own
    cache" is a property of the suite rather than of each test remembering. It is
    the same care taken over the network: a test that builds a real ``BuyAgent``
    gets a model that remembers its answers on disk (ADR-0044), and without this
    the disk would be the developer's -- one run's answers surviving into another
    run's assertions, and a suite that passes only on a machine that has run it
    before.

    One directory per test rather than one for the suite, for the same reason:
    two tests asking one model the same question are two tests, and the second
    reading the first's answer would pass without the model it is about being
    reached at all. Nothing is created until something writes, so this costs the
    tests that never touch a cache nothing.

    The tests that have something to say about ``$BUY_AGENT_CACHE_DIR`` set it
    themselves, and ``monkeypatch`` puts this back afterwards.
    """
    named = re.sub(r"[^\w.-]", "_", request.node.name)
    monkeypatch.setenv("BUY_AGENT_CACHE_DIR", str(_scratch_cache / named))


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
