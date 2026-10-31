# ADR-0097: Rank what a limit could not check after what it could, and say why

- **Status:** Accepted
- **Date:** 2026-10-31

## Context

ADR-0039 keeps a product whose figure the run never learned: a blank is more often a
page that printed none than a product that costs too much, and dropping it would
punish the extractor's misses. That stands. What it left open is where such a
product goes, and testing the request bounds of ADR-0094 end to end showed it.

- **"Under $200" was topped by a product nobody priced.** Over the benchmark's
  headphones pages, with the AirPods Max's price unread, the budget removed the Sony
  and the Bose, kept the AirPods for want of a figure, and ranked it first, above the
  Sennheiser at $179 that the budget had actually checked. Its real price is $479.
  "Under $50" reported it alone, rather than nothing.
- **A place the score did not decide was not explained.** ADR-0095 ranks an
  out-of-stock listing last and keeps its score, so an out-of-stock pair scoring 94%
  sat third under a 66% first, with only an "Out of stock" pill to say why.

The shopper who sets a limit has said what they want checked. A product the limit
could check and passed is an answer to that; one it could not check is a maybe.

## Decision

**`Constraints.apply` marks each product it keeps with the limits that had no figure
to check it by** -- `Product.unchecked`, a list of `models.Limit` setting names. A
price outside the run's currency is unchecked by the budget, as a blank is
(ADR-0043). With no limit set, nothing is marked.

**Ranking puts the unchecked after the checked, in every order**, as one more key of
the stable sort that already sinks out-of-stock listings (ADR-0095): checked, then
unchecked, then out of stock, each group in the order asked for. The score is
untouched, as ADR-0095's is.

**The mark travels with the product**, in the payload and back in a re-sort, so
`rank_again`, which never sees the limits, gives the same order.

**Each place the score did not decide is said in one sentence**,
`ranking.placement_note`, which both doors show beside the score: the card's
`placement_note` and the report's `placed` line. "Ranked last: a page says it is out
of stock." or "Ranked after the products this run's limits could check: its price is
unknown or in another currency." Out of stock takes precedence.

## Consequences

Under a budget, the first answers are products known to be within it; a product
nobody priced is still shown, after them, saying why. The help and the form's hints
say "kept, after the rest".

The obligations.

- **`models.Limit` names every bound `Constraints` applies**, `ranking._UNCHECKED`
  has a phrase for each (`tests/test_ranking.py`), and the TypeScript `LimitName`
  union spells them the same (`tests/test_conventions.py`).
- **Only `Constraints.apply` sets `unchecked`.** A product that enters ranking marked
  by anything else would be sunk for a reason no limit gave.
- **The note is Python's.** The browser shows `placement_note` and decides nothing
  (ADR-0012); a new reason a product sits below a higher score gets its sentence in
  `placement_note`, not in a template.
- **A bound still never removes what it cannot judge** (ADR-0039). Sinking is the
  most this rule may do with a blank.
