# ADR-0056: Let the shopper name the currency a run is counted in

- **Status:** Accepted
- **Date:** 2026-09-18

## Context

ADR-0043 settled that a run's prices are compared on one scale and that nothing
is converted. Which scale that is, it decided by vote: `models.dominant_currency`
takes the commonest currency among the products carrying both a price and a
currency, ties going to whichever was seen first.

The vote is the right default and it is nobody's decision. Which currency wins is
an accident of what the search returned. A shopper in Poland searching
`--region pl-pl` can get a set counted in USD because three American review sites
out-numbered the two Polish shops -- and then every złoty price in the report is
a figure the run cannot place: it scores `NEUTRAL`, it sinks in a price sort, it
passes every bound, and it cannot be paid for. The README says as much already:
*a mixed-currency search scores most of itself on nothing.* What it could not say
is which half of the set that would be.

Converting is still not on the table, for the reason ADR-0043 gave: a rate needs
a source, a date and a refresh policy, and a stale rate is a wrong ranking wearing
a right one's clothes. This record adds no rate. It only lets the shopper say
which of the currencies actually in front of them they want to be counted in.

## Decision

**`currency` is a run setting, and naming one settles the scale outright.**
`dominant_currency(products, named)` answers `named` where there is one and votes
where there is not. It is a choice and not a report, so it is not weighed against
the vote -- a set the shopper's currency cannot place is a set counted in their
currency with nothing on the scale, which is exactly the thing they asked to be
told.

**Nothing downstream changes.** `comparable_price` still answers `None` for a
price outside the scale, and that `None` still takes the whole of ADR-0007's
treatment: `NEUTRAL` in the score, `price` in `ScoreParts.neutral`, last in a
price sort, admitted by every bound (ADR-0039), and refused by `payment._check`
(ADR-0006). Every place that held a price against another price already went
through one function, and it still does.

**It is a code, not a number, so it is checked like `region` rather than bounded
like a number.** `money.placeable` is the narrower question `code_for` does not
answer: that one reads whatever a page wrote and hands an unknown spelling back
as written, which is a price nothing can place; this one answers only a code out
of the table, because a *scale* nothing can place is a report with no price
criterion at all. `config.parse_currency` is where both doors go through it, and
the refusal names the codes that would have worked. A spelling is folded the way
a page's is -- `$`, `usd` and `USD` are one answer -- since ADR-0054 already holds
that in one table and a shopper types what their pages print.

**Blank is the default and means the vote.** An empty flag, an empty form field
and a missing key are the same answer (ADR-0012), which is what the whole of this
project already spells that way.

**A scale nothing is priced in is said out loud.** `BuyAgent.run` warns, before
the ranking, when a named currency places nothing in the set. It is the one way
to ask for a report whose price criterion is entirely assumed, and a run that
quietly ordered by rating and reviews alone would look like a run that had ranked.

**The scale travels with the run.** A re-sort and a payment are both about a run
that already happened (ADR-0035), and both are handed the products by the browser
-- so both are handed the currency too. Letting the set vote again there would
answer a different ordering, and a different cart, for one run.

## Consequences

A shopper who knows what they are shopping in gets a report scored on it, and one
who does not gets exactly the run they got before this record.

The obligations.

- **Still nothing converts.** This adds a *choice* of scale and no arithmetic
  between scales. The answer to "these are not comparable" stays "then do not
  compare them", and a rate table would contradict ADR-0043 rather than extend
  this one.
- **A named currency can empty the price criterion, and must say so.** The
  warning is the load-bearing half of this decision. A future front end that
  swallows it would be offering a setting whose worst outcome is invisible.
- **Every place the scale is settled takes the override.** Four do:
  `rank_products`, `Constraints`, `payment.cart_for` and `api.results_payload`.
  A fifth would have to, or one surface would report a run on a scale the others
  did not use -- a card offering a Buy button the payment would refuse is what
  that looks like.
- **`money.placeable` and `money.code_for` are two questions, deliberately.**
  Folding them back into one would either let a shopper name a scale nothing can
  be placed on, or refuse a page the currency it actually printed.
