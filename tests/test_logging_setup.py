"""The report is the agent's entire output, so it is checked line by line."""

from __future__ import annotations

import logging
import sys

import pytest

from buy_agent.logging_setup import _NOISY_LIBRARIES, configure_logging, log_top_products
from buy_agent.models import Product, RankedProduct
from buy_agent.ranking import rank_products
from tests.conftest import ranked_product, said


def ranked(*products: Product) -> list[RankedProduct]:
    """Wrap products as a finished ranking, best first."""
    return [
        ranked_product(product, score=1.0 - index / 10, rank=index + 1)
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
                opinions=said(
                    "the noise cancelling is uncanny", page="https://shop.example/sony"
                ),
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


@pytest.fixture
def split_streams(monkeypatch, capsys):
    """Set the split up over real streams, and put the loggers back afterwards.

    What it makes is a split between *streams*, so it can only be checked against
    real ones -- and they have to be taken inside the test rather than here:
    pytest replaces them once per phase, so a handler built while this fixture was
    setting up would be writing to a file already closed by the time the test
    runs. It reaches into the root logger and the package logger to make the
    split, and neither is this test's to leave changed.
    """
    package = logging.getLogger("buy_agent")
    root = logging.getLogger()
    kept = (package.handlers[:], root.handlers[:], root.level)
    monkeypatch.setattr(logging, "basicConfig", lambda **kwargs: None)

    def configure():
        root.handlers = [logging.StreamHandler(sys.stderr)]
        root.setLevel(logging.INFO)  # what the stubbed basicConfig would have set
        package.handlers = []
        configure_logging()
        return capsys

    yield configure

    package.handlers, root.handlers, root.level = kept


def test_the_report_goes_to_stdout(split_streams) -> None:
    """`python -m buy_agent ... > top.txt` is asking for the answer, and until the
    report was split off it caught an empty file: all of it went to stderr."""
    streams = split_streams()

    log_top_products(ranked(Product(name="Sony WH-1000XM5")), 1)

    captured = streams.readouterr()
    assert "TOP 1 OF 1 PRODUCTS" in captured.out
    assert "Sony WH-1000XM5" in captured.out
    assert captured.err == ""


def test_the_progress_goes_to_stderr(split_streams) -> None:
    """The narration is not the answer, and must not land in the redirect."""
    streams = split_streams()

    logging.getLogger("buy_agent.agent").info("Fetching 10 result page(s)")

    captured = streams.readouterr()
    assert "Fetching 10 result page(s)" in captured.err
    assert captured.out == ""


def test_neither_stream_gets_the_other_s_lines(split_streams) -> None:
    streams = split_streams()

    logging.getLogger("buy_agent.agent").info("Shopping for: headphones")
    log_top_products(ranked(Product(name="Sony WH-1000XM5")), 1)

    captured = streams.readouterr()
    assert captured.out.count("Sony WH-1000XM5") == 1
    assert "Shopping for: headphones" not in captured.out
    assert captured.err.count("Shopping for: headphones") == 1
    assert "Sony WH-1000XM5" not in captured.err


def test_configuring_twice_does_not_print_the_report_twice(split_streams) -> None:
    """The server configures logging as well as the CLI, and one is importable
    from the other: a second call must replace the first's handler, not add one."""
    streams = split_streams()

    configure_logging()
    log_top_products(ranked(Product(name="Sony WH-1000XM5")), 1)

    assert streams.readouterr().out.count("Sony WH-1000XM5") == 1


def test_a_handler_that_is_not_the_console_still_sees_the_whole_run(split_streams) -> None:
    """The relay behind the browser's progress panel is one of these.

    Only the console handler is told to skip the report -- a handler collecting
    the run, for a transcript or for a test, is nobody's stream to take lines out
    of, and taking them would hide the report from a caller who asked for all of it.
    """
    collected: list[str] = []
    relay = logging.Handler()
    relay.emit = lambda record: collected.append(record.getMessage())  # type: ignore[method-assign]
    split_streams()
    logging.getLogger("buy_agent").addHandler(relay)

    logging.getLogger("buy_agent.agent").info("Shopping for: headphones")
    log_top_products(ranked(Product(name="Sony WH-1000XM5")), 1)

    assert "Shopping for: headphones" in collected
    assert any("Sony WH-1000XM5" in message for message in collected)


def test_nothing_found_is_not_reported_on_stdout(split_streams) -> None:
    """There is no report, so the line saying so is the run talking: stderr."""
    streams = split_streams()

    log_top_products([], 3)

    captured = streams.readouterr()
    assert "No products to report." in captured.err
    assert captured.out == ""


def test_the_report_is_a_block_with_a_rule_at_each_end(report) -> None:
    """The report is what ``> top.txt`` catches and what the browser's panel shows
    as the run's answer, so it has to read as one block rather than as lines that
    happen to follow the narration.

    The closing rule is the half nothing else here would notice going missing: a
    report that opens with one and never closes leaves the last product looking
    like the first line of whatever comes next.
    """
    log_top_products(ranked(Product(name="Alpha"), Product(name="Beta")), 2)

    lines = [record.getMessage() for record in report.records]
    assert set(lines[0]) == {"="}, "the report opens with a rule"
    assert lines[2] == lines[0], "and the title sits between two of them"
    assert lines[-1] == lines[0], "and it closes with the same rule"
    assert lines.count(lines[0]) == 3, "three rules and no more: it is one block"


@pytest.mark.parametrize(
    ("page", "expected"),
    [
        # The ordinary case: the URL is two lines up already, and repeating it
        # under each of three quotes says nothing.
        pytest.param("https://shop.example/sony", "says   : the fit is snug",
                     id="the product's own page"),
        # The case the link is worth printing: words off a page the report would
        # otherwise never name.
        pytest.param("https://audiosite.example/xm5",
                     "says   : the fit is snug  -- https://audiosite.example/xm5",
                     id="another page"),
        # A result the search returned without a URL still printed the words.
        pytest.param(None, "says   : the fit is snug", id="no page behind it"),
    ],
)
def test_a_quote_names_its_page_only_where_that_is_news(
    page: str | None, expected: str, report
) -> None:
    """ADR-0042, as the report writes it."""
    log_top_products(
        ranked(
            Product(
                name="Sony WH-1000XM5",
                url="https://shop.example/sony",
                opinions=said("the fit is snug", page=page),
            )
        ),
        1,
    )

    assert expected in report.text
    assert report.text.count("https://shop.example/sony") == 1


def test_every_opinion_gets_its_own_line(report) -> None:
    """Two verdicts on one line would read as one reviewer's sentence."""
    log_top_products(
        ranked(
            Product(
                name="Sony WH-1000XM5", opinions=said("the fit is snug", "the case is bulky")
            )
        ),
        1,
    )

    assert report.text.count("says   :") == 2


def test_the_report_says_what_a_score_is_made_of(caplog) -> None:
    """A bare 0.670 says where a product placed and nothing about why (ADR-0041)."""
    with caplog.at_level(logging.INFO, logger="buy_agent"):
        log_top_products(
            rank_products(
                [
                    Product(name="Sony", price=328.0, rating=4.7, review_count=12000),
                    Product(name="Anker", price=79.0, rating=4.3, review_count=90000),
                ]
            ),
            1,
        )

    # The Anker tops this pair: better value and more reviewed, and cheapest in
    # a set of two, which is the whole of the price criterion.
    assert "rating 0.86, popularity 1.00, price 1.00" in caplog.text


def test_the_report_marks_a_share_that_was_assumed_rather_than_read(caplog) -> None:
    """The distinction the whole breakdown exists for: 0.50 because nothing was
    published reads identically to 0.50 because the product is middling."""
    with caplog.at_level(logging.INFO, logger="buy_agent"):
        log_top_products(rank_products([Product(name="Silent")]), 1)

    assert "rating 0.50 assumed, popularity 0.50 assumed, price 0.50 assumed" in caplog.text
