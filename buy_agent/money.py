"""How an amount of money is written, read off a page, placed and counted (ADR-0043,
ADR-0054)."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

#: How a page's -- or a small model's -- way of naming a currency reads as the ISO code
#: everything downstream of the extraction compares by.
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

#: Read off a page, and deliberately never placed: ``¥`` is the yen's sign and the yuan's
#: alike, and a guess would put half a set on the wrong scale. :func:`code_for` hands an
#: unknown spelling back as written, which is a price this run cannot place -- and "cannot
#: place" already has an answer everywhere (ADR-0043).
UNPLACEABLE = frozenset({"¥"})

#: Placed, and deliberately never read off a page: "pounds" is a unit of mass beside being
#: GBP, and a review of a 2 lb laptop prints it far more often than a price does.
UNSCANNED = frozenset({"POUND", "POUNDS"})

#: The ISO codes a page is as likely to print as the sign, "129 EUR" being no rarer than
#: "€129".
CODES: frozenset[str] = frozenset(ALIASES.values()) | {
    "JPY", "CHF", "SEK", "HUF", "MXN", "NZD", "SGD", "DKK", "NOK", "CNY", "ZAR"
}

#: Every way a currency may be written beside a figure, the two exemptions applied.
_SPELLINGS: frozenset[str] = (ALIASES.keys() | CODES | UNPLACEABLE) - UNSCANNED

#: The one-character signs, as the character class :mod:`buy_agent.fetch` scans with.
SIGNS = "".join(sorted(s for s in _SPELLINGS if len(s) == 1 and not s.isalpha()))

#: The spellings made of letters, as the alternation it scans with between word
#: boundaries -- folded, whatever case a page prints them in: "129 dollars", "129
#: Dollars" and "129 zł" are one spelling three ways.
WORDS: tuple[str, ...] = tuple(sorted(s for s in _SPELLINGS if s.isalpha() and s not in CODES))

#: The other half of that alternation, and the half that is scanned in its own case
#: alone: a page writes "129 TRY" and never "129 try". Folded in with the words, ``TRY``
#: is the Turkish lira and the English verb alike, so "Try 3 of these before you decide"
#: read as a price line and was kept by the sweep at the expense of one. The collision
#: is a property of the table rather than of that one row -- every code is three letters
#: that may spell something -- so the split is where the codes are, not where ``TRY`` is.
SCANNED_CODES: tuple[str, ...] = tuple(sorted(s for s in _SPELLINGS if s in CODES))

#: Currencies not counted in hundredths.
_ZERO_DECIMAL = frozenset(
    {
        "BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW",
        "PYG", "RWF", "UGX", "UYI", "VND", "VUV", "XAF", "XOF", "XPF",
    }
)
_THREE_DECIMAL = frozenset({"BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"})


def code_for(value: str) -> str | None:
    """The currency a listing named, as the code the rest of the run compares by."""
    code = value.strip().upper()
    return ALIASES.get(code, code) or None


def amount_label(price: float, currency: str | None = None) -> str:
    """An amount as a person reads it, which is how every surface must write it."""
    unit = f" {currency}" if currency else ""
    return f"{price:,.2f}{unit}"


def minor_units(price: float, currency: str) -> int:
    """``price`` in the currency's smallest unit, rounded half up.

    Raises:
        ValueError: if ``price`` is not a figure that can be counted at all. Whoever
            is spending translates that into their own refusal -- this module knows
            what an amount is and nothing about who is being paid.
    """
    exponent = 0 if currency in _ZERO_DECIMAL else 3 if currency in _THREE_DECIMAL else 2
    try:
        scaled = Decimal(str(price)).scaleb(exponent).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        # Inside the guard because this is where a NaN or an infinity fails:
        # ``quantize`` answers NaN happily, and only ``int`` refuses it.
        return int(scaled)
    # ``decimal.InvalidOperation`` is an ``ArithmeticError`` and so is every other
    # ``DecimalException`` -- naming it as well would be one class caught twice and the
    # rest of them, ``Overflow`` included, still caught only by accident.
    except (ArithmeticError, ValueError) as exc:
        raise ValueError(f"{price!r} is not a price this can pay.") from exc
