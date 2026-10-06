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

#: Scanned but never placed: ``¥`` is both yen and yuan.
UNPLACEABLE = frozenset({"¥"})

#: Placed but never scanned: "pounds" is more often a weight than a price.
UNSCANNED = frozenset({"POUND", "POUNDS"})

CODES: frozenset[str] = frozenset(ALIASES.values()) | {
    "JPY", "CHF", "SEK", "HUF", "MXN", "NZD", "SGD", "DKK", "NOK", "CNY", "ZAR"
}

_SPELLINGS: frozenset[str] = frozenset(ALIASES.keys() | CODES | UNPLACEABLE) - UNSCANNED

#: For a character class.
SIGNS = "".join(sorted(s for s in _SPELLINGS if len(s) == 1 and not s.isalpha()))

#: Letter spellings, scanned case-insensitively ("129 Dollars", "129 zł").
WORDS: tuple[str, ...] = tuple(sorted(s for s in _SPELLINGS if s.isalpha() and s not in CODES))

#: Case-sensitive: folded, ``TRY`` would match the verb "try".
SCANNED_CODES: tuple[str, ...] = tuple(sorted(s for s in _SPELLINGS if s in CODES))

_ZERO_DECIMAL = frozenset(
    {
        "BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW",
        "PYG", "RWF", "UGX", "UYI", "VND", "VUV", "XAF", "XOF", "XPF",
    }
)
_THREE_DECIMAL = frozenset({"BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"})

#: A grouped run of digits ends at a decimal comma and its cents, or nothing more.
_GROUPS_END = r"(?=,\d{1,2}(?![\d.,]*\d)|(?![\d.,]*\d))"

#: Where "12.500 KWD" is twelve and a half.
_THOUSANDTHS = "|".join(sorted(_THREE_DECIMAL))

#: "1.299,99 €": a dot before exactly three digits groups thousands, except beside a
#: currency counted in thousandths.
_DOTTED_THOUSANDS = re.compile(
    rf"(?<![\d.,])(?<!(?:{_THOUSANDTHS}) )(?<!{_THOUSANDTHS})"
    rf"[1-9]\d{{0,2}}(?:\.\d{{3}})+{_GROUPS_END}(?!\s?(?:{_THOUSANDTHS})\b)"
)

#: No-break, narrow no-break and thin: the spaces typesetting groups thousands with.
_NO_BREAK_SPACES = "\u00a0\u202f\u2009"
GROUP_SPACES = " " + _NO_BREAK_SPACES

#: A currency after a figure ("1 299 zł"): words folded, codes as written.
_CURRENCY_AFTER = (
    rf"\s?(?:[{re.escape(SIGNS)}]|(?i:{'|'.join(map(re.escape, WORDS))})\b"
    rf"|(?-i:{'|'.join(SCANNED_CODES)})\b)"
)

#: A number grouped with spaces, shared with :mod:`buy_agent.bounds`. An ordinary space
#: also stands between figures ("128 256 512 GB"), so it groups only where a decimal
#: comma or a currency closes the run. No bare space: ``VERBOSE`` patterns use it too.
SPACED_THOUSANDS = (
    rf"[1-9]\d{{0,2}}"
    rf"(?:(?:[{re.escape(_NO_BREAK_SPACES)}]\d{{3}})+(?:,\d{{1,2}})?(?![\d.,]*\d)"
    rf"|(?:[ ]\d{{3}})+"
    rf"(?:,\d{{1,2}}(?![\d.,]*\d)|(?=,-)|(?:,\d{{1,2}})?(?={_CURRENCY_AFTER})))"
)

_SPACED = re.compile(rf"(?<![\d.,]){SPACED_THOUSANDS}")
_GROUP_SPACE = re.compile(f"[{re.escape(GROUP_SPACES)}]")

#: A comma before three digits groups ("1,299"); before one or two it is decimal.
_THOUSANDS_COMMA = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
_DECIMAL_COMMA = re.compile(r"(?<=\d),(?=\d{1,2}(?!\d))")


def plain_figures(text: str) -> str:
    """Every figure ungrouped with a decimal dot ("1.299,99" -> 1299.99), so a page and a
    request are read the same way."""
    ungrouped = _DOTTED_THOUSANDS.sub(lambda match: match.group(0).replace(".", ""), text)
    ungrouped = _SPACED.sub(lambda match: ungroup(match.group(0)), ungrouped)
    return _DECIMAL_COMMA.sub(".", _THOUSANDS_COMMA.sub("", ungrouped))


def ungroup(figure: str) -> str:
    """One known number, without the spaces grouping it."""
    return _GROUP_SPACE.sub("", figure)


def code_for(value: str) -> str | None:
    """The ISO code for a listing's currency spelling; unknown ones come back as written."""
    code = value.strip().upper()
    return ALIASES.get(code, code) or None


def placeable(value: str) -> str | None:
    """``value`` as a code in :data:`CODES`, or ``None`` (ADR-0056)."""
    code = code_for(value)
    return code if code in CODES else None


def amount_label(price: float, currency: str | None = None) -> str:
    unit = f" {currency}" if currency else ""
    return f"{price:,.2f}{unit}"


def minor_units(price: float, currency: str) -> int:
    """``price`` in the currency's smallest unit, rounded half up."""
    exponent = 0 if currency in _ZERO_DECIMAL else 3 if currency in _THREE_DECIMAL else 2
    try:
        scaled = Decimal(str(price)).scaleb(exponent).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        # Inside the guard: ``int`` is what refuses a NaN.
        return int(scaled)
    # Every ``DecimalException`` is an ``ArithmeticError``.
    except (ArithmeticError, ValueError) as exc:
        raise ValueError(f"{price!r} is not a price this can pay.") from exc
