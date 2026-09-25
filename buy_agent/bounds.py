"""Bounds read out of the request's own words -- offered, never applied (ADR-0059).

Read in Python, not by the model, which would take "200 hours of battery" as a budget.
Each shape is anchored on its unit (a currency, stars, reviews); a number followed by an
unknown word ("under 20 hours") is left alone.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

from buy_agent.money import SCANNED_CODES, SIGNS, WORDS, plain_figures

#: The bounds a request can ask for, named as the settings that enforce them.
Bound = Literal["max_price", "min_rating", "min_reviews"]

#: The highest plausible rating.
_TOP_RATING = 5.0

#: A number in either convention ("1,500", "1.299,99"), read by
#: :func:`~buy_agent.money.plain_figures`. The lookahead stops backtracking into part of
#: one ("1500" read as "150").
_NUMBER = r"\d(?:[\d,.]*\d)?(?![\d,.]*\d)"

#: A currency sign before the figure ("$200").
_SIGN = f"[{re.escape(SIGNS)}]" if SIGNS else r"(?!)"

#: A currency after the figure ("200 USD"). Words and codes in one alternation: every
#: pattern here is ``IGNORECASE`` anyway (ADR-0054).
_UNIT = rf"(?:\s*(?:{'|'.join((*WORDS, *SCANNED_CODES))})\b)"

#: Joins that may follow a budget ("under 1500 and ..."), unlike "under 200 hours".
_CARRIES_ON = (
    "and", "or", "but", "with", "without", "for", "from", "in", "on", "that", "which",
    "plus", "if", "ideally", "preferably",
)

#: What may follow a bare figure for it to be an amount: a currency, a join, or no word.
_NOTHING_ELSE = (
    rf"(?:{_UNIT}|(?![\s-]*[A-Za-z])|(?=\s+(?:{'|'.join(_CARRIES_ON)})\b))"
)

#: A budget. A leading sign needs nothing more; a bare figure needs ``_NOTHING_ELSE``.
_MAX_PRICE = re.compile(
    rf"""
      \b(?:under|below|less\s+than|cheaper\s+than|no\s+more\s+than|up\s+to
         |at\s+most|max(?:imum)?(?:\s+of)?)
      \s*(?:{_SIGN}\s*({_NUMBER})|({_NUMBER}){_NOTHING_ELSE})
    | (?:{_SIGN}\s*)?\b({_NUMBER}){_UNIT}?\s+or\s+(?:less|under|cheaper)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

#: A rating, read only beside its scale (as :mod:`buy_agent.verification` does).
_MIN_RATING = re.compile(
    rf"""
      \b(?:at\s+least|min(?:imum)?(?:\s+of)?|over|above|better\s+than|from)
      \s*({_NUMBER})\s*(?:\+\s*)?(?:stars?|/\s*5\b|out\s+of\s+5\b)
    | \b({_NUMBER})\s*\+\s*(?:stars?|/\s*5\b)
    | \b({_NUMBER})\s*(?:stars?|/\s*5\b)\s+or\s+(?:better|higher|more|above)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

#: A review count, read only beside who is counted.
_MIN_REVIEWS = re.compile(
    rf"""
      \b(?:at\s+least|min(?:imum)?(?:\s+of)?|over|more\s+than|with)
      \s*({_NUMBER})\+?\s*(?:reviews?|ratings?)\b
    | \b({_NUMBER})\s*\+\s*(?:reviews?|ratings?)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

#: One pattern per bound, in the order a door offers them.
_PATTERNS: tuple[tuple[Bound, re.Pattern[str]], ...] = (
    ("max_price", _MAX_PRICE),
    ("min_rating", _MIN_RATING),
    ("min_reviews", _MIN_REVIEWS),
)


class Noticed(BaseModel):
    """One bound the request asked for in words, and never got (ADR-0059)."""

    #: The setting that would enforce it.
    bound: Bound
    value: float
    #: The shopper's words, quoted back so a misreading is visible.
    phrase: str

    @property
    def figure(self) -> str:
        """The number as typed: 200, not 200.0."""
        return f"{self.value:g}"

    @property
    def note(self) -> str:
        """Why the box holds a number nobody typed (ADR-0012)."""
        return f'From your request: "{self.phrase}". Clear the box to search without it.'


def notice(request: str) -> list[Noticed]:
    """The bounds ``request`` asks for in words, the first per setting (ADR-0059).

    Nothing is applied or range-checked here; the doors hold the ranges (ADR-0033).
    """
    return [
        found
        for bound, pattern in _PATTERNS
        if (found := _first(bound, pattern, request)) is not None
    ]


def _first(bound: Bound, pattern: re.Pattern[str], request: str) -> Noticed | None:
    """The first thing in ``request`` that reads like this bound, or ``None``."""
    for match in pattern.finditer(request):
        value = _figure(match)
        if value is None or not _plausible(bound, value):
            continue
        return Noticed(bound=bound, value=value, phrase=match.group(0).strip())
    return None


def _figure(match: re.Match[str]) -> float | None:
    """The match's one captured figure, or ``None`` if it is no number ("1.2.3")."""
    written = next(group for group in match.groups() if group)
    try:
        return float(plain_figures(written))
    except ValueError:
        return None


def _plausible(bound: Bound, value: float) -> bool:
    """Whether the figure fits the bound's scale: positive, a rating out of five, a
    count whole."""
    if value <= 0:
        return False
    if bound == "min_reviews":
        return value.is_integer()

    return bound != "min_rating" or value <= _TOP_RATING
