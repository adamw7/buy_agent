# ADR-0099: Hold a name's model numbers to the page, beside the coverage bar

- **Status:** Accepted
- **Date:** 2026-11-25

## Context

`verification.mentions_name` decides whether a product is real, which page it links
to (ADR-0017), which pages may be quoted for it (ADR-0025) and which line a quote is
said of (ADR-0090). It asked that 0.6 of a name's distinctive words appear on the
pages (ADR-0008). A product name is usually three such words -- a brand, a series and
a model number -- so two were enough, and the one left free to be missing was the one
that tells a model from its neighbour:

- "Sony WH-1000XM6" passed on a page about the XM5, and "Apple AirPods Pro 3" on one
  about the Pro 2.
- Grounded whole, the invented XM6 kept the XM5's price, rating and review count
  (figures are checked against the pooled pages, ADR-0006), its page as its link, and
  its quote: `said_of` read the line "Sony WH-1000XM5" as naming the XM6 too. Every
  check `payment._check` asks then passed, so a cart could be signed for a model no
  page printed, at another model's price and page.
- The benchmark knew: ADR-0073 held its scorer to the model number, and
  `test_a_run_reporting_the_wrong_generation_has_invented_a_product` wrote down that
  "grounding lets the XM4 through, and the scorer is what says it is not". Nothing
  the shopper sees runs the scorer.

The scorer's rule could not be borrowed as it stands. It compares two names, and
forgives a number on one side only as a spec or a shortening ("Lenovo Legion Slim 5
16GB" for "Lenovo Legion Slim 5"). Grounding has one name and the pages; a number the
pages do not print is a claim nothing backs, whichever kind it is.

## Decision

**Every word of a name with a digit in it must be on the pages, as well as 0.6 of
its distinctive words.** `mentions_name` asks both, so realness, links, quotes and
`said_of` tighten together.

**A number may be printed as two words.** "16 GB" and "Q 30" back "16GB" and "Q30":
a word the name carries is also found as two adjacent words of the page run together,
for the numbered words only and only when one is otherwise missing.

**The scorer keeps its own rule.** `benchmark/scoring.py` does not import the
pipeline's (ADR-0073): a judgement shared between what is scored and what scores it
is a blind spot.

## Consequences

The scripted runs score as they did, `PERFECT` 1.000 on every case and `SLOPPY`
0.726, 0.574 and 0.540 on headphones, laptops and espresso, before and after: every
name in the answer key and the sloppy answers prints its numbers.

What it costs: a model that adds a number no page prints -- a year, a capacity, a
graphics card -- loses the product rather than its name. That is the safer way round
for a run that may pay, and the drop is recorded like any other ("No page that was
searched mentions it."), so it shows in `dropped`.

The obligations:

- **Anything that decides whether a page names a product goes through
  `mentions_name`**, so the model-number rule cannot be skipped by a reader that
  counts coverage itself; `word_coverage` alone is the bar, not the check.
- **A new tokenisation in `NAME_TOKENS` reaches both halves**: a word it splits
  differently is a number the pages no longer print.
- `tests/test_verification.py` holds the sibling, the two-word spelling and the
  invented generation through `ground`; `tests/test_benchmark.py` holds that the
  wrong generation no longer reaches a report.
