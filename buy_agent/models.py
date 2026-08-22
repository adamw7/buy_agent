"""Data models.

Two shapes of "product" live here on purpose:

``ExtractedProduct`` is what the LLM is asked to produce. Every field has a
concrete type and a sentinel for "unknown" (``-1``, ``""``) rather than being
nullable, because Ollama turns the JSON schema into a decoding grammar: a
required ``number`` makes it structurally impossible for a small model to answer
``"N/A"`` and blow up validation for the whole batch.

``Product`` is the domain model the rest of the code uses, where unknown really
is ``None``.
"""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import BaseModel, Field

_UNKNOWN_NUMBER = -1.0
_WHITESPACE = re.compile(r"\s+")


class ExtractedProduct(BaseModel):
    """One product as read out of the search results by the LLM."""

    name: Annotated[str, Field(description="Product name including brand and model.")]
    price: Annotated[
        float,
        Field(description="Numeric price without currency symbol. Use -1 if unknown."),
    ] = _UNKNOWN_NUMBER
    currency: Annotated[
        str, Field(description="ISO currency code such as USD or EUR. Empty if unknown.")
    ] = ""
    rating: Annotated[
        float, Field(description="Average review score on a 0-5 scale. Use -1 if unknown.")
    ] = _UNKNOWN_NUMBER
    review_count: Annotated[
        int, Field(description="Number of reviews the rating is based on. Use 0 if unknown.")
    ] = 0
    seller: Annotated[
        str, Field(description="Shop or site offering it, e.g. Amazon. Empty if unknown.")
    ] = ""
    url: Annotated[
        str, Field(description="Link to the product or the page it was found on.")
    ] = ""
    notes: Annotated[
        str, Field(description="One short sentence on what stands out about this product.")
    ] = ""

    def to_product(self) -> Product:
        """Convert sentinels back into ``None`` and tidy up whitespace."""
        return Product(
            name=_clean(self.name),
            price=self.price if self.price >= 0 else None,
            currency=_clean(self.currency).upper() or None,
            rating=self.rating if 0 <= self.rating <= 5 else None,
            review_count=self.review_count if self.review_count > 0 else None,
            seller=_clean(self.seller) or None,
            url=_clean(self.url) or None,
            notes=_clean(self.notes) or None,
        )


class ProductList(BaseModel):
    """Wrapper schema — Ollama's structured output needs a JSON object at the root."""

    products: Annotated[
        list[ExtractedProduct], Field(description="The products found in the search results.")
    ] = []


class SearchQuery(BaseModel):
    """The shopping-oriented query the LLM rewrites the user's request into."""

    query: Annotated[
        str, Field(description="A web search query likely to surface products for sale.")
    ]


class Product(BaseModel):
    """A product candidate, with unknown fields left as ``None``."""

    name: str
    price: float | None = None
    currency: str | None = None
    rating: float | None = None
    review_count: int | None = None
    seller: str | None = None
    url: str | None = None
    notes: str | None = None

    @property
    def dedup_key(self) -> str:
        """Loose identity: same name modulo case, punctuation and spacing."""
        return _WHITESPACE.sub(" ", re.sub(r"[^\w\s]", " ", self.name.lower())).strip()

    def price_label(self) -> str:
        if self.price is None:
            return "price unknown"
        currency = f" {self.currency}" if self.currency else ""
        return f"{self.price:,.2f}{currency}"

    def rating_label(self) -> str:
        if self.rating is None:
            return "unrated"
        reviews = f" ({self.review_count:,} reviews)" if self.review_count else ""
        return f"{self.rating:.1f}/5{reviews}"


class RankedProduct(BaseModel):
    """A product plus the score it was sorted by."""

    product: Product
    score: float
    rank: int


def _clean(value: str) -> str:
    return _WHITESPACE.sub(" ", value).strip()
