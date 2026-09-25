"""How money is written, read off a page, placed and counted (ADR-0043, ADR-0054)."""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal

#: Spellings a page or a small model uses for a currency, mapped to its ISO code.
ALIASES: dict[str, str] = {
    "$": "USD",
    "US$": "USD",
    "DOLLAR": "USD",
    "DOLLARS": "USD",
    "€": "EUR",
    "EURO": "EUR",
    "EUROS": "EUR",
    "£": "GBP",
    "POUND": "GBP",
    "POUNDS": "GBP",
    "ZŁ": "PLN",
    "KČ": "CZK",
    "₹": "INR",
    "₩": "KRW",
    "₪": "ILS",
    "₺": "TRY",
    "R$": "BRL",
    "C$": "CAD",
    "CA$": "CAD",
    "A$": "AUD",
    "AU$": "AUD",
}

#: Scanned but never placed: ``¥`` is both yen and yuan, so it stays unplaceable
#: (ADR-0043).
UNPLACEABLE = frozenset({"¥"})

#: Placed but never scanned: "pounds" is more often a weight than a price.
UNSCANNED = frozenset({"POUND", "POUNDS"})

#: ISO codes a page may print instead of a sign ("129 EUR").
CODES: frozenset[str] = frozenset(ALIASES.values()) | {
    "JPY", "CHF", "SEK", "HUF", "MXN", "NZD", "SGD", "DKK", "NOK", "CNY", "ZAR"
}

#: Every way a currency may be written beside a figure, the two exemptions applied.
_SPELLINGS: frozenset[str] = frozenset(ALIASES.keys() | CODES | UNPLACEABLE) - UNSCANNED

#: The one-character signs, as the character class :mod:`buy_agent.fetch` scans with.
SIGNS = "".join(sorted(s for s in _SPELLINGS if len(s) == 1 and not s.isalpha()))

#: Letter spellings, scanned case-insensitively ("129 Dollars", "129 zł").
WORDS: tuple[str, ...] = tuple(sorted(s for s in _SPELLINGS if s.isalpha() and s not in CODES))

#: ISO codes, scanned case-sensitively: folded, ``TRY`` would match the verb "try", and
#: any code may collide with a word.
SCANNED_CODES: tuple[str, ...] = tuple(sorted(s for s in _SPELLINGS if s in CODES))

#: Currencies not counted in hundredths.
_ZERO_DECIMAL = frozenset(
    {
        "BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW",
        "PYG", "RWF", "UGX", "UYI", "VND", "VUV", "XAF", "XOF", "XPF",
    }
)
_THREE_DECIMAL = frozenset({"BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"})

#: Continental dot-grouped thousands ("1.299,99 €", "12.500 Bewertungen"): a dot before
#: exactly three digits is never a fraction in a price or a count.
_DOTTED_THOUSANDS = re.compile(
    r"(?<![\d.,])[1-9]\d{0,2}(?:\.\d{3})+(?=,\d{1,2}(?![\d.,]*\d)|(?![\d.,]*\d))"
)

#: A comma before three digits groups thousands ("1,299"); before one or two it is a
#: decimal point ("129,99").
_THOUSANDS_COMMA = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
_DECIMAL_COMMA = re.compile(r"(?<=\d),(?=\d{1,2}(?!\d))")


def plain_figures(text: str) -> str:
    """Every figure in ``text`` ungrouped with a decimal dot: "1,299.99", "1.299,99" and
    "1299.99" all become 1299.99.

    Shared by :mod:`buy_agent.verification` and :mod:`buy_agent.bounds`, so a page and a
    request are read the same way.
    """
    ungrouped = _DOTTED_THOUSANDS.sub(lambda match: match.group(0).replace(".", ""), text)
    return _DECIMAL_COMMA.sub(".", _THOUSANDS_COMMA.sub("", ungrouped))


def code_for(value: str) -> str | None:
    """The ISO code for a listing's currency spelling; unknown ones come back as written."""
    code = value.strip().upper()
    return ALIASES.get(code, code) or None


def placeable(value: str) -> str | None:
    """``value`` as a code in :data:`CODES`, or ``None`` (ADR-0056).

    Stricter than :func:`code_for`: a shopper naming the scale must pick a known code.
    """
    code = code_for(value)
    return code if code in CODES else None


def amount_label(price: float, currency: str | None = None) -> str:
    """An amount as every surface writes it."""
    unit = f" {currency}" if currency else ""
    return f"{price:,.2f}{unit}"


def minor_units(price: float, currency: str) -> int:
    """``price`` in the currency's smallest unit, rounded half up.

    Raises:
        ValueError: if ``price`` cannot be counted (NaN, infinity).
    """
    exponent = 0 if currency in _ZERO_DECIMAL else 3 if currency in _THREE_DECIMAL else 2
    try:
        scaled = Decimal(str(price)).scaleb(exponent).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        # Inside the guard: ``int`` is what refuses a NaN.
        return int(scaled)
    # Every ``DecimalException`` is an ``ArithmeticError``.
    except (ArithmeticError, ValueError) as exc:
        raise ValueError(f"{price!r} is not a price this can pay.") from exc
