# ADR-0096: Score an unknown price as the dearest one

- **Status:** Accepted
- **Date:** 2026-10-31

## Context

ADR-0007 put ranking in Python and scored every missing figure at `NEUTRAL`, 0.5,
rather than zero: grounding blanks what the sources did not back, and a blank says
more about the extractor or the page than about the product. That holds for a rating
and a review count, and still does.

It does not hold the same way for a price, for two reasons that showed up in one run.

- **0.5 means something different on each scale.** A rating's 0.5 is 2.5 stars,
  below nearly every rated product, so an unrated product sinks in practice. A
  price's share is relative to the set -- cheapest 1.0, dearest 0.0 -- so its 0.5 is
  the middle of the set: a typical price. An unknown price scored as a typical one.
- **So an unpriced product beat priced ones.** A pair rated 4.6 with no price
  ranked third, above a pair rated 4.7 at $328 that was simply the dearest in the set
  and scored 0.0. The "assumed" label was on the card; the order still said the
  product nobody priced was the better buy.

And a product with no price is worse for the shopper on exactly this criterion: it
cannot be held to a budget (ADR-0039 keeps it), and it cannot be paid for
(`payment._check` refuses it).

## Decision

**`ranking.py` contains no LLM call, and the scoring ADR-0007 set out stands** --
rating 0.5 (`rating / 5`), popularity 0.2 (`log10(reviews + 1) / 3`, saturating at
1,000), price 0.3 relative to the candidate set, configurable through
`RankingWeights` -- with one change.

- **An unknown rating or review count scores `NEUTRAL`, 0.5**, as before.
- **An unknown price scores what the dearest placed price does**
  (`ranking._unknown_price_share`): 0.0 when the set has two or more distinct prices
  on its scale, and `NEUTRAL` when it has fewer, where every price ties anyway. A
  price in another currency than the run's is unknown on this scale (ADR-0043) and
  scores the same.
- **The criterion is still named in `ScoreParts.neutral`**, so the card and the
  report still mark the price "assumed".
- `sort_by="price"` and `sort_by="rating"` still sink a product missing the field,
  and the bounds still keep it (ADR-0039).

## Consequences

An unpriced product can still rank first -- on rating and reviews, when they are good
enough to beat every priced product with the price criterion at the dearest's share
-- but never because its price was guessed to be typical.

The obligations.

- **A missing figure never scores better than the worst known one on a relative
  scale.** A new criterion scored relative to the set takes its unknown from the set's
  worst, as price does; one on an absolute scale may take `NEUTRAL`.
- **`neutral` names every criterion scored without a figure**, whatever it scored, so
  "assumed" stays true on the card.
- **The benchmark's ideal ordering ranks through `rank_products`**
  (`benchmark/scoring.py`), so it moves with this. `PERFECT` still scores 1.000 on
  every case. The two `SLOPPY` runs that price a product in a currency the run cannot
  place moved, in opposite directions, which is the trade this record makes:
  headphones 0.675 -> 0.726 (its euro-priced Bose is the dearest, and no longer passes
  the Sony), laptops 0.600 -> 0.574 (its Canadian-priced Acer is the cheapest, and now
  falls below the ASUS); espresso stays 0.540. An unknown price is equally likely to
  be low or high; what this buys is that it is never the reason a product wins. The
  pins in `tests/test_benchmark.py` and `tests/test_benchmark_cases.py` say so beside
  each count.
