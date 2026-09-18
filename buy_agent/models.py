"""Data models (ADR-0004)."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from math import isfinite
from typing import TYPE_CHECKING, Annotated, TypeAlias

from pydantic import BaseModel, Field

from buy_agent.money import amount_label, code_for

if TYPE_CHECKING:
    from collections.abc import Callable

_UNKNOWN_NUMBER = -1.0
_WHITESPACE = re.compile(r"\s+")
_PUNCTUATION = re.compile(r"[^\w\s]")

#: How many opinions a product is reported with.
MAX_OPINIONS = 3

#: Longer than this is not a quote any more; it is the model retelling the page.
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
        # Neither qualifier outlives the figure it describes -- :data:`QUALIFIERS`, one
        # stage earlier than ``verify_numbers``.
        rating = self.rating if 0 <= self.rating <= 5 else None
        # ``> 0`` rather than ``>= 0``, matching ``review_count``: zero is the other
        # thing a model writes for "unknown", and grounding need only find a bare "0" in
        # ten pages of "$0 shipping" for ranking to top the report with it.
        price = self.price if isfinite(self.price) and self.price > 0 else None
        return Product(
            name=_clean(self.name),
            price=price,
            currency=code_for(self.currency) if price is not None else None,
            rating=rating,
            review_count=(
                self.review_count if rating is not None and self.review_count > 0 else None
            ),
            seller=_clean(self.seller) or None,
            url=_clean(self.url) or None,
            notes=_clean(self.notes) or None,
            opinions=_quotes(self.opinions),
        )


class Opinion(BaseModel):
    """One thing a source page said about a product, and the page that said it (ADR-0025,
    ADR-0042, ADR-0017)."""

    text: str
    url: str | None = None


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
    price: Annotated[float | None, Field(allow_inf_nan=False)] = None
    currency: str | None = None
    rating: Annotated[float | None, Field(allow_inf_nan=False, ge=0, le=5)] = None
    review_count: int | None = None
    seller: str | None = None
    url: str | None = None
    #: What the sources say about it, in their words, each beside the page that said it.
    opinions: list[Opinion] = []
    notes: str | None = None

    @property
    def dedup_key(self) -> str:
        """Loose identity: same name modulo case, punctuation and spacing."""
        return _WHITESPACE.sub(" ", _PUNCTUATION.sub(" ", self.name.lower())).strip()

    def price_label(self) -> str:
        if self.price is None:
            return "price unknown"
        return amount_label(self.price, self.currency)

    def rating_label(self) -> str:
        if self.rating is None:
            return "unrated"
        reviews = f" ({self.review_count:,} reviews)" if self.review_count else ""
        return f"{self.rating:.1f}/5{reviews}"


#: Fields that describe another field rather than the product (ADR-0022).
QUALIFIERS: dict[str, tuple[str, ...]] = {
    "price": ("currency",),
    "rating": ("review_count",),
}


def dominant_currency(products: Iterable[Product]) -> str | None:
    """The currency this set of products is priced in, where they agree on one (ADR-0043)."""
    counted = Counter(
        product.currency
        for product in products
        # A currency with no price beside it describes nothing (ADR-0022) and so does
        # not get to decide what the set is counted in.
        if product.price is not None and product.currency is not None
    )
    # ``most_common`` sorts stably, so equal counts stay in first-seen order.
    return counted.most_common(1)[0][0] if counted else None


def comparable_price(product: Product, currency: str | None) -> float | None:
    """``product``'s price on this run's own scale, or ``None`` if it is not on it
    (ADR-0043)."""
    on_the_scale = currency is None or product.currency in (None, currency)
    return product.price if on_the_scale else None


class ScoreParts(BaseModel):
    """What one product's blended score is made of, a share per criterion (ADR-0041)."""

    rating: float
    popularity: float
    price: float
    total: float
    #: The criteria this product published nothing for, each scored ``NEUTRAL``.
    neutral: list[str] = []


class RankedProduct(BaseModel):
    """A product, the score it was sorted by, and what that score is made of."""

    product: Product
    breakdown: ScoreParts
    rank: int

    @property
    def score(self) -> float:
        """The blended score, which is the total of its parts."""
        return self.breakdown.total


class Removal(BaseModel):
    """One candidate that left the report, and what took it out (ADR-0055)."""

    #: The name it was carrying when it went -- the cleaned one where cleaning kept it,
    #: since that is the name the rest of the run would have called it by.
    name: str
    #: Which heuristic removed it, as a word the report can group by.
    step: str
    #: Why, written out: the browser shows this sentence and composes none of its own.
    reason: str


#: How a step hands over what it removed. A step answers its survivors as it always did
#: and says the rest here, which is what keeps the removals out of every signature the
#: pipeline is tested through (ADR-0055).
Recorder: TypeAlias = "Callable[[Removal], None]"


def nothing_recorded(_removal: Removal) -> None:
    """The default recorder: nobody is keeping what the steps took out."""


def _clean(value: str) -> str:
    return _WHITESPACE.sub(" ", value).strip()


def distinct_quotes(values: Iterable[Opinion]) -> list[Opinion]:
    """The first spelling of each quote, at most :data:`MAX_OPINIONS` of them."""
    seen: dict[str, Opinion] = {}
    for quote in values:
        seen.setdefault(quote.text.casefold(), quote)
    return list(seen.values())[:MAX_OPINIONS]


def _quotes(values: list[str]) -> list[Opinion]:
    """Tidy the quoted opinions, dropping blanks, repeats and whole paragraphs (ADR-0017,
    ADR-0042)."""
    cleaned = (_clean(value) for value in values)
    return distinct_quotes(
        Opinion(text=quote)
        for quote in cleaned
        if quote and len(quote) <= _MAX_OPINION_LENGTH
    )
