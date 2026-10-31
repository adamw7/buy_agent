"""Whether a run found the price the shopper is waiting for (ADR-0080). An alert is
told, never applied: it removes nothing and reorders nothing."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from buy_agent.models import and_list, comparable_price, dominant_currency
from buy_agent.money import amount_label, placeable

if TYPE_CHECKING:
    from collections.abc import Sequence

    from buy_agent.models import Product, RankedProduct


@dataclass(frozen=True, slots=True)
class Alert:
    """What a run said about the shopper's price alert."""

    below: float
    below_label: str
    #: The products at or under it that can be bought, cheapest first.
    met: list[str]
    #: The sentence both front ends show.
    detail: str

    def payload(self) -> dict[str, Any]:
        return asdict(self)


def price_alert(ranked: Sequence[RankedProduct], below: float, named: str | None = None) -> Alert:
    """Which products are priced at or under ``below`` in the run's currency and not out
    of stock (ADR-0079); an unknown or unplaceable price never meets it, nor one no page
    printed a currency for, which paying will not take either (ADR-0046)."""
    products = [entry.product for entry in ranked]
    currency = dominant_currency(products, named)
    line = amount_label(below, currency)
    # The vote may land on "¥" (ADR-0054), which ranks but is no amount to pay.
    countable = currency is not None and placeable(currency) is not None
    priced = sorted(
        (
            (price, product)
            for product in products
            # Ranking counts a bare price on the run's scale (ADR-0043); this does not.
            if countable
            and product.currency is not None
            and (price := comparable_price(product, currency)) is not None
        ),
        key=lambda pair: pair[0],
    )
    buyable = [(price, product) for price, product in priced if _buyable(product)]
    met = [(price, product) for price, product in buyable if price <= below]
    gone = [product.name for price, product in priced if price <= below and not _buyable(product)]
    # Under the line on a figure alone: said, as an out-of-stock one is, and never met.
    bare = [
        product.name
        for product in sorted(products, key=lambda product: product.price or 0.0)
        if product.currency is None and product.price is not None and product.price <= below
    ]

    if met:
        listed = and_list([f"{product.name} at {_at(price, product)}" for price, product in met])
        detail = f"At or under {line}: {listed}."
    elif buyable:
        cheapest_price, cheapest = buyable[0]
        detail = (
            f"Nothing found is at or under {line}; the cheapest is "
            f"{cheapest.name} at {_at(cheapest_price, cheapest)}."
        )
    elif priced:
        detail = f"Nothing found that can be bought is at or under {line}."
    elif currency and not countable:
        detail = (
            f"Nothing found is at or under {line}: {currency} names no currency this run "
            f"can count in, and nothing is converted."
        )
    elif currency:
        detail = (
            f"Nothing found is at or under {line}: no page printed a price this run "
            f"can count in {currency}."
        )
    elif any(product.price is not None for product in products):
        detail = f"Nothing found is at or under {line}: no page printed a currency."
    else:
        detail = f"Nothing found is at or under {line}: no page printed a price."
    detail += _also(gone, "at or under it, but out of stock where it was priced")
    detail += _also(bare, "priced at or under it, but with no currency printed")
    return Alert(
        below=below, below_label=line, met=[product.name for _price, product in met], detail=detail,
    )


def _also(names: list[str], said: str) -> str:
    """A sentence after the verdict naming ``names``, or nothing for none."""
    return f" {and_list(names)} {'is' if len(names) == 1 else 'are'} {said}." if names else ""


def _buyable(product: Product) -> bool:
    return product.availability != "out of stock"


def _at(price: float, product: Product) -> str:
    return amount_label(price, product.currency)
