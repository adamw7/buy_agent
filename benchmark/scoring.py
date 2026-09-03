"""Turn one run's products into a scorecard, deterministically.

Nothing here talks to a model, a network or a clock: given the same products and
the same pages, :func:`score_run` answers the same eight numbers every time. That
is the whole point -- the model is the only thing in a benchmark run that is
allowed to vary, so everything downstream of it has to be a pure function or the
score means nothing.

The eight metrics are the pipeline's own promises, one apiece, all in ``[0, 1]``
and all higher-is-better so they can be weighed into one number:

===============  ==============================================================
``identified``   Recall: how many of the slots the run has hold a real product.
``genuine``      Precision: reported entries that are a real product, and not a
                 second listing of one already reported (``clean_products``,
                 ``drop_ungrounded``, ``deduplicate``).
``figures``      Price, rating and review count reported and printed for *that*
                 product -- accuracy and completeness together, a blank being a
                 miss rather than an error.
``attribution``  The other half of that: reported figures **not** printed for
                 that product. The one thing grounding structurally cannot see,
                 since ``verify_numbers`` pools the pages (see
                 :mod:`benchmark.answers`).
``links``        Products pointed at a page that is really about them (ADR-0017).
``quotes``       Products carrying at least one verdict a page about them
                 printed (ADR-0024).
``faithful``     The other half of that: quotes that are *not* verbatim on such
                 a page. Stricter than ``verify_opinions``, which tolerates a
                 word of the model's own at either end (ADR-0025) -- the
                 benchmark asks for the sentence, not for most of it.
``order``        Whether the ranking came out in the order the answer key's own
                 figures would have produced (ADR-0007).
===============  ==============================================================

Splitting each of the three "did it copy correctly" questions into a
completeness half and an error half is deliberate. A model that reports nothing
scores 0 on ``figures`` and 1.0 on ``attribution``, and one that reports
confident nonsense scores the other way round; a single blended number would
call those two runs equally good.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import TYPE_CHECKING

from buy_agent.extraction import GENERIC_WORDS, NAME_TOKENS
from buy_agent.ranking import rank_products
from buy_agent.verification import build_haystack, running_words
from benchmark.answers import ANSWER_KEY, Expected
from benchmark.corpus import NUM_PRODUCTS

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from buy_agent.models import Product
    from buy_agent.search import SearchResult

#: Fraction of a name's distinctive words that has to be found on the other side
#: for two names to be the same product. The bar
#: :func:`buy_agent.verification.mentions_name` sets, applied in both directions
#: -- see :func:`identifies`.
MATCH_COVERAGE = 0.6

#: What each metric contributes to the overall score. Finding the products at all
#: carries the most, because everything else is measured over what was found and
#: goes vacuous without it; the two error halves carry as much as the two
#: completeness halves they qualify, a confident wrong answer being worse than a
#: blank in a report somebody is going to act on.
WEIGHTS: dict[str, float] = {
    "identified": 3.0,
    "genuine": 2.0,
    "figures": 2.0,
    "attribution": 2.0,
    "links": 1.0,
    "quotes": 1.0,
    "faithful": 1.0,
    "order": 1.0,
}

#: What the nightly run refuses to go below (``integration/test_benchmark.py``).
#:
#: These are a **tripwire, not a target**. The model behind them is a 0.6B one
#: chosen for being able to run on a CI runner's four cores (ADR-0026), and the
#: thing worth catching is that it stopped being able to read these pages at all
#: -- a model update, an Ollama release that changes how ``json_schema`` decoding
#: works, a prompt grown past what it can follow. A floor set where the model
#: happens to sit today would fail the job for a rewording, which is how a
#: scheduled run gets ignored.
#:
#: So they are set low on purpose and raised deliberately: a run that scores well
#: above one of these is an argument for lifting it in a commit of its own, with
#: the run that justified it quoted in the message. The nightly logs the whole
#: scorecard whether it passes or not, which is where the numbers to argue from
#: come from.
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


def _distinctive(name: str) -> list[str]:
    """The words of ``name`` that identify something, by the shared rule.

    ``GENERIC_WORDS`` and ``NAME_TOKENS`` come from
    :mod:`buy_agent.extraction`, so the benchmark splits and ignores words
    exactly as merging and grounding do. A benchmark with its own idea of what a
    name's words are would score the pipeline against a rule the pipeline does
    not follow.
    """
    return [word for word in NAME_TOKENS.findall(name.lower()) if word not in GENERIC_WORDS]


def _covered(tokens: Sequence[str], text: str) -> float:
    """Share of ``tokens`` that appear in ``text`` as words of their own."""
    if not tokens:
        return 0.0
    words = frozenset(NAME_TOKENS.findall(text.lower()))
    return sum(token in words for token in tokens) / len(tokens)


def identifies(reported: str, expected: Expected) -> float:
    """How well ``reported`` names ``expected``, or 0.0 if it does not.

    Checked in **both** directions, and that is the whole subtlety. Forwards
    alone -- every distinctive word of the reported name being in the expected
    one -- makes "Sony" the Sony, which would let a model score full marks for
    naming brands. Backwards alone rejects "WH-1000XM5", which is the product.
    Requiring :data:`MATCH_COVERAGE` of each admits a name missing a word and
    refuses one that is only a fragment.

    Returns:
        The two coverages added, so an ambiguous name goes to its best match, or
        0.0 where either direction falls short.
    """
    mine, theirs = _distinctive(reported), _distinctive(expected.name)
    forwards, backwards = _covered(mine, expected.name), _covered(theirs, reported)
    if forwards < MATCH_COVERAGE or backwards < MATCH_COVERAGE:
        return 0.0
    return forwards + backwards


def best_match(name: str, key: Sequence[Expected] = ANSWER_KEY) -> Expected | None:
    """The answer-key entry ``name`` identifies, or None.

    Ties go to the earlier entry, so this is a function of the key's order and
    not of dictionary iteration -- a benchmark that scored differently on two
    runs of the same answer would not be one.
    """
    scored = [(identifies(name, entry), -index, entry) for index, entry in enumerate(key)]
    strength, _, entry = max(scored, key=lambda item: item[:2])
    return entry if strength else None


def _prices(entry: Expected) -> frozenset[float]:
    return frozenset(price for price, _ in entry.prices)


def _ratings(entry: Expected) -> frozenset[float]:
    return frozenset(rating for rating, _ in entry.ratings)


def _counts(entry: Expected) -> frozenset[int]:
    return frozenset(count for _, count in entry.ratings)


def figure_verdicts(product: Product, entry: Expected) -> list[bool | None]:
    """Each of the three figures: True printed for it, False not, None blank.

    A qualifier is judged with the figure it qualifies rather than beside it
    (ADR-0022): a price is checked as ``(price, currency)`` where a currency was
    reported, so ``329 USD`` -- two figures the corpus prints and a pairing it
    never does -- is one wrong price rather than a right price and a right
    currency.
    """
    if product.price is None:
        price: bool | None = None
    elif product.currency is None:
        price = product.price in _prices(entry)
    else:
        price = (product.price, product.currency) in entry.prices

    if product.rating is None:
        rating: bool | None = None
    elif product.review_count is None:
        rating = product.rating in _ratings(entry)
    else:
        rating = (product.rating, product.review_count) in entry.ratings

    count = None if product.review_count is None else product.review_count in _counts(entry)
    return [price, rating, count]


def _share(good: int, total: int, *, empty: float = 1.0) -> float:
    """``good/total``, and ``empty`` where there was nothing to be right about."""
    return good / total if total else empty


@dataclass(frozen=True, slots=True)
class Scorecard:
    """What a run got right, as counts, with the metrics read off them.

    Counts rather than ratios, so a report can say "3 of 5" and a regression can
    be read without recomputing anything. Every metric below is a property over
    these, which is what keeps :data:`WEIGHTS`, :data:`FLOORS` and the printed
    table reading the same numbers.
    """

    #: Products the run reported, and the slots it had to fill (the cap, not the
    #: whole key -- see :data:`benchmark.answers.ANSWER_KEY`).
    reported: int
    slots: int
    #: Reported entries that are a real product, and a first sighting of it.
    matched: int
    #: The rest, split so a scorecard says which mistake was made.
    invented: int
    repeated: int
    #: Figures: right, blank-or-wrong out of three per matched product, and how
    #: many of the ones actually reported were not printed for that product.
    figures_right: int
    figures_reported: int
    figures_wrong: int
    #: Matched products linked to a page that is about them.
    linked: int
    #: Matched products carrying at least one verbatim verdict, and the quotes.
    quoted: int
    quotes_reported: int
    quotes_wrong: int
    #: Pairs of matched products the ranking put in the order the key would.
    concordant: int
    pairs: int

    @property
    def identified(self) -> float:
        return _share(self.matched, self.slots, empty=0.0)

    @property
    def genuine(self) -> float:
        return _share(self.matched, self.reported, empty=0.0)

    @property
    def figures(self) -> float:
        return _share(self.figures_right, 3 * self.matched, empty=0.0)

    @property
    def attribution(self) -> float:
        return 1.0 - _share(self.figures_wrong, self.figures_reported, empty=0.0)

    @property
    def links(self) -> float:
        return _share(self.linked, self.matched, empty=0.0)

    @property
    def quotes(self) -> float:
        return _share(self.quoted, self.matched, empty=0.0)

    @property
    def faithful(self) -> float:
        return 1.0 - _share(self.quotes_wrong, self.quotes_reported, empty=0.0)

    @property
    def order(self) -> float:
        return _share(self.concordant, self.pairs)

    @property
    def metrics(self) -> dict[str, float]:
        """Every metric by name, in :data:`WEIGHTS` order."""
        return {name: getattr(self, name) for name in WEIGHTS}

    @property
    def score(self) -> float:
        """The eight metrics weighed into one number in ``[0, 1]``."""
        total = sum(WEIGHTS.values())
        return sum(WEIGHTS[name] * value for name, value in self.metrics.items()) / total

    def table(self) -> str:
        """The scorecard as lines, for a job log and for ``python -m benchmark``."""
        rows = [
            f"  {name:<12} {value:>6.3f}   floor {FLOORS[name]:.2f}"
            f"{'' if value >= FLOORS[name] else '   UNDER'}"
            for name, value in self.metrics.items()
        ]
        counts = (
            f"  {self.matched} of {self.slots} slots filled with a real product; "
            f"{self.invented} invented, {self.repeated} repeated; "
            f"{self.figures_right}/{3 * self.matched} figures right, "
            f"{self.figures_wrong} misattributed; "
            f"{self.quoted} quoted, {self.quotes_wrong} quotes not on the page."
        )
        under = "" if self.score >= FLOORS["score"] else "   UNDER"
        return "\n".join(
            [*rows, f"  {'score':<12} {self.score:>6.3f}   floor {FLOORS['score']:.2f}{under}", counts]
        )


def page_words(results: Sequence[SearchResult]) -> dict[str, str]:
    """Each searched page as its running words, by URL.

    The text the *model saw*, condensed, not the raw fixture: a quote is faithful
    when it is on the page as the pipeline had it, and scoring against the
    un-condensed original would credit a sentence the fetch layer threw away.
    """
    return {
        result.url: running_words(build_haystack([result]))
        for result in results
        if result.url
    }


def _quotes_verbatim(quote: str, entry: Expected, pages: Mapping[str, str]) -> bool:
    """Whether some page about this product printed ``quote`` word for word."""
    words = running_words(quote)
    if not words:
        return False
    return any(
        f" {words} " in f" {pages[url]} " for url in entry.pages if url in pages
    )


def _ordering(pairs: Sequence[tuple[Product, Expected]]) -> tuple[int, int]:
    """Concordant pairs and total pairs, against the ranking the key would give.

    The ideal is built over the *matched* products alone rather than over the
    whole key, because ``score_product`` scores price relative to the candidate
    set: ranking seven and comparing against five would mark a run down for a
    product it never reported.
    """
    if len(pairs) < 2:
        return 0, 0
    ideal = rank_products([entry.as_product() for _, entry in pairs])
    place = {ranked.product.name: ranked.rank for ranked in ideal}
    positions = [(index, place[entry.name]) for index, (_, entry) in enumerate(pairs)]
    concordant = sum(
        (left[0] - right[0]) * (left[1] - right[1]) > 0
        for left, right in combinations(positions, 2)
    )
    return concordant, len(positions) * (len(positions) - 1) // 2


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
        results: The pages the run was given, enriched -- the corpus as the model
            saw it, which is what a quote is checked against.
        key: The answer key; :data:`~benchmark.answers.ANSWER_KEY` by default.
        slots: How many products the run was allowed to report. Recall is
            measured against this rather than against the key, the cap being
            part of the run rather than a failure of it.

    Returns:
        A :class:`Scorecard`. Every count is over the products that matched the
        key: a hallucinated product is one mistake, counted once against
        ``genuine``, and grading its invented price a second time would charge
        twice for it.
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

    verdicts = [verdict for product, entry in matched for verdict in figure_verdicts(product, entry)]
    faithful = [
        [quote for quote in product.opinions if _quotes_verbatim(quote, entry, pages)]
        for product, entry in matched
    ]
    quotes_reported = sum(len(product.opinions) for product, _ in matched)
    concordant, total_pairs = _ordering(matched)

    return Scorecard(
        reported=len(products),
        slots=min(len(key), slots),
        matched=len(matched),
        invented=invented,
        repeated=repeated,
        figures_right=sum(verdict is True for verdict in verdicts),
        figures_reported=sum(verdict is not None for verdict in verdicts),
        figures_wrong=sum(verdict is False for verdict in verdicts),
        linked=sum(product.url in entry.pages for product, entry in matched),
        quoted=sum(bool(kept) for kept in faithful),
        quotes_reported=quotes_reported,
        quotes_wrong=quotes_reported - sum(len(kept) for kept in faithful),
        concordant=concordant,
        pairs=total_pairs,
    )


__all__ = [
    "FLOORS",
    "MATCH_COVERAGE",
    "WEIGHTS",
    "Scorecard",
    "best_match",
    "figure_verdicts",
    "identifies",
    "page_words",
    "score_run",
]
