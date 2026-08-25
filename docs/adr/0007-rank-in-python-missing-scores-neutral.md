# ADR-0007: Rank in Python, and score missing data neutral rather than zero

- **Status:** Accepted
- **Date:** 2026-08-25

## Context

The ordering is the product. Asking the model to rank -- "here are nine
products, sort them" -- puts the one thing the user actually reads behind a step
that is neither reproducible nor testable, performed by a model that is good at
copying and bad at arithmetic.

Given ranking in code, the scoring has to answer a second question: what does a
missing figure score? Grounding blanks any price, rating or review count the
sources did not back (ADR-0006), so blanks are common and mostly say something
about the extractor or the page, not about the product. Scoring a blank as zero
punishes a listing for not publishing a rating more harshly than a listing that
published a bad one.

## Decision

`ranking.py` contains no LLM call. `rank_products` scores each product in
`[0, 1]` as a weighted sum: rating 0.5 (`rating / 5`), popularity 0.2
(`log10(reviews)`, saturating at 1,000 -- the tenth review says far more than the
ten-thousandth), price 0.3 (relative to the candidate set: cheapest 1.0, dearest
0.0, everything tied at neutral when there is one distinct price). The mix is
configurable through `RankingWeights`.

**`NEUTRAL` is 0.5, and every unknown scores it.** For the same reason,
`sort_by="price"` and `sort_by="rating"` sink products missing that field to the
bottom of the list instead of dropping them: the user asked to sort, not to
filter.

`clean_products` applies the same principle earlier, filtering the article
headlines the model reports as products ("12 Best Headphones Under $200") in
code rather than asking the model to use better judgement.

## Consequences

Ordering is deterministic, unit-tested with plain data, and explainable -- the
score for any product can be worked out by hand. Adjusting the emphasis is a
constructor argument, not a prompt rewrite.

The scoring is only as good as its arithmetic: it does not understand that a
gaming laptop and a Chromebook are not comparable, and it will happily rank both
if the search returned both. And neutral scoring is a deliberate blunt edge --
a product with no data at all scores 0.5 across the board, which is mid-field
rather than last, so an extraction that found nothing about a product still
leaves it plausibly placed.
