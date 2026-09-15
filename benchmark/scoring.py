"""Turn one run's products into a scorecard, deterministically."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import TYPE_CHECKING

from buy_agent.ranking import rank_products
from buy_agent.verification import (
    NAME_COVERAGE,
    build_haystack,
    distinctive_words,
    running_words,
    word_coverage,
)
from benchmark.answers import ANSWER_KEY, Expected
from benchmark.corpus import NUM_PRODUCTS

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from buy_agent.models import Product
    from buy_agent.search import SearchResult

#: Fraction of a name's distinctive words that has to be found on the other side for two
#: names to be the same product -- the bar :func:`buy_agent.verification.mentions_name`
#: sets, applied both ways.
MATCH_COVERAGE = NAME_COVERAGE

#: Each metric: what it weighs, and what it scores on an empty denominator.
METRICS: dict[str, tuple[float, float]] = {
    "identified": (3.0, 0.0),  # slots filled with a product that is really there
    "genuine": (2.0, 0.0),  # reported entries that are a real product, once each
    "figures": (2.0, 0.0),  # of three per product, those printed for it
    "attribution": (2.0, 1.0),  # of those reported, those not somebody else's
    "links": (1.0, 0.0),  # products pointed at a page about them (ADR-0017)
    "quotes": (1.0, 0.0),  # products carrying a verdict their page printed
    "faithful": (1.0, 1.0),  # of the quotes reported, those word for word
    "order": (1.0, 1.0),  # ranked pairs the answer key would order the same way
}

#: What the nightly run refuses to go below (``integration/test_benchmark.py``).
FLOORS: dict[str, float] = {
    "identified": 0.4,
    "genuine": 0.6,
    "figures": 0.25,
    "attribution": 0.5,
    "links": 0.5,
    "quotes": 0.0,
    "faithful": 0.5,
    "order": 0.25,
    "score": 0.4,
}


def identifies(reported: str, expected: Expected) -> float:
    """How well ``reported`` names ``expected``, or 0.0 if it does not.

    Returns:
        The two coverages added, so an ambiguous name goes to its best match.
    """
    mine, theirs = distinctive_words(reported), distinctive_words(expected.name)
    forwards = word_coverage(mine, expected.name)
    backwards = word_coverage(theirs, reported)
    if forwards < MATCH_COVERAGE or backwards < MATCH_COVERAGE:
        return 0.0
    return forwards + backwards


def best_match(name: str, key: Sequence[Expected] = ANSWER_KEY) -> Expected | None:
    """The answer-key entry ``name`` identifies, or None."""
    strength, _, entry = max(
        (identifies(name, entry), -index, entry) for index, entry in enumerate(key)
    )
    return entry if strength else None


def figure_verdicts(product: Product, entry: Expected) -> list[bool | None]:
    """Each of the three figures: True printed for it, False not, None blank."""

    def judged(value: float | None, qualifier: float | None, printed: frozenset) -> bool | None:
        if value is None:
            return None
        if qualifier is not None:
            return (value, qualifier) in printed
        return value in {figure for figure, _ in printed}

    return [
        judged(product.price, product.currency, entry.prices),
        judged(product.rating, product.review_count, entry.ratings),
        None
        if product.review_count is None
        else product.review_count in {count for _, count in entry.ratings},
    ]


def page_words(results: Sequence[SearchResult]) -> dict[str, str]:
    """Each searched page as its running words, by URL."""
    return {
        result.url: running_words(build_haystack([result])) for result in results if result.url
    }


def _quotes_verbatim(quote: str, entry: Expected, pages: Mapping[str, str]) -> bool:
    """Whether some page about this product printed ``quote`` word for word."""
    words = running_words(quote)
    return bool(words) and any(
        f" {words} " in f" {pages[url]} " for url in entry.pages if url in pages
    )


def _ordering(pairs: Sequence[tuple[Product, Expected]]) -> tuple[int, int]:
    """Concordant pairs and total pairs, against the ranking the key would give."""
    ideal = rank_products([entry.as_product() for _, entry in pairs])
    place = {ranked.product.name: ranked.rank for ranked in ideal}
    seats = [(index, place[entry.name]) for index, (_, entry) in enumerate(pairs)]
    concordant = sum(
        (left[0] - right[0]) * (left[1] - right[1]) > 0
        for left, right in combinations(seats, 2)
    )
    return concordant, len(seats) * (len(seats) - 1) // 2


@dataclass(frozen=True, slots=True)
class Scorecard:
    """What a run got right, as ``right out of`` per metric."""

    counts: dict[str, tuple[int, int]]
    invented: int
    repeated: int

    @property
    def metrics(self) -> dict[str, float]:
        """Every metric by name, in :data:`METRICS` order."""
        return {
            name: right / out_of if out_of else empty
            for name, (_, empty) in METRICS.items()
            for right, out_of in [self.counts[name]]
        }

    @property
    def score(self) -> float:
        """The metrics weighed into one number in ``[0, 1]``."""
        weights = {name: weight for name, (weight, _) in METRICS.items()}
        weighted = sum(weights[name] * value for name, value in self.metrics.items())
        return weighted / sum(weights.values())

    def table(self) -> str:
        """The scorecard as lines, for a job log and for ``python -m benchmark``."""
        rows = {**self.metrics, "score": self.score}
        matched, reported = self.counts["genuine"]
        return "\n".join(
            [
                *(
                    f"  {name:<12} {value:>6.3f}   floor {FLOORS[name]:.2f}"
                    f"{'' if value >= FLOORS[name] else '   UNDER'}"
                    for name, value in rows.items()
                ),
                f"  {matched} of {self.counts['identified'][1]} slots hold a real product "
                f"({self.invented} invented, {self.repeated} repeated, {reported} reported); "
                f"{'/'.join(map(str, self.counts['figures']))} figures right, "
                f"{self.counts['attribution'][1] - self.counts['attribution'][0]} "
                f"misattributed; {self.counts['quotes'][0]} quoted, "
                f"{self.counts['faithful'][1] - self.counts['faithful'][0]} "
                "quotes not on the page.",
            ]
        )


def score_run(
    products: Sequence[Product],
    results: Sequence[SearchResult],
    *,
    key: Sequence[Expected] = ANSWER_KEY,
    slots: int = NUM_PRODUCTS,
) -> Scorecard:
    """Score one run's products against the answer key.

    Args:
        products: What the run reported, **in the order it ranked them**.
        results: The pages it was given, enriched -- the corpus as the model saw
        it, which is what a quote is checked against.
        key: The answer key; :data:`~benchmark.answers.ANSWER_KEY` by default.
        slots: How many products the run was allowed to report. Recall is
        measured against this rather than against the whole key, the cap
        being part of the run rather than a failure of it.
    Returns:
        A :class:`Scorecard`. Every count but ``genuine``'s is over the products
        that matched: a hallucinated product is one mistake, and grading its
        invented price a second time would charge twice for it.
    """
    pages = page_words(results)
    matched: list[tuple[Product, Expected]] = []
    seen: set[str] = set()
    invented = repeated = 0

    for product in products:
        entry = best_match(product.name, key)
        if entry is None:
            invented += 1
        elif entry.name in seen:
            repeated += 1
        else:
            seen.add(entry.name)
            matched.append((product, entry))

    verdicts = [
        verdict for product, entry in matched for verdict in figure_verdicts(product, entry)
    ]
    reported_figures = sum(verdict is not None for verdict in verdicts)
    faithful = [
        [
            quote
            for quote in product.opinions
            if _quotes_verbatim(quote.text, entry, pages)
        ]
        for product, entry in matched
    ]
    quoted = sum(len(product.opinions) for product, _ in matched)
    kept = sum(len(quotes) for quotes in faithful)
    concordant, ranked_pairs = _ordering(matched)

    return Scorecard(
        counts={
            "identified": (len(matched), min(len(key), slots)),
            "genuine": (len(matched), len(products)),
            "figures": (sum(verdict is True for verdict in verdicts), 3 * len(matched)),
            "attribution": (
                reported_figures - sum(verdict is False for verdict in verdicts),
                reported_figures,
            ),
            "links": (sum(p.url in e.pages for p, e in matched), len(matched)),
            "quotes": (sum(bool(quotes) for quotes in faithful), len(matched)),
            "faithful": (kept, quoted),
            "order": (concordant, ranked_pairs),
        },
        invented=invented,
        repeated=repeated,
    )


__all__ = [
    "FLOORS",
    "MATCH_COVERAGE",
    "METRICS",
    "Scorecard",
    "best_match",
    "figure_verdicts",
    "identifies",
    "page_words",
    "score_run",
]
