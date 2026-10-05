# ADR-0073: Hold the scorer to a product's model number and to the verdicts passed on it

- **Status:** Accepted
- **Date:** 2026-10-05

## Context

ADR-0036 wrote down, product by product, the figures the pages print, so the scorer
could see the mistake grounding cannot: a price true of the corpus and false of the
product it was reported for. Names and quotes got no such key. Both were judged by
rules no stricter than the pipeline's own, so the benchmark was blind to exactly the
mistakes the pipeline is blind to.

- **Names.** `identifies` held a reported name to `mentions_name`'s bar of 0.6, in both
  directions. One model number in three words is under that bar, so "Sony WH-1000XM4"
  matched the XM5's entry. Of 21 near neighbours tried against the three keys -- the
  previous generation, the next size, a sibling in the same line -- 20 matched an entry,
  and the perfect answer with "WH-1000XM4" in place of the XM5 scored 1.000. Grounding
  keeps that name for the same reason, so nothing else in the project can notice it.
- **Quotes.** A quote counted when some page about the product printed it, and review
  pages name four to eight products each. The Sony's verdict reported for the Bose,
  which AudioSite also names, scored 1.000. So did a price line passed off as a
  verdict, and two verdicts run together across a line break. The extraction prompt
  forbids the first -- "never give a product an opinion the results gave to a different
  one" -- and nothing measured whether a model obeyed. The reference answer broke the
  rule itself: `PERFECT` gave the AirPods Max AudioDeal's "Reviewers found the fit
  comfortable over a full working day.", a verdict on the Sony that page is about.

## Decision

The key says which product a page's verdict is about, and the scorer tells two models
apart by their numbers.

- **Two names that each carry a model number the other lacks are two products**,
  whatever words they share. A model number is any word with a digit in it
  (`scoring.model_numbers`): "1000xm5", "g14", "3200", the "5" of "Slim 5". A number on
  one side only is a spec, a year or a shortening -- "Slim 5 16GB", "De'Longhi Dedica"
  -- and is matched by words as before. This is a rule grounding does not have. The
  scorer still reads names with `NAME_TOKENS` and `GENERIC_WORDS` (ADR-0036); it is
  stricter only about what tells one product from the next.
- **Each entry lists the verdicts its pages pass on it** (`Expected.verdicts`): whole
  lines, as the condensed pages show them, a line judging two products listed under
  both. A quote is faithful when it is one of them, or a run of words out of one,
  printed on a page about the product that the run was shown.
- **`quotes` counts over the products some page judges.** A product priced and never
  judged is not asked for a quote it could not give.
- **Each case lists the judgements its pages pass on no product** (`Case.about_nobody`):
  a sale, a headline, a cabin crew's advice.
- **The verdicts are part of a case's fingerprint**, so a run kept against the old key
  is left off the board (ADR-0070).

## Consequences

The four answers above no longer score 1.000. The XM4 counts as a product nobody wrote
about, and the borrowed verdict, the price line and the stitched quote count as quotes
nobody wrote. `PERFECT` loses its AirPods quote and still scores exactly 1.000.

What it obliges:

- **A key is now a transcription of judgements as well as figures.** Editing a case's
  pages means editing its verdicts. `tests/test_benchmark_cases.py` holds them from
  three sides: every verdict is a line of a page about its product; every line the
  pipeline's own opinion sweep would take -- `fetch.reads_like_an_opinion`, at its
  `_MIN_OPINION` floor, snippets included -- is somebody's verdict or listed as about
  nobody; and every line about nobody is on the pages and nobody's verdict.
- **The opinion vocabulary now reaches into the keys.** A word added to `fetch._OPINION`
  can fail that test until a case's key says whose line it newly catches. That is the
  test working: the line was always a verdict a model could quote.
- **Names alone cannot tell a sibling from a shortening.** "AirPods Pro" still matches
  the AirPods Max, "Sage Bambino" the Bambino Plus, and "QuietComfort 45" the Ultra,
  since only one side carries a number. A case that needs such a pair told apart must
  name both products in its key, where `best_match` picks the closer one.
- **Grounding still lets "WH-1000XM4" through.** The README lists it as a limitation,
  as it lists the borrowed quote; the benchmark is where both are counted.
