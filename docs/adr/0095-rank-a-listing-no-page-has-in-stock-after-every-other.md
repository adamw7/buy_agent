# ADR-0095: Rank a listing no page has in stock after every other

- **Status:** Accepted
- **Date:** 2026-10-31

## Context

ADR-0079 grounded a listing's stock and condition and left ranking alone, saying
that sinking out-of-stock products "would be a change to what the report holds and
needs its own record". This is that record.

What was observed: an $89 pair of headphones, refurbished and marked out of stock by
its page, ranked first with 90%, ahead of an in-stock pair rated 4.7 over 3,200
reviews, which fell outside the top three into "more the agent found". The report's
first answer was the one product nobody could buy, and the one `payment._check`
refuses to pay for -- so `--pay`, which buys the top-ranked product, would fail on
it rather than buy the best one that is for sale.

The card already said "Out of stock", in red. A label beside the first place does
not undo the first place: the shopper reads the order before the pills.

## Decision

**After the chosen order, a listing whose `availability` is "out of stock" moves
below every other, keeping its order within its half.** `rank_products` does it as a
second, stable sort on that one test, so it holds for every `SortBy` -- best score,
cheapest and best rated alike -- and for a re-sort (`api.rank_again`), which ranks
again through the same function.

Only "out of stock" counts. An unknown standing is most pages and is not evidence of
anything (ADR-0079); "in stock" and an unknown rank together. Condition is not read:
refurbished and used are what some shoppers want, and the card says which.

The score is untouched. `ScoreParts` still says what the listing would score, so the
card shows why it was a contender; only its place changes, and its rank number is the
place.

## Consequences

The first answer is always one somebody can buy, while there is one. A run where
everything is out of stock ranks exactly as before.

The obligations.

- **Stock is read in one place in ranking**, `ranking._GONE`, spelled as
  `models.Availability` spells it; a new standing that means "cannot be bought now"
  joins it there, and nowhere else decides order on stock.
- **Every order sinks it.** A new `SortBy` is sorted first and sunk after, by the same
  line, not given its own rule.
- **The score does not move.** A future change that folds stock into `score_product`
  would make the breakdown lie about what the listing would score, and a bound on
  score would start removing what this only reorders.
- `tests/test_ranking.py` holds each order to it, and holds a set with nothing in
  stock to its old order.
