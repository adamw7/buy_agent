"""Bounds read out of the request's own words, offered and never applied (ADR-0059).
Each is anchored on its unit, so "under 20 hours" is left alone."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

from buy_agent.money import (
    GROUP_SPACES,
    SCANNED_CODES,
    SIGNS,
    SPACED_THOUSANDS,
    WORDS,
    plain_figures,
    ungroup,
)

#: Named as the settings that enforce them.
Bound = Literal["max_price", "min_rating", "min_reviews"]

_TOP_RATING = 5.0

#: A number in any convention ("1.299,99", "1 500 zł"); the lookaheads stop backtracking
#: into part of one ("1500" read as "150").
_NUMBER = (
    rf"(?:{SPACED_THOUSANDS}"
    rf"|\d(?:[\d,.]*\d)?(?![\d,.]*\d)(?![{re.escape(GROUP_SPACES)}]\d{{3}}(?!\d)))"
)

_SIGN = f"[{re.escape(SIGNS)}]" if SIGNS else r"(?!)"

#: After the figure ("200 USD"); every pattern here is ``IGNORECASE`` anyway.
_UNIT = rf"(?:\s*(?:{'|'.join((*WORDS, *SCANNED_CODES))})\b)"

#: Joins that may follow a budget ("under 1500 and ..."), unlike "under 200 hours".
_CARRIES_ON = (
    "and", "or", "but", "with", "without", "for", "from", "in", "on", "that", "which",
    "plus", "if", "ideally", "preferably",
)

#: What may follow a bare figure for it to be an amount: a currency, a join, or no word.
_NOTHING_ELSE = rf"(?:{_UNIT}|(?![\s-]*[A-Za-z])|(?=\s+(?:{'|'.join(_CARRIES_ON)})\b))"

#: Not in a letter running on ("$2k" is no budget of 2), unless a currency ("$200USD").
_FIGURE_ENDS = rf"(?={_UNIT}|(?![A-Za-z]))"

_MAX_PRICE = re.compile(
    rf"""
      \b(?:under|below|less\s+than|cheaper\s+than|no\s+more\s+than|up\s+to
         |at\s+most|max(?:imum)?(?:\s+of)?)
      \s*(?:{_SIGN}\s*({_NUMBER}){_FIGURE_ENDS}|({_NUMBER}){_NOTHING_ELSE})
    | (?:{_SIGN}\s*)?\b({_NUMBER}){_UNIT}?\s+or\s+(?:less|under|cheaper)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

#: Only beside its scale, as :mod:`buy_agent.verification` reads one.
_MIN_RATING = re.compile(
    rf"""
      \b(?:at\s+least|min(?:imum)?(?:\s+of)?|over|above|better\s+than|from)
      \s*({_NUMBER})\s*(?:\+\s*)?(?:stars?|/\s*5\b|out\s+of\s+5\b)
    | \b({_NUMBER})\s*\+\s*(?:stars?|/\s*5\b)
    | \b({_NUMBER})\s*(?:stars?|/\s*5\b)\s+or\s+(?:better|higher|more|above)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

#: Only beside who is counted.
_MIN_REVIEWS = re.compile(
    rf"""
      \b(?:at\s+least|min(?:imum)?(?:\s+of)?|over|more\s+than|with)
      \s*({_NUMBER})\+?\s*(?:reviews?|ratings?)\b
    | \b({_NUMBER})\s*\+\s*(?:reviews?|ratings?)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

_PATTERNS: tuple[tuple[Bound, re.Pattern[str]], ...] = (
    ("max_price", _MAX_PRICE),
    ("min_rating", _MIN_RATING),
    ("min_reviews", _MIN_REVIEWS),
)


class Noticed(BaseModel):
    """One bound the request asked for in words, and never got."""

    bound: Bound
    value: float
    #: The shopper's words, quoted back so a misreading is visible.
    phrase: str

    @property
    def figure(self) -> str:
        """As typed: 200, not 200.0, and 1234567, never ``:g``'s 1.23457e+06."""
        return f"{self.value:.0f}" if self.value.is_integer() else str(self.value)

    @property
    def note(self) -> str:
        """Why the box holds a number nobody typed."""
        return f'From your request: "{self.phrase}". Clear the box to search without it.'


def notice(request: str) -> list[Noticed]:
    """The first bound per setting ``request`` asks for in words; the doors hold the
    ranges (ADR-0033)."""
    return [
        found
        for bound, pattern in _PATTERNS
        if (found := _first(bound, pattern, request)) is not None
    ]


def _first(bound: Bound, pattern: re.Pattern[str], request: str) -> Noticed | None:
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
        # The currency that let a space group it is outside the capture.
        return float(plain_figures(ungroup(written)))
    except ValueError:
        return None


def _plausible(bound: Bound, value: float) -> bool:
    """Positive, a rating out of five, a count whole."""
    if value <= 0:
        return False
    if bound == "min_reviews":
        return value.is_integer()
    return bound != "min_rating" or value <= _TOP_RATING
