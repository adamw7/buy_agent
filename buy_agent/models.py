"""Data models.

Two shapes of "product" on purpose. ``ExtractedProduct`` is what the LLM is asked
for: every field concrete, with a sentinel for "unknown" (``-1``, ``""``, ``[]``)
rather than nullable -- the JSON schema becomes a decoding grammar, and a required
``number`` makes it structurally impossible for a small model to answer ``"N/A"``
and fail validation for the whole batch. ``Product`` is the domain model the rest
of the code uses, where unknown really is ``None``.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from typing import Annotated

from pydantic import BaseModel, Field

_UNKNOWN_NUMBER = -1.0
_WHITESPACE = re.compile(r"\s+")
_PUNCTUATION = re.compile(r"[^\w\s]")

#: How many opinions a product is reported with. Three fits a card and a log block
#: without turning either into a review page, and asking a small model for more
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
        # Neither qualifier outlives the figure it describes -- :data:`QUALIFIERS`,
        # one stage earlier than ``verify_numbers``. A model that reads a currency
        # off a page and no price to go with it has read a fact about nothing.
        rating = self.rating if 0 <= self.rating <= 5 else None
        # ``> 0`` rather than ``>= 0``, matching ``review_count``: zero is the
        # other thing a model writes for "unknown", and kept it is worse than a
        # blank -- grounding need only find a bare "0" in ten pages of "$0
        # shipping", and ranking then calls it the cheapest and tops the report.
        price = self.price if self.price > 0 else None
        return Product(
            name=_clean(self.name),
            price=price,
            currency=(_clean(self.currency).upper() or None) if price is not None else None,
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
    """One thing a source page said about a product, and the page that said it.

    The quote and its page are one fact, not two: ``verify_opinions`` keeps a
    verdict only where *one page that mentions this product* printed it
    (ADR-0025), so which page that was is known where the quote is kept, and it
    is the only way a shopper can check a quote -- a figure is checked by
    following ``Product.url`` (ADR-0042).

    ``url`` is nullable because a result can carry no URL of its own, and the page
    printed the words all the same. It is never the model's: like ``Product.url``
    it is written out of the results that were searched, and
    :class:`ExtractedProduct` is never asked for one (ADR-0017).
    """

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
    price: float | None = None
    currency: str | None = None
    rating: float | None = None
    review_count: int | None = None
    seller: str | None = None
    url: str | None = None
    #: What the sources say about it, in their words, each beside the page that
    #: said it. A list rather than a nullable field: "nobody said anything" and
    #: "no opinion survived grounding" are one empty answer, and a second
    #: spelling of it -- ``None`` beside ``[]`` -- would be one every caller had
    #: to handle.
    opinions: list[Opinion] = []
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
#: of a page saying 129 and a page saying "249 EUR", or a count reading "unrated"
#: beside nothing while still feeding the popularity half of the score.
#:
#: Declared beside the fields it names, because both places that move a figure
#: need it (:func:`buy_agent.extraction._fill_gaps`,
#: :func:`buy_agent.verification.verify_numbers`).
QUALIFIERS: dict[str, tuple[str, ...]] = {
    "price": ("currency",),
    "rating": ("review_count",),
}


def dominant_currency(products: Iterable[Product]) -> str | None:
    """The currency this set of products is priced in, where they agree on one.

    The most common currency among the priced products that named one, ties going
    to the one seen first -- which is the search's own order, and the tie-break
    every other merge here makes. ``None`` where no priced product named a
    currency at all: nothing then says the figures are in different ones, and they
    are compared as they always were.

    A run's prices are only comparable inside one currency (ADR-0043), and this is
    the one that gets to be it. Not converted -- no rate is shipped and a stale one
    is a wrong ranking dressed as a right one -- so what is outside it is treated
    as a figure this run cannot place rather than as a smaller number.
    """
    counted = Counter(
        product.currency
        for product in products
        # A currency with no price beside it describes nothing (ADR-0022) and so
        # does not get to decide what the set is counted in.
        if product.price is not None and product.currency is not None
    )
    # ``most_common`` sorts stably, so equal counts stay in first-seen order.
    return counted.most_common(1)[0][0] if counted else None


def comparable_price(product: Product, currency: str | None) -> float | None:
    """``product``'s price on this run's own scale, or ``None`` if it is not on it.

    A price the page printed without a currency is taken as the run's own: that is
    what every price in this pipeline was until ADR-0043, it is what a search in
    one region overwhelmingly returns, and the alternative -- refusing to place the
    commonest shape of price there is -- would score most sets on nothing.

    A price in some *other* currency is not a smaller or a bigger number, it is a
    number this run cannot place. ``None`` is how that is said, which is the same
    answer a price nobody published gets -- and is why an unpriced product needs
    no case of its own here: its ``None`` price is already the answer.
    """
    on_the_scale = currency is None or product.currency in (None, currency)
    return product.price if on_the_scale else None


class ScoreParts(BaseModel):
    """What one product's blended score is made of, a share per criterion.

    Reported rather than kept, because a bare 0.62 says nothing about *why* a
    product placed where it did -- and the one thing a shopper most needs to tell
    apart is a criterion that scored middling from one that was never known at
    all, which :data:`buy_agent.ranking.NEUTRAL` deliberately makes look identical
    (ADR-0041). ``neutral`` names the criteria that were assumed rather than read,
    so the two are separable again by whoever is showing them.

    Every share is in ``[0, 1]`` and none is weighted: how much each counts is
    ``RankingWeights``, which is one setting for the whole run rather than a fact
    about this product. ``total`` is what they blend to, and the number the
    ordering was actually made on.
    """

    rating: float
    popularity: float
    price: float
    total: float
    #: The criteria this product published nothing for, each scored ``NEUTRAL``.
    #: A list rather than three booleans: it is read as a set of names, and the
    #: names are the field names above.
    neutral: list[str] = []


class RankedProduct(BaseModel):
    """A product, the score it was sorted by, and what that score is made of."""

    product: Product
    breakdown: ScoreParts
    rank: int

    @property
    def score(self) -> float:
        """The blended score, which is the total of its parts.

        A property and not a field, so there is one number and not two that agree
        until somebody constructs a ``RankedProduct`` by hand and they do not.
        """
        return self.breakdown.total


def _clean(value: str) -> str:
    return _WHITESPACE.sub(" ", value).strip()


def distinct_quotes(values: Iterable[Opinion]) -> list[Opinion]:
    """The first spelling of each quote, at most :data:`MAX_OPINIONS` of them.

    Identity is the casefolded *text* and not the pair: two listings quoting one
    reviewer differ by capitalisation and by which page was read, and they are
    still one quote. The one kept is the earlier -- the tie-break the merge makes
    everywhere else -- which is also what keeps a quote pointing at the first page
    that printed it, the page :func:`buy_agent.verification.attribute_sources`
    would have picked.
    """
    seen: dict[str, Opinion] = {}
    for quote in values:
        seen.setdefault(quote.text.casefold(), quote)
    return list(seen.values())[:MAX_OPINIONS]


def _quotes(values: list[str]) -> list[Opinion]:
    """Tidy the quoted opinions, dropping blanks, repeats and whole paragraphs.

    Every one comes out pointing at nothing: the model is asked for the words and
    never for the page, which is
    :func:`buy_agent.verification.verify_opinions`' to fill in out of the pages
    that were actually searched (ADR-0017, ADR-0042).
    """
    cleaned = (_clean(value) for value in values)
    return distinct_quotes(
        Opinion(text=quote)
        for quote in cleaned
        if quote and len(quote) <= _MAX_OPINION_LENGTH
    )
