"""Shared fakes. No test in this suite touches the network or Ollama."""

from __future__ import annotations

from typing import Any

import pytest

from buy_agent.models import (
    ExtractedProduct,
    Product,
    ProductList,
    RankedProduct,
    ScoreParts,
    SearchQuery,
)
from buy_agent.search import SearchResult


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
