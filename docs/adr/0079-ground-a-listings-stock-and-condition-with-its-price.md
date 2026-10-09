# ADR-0079: Ground a listing's stock and condition with its price

- **Status:** Accepted
- **Date:** 2026-10-09

## Context

A product carried a price, a currency and a seller, and nothing saying whether the
listing could be bought or what state it came in. A refurbished or used listing is
usually the cheapest, so it led a price sort and scored best on price, read as the new
item the name implies. A listing a shop marked out of stock kept its last price, and
`payment._check` would sign a mandate for it. Both facts are on the pages, in visible
text and in the declarations ADR-0078 now reads.

## Decision

**`availability` ("in stock", "out of stock") and `condition` ("new", "used",
"refurbished") describe a listing's price, and are grounded and moved with it.**

- `ExtractedProduct` asks for both as plain strings with `""` for unknown (ADR-0004);
  `to_product` folds a model's spelling ("sold out", "renewed") into one of the
  `Literal` values or `None`, and drops both where there is no price.
- They join `models.QUALIFIERS["price"]`, so `verify_numbers` blanks them with an
  unbacked price and `_fill_gaps` moves them with the price it fills (ADR-0022). Each
  `Offer` carries its own (ADR-0058).
- `verification.verify_standing` keeps a standing only if a page that `mentions_name`
  says is about the product prints it, in the words `models.STANDING_PHRASES` gives
  for it. "New" and "used" alone are everywhere, so each needs the words that make it a
  condition ("brand new", "condition: used", "pre-owned"); "not in stock" is never "in
  stock". `fetch.condense` keeps lines in that vocabulary with the figures.
- `payment._check` refuses a product whose page says it is out of stock. An unknown
  standing is not refused: most pages never say, and refusing them would refuse nearly
  everything.
- `Product.listing_label` ("In stock, refurbished") is the one wording: the report's
  `state` line, the card's pill, the payload's `listing_label`, and `Cart.listing`,
  which the CLI's approval prompt restates.

Ranking does not read either. An out-of-stock or used product is ranked as before,
and says what it is.

## Consequences

A cheap refurbished listing now says so wherever its price is shown, and a payment
can no longer be authorised for stock a page said was gone.

The obligations.

- **`STANDING_PHRASES` has exactly one row per `Availability` and `Condition` value**
  (`tests/test_models.py`), and it is the one vocabulary: grounding reads it, `fetch`
  keeps by it, and `structured` writes in it.
- **A standing is never kept without the price it describes.** A new qualifier of a
  listing joins `QUALIFIERS["price"]`, and so `to_product`, `_fill_gaps`,
  `verify_numbers` and `_as_a_listing` all carry it.
- **`verify_standing` reports as the other blanking steps do** (count at INFO, names
  at DEBUG), and `tests/test_logging_contract.py` drives it.
- **Ranking on stock or condition is a new decision.** Sinking out-of-stock products,
  or a new-only bound, would be a change to what the report holds and needs its own
  record.
