"""What the shopper's own words say about the bounds -- offered, and never applied
(ADR-0059).

"under $200" shapes the search query and nothing else; ``max_price`` is what enforces
it. That is deliberate and stays: a model asked to read bounds out of "headphones with
200 hours of battery" drops every product in the run, and the report then says only that
nothing was found. So the reading is done here, in ordinary Python over a string, the
way :func:`~buy_agent.extraction.clean_products` does its work -- and what comes out of
it is an offer. Each door shows the number it noticed where the shopper can see it and
take it or leave it; neither door applies one.

The scan is deliberately narrow. It matches the shapes a person actually writes, each
anchored on the unit that makes it a bound at all: a price on a currency mark or on
nothing following it, a rating on stars, a review count on reviews. A number followed by
a word this module does not recognise is a number about something else -- "under 20
hours" is the case the whole design is arranged around -- and is left alone.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

from buy_agent.money import SCANNED_CODES, SIGNS, WORDS, plain_figures

#: The three bounds a request can ask for, named as the settings that would enforce
#: them: this is what a door looks the noticed value up on and fills in.
Bound = Literal["max_price", "min_rating", "min_reviews"]

#: The highest a rating can be, and so the highest a number followed by "stars" can
#: plausibly be one: "4.5 stars" is a rating and "2000 stars" is a review count written
#: oddly.
_TOP_RATING = 5.0

#: A number as a person writes one, separators and all, in either convention: "200",
#: "1,500", "4.5", "129,99", "1.299,99". Taken whole and read by
#: :func:`~buy_agent.money.plain_figures`, which is how a page's figures are read too --
#: read here as digits, commas and one decimal point, "under 129,99 €" offered a budget
#: of 12999 and "at least 1.200 reviews" a count of 1.2. The lookahead is what stops the
#: engine reading half of one: without it "under 1500 and ..." backtracked to "150", the
#: "0" after it satisfying "nothing else follows", and the request came back offering a
#: budget nobody had written.
_NUMBER = r"\d(?:[\d,.]*\d)?(?![\d,.]*\d)"

#: A currency written before the figure -- "$200", "€1,500". Only the one-character
#: signs, which are the only spellings that go in front.
_SIGN = f"[{re.escape(SIGNS)}]" if SIGNS else r"(?!)"

#: A currency written after it -- "200 dollars", "200 USD". One alternation over both
#: halves of :mod:`buy_agent.money`'s scan, and not the split :mod:`buy_agent.fetch`
#: keeps: that one reads a page, where the case tells the Turkish lira from the English
#: "try", and this one reads a line somebody typed into a box, where every pattern below
#: is ``IGNORECASE`` anyway. Split here, the two halves would spell one rule twice and
#: mean the same thing (ADR-0054). Grouped whole, because one use below makes it optional
#: with a trailing ``?``.
_UNIT = rf"(?:\s*(?:{'|'.join((*WORDS, *SCANNED_CODES))})\b)"

#: The words a request carries on in after it has named a budget. A sentence does not
#: end at the figure -- "under 1500 and at least 4 stars" is one request -- so the guard
#: below cannot simply be "nothing follows", and a closed list of the joins is the one
#: thing that tells "under 1500 and ..." from "under 200 hours ...".
_CARRIES_ON = (
    "and", "or", "but", "with", "without", "for", "from", "in", "on", "that", "which",
    "plus", "if", "ideally", "preferably",
)

#: What may follow a bare figure for it still to be an amount: a currency, a join, or
#: nothing that starts another word. "under 200" is a budget; "under 200 hours" is the
#: battery life this module exists not to mistake for one.
_NOTHING_ELSE = (
    rf"(?:{_UNIT}|(?![\s-]*[A-Za-z])|(?=\s+(?:{'|'.join(_CARRIES_ON)})\b))"
)

#: How a shopper writes a budget. The figure is captured and the words around it are
#: not, so the phrase a door quotes back is the shopper's own and not a paraphrase.
#:
#: Two ways of writing the amount, and the guard applies to one of them: a currency mark
#: says on its own that this is money, so "under $200 for the gym" needs nothing more,
#: while a bare "under 200" has to be followed by a currency, a join, or the end of the
#: thought.
_MAX_PRICE = re.compile(
    rf"""
      \b(?:under|below|less\s+than|cheaper\s+than|no\s+more\s+than|up\s+to
         |at\s+most|max(?:imum)?(?:\s+of)?)
      \s*(?:{_SIGN}\s*({_NUMBER})|({_NUMBER}){_NOTHING_ELSE})
    | (?:{_SIGN}\s*)?\b({_NUMBER}){_UNIT}?\s+or\s+(?:less|under|cheaper)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

#: A rating, which is a small number and so is only ever read beside its scale -- the
#: rule :mod:`buy_agent.verification` already holds for a figure off a page.
_MIN_RATING = re.compile(
    rf"""
      \b(?:at\s+least|min(?:imum)?(?:\s+of)?|over|above|better\s+than|from)
      \s*({_NUMBER})\s*(?:\+\s*)?(?:stars?|/\s*5\b|out\s+of\s+5\b)
    | \b({_NUMBER})\s*\+\s*(?:stars?|/\s*5\b)
    | \b({_NUMBER})\s*(?:stars?|/\s*5\b)\s+or\s+(?:better|higher|more|above)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

#: A review count, anchored the same way: somebody has to be counted.
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

    #: The setting that would enforce it, named as both doors name it.
    bound: Bound
    value: float
    #: The shopper's own words, quoted back so the offer is arguable: a run that
    #: pre-filled 200 off "200 hours of battery" has said which words it read.
    phrase: str

    @property
    def figure(self) -> str:
        """The number as it goes in a box or on a command line -- 200, not 200.0."""
        return f"{self.value:g}"

    @property
    def note(self) -> str:
        """Why this box holds a number nobody typed, in Python's words (ADR-0012)."""
        return f'From your request: "{self.phrase}". Clear the box to search without it.'


def notice(request: str) -> list[Noticed]:
    """The bounds ``request`` asks for in words, at most one per setting (ADR-0059).

    The first match of each shape wins: a request naming two budgets is one somebody has
    reworded mid-sentence, and picking between them is a judgement this deliberately
    does not make. Nothing here is applied and nothing is refused -- a figure outside
    what a setting will take is still noticed, and the door that offers it is where a
    range lives (ADR-0033).
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
    """The number one match found, out of whichever alternative matched, or ``None`` for
    one that reads as no number at all.

    Every alternative of every pattern above captures exactly one figure, so there is
    always one -- but digits and separators in any order are not always a number:
    "1.2.3" is a version, and offering it as anything would be a guess.
    """
    written = next(group for group in match.groups() if group)
    try:
        return float(plain_figures(written))
    except ValueError:
        return None


def _plausible(bound: Bound, value: float) -> bool:
    """Whether this figure could be this bound at all.

    The one judgement made here, and it is about the scale rather than about the
    shopper: a rating is out of five, so "over 2000 ratings" is not one however it was
    phrased, and a count is whole. Everything else is left to the door, where the ranges
    are.
    """
    if value <= 0:
        return False
    if bound == "min_reviews":
        # A count is a whole number: offered as "4.5 reviews", no box could take it.
        return value.is_integer()
    return bound != "min_rating" or value <= _TOP_RATING
