"""Every use of the agent the benchmark scores, by name (ADR-0036, ADR-0070)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from buy_agent.config import AgentConfig
from benchmark import corpus, espresso, laptops
from benchmark.answers import ANSWER_KEY
from benchmark.scoring import METRICS
from benchmark.scripted import PERFECT, REFINED_QUERY, SLOPPY, ScriptedLLM

if TYPE_CHECKING:
    from collections.abc import Mapping

    from buy_agent.models import ProductList
    from buy_agent.search import SearchResult
    from benchmark.answers import Expected
    from benchmark.query import QueryKey

#: The hand-written answers every case carries, and what each one is for.
SCRIPTS: dict[str, str] = {
    "perfect": "The answer key copied out, with no model: 1.000 on every case.",
    "sloppy": "The same answer, wrong in the ways small models are: a reference low.",
}


@dataclass(frozen=True, slots=True, eq=False)
class Case:
    """One use of the agent: a request, the web it searches, what that web says, and the
    hand-written answers that check the scorer.

    Attributes:
        name: What the command line and the page call it.
        title: What it is, in a few words.
        asks: What it asks of a model that the other cases do not.
        refined: The query the scripted answers search with.
        scripts: :data:`SCRIPTS`' answers for this case, by name.
    """

    name: str
    title: str
    asks: str
    request: str
    pages: tuple[SearchResult, ...]
    page_text: Mapping[str, str]
    key: tuple[Expected, ...]
    query: QueryKey
    refined: str
    scripts: Mapping[str, ProductList]
    num_products: int = corpus.NUM_PRODUCTS
    top_n: int = corpus.TOP_N

    def settings(self, **overrides: object) -> AgentConfig:
        """The config a run of this case uses: the shipped defaults, on its pages.

        Args:
            **overrides: Fields to set instead -- the model and its server, which belong
            to whoever is being scored rather than to the case.
        """
        fields: dict[str, object] = {
            "search_results": len(self.pages),
            "num_products": self.num_products,
            "top_n": self.top_n,
            # Nothing is remembered between runs (ADR-0044).
            "cache_ttl": 0,
        }
        return AgentConfig(**(fields | overrides))  # type: ignore[arg-type]

    def scripted(self, script: str) -> ScriptedLLM:
        """The model that answers this case with one of its scripts."""
        return ScriptedLLM(self.scripts[script], self.refined)

    @property
    def fingerprint(self) -> str:
        """What a result was scored against: the request, the pages, the key and the
        metrics. A kept result with another fingerprint scored a different case."""
        document = {
            "request": self.request,
            "pages": [[page.title, page.url, page.snippet] for page in self.pages],
            "text": dict(self.page_text),
            "key": [
                [
                    entry.name,
                    entry.price,
                    entry.rating,
                    entry.review_count,
                    entry.currency,
                    sorted(entry.prices),
                    sorted(entry.ratings),
                    sorted(entry.pages),
                ]
                for entry in self.key
            ],
            "slots": self.num_products,
            "metrics": sorted(METRICS),
        }
        encoded = json.dumps(document, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:16]


HEADPHONES = Case(
    name="headphones",
    title="Headphones for flights, in dollars",
    asks=(
        "Review pages that each price several products, a euro listing, a headline "
        "and a shop's name to mistake for products, and the same product named two ways."
    ),
    request=corpus.REQUEST,
    pages=corpus.PAGES,
    page_text=corpus.PAGE_TEXT,
    key=ANSWER_KEY,
    query=corpus.QUERY,
    refined=REFINED_QUERY,
    scripts={"perfect": PERFECT, "sloppy": SLOPPY},
)

LAPTOPS = Case(
    name="laptops",
    title="A gaming laptop to carry, in dollars",
    asks=(
        "Prices in the thousands beside spec sheets, a monthly payment, a student "
        "price, the gap between two prices, and a listing in Canadian dollars."
    ),
    request=laptops.REQUEST,
    pages=laptops.PAGES,
    page_text=laptops.PAGE_TEXT,
    key=laptops.ANSWER_KEY,
    query=laptops.QUERY,
    refined=laptops.REFINED_QUERY,
    scripts={"perfect": laptops.PERFECT, "sloppy": laptops.SLOPPY},
)

ESPRESSO = Case(
    name="espresso",
    title="An espresso machine, in euros",
    asks=(
        "Decimal commas and dotted thousands, euro prices beside an American review "
        "in dollars, and cashback and accessories to mistake for a price."
    ),
    request=espresso.REQUEST,
    pages=espresso.PAGES,
    page_text=espresso.PAGE_TEXT,
    key=espresso.ANSWER_KEY,
    query=espresso.QUERY,
    refined=espresso.REFINED_QUERY,
    scripts={"perfect": espresso.PERFECT, "sloppy": espresso.SLOPPY},
)

#: Every case, by name, in the order a comparison runs them.
CASES: dict[str, Case] = {case.name: case for case in (HEADPHONES, LAPTOPS, ESPRESSO)}


def case_for(name: str) -> Case:
    """The case called ``name``."""
    try:
        return CASES[name]
    except KeyError:
        raise ValueError(
            f"Unknown case {name!r}; expected one of {', '.join(CASES)}."
        ) from None


__all__ = ["CASES", "ESPRESSO", "HEADPHONES", "LAPTOPS", "SCRIPTS", "Case", "case_for"]
