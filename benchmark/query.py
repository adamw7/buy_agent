"""The first of the two things the agent asks a model: did it turn the request into a
search query, and keep what the shopper asked for? (ADR-0070)"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from buy_agent.verification import running_words

#: Past this many words a query is an explanation of one, which a search engine reads
#: as a very long query.
MAX_WORDS = 20

#: The one check a run is given when the model answered with no query at all, and the
#: agent searched with the shopper's own words instead.
NO_QUERY = "Answered with a query the run could search with"


@dataclass(frozen=True, slots=True)
class QueryKey:
    """What a refined query owes the request it was refined from.

    Attributes:
        keeps: Each constraint the shopper stated, as the spellings that keep it --
            ``("noise cancelling", "noise canceling", "anc")``. A spelling of letters and
            digits is matched as words; one with a sign in it (``"€"``) as written.
        brands: Brands and product lines the pages name and the shopper did not. A
            query naming one has narrowed the search on the shopper's behalf, which the
            prompt forbids ("Do not add constraints the shopper never mentioned").
    """

    keeps: tuple[tuple[str, ...], ...]
    brands: tuple[str, ...]


class QueryCheck(BaseModel):
    """One thing a query was checked for, in the sentence the page and the CLI show."""

    check: str
    passed: bool


class QueryVerdict(BaseModel):
    """The query a run searched with, and what it was checked for."""

    #: What the model answered, or ``None`` where it answered nothing usable.
    query: str | None = None
    checks: list[QueryCheck]

    @property
    def score(self) -> float:
        """The share of checks passed, in ``[0, 1]``."""
        if not self.checks:
            return 0.0
        return sum(check.passed for check in self.checks) / len(self.checks)


def _keeps(spelling: str, words: str, raw: str) -> bool:
    """Whether a query keeps one spelling of a constraint: as words where the spelling
    is words ("1,500" and "$1500" both keep "1500"), else as written ("€")."""
    if spelling.replace(" ", "").isalnum():
        return f" {running_words(spelling)} " in f" {words} "
    return spelling.lower() in raw


def judge_query(query: str | None, request: str, key: QueryKey) -> QueryVerdict:
    """Check the query a model refined ``request`` into against ``key``.

    Returns:
        A verdict whose checks are, in order: one per constraint kept, no brand added,
        no figure added, and short enough to be one query. A blank or missing query is
        one failed check, since the agent then searches with the request itself.
    """
    if query is None or not query.strip():
        return QueryVerdict(query=None, checks=[QueryCheck(check=NO_QUERY, passed=False)])

    query = query.strip()
    raw = query.lower()
    words = running_words(query)
    checks = [
        QueryCheck(check=f"{'Keeps' if kept else 'Drops'} {spellings[0]!r}", passed=kept)
        for spellings in key.keeps
        for kept in [any(_keeps(spelling, words, raw) for spelling in spellings)]
    ]

    padded = f" {words} "
    asked = f" {running_words(request)} "
    named = [
        brand
        for brand in key.brands
        if f" {running_words(brand)} " in padded and f" {running_words(brand)} " not in asked
    ]
    checks.append(
        QueryCheck(
            check=(
                f"Names a brand the shopper did not: {', '.join(named)}"
                if named
                else "Names no brand the shopper did not"
            ),
            passed=not named,
        )
    )

    given = set(asked.split())
    added = [
        word
        for word in dict.fromkeys(words.split())
        if any(character.isdigit() for character in word)
        and word not in given
        # The cents of a round price: "$1,500.00" is the words "1500" and "00".
        and word.strip("0")
    ]
    checks.append(
        QueryCheck(
            check=(
                f"Adds a figure the shopper did not give: {', '.join(added)}"
                if added
                else "Adds no figure the shopper did not give"
            ),
            passed=not added,
        )
    )

    count = len(query.split())
    checks.append(
        QueryCheck(
            check=(
                f"Reads as one query ({count} words)"
                if count <= MAX_WORDS
                else f"Reads as an explanation, not a query ({count} words)"
            ),
            passed=count <= MAX_WORDS,
        )
    )
    return QueryVerdict(query=query, checks=checks)


__all__ = ["MAX_WORDS", "NO_QUERY", "QueryCheck", "QueryKey", "QueryVerdict", "judge_query"]
