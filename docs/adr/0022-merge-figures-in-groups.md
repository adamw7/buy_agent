# ADR-0022: Merge a listing's figures in groups, never field by field

- **Status:** Accepted
- **Date:** 2026-08-26

## Context

ADR-0006 checks every figure against the source text, and ADR-0008 puts `ground`
before `deduplicate` so that a merge only ever combines figures the sources back.
That is necessary and it is not sufficient, because the merge does not only carry
figures over -- it puts them next to each other, and a *pairing* is a claim the
sources never made.

`_combine` fills the blanks of the more complete listing from the other one.
Taken a field at a time, that reported combinations neither page printed:

- a page quoting `129` merged with a page quoting "249 EUR" was reported as
  "129.00 EUR";
- a `4.4` with no review count merged with a `4.9` averaged over 12,000 of them
  became "4.4/5 (12,000 reviews)" -- which also lifted the popularity half of the
  score onto a count belonging to the other rating.

Grounding cannot see either. It has already run by then (ADR-0008), and each half
really is in the sources: the currency was printed, the price was printed, and
nothing in `verification.py` knows they were printed on different pages. This is
the attribution gap ADR-0006 records as a limitation, except that here the
codebase is not failing to notice a bad pairing on a page -- it is creating one.

## Decision

A figure travels with the words that qualify it. `_MERGEABLE_FIELDS` groups each
figure with whatever only makes sense beside it, and `_fill_gaps` moves whole
groups rather than fields:

```python
("price", "currency"), ("rating", "review_count"), ("seller",), ("url",), ("notes",)
```

The loser's qualifier crosses over in exactly two cases: where its figure crosses
over too, so the pair arrives as the one listing printed it; or where both
listings quote the *same* figure, in which case the qualifier is describing the
very figure being kept. A field that qualifies nothing is a group of one and
merges as before.

**A new field that only means something next to another belongs in that other's
group**, not in a group of its own. `currency` alone says nothing; a review count
without the rating it averages is not a fact about the product.

## Consequences

Each pair in the report -- price with currency, rating with review count -- is one
that some single listing actually printed, which is what `ranking` then scores.

The cost is the familiar one from ADR-0006, one level down: a genuine currency or
review count is dropped when the figure it describes is not the figure kept, so
the merge loses detail rather than inventing it. That is the direction to fail in.

`_MERGEABLE_FIELDS` is now the list to edit when a field is added to `Product`,
and nothing enforces that. A field left out of it is not a test failure and not a
wrong answer -- it simply never merges, so a product whose better listing left it
blank reports it blank for ever. `tests/test_conventions.py` cannot catch this
either: it reads declarations that exist rather than asking which fields should
be in one.
