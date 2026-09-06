# ADR-0045: Report the weights a score was blended by

- **Status:** Accepted
- **Date:** 2026-09-06

## Context

ADR-0041 stopped `score_product` throwing away the three shares behind a blend,
and both front ends draw them. What neither of them draws is how much each one
counted, and without that the parts are not readable -- they are three numbers
sitting under a fourth they do not add up to.

The card is where it goes wrong hardest, because the shares are drawn as
percentages beside a total drawn as a percentage:

```
Score  ████████░░  91%
rating      94%
popularity 100%
price       32%
```

94, 100 and 32 under a 91 reads as a sum that has gone wrong. It is not a sum at
all: each criterion is scored on its own out of 1, and the blend is
`0.5·0.94 + 0.2·1.00 + 0.3·0.32`. A reader with no weights in front of them can
neither reconstruct the total nor answer the question the breakdown exists for --
*which* criterion put this product first. A product carried by its rating and one
carried by its price are indistinguishable here, and the second is the one worth
knowing about when the price came off a page the shopper has not read.

The report has the same hole in a quieter form: `score : 0.660 (rating 0.92,
popularity 1.00, price 0.00)` invites the same arithmetic.

ADR-0041 named this and left it: "the weights that decided it are not reachable
from either front end by design". That is true of *setting* them -- `weights` is
the one field neither door fills in, and rebalancing the blend is a code change.
It was never a reason not to *say* what they are.

## Decision

A finished run reports the weights its scores were blended by. `_run_payload`
sends `weights` beside `products`, and `log_top_products` takes them and writes
each one on the score line:

```
     score  : 0.660  (rating 0.92 x0.50, popularity 1.00 x0.20, price 0.00 x0.30)
```

Three rules hold it in place.

**The weights are a fact about the run, not about the product.** They travel on
the result and not inside `ScoreParts`, which stays exactly what ADR-0041 made
it: three unweighted shares, their blend, and the names of the ones nobody
published. `ProductCard` takes them as a second input; the cards below the fold
get the same ones as the cards above it.

**They are reported as fractions of the blend.** `RankingWeights.fractions`
normalises by `total`, because 3 out of 4 and 0.75 weigh the same and only the
second can be drawn beside a share. Weights totalling nothing are all-zero
fractions, matching the 0.0 score `score_product` already answers with there.

**Python normalises; the browser draws.** The card multiplies by 100 and rounds,
which is what it already does to a share -- it works out no weight, and infers
none from a share, for the reason ADR-0041 gives about `neutral`.

The card also carries one sentence under the list -- "Each criterion is scored on
its own, then blended by weight" -- because the numbers alone still invite adding
up, and one line of prose is cheaper than a reader deciding the total is wrong.

## Consequences

A breakdown can be read. "Placed first on a price nobody published" is now
visible as `price 50% assumed` against `30% of the score`, which is the reading
ADR-0041 wanted and could only half deliver.

The costs are the ordinary ones of a payload growing. `SearchResult` gained a
required field, so `rank_again` -- which has no config to read the weights off --
names `RankingWeights()` explicitly and passes the same object to
`rank_products` and to `_run_payload`, rather than letting each fall back to its
own default. `agent.types.ts` mirrors it as `ScoreWeights`, and
`tests/test_conventions.py` holds the two together the way it holds every other
payload.

Three obligations.

- **`ranking.CRITERIA` is what pairs a weight with the share it weighs**, in the
  report and on the card alike. A fourth criterion is now five edits, not the
  four ADR-0041 named: the share in `ScoreParts`, the `neutral` clause, the
  field on `RankingWeights`, the name in `CRITERIA`, and the row in
  `ProductCard.parts`. `test_every_criterion_a_score_has_is_one_the_weights_name`
  catches a criterion the weights cannot name; nothing catches one that is never
  drawn.
- **Both entry points into the ranking report what they ranked with.** A run
  reports `config.weights`; a re-sort reports the defaults it used. A third
  would have to report its own, or answer a shape the card reads a weight of
  `undefined` out of.
- **The shares stay unweighted.** Multiplying the weight in at scoring time
  would make three numbers that add to the total and no longer mean "how well
  did this score on rating" -- which is what ADR-0041 refused, and this record
  does not reopen.
