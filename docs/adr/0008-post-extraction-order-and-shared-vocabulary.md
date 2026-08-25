# ADR-0008: Clean, then ground, then deduplicate -- over one shared word list

- **Status:** Accepted
- **Date:** 2026-08-25

## Context

Four things happen to the extracted candidates before they are ranked: names are
cleaned, headline-like entries are filtered, figures are grounded (ADR-0006), and
duplicate listings for one product are merged. Each is independently sensible,
and two orderings of them are quietly wrong.

Grounding before cleaning fails good products. A name arrives still wearing its
publisher suffix -- "Sony WH-1000XM5 Review | AudioSite" -- and the coverage
check then demands that "review", "audiosite" appear in the page text, which
they never had to. The product is dropped for words that were never part of its
name.

Deduplicating before grounding merges figures nothing backs. `_combine` fills a
listing's blanks from the other listing for the same product; run it first and an
invented price is copied onto a product whose own page never quoted one, where
grounding can no longer tell it apart from a real one.

Separately, two steps need to agree on what a name's *words* are.
`merge_variants` folds "Sony WH-CH720N" and "Sony WH-CH720N Noise Canceling
Wireless Headphones" together by checking that the difference between their
tokens is all generic; `mentions_name` decides coverage by ignoring those same
generic words. If the two lists drifted apart, names would merge that grounding
judged differently.

## Decision

The order in `BuyAgent.run()` is fixed and load-bearing:

```
extract -> clean_products -> ground -> deduplicate -> rank
```

`verification.py` imports `GENERIC_WORDS` and `NAME_TOKENS` from `extraction.py`
rather than keeping its own copies, so merging and grounding always share one
definition of a name's distinctive words.

**Only words that identify nothing belong in `GENERIC_WORDS`** -- "wireless",
"black", "headphones". Adding a word pulls in two directions at once: it makes
`merge_variants` fold *more* names into one product, and it makes `mentions_name`
*stricter*, because ignored words leave fewer distinctive tokens to clear the 0.6
bar. A brand or a model number in that set would let an invented product pass
grounding.

## Consequences

The two joints are invisible in the code -- four calls in a row, each of which
would still run in another order -- so they are pinned by tests and stated in
`CLAUDE.md`, `docs/architecture.md` and here. A new post-extraction step has to
be placed against the same two questions: does it need a cleaned name, and does
it combine figures from different sources?

Editing `GENERIC_WORDS` is a change to two behaviours in opposite directions, and
should be made with both in view.
