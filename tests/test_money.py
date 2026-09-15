"""How an amount of money is written, read off a page, placed and counted.

One table answers all four questions (ADR-0054). The rules that span it and the
modules reading it -- every spelling scanned for is one that can be placed, and
every spelling that can be placed is one a price is read in -- are in
``tests/test_conventions.py``, where the cross-module rules live. What is here is
the table's own behaviour.
"""

from __future__ import annotations

import pytest

from buy_agent.money import ALIASES, CODES, SIGNS, WORDS, amount_label, code_for, minor_units


# -- placing a spelling --------------------------------------------------------


@pytest.mark.parametrize(
    ("named", "expected"),
    [
        pytest.param("USD", "USD", id="already a code"),
        pytest.param("$", "USD", id="the sign a page printed"),
        pytest.param("usd", "USD", id="lower case"),
        pytest.param("  EUR  ", "EUR", id="padded"),
        pytest.param("dollars", "USD", id="the word a page printed"),
        pytest.param("zł", "PLN", id="a sign of more than one character"),
        pytest.param("HUF", "HUF", id="a code no alias names"),
        pytest.param("", None, id="nothing named"),
        pytest.param("   ", None, id="nothing but space"),
    ],
)
def test_a_spelling_is_placed_as_the_code_the_run_compares_by(
    named: str, expected: str | None
) -> None:
    assert code_for(named) == expected


def test_a_spelling_nothing_knows_is_handed_back_rather_than_guessed_at() -> None:
    """Which is what the whole ``UNPLACEABLE`` exemption rests on: a code the table
    does not know is a price this run cannot place, and "cannot place" already has
    an answer everywhere -- ``NEUTRAL``, last in a price sort, inside every bound
    and unpayable (ADR-0043). Guessing would put half a set on the wrong scale."""
    assert code_for("¥") == "¥"
    assert code_for("XYZ") == "XYZ"


# -- what the scan is built from -----------------------------------------------


def test_the_signs_are_single_characters_and_the_words_are_letters() -> None:
    """``fetch`` scans with the first as a character class and the second between
    word boundaries, so a spelling in the wrong half is a pattern that cannot
    match. The split is a derivation, which is why it is asserted rather than
    trusted."""
    assert all(len(sign) == 1 and not sign.isalpha() for sign in SIGNS)
    assert all(word.isalpha() for word in WORDS)


def test_every_currency_with_a_sign_is_scanned_for_by_its_code_too() -> None:
    """A page prints "129 EUR" as readily as "€129", so a sign added to the table
    brings its code into the scan with it rather than needing a second edit --
    which is the edit that was missed for ``TRY`` (ADR-0054)."""
    assert set(ALIASES.values()) <= CODES
    assert "TRY" in CODES


# -- writing one down ----------------------------------------------------------


@pytest.mark.parametrize(
    ("price", "currency", "expected"),
    [
        pytest.param(329.99, "USD", "329.99 USD", id="an amount in a currency"),
        pytest.param(1234.5, "EUR", "1,234.50 EUR", id="grouped and padded"),
        pytest.param(329.0, None, "329.00", id="a price no page gave a currency for"),
    ],
)
def test_an_amount_is_written_the_one_way_every_surface_writes_it(
    price: float, currency: str | None, expected: str
) -> None:
    assert amount_label(price, currency) == expected


# -- counting one --------------------------------------------------------------


@pytest.mark.parametrize(
    ("price", "currency", "expected"),
    [
        (329.99, "USD", 32999),
        # The reason this goes through Decimal: 19.99 * 100 is
        # 1998.9999999999998, and a payment is not a place to truncate.
        (19.99, "EUR", 1999),
        (0.1 + 0.2, "USD", 30),
        # Half up, which is the rounding a price tag implies.
        (1.005, "USD", 101),
        # Currencies that are not counted in hundredths, which is the whole
        # reason the exponent is looked up rather than assumed.
        (4980.0, "JPY", 4980),
        (12.345, "KWD", 12345),
        # A currency nothing knows falls back to two places, which is what all
        # but two dozen of them use.
        (10.5, "XYZ", 1050),
    ],
)
def test_a_price_becomes_the_currencys_smallest_unit(
    price: float, currency: str, expected: int
) -> None:
    assert minor_units(price, currency) == expected


def test_a_figure_that_is_not_a_number_is_refused_rather_than_counted() -> None:
    """A ``ValueError`` and not a ``PaymentError``: this module knows what an amount
    is and nothing about who is being paid, so whoever is spending translates it --
    ``payment.cart_for`` does, and ``tests/test_payment.py`` says so."""
    with pytest.raises(ValueError, match="not a price"):
        minor_units(float("nan"), "USD")
