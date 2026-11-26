# ADR-0100: Read a model number run together either way, beside the coverage bar

- **Status:** Accepted
- **Date:** 2026-11-26

## Context

ADR-0099 asked that every word of a name with a digit in it be on the pages, beside
the 0.6 coverage bar, so "Sony WH-1000XM6" no longer passed on a page about the XM5.
It allowed for a number being spelt across two words in one direction only: the
page's "16 GB" backed the name's "16GB", but the name's "16 GB" was never backed by
the page's "16GB".

The model writes names however it likes, and shops print capacities and series run
together, so the missing direction is the common one:

- "Apple iPhone 15 Pro 256 GB" on a page printing "Apple iPhone 15 Pro 256GB" lost
  "256", a word no page prints on its own, and the product was dropped as "No page
  that was searched mentions it." Four of its six words were there, so before
  ADR-0099 the bar alone had kept it.
- "Samsung Galaxy S 24 Ultra" on a page printing "Galaxy S24 Ultra" was dropped the
  same way.

That is a regression ADR-0099 brought in, not a cost it chose: its Consequences name
the price of a number no page prints, and these are numbers every page prints.

## Decision

**Every word of a name with a digit in it must be on the pages, as well as 0.6 of
its distinctive words.** This stands as ADR-0099 decided it, with one more way of
being on the pages.

**A number may be run together either way.** A word the name carries is also found
as two adjacent words of the page run together ("16 GB" backs "16GB"), and a numbered
word the name carries is also found where it and the name's word beside it are one
word of the page ("256GB" backs the "256" of "256 GB"). Both are asked only for a
numbered word otherwise missing, and both read whole words: "S25" is not backed by
"S24", and "512 GB" not by "256GB".

**The scorer keeps its own rule** (ADR-0073), as ADR-0099 decided.

## Consequences

Names whose model number the model split where the page did not are grounded again,
with their figures, links and quotes checked as any other's. The scripted runs score
as they did, `PERFECT` 1.000 on every case and `SLOPPY` 0.726, 0.574 and 0.540 on
headphones, laptops and espresso, before and after: no name in the answer key or the
sloppy answers is split this way.

What it costs: a numbered word now passes where the page prints it fused to its
neighbour in the name, so "Galaxy S 24" grounds on "S24" and on nothing that merely
contains "24". A page printing "S24" names the S24, so nothing a check relied on is
given up.

The obligations, ADR-0099's still among them:

- **Anything that decides whether a page names a product goes through
  `mentions_name`**, so neither the model-number rule nor its two spellings can be
  skipped by a reader that counts coverage itself.
- **A new tokenisation in `NAME_TOKENS` reaches both directions**: the name's words
  and the page's are split by the same pattern, and a pair joined on one side is
  looked up whole on the other.
- `tests/test_verification.py` holds both directions
  (`test_a_model_number_may_be_printed_as_two_words`,
  `test_a_model_number_named_as_two_words_may_be_printed_as_one`), and that neither
  lets in a neighbouring generation.
