"""The report is the agent's entire output, so it is checked line by line."""

from __future__ import annotations

import logging

import pytest

from buy_agent.logging_setup import _NOISY_LIBRARIES, configure_logging, log_top_products
from buy_agent.models import Product, RankedProduct


def ranked(*products: Product) -> list[RankedProduct]:
    """Wrap products as a finished ranking, best first."""
    return [
        RankedProduct(product=product, score=1.0 - index / 10, rank=index + 1)
        for index, product in enumerate(products)
    ]


@pytest.fixture(autouse=True)
def restore_httpx_level():
    """configure_logging() reaches into a global logger; put it back afterwards."""
    httpx_logger = logging.getLogger("httpx")
    level = httpx_logger.level
    yield
    httpx_logger.setLevel(level)


@pytest.fixture
def report(caplog):
    """Capture the report at INFO, the level the agent reports at."""
    caplog.set_level(logging.INFO, logger="buy_agent")
    return caplog


def test_nothing_to_report_is_a_warning(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="buy_agent"):
        log_top_products([], 3)

    assert "No products to report." in caplog.text


def test_only_the_top_n_are_logged(report) -> None:
    log_top_products(
        ranked(Product(name="First"), Product(name="Second"), Product(name="Third")), 2
    )

    assert "TOP 2 OF 3 PRODUCTS" in report.text
    assert "Third" not in report.text


def test_asking_for_more_than_there_is_reports_what_there_is(report) -> None:
    log_top_products(ranked(Product(name="Only one")), 10)

    assert "TOP 1 OF 1 PRODUCTS" in report.text


def test_every_known_field_reaches_the_report(report) -> None:
    log_top_products(
        ranked(
            Product(
                name="Sony WH-1000XM5",
                price=328.0,
                currency="USD",
                rating=4.7,
                review_count=12000,
                seller="Amazon",
                url="https://shop.example/sony",
                notes="Best noise cancelling.",
                opinions=["the noise cancelling is uncanny"],
            )
        ),
        1,
    )

    text = report.text
    assert "#1  Sony WH-1000XM5" in text
    assert "score  : 1.000" in text
    assert "price  : 328.00 USD" in text
    assert "rating : 4.7/5 (12,000 reviews)" in text
    assert "seller : Amazon" in text
    assert "url    : https://shop.example/sony" in text
    assert "note   : Best noise cancelling." in text
    assert "says   : the noise cancelling is uncanny" in text


def test_unknown_fields_are_left_out_rather_than_shown_blank(report) -> None:
    log_top_products(ranked(Product(name="Mystery Gadget")), 1)

    text = report.text
    assert "price  : price unknown" in text
    assert "rating : unrated" in text
    assert "seller" not in text
    assert "url" not in text
    assert "note" not in text
    assert "says" not in text


def test_each_product_gets_its_own_numbered_block(report) -> None:
    log_top_products(ranked(Product(name="Alpha"), Product(name="Beta")), 3)

    assert "#1  Alpha" in report.text
    assert "#2  Beta" in report.text


@pytest.fixture
def basic_config(monkeypatch):
    """Record what configure_logging asks logging.basicConfig for."""
    captured: dict = {}
    monkeypatch.setattr(logging, "basicConfig", lambda **kwargs: captured.update(kwargs))
    return captured


def test_logging_defaults_to_info(basic_config) -> None:
    configure_logging()

    assert basic_config["level"] == logging.INFO


def test_verbose_logging_is_debug(basic_config) -> None:
    configure_logging(verbose=True)

    assert basic_config["level"] == logging.DEBUG


def test_the_format_names_the_logger_and_the_message(basic_config) -> None:
    configure_logging()

    assert "%(name)s" in basic_config["format"]
    assert "%(message)s" in basic_config["format"]


@pytest.mark.parametrize("library", _NOISY_LIBRARIES)
def test_the_http_clients_are_quietened_so_they_cannot_drown_out_the_report(
    basic_config, library: str
) -> None:
    """httpx logs every single Ollama call at INFO, and the OpenAI client logs a
    line per retry -- so a stopped vLLM prints its own retries above the one
    message that says what to do about it. One per model server; parametrized off
    the list, so a third arrives here with the provider that needs it."""
    logging.getLogger(library).setLevel(logging.NOTSET)

    configure_logging()

    assert logging.getLogger(library).level == logging.WARNING


@pytest.mark.parametrize("library", _NOISY_LIBRARIES)
def test_verbose_leaves_them_alone(basic_config, library: str) -> None:
    """Debugging is exactly when those calls are worth seeing."""
    logging.getLogger(library).setLevel(logging.NOTSET)

    configure_logging(verbose=True)

    assert logging.getLogger(library).level == logging.NOTSET


def test_every_opinion_gets_its_own_line(report) -> None:
    """Two verdicts on one line would read as one reviewer's sentence."""
    log_top_products(
        ranked(Product(name="Sony WH-1000XM5", opinions=["the fit is snug", "the case is bulky"])),
        1,
    )

    assert report.text.count("says   :") == 2
