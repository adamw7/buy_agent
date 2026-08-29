"""Data models.

Two shapes of "product" on purpose. ``ExtractedProduct`` is what the LLM is asked
for: every field concrete, with a sentinel for "unknown" (``-1``, ``""``, ``[]``)
rather than nullable, because the JSON schema becomes a decoding grammar and a
required ``number`` makes it structurally impossible for a small model to answer
``"N/A"`` and blow up validation for the whole batch. ``Product`` is the domain
model the rest of the code uses, where unknown really is ``None``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Annotated

from pydantic import BaseModel, Field

_UNKNOWN_NUMBER = -1.0
_WHITESPACE = re.compile(r"\s+")
_PUNCTUATION = re.compile(r"[^\w\s]")

#: How many opinions a product is reported with. Three fits a card and a log block
#: without turning either into a review page -- and asking a small model for more
#: only trades quotes it read for quotes it wrote.
MAX_OPINIONS = 3

#: Longer than this is not a quote any more; it is the model retelling the page.
#: An over-long one is dropped rather than cut short, the way an over-long name
#: is: half a sentence attributed to a reviewer says something they did not.
_MAX_OPINION_LENGTH = 240


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
    opinions: Annotated[
        list[str],
        Field(
            description=(
                "Up to 3 short quotes, copied word for word from the results, saying "
                "what this product is like to own -- praise, complaints, a verdict. "
                "Empty list if the results give no opinion about it."
            )
        ),
    ] = []

    def to_product(self) -> Product:
        """Convert sentinels back into ``None`` and tidy up whitespace."""
        # A count is only a fact about the product while the rating it counts
        # stands -- :data:`QUALIFIERS` one stage earlier than ``verify_numbers``.
        # Read out here because the count below is judged against it.
        rating = self.rating if 0 <= self.rating <= 5 else None
        return Product(
            name=_clean(self.name),
            # ``> 0`` rather than ``>= 0``, matching ``review_count``: zero is not
            # a price anyone pays but the other thing a model writes for "unknown".
            # Kept, it is worse than a blank -- grounding need only find a bare "0"
            # in ten pages of "$0 shipping", and ranking then calls it the cheapest
            # in the set and hands it the top spot.
            price=self.price if self.price > 0 else None,
            currency=_clean(self.currency).upper() or None,
            rating=rating,
            # Left standing on its own it would read as "unrated" beside nothing
            # and still feed the popularity half of the score (ADR-0022).
            review_count=(
                self.review_count if rating is not None and self.review_count > 0 else None
            ),
            seller=_clean(self.seller) or None,
            url=_clean(self.url) or None,
            notes=_clean(self.notes) or None,
            opinions=_quotes(self.opinions),
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
    #: What the sources say about it, in their words. A list rather than a
    #: nullable field: "nobody said anything" and "no opinion survived
    #: grounding" are the same empty answer, and a second way to spell it --
    #: ``None`` beside ``[]`` -- would be one every caller had to handle.
    opinions: list[str] = []
    notes: str | None = None

    @property
    def dedup_key(self) -> str:
        """Loose identity: same name modulo case, punctuation and spacing."""
        return _WHITESPACE.sub(" ", _PUNCTUATION.sub(" ", self.name.lower())).strip()

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


#: Fields that describe another field rather than the product (ADR-0022). A
#: currency is a fact about *that listing's* price and a review count is what
#: *that listing's* rating was averaged over, so a figure carries its qualifiers
#: wherever it moves and takes them down wherever it is rejected. Left standing
#: alone either describes a figure it was never printed against: "129.00 EUR" out
#: of a page saying 129 and a page saying "249 EUR", or a count whose rating has
#: just been thrown out, reading "unrated" beside nothing while still feeding the
#: popularity half of the score.
#:
#: Beside the fields it names, because both places that move a figure need it
#: (:func:`buy_agent.extraction._fill_gaps`,
#: :func:`buy_agent.verification.verify_numbers`); stated twice, the two could
#: disagree about what a review count belongs to.
QUALIFIERS: dict[str, tuple[str, ...]] = {
    "price": ("currency",),
    "rating": ("review_count",),
}


class RankedProduct(BaseModel):
    """A product plus the score it was sorted by."""

    product: Product
    score: float
    rank: int


def _clean(value: str) -> str:
    return _WHITESPACE.sub(" ", value).strip()


def distinct_quotes(values: Iterable[str]) -> list[str]:
    """The first spelling of each quote, at most :data:`MAX_OPINIONS` of them.

    Identity is the casefolded text -- two listings quoting one reviewer differ by
    capitalisation and nothing else -- and the spelling kept is the earlier
    listing's, the tie-break the merge makes everywhere else.
    """
    seen: dict[str, str] = {}
    for quote in values:
        seen.setdefault(quote.casefold(), quote)
    return list(seen.values())[:MAX_OPINIONS]


def _quotes(values: list[str]) -> list[str]:
    """Tidy the quoted opinions, dropping blanks, repeats and whole paragraphs."""
    cleaned = (_clean(value) for value in values)
    return distinct_quotes(
        quote for quote in cleaned if quote and len(quote) <= _MAX_OPINION_LENGTH
    )
