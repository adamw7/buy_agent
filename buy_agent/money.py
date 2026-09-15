"""How an amount of money is written, read off a page, placed and counted (ADR-0043,
ADR-0054).

Every module that touches money asks one of four questions, and all four are answered
from the one table below. ``fetch`` asks which spellings make a line worth keeping;
``models`` asks which spellings are the same currency; every surface asks how an amount
is written for somebody to read; and ``payment`` asks how many of the currency's
smallest units it comes to. Those used to be three tables in three modules, and the two
that were held to each other were held by a test reaching into both modules' privates --
which could only ever check one direction, and both the directions it could not check
were wrong (ADR-0054).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

#: How a page's -- or a small model's -- way of naming a currency reads as the ISO code
#: everything downstream of the extraction compares by. Keys are upper-cased, which is
#: how :func:`code_for` looks one up.
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

#: Read off a page, and deliberately never placed: ``¥`` is the yen's sign and the
#: yuan's alike, and a guess would put half a set on the wrong scale. :func:`code_for`
#: hands an unknown spelling back as written, which is a price this run cannot place --
#: and "cannot place" already has an answer everywhere (ADR-0043). Named here rather
#: than worked out, so a second ambiguous sign is a line in this file and an argument to
#: go with it.
UNPLACEABLE = frozenset({"¥"})

#: Placed, and deliberately never read off a page: "pounds" is a unit of mass beside
#: being GBP, and a review of a 2 lb laptop prints it far more often than a price does.
#: A model handing back "pounds" in the currency field means the currency and is placed
#: as one; scanning pages for it would keep a line per weight. The other way round from
#: :data:`UNPLACEABLE`, and named for the same reason.
UNSCANNED = frozenset({"POUND", "POUNDS"})

#: The ISO codes a page is as likely to print as the sign, "129 EUR" being no rarer than
#: "€129". Every currency the table above can place is one of them -- so a sign added
#: there is scanned for by its code too, with nobody having to write the code down as
#: well -- plus the ones no sign of their own reaches.
CODES: frozenset[str] = frozenset(ALIASES.values()) | frozenset(
    {"JPY", "CHF", "SEK", "HUF", "MXN", "NZD", "SGD", "DKK", "NOK", "CNY", "ZAR"}
)

#: Every way a currency may be written beside a figure, the two exemptions applied.
_SPELLINGS: frozenset[str] = (ALIASES.keys() | CODES | UNPLACEABLE) - UNSCANNED

#: The one-character signs, as the character class :mod:`buy_agent.fetch` scans with.
SIGNS = "".join(sorted(s for s in _SPELLINGS if len(s) == 1 and not s.isalpha()))

#: The spellings made of letters, as the alternation it scans with between word
#: boundaries. Everything left over -- ``US$``, ``C$`` and the rest -- carries a sign
#: :data:`SIGNS` already holds, so a line printing one is kept by that half; the
#: convention test asks after every spelling rather than after this split, so a new one
#: that neither half reaches fails there rather than going quiet.
WORDS: tuple[str, ...] = tuple(sorted(s for s in _SPELLINGS if s.isalpha()))

#: Currencies not counted in hundredths. ISO 4217 gives most an exponent of 2, so only
#: the exceptions are written down: a table of every currency is one to keep current for
#: no gain, while one of these wrong is a payment a hundredfold.
_ZERO_DECIMAL = frozenset(
    {
        "BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW",
        "PYG", "RWF", "UGX", "UYI", "VND", "VUV", "XAF", "XOF", "XPF",
    }
)
_THREE_DECIMAL = frozenset({"BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"})


def code_for(value: str) -> str | None:
    """The currency a listing named, as the code the rest of the run compares by.

    A spelling the table does not know is handed back as written rather than guessed
    at, which is what :data:`UNPLACEABLE` relies on: an unknown code is a price this
    run cannot place, and that scores ``NEUTRAL``, sinks in a price sort, passes every
    bound and cannot be paid for (ADR-0043).
    """
    code = value.strip().upper()
    return ALIASES.get(code, code) or None


def amount_label(price: float, currency: str | None = None) -> str:
    """An amount as a person reads it, which is how every surface must write it.

    One wording for the card, the report and the cart a payment is authorised for:
    the figure a page printed and the figure a mandate carries are the same money,
    and a confirmation that spelt it differently from the product beside it would be
    asking somebody to agree to two amounts. ``None`` is a price no page gave a
    currency for, which is a number written without a unit rather than one in the
    run's own (ADR-0043).
    """
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
