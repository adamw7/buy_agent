"""How an amount of money is written, read off a page, placed and counted."""

from __future__ import annotations

import pytest

from buy_agent.money import (
    ALIASES,
    CODES,
    SCANNED_CODES,
    SIGNS,
    UNPLACEABLE,
    UNSCANNED,
    WORDS,
    amount_label,
    code_for,
    minor_units,
    placeable,
)


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


def test_the_signs_are_symbols_and_the_words_are_letters() -> None:
    """``fetch`` scans with the first as a character class and the second between word
    boundaries, so a spelling in the wrong half is a pattern that cannot match."""
    assert len(SIGNS) == len(set(SIGNS)), "a repeated character is a spelling let in whole"
    assert not any(sign.isalpha() for sign in SIGNS), "a sign is a symbol, never a letter"
    assert all(word.isalpha() for word in WORDS), "a word is scanned between boundaries"
    assert all(code.isalpha() for code in SCANNED_CODES), "a code is scanned the same way"


def test_the_letters_are_split_at_the_codes_and_nowhere_else() -> None:
    """Which half a spelling lands in decides the cases a page may print it in: the
    words are folded and the codes are read as written. So the line has to be the
    table's own -- a code is three letters a page prints as three letters, an alias is
    a word it writes however it likes -- and an alias that drifted into the cased half
    would be a price missed for a capital letter."""
    letters = {s for s in (ALIASES.keys() | CODES) - UNSCANNED if s.isalpha()}

    assert set(SCANNED_CODES) == CODES - UNSCANNED, "the codes, and every one of them"
    assert not set(WORDS) & set(SCANNED_CODES), "a spelling scanned two ways twice over"
    assert set(WORDS) | set(SCANNED_CODES) == letters, "and between them, all of them"


def test_the_derivation_applies_both_exemptions() -> None:
    """Each is subtracted from exactly one half, and the halves are what ``fetch`` scans
    with -- so an exemption that failed to reach the derivation would be a sentence in
    this module and no behaviour anywhere."""
    assert UNPLACEABLE <= set(SIGNS), "read off a page, so it stays in the scan"
    assert not UNSCANNED & (set(WORDS) | set(SCANNED_CODES)), "never read off a page"
    assert UNSCANNED <= ALIASES.keys(), "...and still a spelling the table places"


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


# -- the currency a run may be told to count itself in (ADR-0056) -------------


@pytest.mark.parametrize("spelling", ["USD", "usd", "$", " eur ", "zł", "PLN"])
def test_a_spelling_a_page_could_have_printed_is_one_a_shopper_may_name(
    spelling: str,
) -> None:
    """One table decides which spellings are one currency (ADR-0054), so choosing a
    scale and reporting one are the same question about a spelling."""
    assert placeable(spelling) == code_for(spelling)


@pytest.mark.parametrize("spelling", ["XXX", "dollarydoos", "", "  "])
def test_a_spelling_this_run_could_never_place_is_no_scale_to_count_on(
    spelling: str,
) -> None:
    """The narrower question ``code_for`` does not answer: that one hands an unknown
    spelling back as written, which is a price nothing can place -- and a *scale*
    nothing can place is a report with no price criterion at all."""
    assert placeable(spelling) is None


def test_a_sign_read_off_a_page_and_never_placed_is_never_a_scale_either() -> None:
    """``UNPLACEABLE``, from this side: the yen's sign and the yuan's alike."""
    assert all(placeable(sign) is None for sign in UNPLACEABLE)
