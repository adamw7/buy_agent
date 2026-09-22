"""Shared fakes, and where to read this project back off its own disk."""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from buy_agent import mandates
from buy_agent.logging_setup import _NOISY_LIBRARIES, _TRACE_LIBRARIES
from buy_agent.models import (
    ExtractedProduct,
    Opinion,
    Product,
    ProductList,
    RankedProduct,
    ScoreParts,
    SearchQuery,
)
from buy_agent.screenshots import ScreenshotError
from buy_agent.search import SearchResult

if TYPE_CHECKING:
    from collections.abc import Iterator


#: This repository as it is written, which is not always the tree the suite is running in.
SOURCE_ROOT = Path(__file__).resolve().parents[1]
if SOURCE_ROOT.name == "mutants":
    SOURCE_ROOT = SOURCE_ROOT.parent


#: Skips a test that cannot run without the optional AP2 SDK, the way
#: ``tests/test_start_script.py`` skips what cannot run without a PowerShell.
needs_ap2 = pytest.mark.skipif(
    not mandates.available(),
    reason=f"the optional AP2 SDK is not installed -- add it with:  {mandates.INSTALL}",
)


@pytest.fixture(scope="session")
def _scratch_cache(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("cache")


@pytest.fixture(autouse=True)
def cache_somewhere_disposable(
    _scratch_cache: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Point every test's cache at a scratch directory of its own."""
    named = re.sub(r"[^\w.-]", "_", request.node.name)
    monkeypatch.setenv("BUY_AGENT_CACHE_DIR", str(_scratch_cache / named))


@pytest.fixture(autouse=True)
def pay_with_nothing_of_the_developers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset the two payment variables for every test in the suite."""
    monkeypatch.delenv("BUY_AGENT_AP2_KEY", raising=False)
    monkeypatch.delenv("BUY_AGENT_AP2_MANDATE", raising=False)
    monkeypatch.delenv("BUY_AGENT_MERCHANT_URL", raising=False)


#: Every logger ``configure_logging`` sets a level on, which is every logger a test can
#: leave changed for the ones after it.
_LEVELS_CONFIGURE_LOGGING_SETS = ("", "buy_agent", *_NOISY_LIBRARIES, *_TRACE_LIBRARIES)


@pytest.fixture(autouse=True)
def leave_every_logger_as_it_was() -> Iterator[None]:
    """Put back every level ``configure_logging`` sets, after every test."""
    kept = [
        (logger, logger.level)
        for logger in map(logging.getLogger, _LEVELS_CONFIGURE_LOGGING_SETS)
    ]
    yield
    for logger, level in kept:
        logger.setLevel(level)


#: What an open mandate is allowed to be spent on, where a test does not care.
OPEN_MANDATE_LIMIT = 40_000
OPEN_MANDATE_PAYEE = "audiosite.example"


def open_mandate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    maximum: int = OPEN_MANDATE_LIMIT,
    payee: str = OPEN_MANDATE_PAYEE,
) -> tuple[Any, Any]:
    """Put a pre-signed open mandate where ``$BUY_AGENT_AP2_MANDATE`` will find it.

    Returns:
        The agent key the mandate delegates to, and the issuer's.
    """
    from ap2.sdk.generated.open_payment_mandate import (
        AllowedPayees,
        AmountRange,
        OpenPaymentMandate,
    )
    from ap2.sdk.generated.types.merchant import Merchant
    from ap2.sdk.mandate import MandateClient

    issuer = mandates.generate_key("issuer")
    agent = mandates.generate_key("agent")
    now = int(time.time())
    token = MandateClient().create(
        payloads=[
            OpenPaymentMandate(
                constraints=[
                    AmountRange(currency="USD", min=0, max=maximum),
                    AllowedPayees(
                        allowed=[
                            Merchant(id=payee, name="AudioSite", website=f"https://{payee}")
                        ]
                    ),
                ],
                cnf={"jwk": json.loads(agent.export_public())},
                iat=now,
                exp=now + 3600,
            )
        ],
        issuer_key=issuer,
    )
    path = tmp_path / "mandate.json"
    path.write_text(
        json.dumps({"mandate": token, "issuer_jwk": json.loads(issuer.export_public())}),
        encoding="utf-8",
    )
    monkeypatch.setenv(mandates.MANDATE_PATH, str(path))
    return agent, issuer


def enrolled_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: Any | None = None
) -> Any:
    """An EC P-256 key on disk, with ``$BUY_AGENT_AP2_KEY`` naming it.

    Returns:
        The key, whether it was handed in or made here.
    """
    key = key or mandates.generate_key("agent")
    path = tmp_path / "agent.pem"
    path.write_bytes(key.export_to_pem(private_key=True, password=None))
    monkeypatch.setenv(mandates.KEY_PATH, str(path))
    return key


class FakeLLM:
    """Stands in for a model server: a canned object per requested schema."""

    def __init__(
        self,
        *,
        query: SearchQuery | None = None,
        products: ProductList | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.query = query or SearchQuery(query="fake refined query")
        self.products = products or ProductList()
        self.raises = raises
        self.calls: list[Any] = []

    def answer(self, messages: Any, schema: type) -> Any:
        self.calls.append(messages)
        if self.raises is not None:
            raise self.raises
        return self.query if schema is SearchQuery else self.products


class Photographer:
    """Stands in for the server's camera, the way ``FakeLLM`` stands in for the model: a
    picture naming the address it was asked for, or the failure it was handed (ADR-0065).
    """

    def __init__(self, failure: ScreenshotError | None = None) -> None:
        self.failure = failure
        self.asked: list[str] = []
        self.closed = False

    def shoot(self, url: str) -> bytes:
        self.asked.append(url)
        if self.failure is not None:
            raise self.failure
        return f"jpeg of {url}".encode()

    def close(self) -> None:
        self.closed = True


def said(*quotes: str, page: str | None = None) -> list[Opinion]:
    """Quotes as a grounded product carries them: words beside the page that printed them
    (ADR-0042)."""
    return [Opinion(text=quote, url=page) for quote in quotes]


def ranked_product(product: Product, *, score: float, rank: int) -> RankedProduct:
    """One finished ranking entry with the score a test wants it to have."""
    assumed = [
        name
        for name, figure in (
            ("rating", product.rating),
            ("popularity", product.review_count),
            ("price", product.price),
        )
        if figure is None
    ]
    return RankedProduct(
        product=product,
        breakdown=ScoreParts(
            rating=score, popularity=score, price=score, total=score, neutral=assumed
        ),
        rank=rank,
    )


def payable_product(**extra: Any) -> Product:
    """A product a run really could pay for, and the one four files needed."""
    return Product(
        **{
            "name": "Sony WH-1000XM5",
            "price": 329.99,
            "currency": "USD",
            "seller": "AudioSite",
            "url": "https://audiosite.example/xm5",
        }
        | extra
    )


@pytest.fixture
def search_results() -> list[SearchResult]:
    return [
        SearchResult(
            title="Sony WH-1000XM5 review",
            url="https://example.com/sony",
            snippet="$328, rated 4.7 out of 5 from 12000 reviews.",
        ),
        SearchResult(
            title="Anker Soundcore Q30",
            url="https://example.com/anker",
            snippet="$79, rated 4.3 out of 5 from 90000 reviews.",
        ),
        SearchResult(
            title="Unknown Brand Buds land this week",
            url="https://example.com/unknown",
            snippet="The Unknown Brand Buds are out now. No word on pricing.",
        ),
    ]


@pytest.fixture
def extracted_products() -> ProductList:
    return ProductList(
        products=[
            ExtractedProduct(
                name="Sony WH-1000XM5",
                price=328.0,
                currency="usd",
                rating=4.7,
                review_count=12000,
                seller="Amazon",
                url="https://example.com/sony",
                notes="Best noise cancelling.",
            ),
            ExtractedProduct(
                name="Anker Soundcore Q30",
                price=79.0,
                currency="USD",
                rating=4.3,
                review_count=90000,
                seller="Anker",
                url="https://example.com/anker",
            ),
            ExtractedProduct(name="Unknown Brand Buds"),
        ]
    )
