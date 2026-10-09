"""What a page declares about its products in schema.org JSON-LD, written out as the
lines a shop would print (ADR-0078). No I/O, and no HTML: ``fetch.py`` hands over the
scripts' text."""

from __future__ import annotations

import json
import re
from math import isfinite
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

#: The schema.org types whose objects are products, a group of variants among them.
_PRODUCT_TYPES = frozenset({"product", "productgroup", "individualproduct", "productmodel"})

#: schema.org's ``ItemAvailability``, as the words a page would print for it. Pre-orders
#: and back-orders are neither, so they are said as such and left for nobody to claim.
_AVAILABILITY: dict[str, str] = {
    "instock": "in stock",
    "limitedavailability": "in stock",
    "onlineonly": "in stock",
    "instoreonly": "in stock",
    "outofstock": "out of stock",
    "soldout": "out of stock",
    "discontinued": "out of stock",
    "preorder": "available to pre-order",
    "presale": "available to pre-order",
    "backorder": "available on back-order",
}

#: schema.org's ``OfferItemCondition``, written with "condition:" so it reads as one.
_CONDITION: dict[str, str] = {
    "newcondition": "condition: new",
    "usedcondition": "condition: used",
    "refurbishedcondition": "condition: refurbished",
    "damagedcondition": "condition: damaged",
}

#: A page declaring a whole catalogue is a page, not a source of a hundred products.
_MAX_PRODUCTS = 20
#: Past this a name is a description, and the line would be too long to keep.
_MAX_NAME = 120
#: Deep enough for ``@graph`` > ``ItemList`` > ``ListItem`` > ``Product`` > ``offers``.
_MAX_DEPTH = 8
_CODE = re.compile(r"[A-Z]{3}")
_WHITESPACE = re.compile(r"\s+")


def declared(scripts: Iterable[str]) -> list[str]:
    """One line per offer and one per rating, for each product the scripts declare;
    a script that is not JSON is skipped, as a browser would."""
    lines: list[str] = []
    seen = 0
    for script in scripts:
        try:
            data = json.loads(script)
        except ValueError:
            continue
        for product in _products(data, 0):
            written = _lines(product)
            if not written:
                continue
            lines.extend(line for line in written if line not in lines)
            seen += 1
            if seen >= _MAX_PRODUCTS:
                return lines
    return lines


def _products(node: Any, depth: int) -> Iterator[dict[str, Any]]:
    """Every object of a product type in ``node``, outermost first."""
    if depth > _MAX_DEPTH:
        return
    if isinstance(node, list):
        for item in node:
            yield from _products(item, depth + 1)
        return
    if not isinstance(node, dict):
        return
    if _types(node) & _PRODUCT_TYPES:
        yield node
    for key, value in node.items():
        # A product's own offers and rating are read with it, not as products.
        if key not in ("offers", "aggregateRating", "review"):
            yield from _products(value, depth + 1)


def _types(node: dict[str, Any]) -> frozenset[str]:
    declared_type = node.get("@type")
    names = declared_type if isinstance(declared_type, list) else [declared_type]
    return frozenset(_term(name) for name in names if isinstance(name, str))


def _term(value: str) -> str:
    """``https://schema.org/InStock`` and ``InStock`` alike, as ``instock``."""
    return value.rstrip("/").rsplit("/", 1)[-1].casefold()


def _lines(product: dict[str, Any]) -> list[str]:
    name = _name(product)
    if not name:
        return []
    lines = [f"{name}: {offer}" for offer in _offers(product.get("offers"))]
    if rating := _rating(product.get("aggregateRating")):
        lines.append(f"{name}: {rating}")
    return lines


def _name(product: dict[str, Any]) -> str:
    name = product.get("name")
    if not isinstance(name, str):
        return ""
    text = _WHITESPACE.sub(" ", name).strip()
    return text if len(text) <= _MAX_NAME else ""


def _offers(node: Any) -> list[str]:
    """Each offer as "348.00 USD, in stock, condition: new, sold by Shop"."""
    offers = node if isinstance(node, list) else [node]
    written: list[str] = []
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        if "aggregateoffer" in _types(offer):
            written.extend(_aggregate(offer))
            continue
        line = _offer(offer)
        if line and line not in written:
            written.append(line)
    return written


def _aggregate(offer: dict[str, Any]) -> list[str]:
    """The listings an ``AggregateOffer`` holds, else its lowest price on its own."""
    if inner := _offers(offer.get("offers")):
        return inner
    line = _offer({**offer, "price": offer.get("lowPrice")})
    return [line] if line else []


def _offer(offer: dict[str, Any]) -> str:
    parts: list[str] = []
    price = _number(offer.get("price"))
    if price is None:
        price = _number(_specified(offer.get("priceSpecification")))
    currency = offer.get("priceCurrency")
    if price is not None and price > 0:
        code = currency.strip().upper() if isinstance(currency, str) else ""
        parts.append(f"{price:.2f} {code}" if _CODE.fullmatch(code) else f"{price:.2f}")
    for value, words in (
        (offer.get("availability"), _AVAILABILITY),
        (offer.get("itemCondition"), _CONDITION),
    ):
        if isinstance(value, str) and (said := words.get(_term(value))):
            parts.append(said)
    if not parts:
        return ""
    if seller := _seller(offer.get("seller")):
        parts.append(f"sold by {seller}")
    return ", ".join(parts)


def _specified(node: Any) -> Any:
    specification = node[0] if isinstance(node, list) and node else node
    return specification.get("price") if isinstance(specification, dict) else None


def _seller(node: Any) -> str:
    name = node.get("name") if isinstance(node, dict) else node
    if not isinstance(name, str):
        return ""
    text = _WHITESPACE.sub(" ", name).strip()
    return text if len(text) <= _MAX_NAME else ""


def _rating(node: Any) -> str:
    """ "rated 4.6/5 from 3200 reviews", put on a five-point scale when declared on
    another; nothing for a rating with no value."""
    if not isinstance(node, dict):
        return ""
    value = _number(node.get("ratingValue"))
    best = _number(node.get("bestRating")) or 5.0
    if value is None or best <= 0 or not 0 <= value <= best:
        return ""
    stars = round(value * 5 / best, 2)
    rated = f"rated {stars:g}/5"
    count = _number(node.get("reviewCount")) or _number(node.get("ratingCount"))
    if count is None or count < 1 or not count.is_integer():
        return rated
    return f"{rated} from {int(count)} reviews"


def _number(value: Any) -> float | None:
    """A declared figure: JSON's number, or the string schema.org allows instead."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip().replace(",", ""))
        except ValueError:
            return None
    else:
        return None
    return number if isfinite(number) else None
