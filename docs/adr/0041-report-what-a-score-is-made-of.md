# ADR-0041: Report what a score is made of, and name the parts that were assumed

- **Status:** Accepted
- **Date:** 2026-09-05

## Context

`score_product` computes three shares -- rating, popularity, price -- blends them
by `RankingWeights` and returns one number. The three were thrown away at the
`return`, and the report and the card showed the blend:

```
#1  Sony WH-1000XM5
     score  : 0.812
```

Two things were wrong with that, and the second is the serious one.

The first is ordinary: 0.812 says where a product placed and nothing about why.
A shopper cannot tell the well-reviewed expensive one from the cheap unremarkable
one, and the weights that decided it are not reachable from either front end by
design -- `RankingWeights` is a code change (this project's own rule), so the
blend is fixed and unexplained.

The second is that **the score hides its own uncertainty**. `NEUTRAL` is 0.5, and
a product with no rating scores 0.5 on rating because ADR-0007 decided that a
listing which published nothing must not be buried beneath one that published
something bad. That is the right rule. But it means 0.5 is two completely
different claims wearing the same number: "this was rated 2.5 out of 5" and
"nobody rated this". Grounding makes the second common -- it blanks every figure
the pages did not back -- so a report's top product is routinely one that scored
mid-field three times over because the run learned nothing about it, and there is
nothing on the screen that says so.

The card had the same hole with a bar drawn over it. `price unknown` and
`unrated` appear among the figures, so the *facts* are honest; the score sitting
underneath them silently treats both blanks as an average result and then
presents the total as a measurement.

Everything needed to fix this already existed inside `score_product` and was
being discarded one line before it could be used.

## Decision

`score_product` returns `models.ScoreParts`: the three shares, the `total` they
blend to, and `neutral` -- the names of the criteria this product published
nothing for, which therefore scored `NEUTRAL` rather than being read off a page.
`RankedProduct` carries it as `breakdown`, and `score` becomes a property
returning `breakdown.total`, so there is one number and not two that agree until
somebody constructs a `RankedProduct` by hand.

The shares are **unweighted**, each in `[0, 1]`. How much a criterion counts is
`RankingWeights`, one setting for the whole run rather than a fact about this
product; multiplying it in would make three numbers that no longer mean "how well
did this score on rating".

`neutral` is the field that could not be recovered afterwards, and it is decided
where the scoring happens rather than by testing a share against 0.5 later. A
product priced exactly mid-way through the candidate set scores 0.5 on price
*having been read off a page*, and a consumer that inferred "assumed" from the
value would mark the one case this whole record exists to tell apart. The card is
tested for exactly that.

Both front ends show it. The report puts the parts on the score's own line, since
a report is read down its left edge:

```
     score  : 0.650  (rating 0.50 assumed, popularity 0.50 assumed, price 1.00)
```

The card puts them under the bar, each marked in words as well as by colour --
"assumed" is the difference between a rating of 2.5 and no rating at all, and a
reader who cannot see the dimming still needs it.

`api.product_payload` sends `breakdown` whole rather than pre-formatted: how to
draw three shares is the page's business, what they are is Python's (ADR-0012).

## Consequences

The ordering explains itself. A product that placed first on three assumptions
now says so, on the CLI and on the page, which is the honest reading of a
pipeline that blanks every figure it could not verify.

It also makes the *run* legible in a way the fetch tally could not quite manage:
a report whose every product is "assumed" three times is a run that read nothing
useful, and that now shows up next to the answer rather than in a log line above
it.

The costs are small and worth naming. `RankedProduct` gained a required field, so
every construction site had to give it one -- `tests/conftest.ranked_product` is
the helper the suite builds them through, and it derives `neutral` from the
product so a fixture cannot claim a figure it does not have. And the payload grew
a nested object, which `agent.types.ts` mirrors and
`tests/test_conventions.py` holds to `product_payload`.

Two obligations.

- **A fourth criterion is four edits, not one**: the share in `ScoreParts`, the
  `neutral` clause beside it, the line in `logging_setup._parts` and the row in
  `ProductCard.parts`. The convention test catches the TypeScript half; nothing
  catches a criterion that scores but is never shown, which would be a total the
  parts do not add up to.
- **`neutral` is decided at scoring time and nowhere else.** Any consumer that
  re-derives it from a share equalling 0.5 is wrong about a genuinely mid-scoring
  product, and it is wrong in the direction of calling a real measurement a
  guess.
