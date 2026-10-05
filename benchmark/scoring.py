"""Turn one run's products into a scorecard, deterministically."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import TYPE_CHECKING

from buy_agent.models import Product
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
    from collections.abc import Iterable, Mapping, Sequence

    from buy_agent.search import SearchResult

#: Fraction of a name's distinctive words that has to be found on the other side for two
#: names to be the same product -- the bar :func:`buy_agent.verification.mentions_name`
#: sets, applied both ways.
MATCH_COVERAGE = NAME_COVERAGE

#: Each metric: what it weighs, and what it shows on an empty denominator. The empty
#: value is what the metric reads and is floored at; the score never counts it (ADR-0074).
METRICS: dict[str, tuple[float, float]] = {
    "identified": (3.0, 0.0),  # slots filled with a product that is really there
    "genuine": (2.0, 0.0),  # reported entries that are a real product, once each
    "figures": (2.0, 0.0),  # of three per product, those printed for it
    "attribution": (2.0, 1.0),  # of those reported, those not somebody else's
    "links": (1.0, 0.0),  # products pointed at a page about them (ADR-0017)
    "quotes": (1.0, 0.0),  # of the products judged, those quoting a verdict on them
    "faithful": (1.0, 1.0),  # of the quotes reported, those a verdict on that product
    "order": (1.0, 1.0),  # ranked pairs the answer key would order the same way
}

#: What each metric counts, in the words the page and ``docs/testing.md`` use.
MEANINGS: dict[str, str] = {
    "identified": "Slots filled with a product that is really there",
    "genuine": "Reported entries that are a real product, not a shop and not a repeat",
    "figures": "Price, rating and review count reported and printed for that product",
    "attribution": "Figures reported that are not somebody else's",
    "links": "Products pointed at a page that is about them",
    "quotes": "Products a page judges, carrying one of the verdicts it passed on them",
    "faithful": "Quotes reported that are, word for word, a verdict passed on that product",
    "order": "Pairs ranked in the order the key's figures give; a shuffle gets half",
}

#: Each completeness half and the error half it is weighed with into the score, by their
#: weighted harmonic mean: a pair counts only as far as both halves do, so reporting
#: nothing earns nothing, and neither does reporting nonsense (ADR-0074).
PAIRS: tuple[tuple[str, str], ...] = (
    ("identified", "genuine"),
    ("figures", "attribution"),
    ("quotes", "faithful"),
)

#: What a metric scores by luck alone, which the score does not pay for: a shuffled
#: ranking puts half its pairs in order (ADR-0074).
CHANCE: dict[str, float] = {"order": 0.5}

#: What a reported product is to the key: one it names, a second report of one, or
#: something the pages are not about.
REAL, REPEATED, INVENTED = "real", "repeated", "invented"

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


def model_numbers(words: Iterable[str]) -> set[str]:
    """The words of a name with a digit in them, which is what tells one model from the
    next: the "1000xm5" of "WH-1000XM5", "g14", "3200", the "5" of "Slim 5"."""
    return {word for word in words if any(character.isdigit() for character in word)}


def identifies(reported: str, expected: Expected) -> float:
    """How well ``reported`` names ``expected``, or 0.0 if it does not.

    Two names that each carry a model number the other lacks are two products, however
    many words they share: "WH-1000XM4" is not the XM5, nor "Blade 16" the 14 (ADR-0073).
    A number on one side only is a spec or a shortening -- "Slim 5 16GB", "De'Longhi
    Dedica" -- and is matched by words as before.

    Returns:
        The two coverages added, so an ambiguous name goes to its best match.
    """
    mine, theirs = distinctive_words(reported), distinctive_words(expected.name)
    if model_numbers(mine) - set(theirs) and model_numbers(theirs) - set(mine):
        return 0.0
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


def match_products(
    products: Sequence[Product], key: Sequence[Expected] = ANSWER_KEY
) -> list[tuple[Expected | None, str]]:
    """Each reported product's entry in the key and what it is to it, in the order
    reported: :data:`REAL` the first time an entry is named, :data:`REPEATED` after, and
    :data:`INVENTED` where no entry is."""
    seen: set[str] = set()
    matched: list[tuple[Expected | None, str]] = []
    for product in products:
        entry = best_match(product.name, key)
        if entry is None:
            matched.append((None, INVENTED))
        elif entry.name in seen:
            matched.append((entry, REPEATED))
        else:
            seen.add(entry.name)
            matched.append((entry, REAL))
    return matched


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


def quotes_a_verdict(quote: str, entry: Expected, pages: Mapping[str, str]) -> bool:
    """Whether ``quote`` is one of the verdicts the pages pass on this product, or a run of
    words out of one, printed on a page about it that the run was shown (ADR-0025,
    ADR-0073). A line about the product beside it on the same page is not this one's."""
    words = running_words(quote)
    padded = f" {words} "
    return (
        bool(words)
        and any(padded in f" {running_words(verdict)} " for verdict in entry.verdicts)
        and any(padded in f" {pages[url]} " for url in entry.pages if url in pages)
    )


def _as_it_should_be(product: Product, entry: Expected) -> Product:
    """``entry`` as this run should have reported it: each pair of figures the run
    reported where the key accepts it, the entry's own where it does not (ADR-0074). A
    run is never ranked against figures other than the ones it was right to report."""
    price, currency = (product.price, product.currency)
    if (price, currency) not in entry.prices:
        price, currency = entry.price, entry.currency
    rating, count = (product.rating, product.review_count)
    if (rating, count) not in entry.ratings:
        rating, count = entry.rating, entry.review_count
    return Product(
        name=entry.name, price=price, currency=currency, rating=rating, review_count=count
    )


