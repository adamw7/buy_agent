"""Data models (ADR-0004)."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from math import isfinite
from typing import TYPE_CHECKING, Annotated, Literal, TypeAlias, cast, get_args

from pydantic import BaseModel, Field

from buy_agent.money import amount_label, code_for

if TYPE_CHECKING:
    from collections.abc import Callable

_UNKNOWN_NUMBER = -1.0
_WHITESPACE = re.compile(r"\s+")
_PUNCTUATION = re.compile(r"[^\w\s]")

MAX_OPINIONS = 3

#: Longer than this is a retelling, not a quote.
_MAX_OPINION_LENGTH = 240

#: Whether a listing can be bought now, and what state it comes in (ADR-0079).
Availability: TypeAlias = Literal["in stock", "out of stock"]
Condition: TypeAlias = Literal["new", "used", "refurbished"]

#: How a page prints each standing: what grounding looks for and ``fetch`` keeps. "New"
#: and "used" alone are everywhere ("new for 2026", "used it daily"), so each needs the
#: words that make it a listing's condition.
STANDING_PHRASES: dict[str, str] = {
    "in stock": r"(?<!not )(?<!no longer )\bin[-\s]stock\b|\bavailable\s+now\b",
    "out of stock": (
        r"\bout[-\s]of[-\s]stock\b|\bsold[-\s]out\b|\bcurrently\s+unavailable\b"
        r"|\bnot\s+in\s+stock\b|\bno\s+longer\s+available\b|\bdiscontinued\b"
    ),
    "new": r"\bbrand[-\s]new\b|\bcondition:?\s+new\b|\bnew\s+condition\b|\bfactory[-\s]sealed\b",
    "used": (
        r"\bpre[-\s]?owned\b|\bsecond[-\s]?hand\b|\bcondition:?\s+used\b|\bused\s+condition\b"
        r"|\bused\s*[-(:]\s*(?:like\s+new|very\s+good|good|acceptable)\b"
    ),
    "refurbished": r"\brefurbished\b|\brenewed\b|\breconditioned\b",
}

#: What a model may write for each standing, folded; anything else is unknown.
_STANDING_SPELLINGS: dict[str, str] = {
    "in stock": "in stock",
    "in-stock": "in stock",
    "instock": "in stock",
    "available": "in stock",
    "out of stock": "out of stock",
    "out-of-stock": "out of stock",
    "outofstock": "out of stock",
    "sold out": "out of stock",
    "unavailable": "out of stock",
    "discontinued": "out of stock",
    "new": "new",
    "brand new": "new",
    "used": "used",
    "pre-owned": "used",
    "preowned": "used",
    "second-hand": "used",
    "secondhand": "used",
    "refurbished": "refurbished",
    "renewed": "refurbished",
    "reconditioned": "refurbished",
}


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
    availability: Annotated[
        str,
        Field(description='"in stock" or "out of stock", as the results say. Empty if unknown.'),
    ] = ""
    condition: Annotated[
        str,
        Field(
            description='"new", "used" or "refurbished", as the results say. Empty if unknown.'
        ),
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
        """Sentinels back into ``None``; a qualifier never outlives its figure."""
        rating = self.rating if 0 <= self.rating <= 5 else None
        # ``> 0``: models also write 0 for unknown, and "$0 shipping" would ground it.
        price = self.price if isfinite(self.price) and self.price > 0 else None
        return Product(
            name=_clean(self.name),
            price=price,
            currency=code_for(self.currency) if price is not None else None,
            # A listing's standing describes its price, and goes with it (ADR-0022).
            availability=_availability(self.availability) if price is not None else None,
            condition=_condition(self.condition) if price is not None else None,
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
    """A quote about a product, and the page that printed it (ADR-0025, ADR-0042,
    ADR-0017)."""

    text: str
    url: str | None = None


class Offer(BaseModel):
    """One listing's price, currency, shop and page, kept whole by merges (ADR-0058)."""

    price: float
    currency: str | None = None
    seller: str | None = None
    url: str | None = None
    availability: Availability | None = None
    condition: Condition | None = None


class ProductList(BaseModel):
    """Ollama's structured output needs a JSON object at the root."""

    products: Annotated[
        list[ExtractedProduct], Field(description="The products found in the search results.")
    ] = []


class SearchQuery(BaseModel):
    """The query the LLM rewrites the request into."""

    query: Annotated[
        str, Field(description="A web search query likely to surface products for sale.")
    ]


