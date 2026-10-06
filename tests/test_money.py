"""How an amount of money is written, read off a page, placed and counted."""

from __future__ import annotations

import pytest

from buy_agent.money import (
    minor_units,
)


# -- placing a spelling --------------------------------------------------------


# -- what the scan is built from -----------------------------------------------


# -- writing one down ----------------------------------------------------------


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
