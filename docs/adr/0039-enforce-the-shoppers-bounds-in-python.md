# ADR-0039: Let the shopper set bounds, and enforce them after grounding

- **Status:** Accepted
- **Date:** 2026-09-05

## Context

The example request this project has carried since the first commit is "wireless
headphones under $200". Nothing ever enforced the $200.

The budget went into the query. `extraction.build_query_chain` asks the model to
"keep the shopper's constraints (budget, brand, size, use case)" when it rewrites
the request into a search query, and that is as far as a query can take them: a
page is returned for matching the words, not for obeying them. DuckDuckGo
answered with ten pages about headphones, several of them dearer than the budget,
and every stage after that treated them as candidates like any other.

Ranking then made it worse rather than better. `score_product` scores price
*relative to the candidate set* -- cheapest in the set 1.0, dearest 0.0 -- which
is the right rule for "which of these is good value" and the wrong one for "which
of these can I buy". A run that came back with nine pairs between $280 and $400
put the $280 one top with a price score of 1.0, and the report said, in the
project's own format:

```
#1  Sony WH-1000XM5
     score  : 0.812
     price  : 328.00 USD
```

That is a confident answer to a question nobody asked. The shopper said $200.

Three smaller versions of the same gap sat beside it. A 5.0 averaged over two
reviews outranks a 4.6 over nine thousand on the rating criterion, and while
`popularity` discounts it, nothing refuses it. A run cannot be told to ignore
badly-reviewed products at all. And the report's own count -- "TOP 3 OF 10
PRODUCTS" -- was the only thing a shopper could use to tell "the web had little to
say" from "most of what came back was irrelevant", which it cannot.

The obvious alternative was to read the bounds out of the request with the model:
a third chain, asked for `{max_price, min_rating}` alongside the query
refinement. It was rejected. It puts the model in the position of deciding which
products the shopper never sees, on a project whose central rule is that model
output is never trusted as judgement (ADR-0002) -- and the failure is silent and
expensive: a model that reads "under $200" out of "headphones with 200 hours of
battery" drops every product in the run and the report says only that nothing was
found.

## Decision

Three bounds are settings, said as numbers, applied in Python:
`AgentConfig.max_price`, `min_rating` and `min_reviews`, each defaulting to
`None`, which is no bound. They are ordinary options -- a row in `config.LIMITS`,
a flag (`--max-price`, `--min-rating`, `--min-reviews`), a key
`api.parse_options` reads, a field in `defaults_payload` and a box in the form --
so they go through the machinery every other number goes through and are refused
at both doors by the same range.

`buy_agent.constraints` is the module that applies them. `Constraints` holds the
three, `from_config` builds it, and `apply` answers the products that are inside
every bound that was set.

**A product whose figure is unknown passes.** Each clause is "known *and*
outside", never "not inside". `verification.ground` blanks every figure the
source pages did not back, so a blank here is as often the extractor having
missed something as the page never having printed it -- and dropping blanks would
reject real products for a model's bad afternoon. This is ADR-0007's reasoning,
one stage later: missing data scores neutral rather than zero, and here it
survives rather than being dropped.

**The bounds are applied after `deduplicate` and before `rank_products`.** After
the merging, because `extraction._fill_gaps` fills one listing's blank price from
another listing of the same product, so a product judged before the merge would
be judged on a blank and kept for the wrong reason. Before the ranking, because
the price criterion is relative to the candidate set, and the set that matters is
the one being reported: scored against products the shopper cannot buy, "the
cheapest of these" names an option that is not on offer.

**Whenever a bound was set, the run says what it did**, at INFO where anything
survived and WARNING where nothing did:

```
1 of 10 product(s) are within the limits (at most 200.00, rated at least 4.5)
```

Set no bounds and there is no line, because there is nothing to say. Set bounds
that dropped nothing and the line still goes out: "10 of 10" is the answer that
says the bound did nothing, and it is not the same answer as silence.

A price is compared in whatever currency the page printed, and nothing is
converted. A rate table is a live data source with a refresh policy and a wrong
answer when it is stale, which is more than this project has any business
shipping; the honest version of that feature is a currency-aware ranking, and it
is not this record's.

## Consequences

The report answers the question that was asked. A shopper who says $200 and means
it gets a run that says so, including when the answer is that the web offered
nothing under $200 -- which is a useful answer, and one no previous version of
this code could give.

The bounds have to be **said**, not implied. "under $200" in the request text
still does nothing but shape the query; the number goes in the box. That is the
cost of refusing the model-read version above, and it is deliberate: a bound
nobody typed is a bound nobody can be surprised by.

Three obligations follow.

- **A new bound is a new field, a new `LIMITS` row and a clause in `admits`, and
  it must admit the unknown.** A bound that drops products with a blank figure
  would quietly re-introduce exactly what ADR-0007 refused, and nothing in the
  suite would notice: the products it drops are the ones the model failed on.
- **The stage order is load-bearing in both directions** and is asserted by
  `tests/test_agent.py`: merged before judged, judged before scored. A bound
  applied after `rank_products` would leave the ranks numbered over products the
  report does not contain.
- **The count line is part of the feature, not narration.** A run that reports
  two products because eight were over budget is indistinguishable from a run
  that found two, and only this line separates them. A future change that makes
  the filtering quieter takes the feature's honesty with it.

An empty answer is still not a failure: a run where nothing is inside the bounds
returns `[]`, which the CLI already reports as `NOTHING_FOUND` (3) and the API as
a run with no products. The three failure modes of ADR-0009 are untouched.