class Product(BaseModel):
    """A product candidate, with unknown fields left as ``None``."""

    name: str
    price: Annotated[float | None, Field(allow_inf_nan=False)] = None
    currency: str | None = None
    #: The headline listing's, grounded as its price is and blanked with it (ADR-0079).
    availability: Availability | None = None
    condition: Condition | None = None
    rating: Annotated[float | None, Field(allow_inf_nan=False, ge=0, le=5)] = None
    review_count: int | None = None
    seller: str | None = None
    url: str | None = None
    opinions: list[Opinion] = []
    #: Every priced listing, the headline among them; seeded by ``deduplicate``.
    offers: list[Offer] = []
    notes: str | None = None

    @property
    def dedup_key(self) -> str:
        return dedup_key(self.name)

    def price_label(self) -> str:
        return price_label(self.price, self.currency)

    def rating_label(self) -> str:
        if self.rating is None:
            return "unrated"
        reviews = f" ({self.review_count:,} reviews)" if self.review_count else ""
        return f"{self.rating:.1f}/5{reviews}"

    def listing_label(self) -> str | None:
        """ "In stock, refurbished", or ``None`` where no page said either (ADR-0079)."""
        said = [value for value in (self.availability, self.condition) if value]
        return ", ".join(said).capitalize() if said else None

    def offers_label(self) -> str | None:
        """The spread of listings' prices in the headline's currency, with the others
        counted and said to be elsewhere; ``None`` for fewer than two (ADR-0058)."""
        if len(self.offers) < 2:
            return None
        listings = f"{len(self.offers)} listings"
        placed = sorted(
            offer.price for offer in self.offers if offer.currency == self.currency
        )
        if not placed:
            return listings
        spread = (
            amount_label(placed[0], self.currency)
            if placed[0] == placed[-1]
            else f"{placed[0]:,.2f}-{amount_label(placed[-1], self.currency)}"
        )
        elsewhere = [offer.currency for offer in self.offers if offer.currency != self.currency]
        if not elsewhere:
            return f"{listings}, {spread}"
        # Said, not only counted: "2 listings, 749.00 USD" read as two shops at 749.00.
        codes = set(elsewhere)
        if codes == {None}:
            where = "with no currency printed"
        elif len(codes) == 1:
            where = f"in {codes.pop()}"
        else:
            where = "in other currencies"
        return f"{listings}: {spread}, and {len(elsewhere)} {where}"


#: Fields that describe another field, and move with it (ADR-0022).
QUALIFIERS: dict[str, tuple[str, ...]] = {
    "price": ("currency", "availability", "condition"),
    "rating": ("review_count",),
}


def dominant_currency(products: Iterable[Product], named: str | None = None) -> str | None:
    """``named`` if given (ADR-0056), else the majority of priced products' (ADR-0043)."""
    if named:
        return named
    counted = Counter(
        product.currency
        for product in products
        # A currency with no price describes nothing (ADR-0022).
        if product.price is not None and product.currency is not None
    )
    # Stable, so equal counts stay in first-seen order.
    return counted.most_common(1)[0][0] if counted else None


def comparable_price(product: Product, currency: str | None) -> float | None:
    """``product``'s price if it is on this run's scale, else ``None`` (ADR-0043)."""
    on_the_scale = currency is None or product.currency in (None, currency)
    return product.price if on_the_scale else None


class ScoreParts(BaseModel):
    """What one product's blended score is made of, a share per criterion (ADR-0041)."""

    rating: float
    popularity: float
    price: float
    total: float
    #: The criteria scored ``NEUTRAL`` for want of a figure.
    neutral: list[str] = []


class RankedProduct(BaseModel):
    """A product, the score it was sorted by, and what that score is made of."""

    product: Product
    breakdown: ScoreParts
    rank: int
    #: The currency the set was ranked in, which a re-sort or a payment reuses rather
    #: than voting again (ADR-0056).
    scale: str | None = None

    @property
    def score(self) -> float:
        return self.breakdown.total


class Removal(BaseModel):
    """One candidate that left the report, and what took it out (ADR-0055)."""

    name: str
    step: str
    #: The sentence the browser shows.
    reason: str


#: How a step reports what it removed, beside returning survivors (ADR-0055).
Recorder: TypeAlias = "Callable[[Removal], None]"


def nothing_recorded(_removal: Removal) -> None:
    """The default recorder."""


def dedup_key(name: str) -> str:
    """A name modulo case, punctuation and spacing; shared with the journal."""
    return _WHITESPACE.sub(" ", _PUNCTUATION.sub(" ", name.lower())).strip()


def price_label(price: float | None, currency: str | None) -> str:
    """A price as every surface writes it, an unknown one included (ADR-0012)."""
    return "price unknown" if price is None else amount_label(price, currency)


def _clean(value: str) -> str:
    return _WHITESPACE.sub(" ", value).strip()


def distinct_quotes(values: Iterable[Opinion]) -> list[Opinion]:
    """The first spelling of each quote, at most :data:`MAX_OPINIONS` of them."""
    seen: dict[str, Opinion] = {}
    for quote in values:
        seen.setdefault(quote.text.casefold(), quote)
    return list(seen.values())[:MAX_OPINIONS]


def _availability(value: str) -> Availability | None:
    """A model's word for whether a listing is in stock, if it is one."""
    return cast("Availability | None", _standing(value, get_args(Availability)))


def _condition(value: str) -> Condition | None:
    """A model's word for the state a listing comes in, if it is one."""
    return cast("Condition | None", _standing(value, get_args(Condition)))


def _standing(value: str, allowed: tuple[str, ...]) -> str | None:
    said = _STANDING_SPELLINGS.get(_clean(value).casefold())
    return said if said in allowed else None


def _quotes(values: list[str]) -> list[Opinion]:
    """Tidy quotes, dropping blanks, repeats and paragraphs (ADR-0042)."""
    cleaned = (_clean(value) for value in values)
    return distinct_quotes(
        Opinion(text=quote) for quote in cleaned if quote and len(quote) <= _MAX_OPINION_LENGTH
    )