def _ordering(pairs: Sequence[tuple[Product, Expected]]) -> tuple[int, int]:
    """Concordant pairs and total pairs, against the ranking the key would give."""
    ideal = rank_products([_as_it_should_be(product, entry) for product, entry in pairs])
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
    def parts(self) -> dict[str, tuple[float, float]]:
        """What the score is made of: each pair of :data:`PAIRS` and each metric in none,
        as its weight and its value, in :data:`METRICS` order (ADR-0074).

        A metric with nothing to count counts 0 here, whatever it shows: silence earns
        nothing. A pair is the weighted harmonic mean of its halves, and a metric with a
        :data:`CHANCE` level counts only what it scored above it.
        """
        counted = {
            name: right / out_of if out_of else 0.0
            for name, (right, out_of) in self.counts.items()
        }
        paired = {name: pair for pair in PAIRS for name in pair}
        parts: dict[str, tuple[float, float]] = {}
        for name, (weight, _) in METRICS.items():
            pair = paired.get(name, (name,))
            if name != pair[0]:
                continue
            weights = [METRICS[half][0] for half in pair]
            if len(pair) > 1:
                value = _harmonic([counted[half] for half in pair], weights)
            else:
                value = _above(counted[name], CHANCE.get(name, 0.0))
            parts["/".join(pair)] = (sum(weights), value)
        return parts

    @property
    def score(self) -> float:
        """The :attr:`parts` weighed into one number in ``[0, 1]``."""
        parts = self.parts.values()
        return sum(weight * value for weight, value in parts) / sum(weight for weight, _ in parts)

    def parts_label(self) -> str:
        """The :attr:`parts` in a line: "identified/genuine 0.600 x5, ..."."""
        return ", ".join(
            f"{name} {value:.3f} x{weight:g}" for name, (weight, value) in self.parts.items()
        )

    @property
    def cleared(self) -> bool:
        """Whether every metric and the score are at or above their floors."""
        rows = {**self.metrics, "score": self.score}
        return all(value >= FLOORS[name] for name, value in rows.items())

    def summary(self) -> str:
        """What the counts come to, in one sentence."""
        matched, reported = self.counts["genuine"]
        return (
            f"{matched} of {self.counts['identified'][1]} slots hold a real product "
            f"({self.invented} invented, {self.repeated} repeated, {reported} reported); "
            f"{'/'.join(map(str, self.counts['figures']))} figures right, "
            f"{self.counts['attribution'][1] - self.counts['attribution'][0]} "
            f"misattributed; {self.counts['quotes'][0]} quoted, "
            f"{self.counts['faithful'][1] - self.counts['faithful'][0]} "
            "quotes no page passed on that product."
        )

    def table(self) -> str:
        """The scorecard as lines, for a job log and for ``python -m benchmark``."""
        rows = {**self.metrics, "score": self.score}
        return "\n".join(
            [
                *(
                    f"  {name:<12} {value:>6.3f}   floor {FLOORS[name]:.2f}"
                    f"{'' if value >= FLOORS[name] else '   UNDER'}"
                    for name, value in rows.items()
                ),
                f"  weighed as   {self.parts_label()}",
                f"  {self.summary()}",
            ]
        )


def _harmonic(values: Sequence[float], weights: Sequence[float]) -> float:
    """The weighted harmonic mean of ``values``, which is 0 where any of them is."""
    if not all(values):
        return 0.0
    return sum(weights) / sum(weight / value for weight, value in zip(weights, values, strict=True))


def _above(value: float, chance: float) -> float:
    """``value`` rescaled so that ``chance`` is 0 and 1 is still 1, and nothing below."""
    return max(0.0, (value - chance) / (1.0 - chance))


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
    verdicts = match_products(products, key)
    matched = [
        (product, entry)
        for product, (entry, verdict) in zip(products, verdicts, strict=True)
        if entry is not None and verdict == REAL
    ]
    invented = sum(verdict == INVENTED for _, verdict in verdicts)
    repeated = sum(verdict == REPEATED for _, verdict in verdicts)

    figures = [
        verdict for product, entry in matched for verdict in figure_verdicts(product, entry)
    ]
    reported_figures = sum(verdict is not None for verdict in figures)
    faithful = [
        [quote for quote in product.opinions if quotes_a_verdict(quote.text, entry, pages)]
        for product, entry in matched
    ]
    # Only a product some page judges can be quoted: the AirPods Max is priced, not judged.
    judged = sum(bool(entry.verdicts) for _, entry in matched)
    quoted = sum(len(product.opinions) for product, _ in matched)
    kept = sum(len(quotes) for quotes in faithful)
    concordant, ranked_pairs = _ordering(matched)

    return Scorecard(
        counts={
            "identified": (len(matched), min(len(key), slots)),
            "genuine": (len(matched), len(products)),
            "figures": (sum(verdict is True for verdict in figures), 3 * len(matched)),
            "attribution": (
                reported_figures - sum(verdict is False for verdict in figures),
                reported_figures,
            ),
            "links": (sum(p.url in e.pages for p, e in matched), len(matched)),
            "quotes": (sum(bool(quotes) for quotes in faithful), judged),
            "faithful": (kept, quoted),
            "order": (concordant, ranked_pairs),
        },
        invented=invented,
        repeated=repeated,
    )


__all__ = [
    "CHANCE",
    "FLOORS",
    "INVENTED",
    "MATCH_COVERAGE",
    "MEANINGS",
    "METRICS",
    "PAIRS",
    "REAL",
    "REPEATED",
    "Scorecard",
    "best_match",
    "figure_verdicts",
    "identifies",
    "match_products",
    "model_numbers",
    "page_words",
    "quotes_a_verdict",
    "score_run",
]
