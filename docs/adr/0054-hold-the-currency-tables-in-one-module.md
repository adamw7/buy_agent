# ADR-0054: Hold every currency table in one module

- **Status:** Accepted
- **Date:** 2026-09-15

## Context

ADR-0043 settled that prices are compared inside one currency and converted
never. Three modules then grew a table of their own to hold up their end of it:

- `fetch._CURRENCY_SIGNS` and `_CURRENCY_WORDS`, the spellings that make a page's
  line worth keeping;
- `models._CURRENCY_ALIASES`, the spellings that are the same currency;
- `payment._ZERO_DECIMAL` and `_THREE_DECIMAL`, the currencies not counted in
  hundredths.

The first two are one rule. A spelling `fetch` does not scan for is every price
on that shop dropped before the model sees it; a spelling `models` cannot place
is a price that scores `NEUTRAL`, sinks in a price sort, passes every bound and
cannot be paid for. `--region pl-pl` was the first half of that, invisibly, until
`zł` was added to one table and not the other.

What was done about it was a convention test reaching into both modules'
privates -- `fetch_module._CURRENCY_SIGNS` against `models_module._currency` --
and it could only ever check one direction: that everything scanned for could be
placed. The other direction was wrong in three places at once. `models` placed
`DOLLAR`, `DOLLARS`, `EURO`, `EUROS`, `POUND` and `POUNDS`, and `fetch` kept a
line for none of them, so a page writing "sells for 349 dollars" contributed no
price at all. `₺` was scanned for and placed as `TRY`, which was in neither
module's list of codes, so "8999 TRY" was dropped as well.

A test whose own docstring reads *the two tables are one rule across two modules,
and neither module can hold it* is naming a module that has not been written.

## Decision

`buy_agent/money.py` holds every table: how a spelling is placed (`ALIASES`,
`code_for`), which spellings a page is scanned for (`CODES`, `SIGNS`, `WORDS`),
how an amount is written for somebody to read (`amount_label`), and how many of
the currency's smallest units it comes to (`minor_units`). It sits in the domain
layer and imports nothing from the package.

`SIGNS` and `WORDS` are **derived** from the one table rather than written beside
it, split by whether a spelling is a single character or letters -- the two ways
`fetch` can scan for one. `CODES` contains every code `ALIASES` maps to, so a
sign added to the table is scanned for by its code with nobody having to write
the code down again.

Two exemptions are named, each with its sentence, and each held to by a test:

- `UNPLACEABLE` is read off a page and deliberately never placed. `¥` is the
  yen's sign and the yuan's alike.
- `UNSCANNED` is placed and deliberately never read off a page. "Pounds" is a
  unit of mass beside being GBP, and a review of a 2 lb laptop prints it far more
  often than a price does.

`minor_units` raises `ValueError`, not `PaymentError`: this module knows what an
amount is and nothing about who is being paid. `payment.cart_for` translates it.

## Consequences

The rule is structural instead of tested-in-one-direction, and the two gaps above
close: a page writing "349 dollars", "1,299 euros" or "8999 TRY" now contributes
its price.

Both directions are asserted in `tests/test_conventions.py` anyway, because the
derivation has a seam of its own: `US$` and `C$` are reached by neither `SIGNS`
nor `WORDS` on their own, only by the `$` inside them. So the test asks
`fetch.quotes_a_figure` after every spelling rather than after the split, and a
new one that the derivation drops fails there rather than going quiet. Both
exemptions are checked from the other side too, so neither can outlive its
reason.

What this obliges:

- A currency added anywhere is added to `money.ALIASES` or `money.CODES`, and
  nowhere else. A second table in a module that needs one is the thing this
  record exists to stop.
- A spelling that is neither a single sign nor plain letters needs the derivation
  widened, not an entry smuggled into `fetch`. The convention test is what says
  so.
- `payment.cart_for` keeps its translation. It is not defensive: `Product`
  refuses an infinite price, but a finite `1e308` is a product pydantic builds
  and `_check` passes, and scaling it into minor units overflows the decimal
  context. Untranslated it reaches `pay_now`, which catches `PaymentError` and
  not `ValueError`, and the browser gets a 500 for a number.
- The cost is one more module in the domain layer, and one more import in
  `fetch`, `models`, `payment` and `api`.
- [ADR-0049](0049-load-the-optional-pylint-checkers.md) cites `minor_units` by
  its old home, `payment.py`, as the one `try` wider than a statement. The
  function moved here whole, comment included; the record stays as written, an
  accepted one not being rewritten.
