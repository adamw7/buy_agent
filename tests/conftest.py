"""Shared fakes. No test in this suite touches the network or Ollama."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.runnables import RunnableLambda

from buy_agent.models import ExtractedProduct, ProductList, SearchQuery
from buy_agent.search import SearchResult


class FakeLLM:
    """Stands in for ChatOllama: returns a canned object per requested schema.

    Only ``with_structured_output`` is needed, because that is the entire surface
    the chains use. Raising is supported so error paths can be exercised.
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

    def with_structured_output(self, schema: type, **_: Any) -> RunnableLambda:
        def respond(value: Any) -> Any:
            self.calls.append(value)
            if self.raises is not None:
                raise self.raises
            return self.query if schema is SearchQuery else self.products

        return RunnableLambda(respond)


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
