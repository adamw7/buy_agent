# ADR-0043: Compare prices only within one currency, and convert nothing

- **Status:** Accepted
- **Date:** 2026-09-05

## Context

`ranking.score_product` scores price *relative to the candidate set*: the cheapest
product in the set gets 1.0, the most expensive 0.0, everything else in between.
That is the right shape for the question -- "is this good value among what was
found" has no absolute answer -- and it was doing arithmetic on unlike things.
Nothing anywhere read `Product.currency` before deciding which price was the
cheapest.

A search returns mixed currencies more often than it looks. The benchmark's own
corpus has a euro shop in it, listing the Sony at 329 EUR beside the dollar
listings; a shopper in a non-US region gets a whole page of them; and a single
result from a European retailer is enough. The failure is not subtle when it
happens. One price in yen makes `priciest` 12,800 and every dollar figure in the
set lands within a few percent of 1.0 -- the price criterion stops separating
anything, which is 30% of the blended score gone silently. Turned around, one
cheap euro listing takes the top of the report on a price nothing in the set is
comparable with.

The bounds had the same hole from the other direction. ADR-0039 introduced
`max_price` and said, honestly, that it is "in whatever currency the page
printed" and not converted, because a rate table is not this project's to ship.
That is the right call about conversion and it left the comparison itself
unexamined: `£180 > 200` is not false, it is meaningless, and the run was
answering it either way.

Converting remains off the table. A rate needs a source, a date and a refresh
policy; a stale rate is a wrong ranking wearing a right one's clothes, and it
would be the first figure in this pipeline that no source page printed -- which
is the thing ADR-0006 exists to forbid.

## Decision

**A run's prices are compared on one scale, and that scale has a currency.**
`models.dominant_currency` answers it: the commonest currency among the products
that have both a price and a currency, ties going to the one seen first, which is
the search's own order. `None` where no priced product named a currency at all --
nothing then says these figures are in different ones, and they are compared as
they always were.

**A price the set cannot place is not a number, it is a blank.**
`models.comparable_price` answers a product's price where it is on that scale and
`None` where it is not. `None` is the same answer a price nobody published gets,
so it takes the whole of ADR-0007's existing treatment: it scores `NEUTRAL`
rather than 0 or 1, it names `price` in `ScoreParts.neutral` so both front ends
show it as assumed rather than measured (ADR-0041), and it is *kept* rather than
dropped.

**A price printed without a currency is taken as the set's own.** That is what
every price in this pipeline was until this record, it is what a search in one
region overwhelmingly returns, and refusing to place the commonest shape of price
there is would score most sets on nothing at all.

**The shopper's budget is read in that same currency.** `constraints._BOUNDS`
reads the price through `comparable_price` rather than off the product, so a
price in another currency is a figure the bound cannot judge -- and an
unjudgeable figure passes, exactly as an unknown one does, which is the rule the
whole module is built on. The line the run logs names the currency the budget was
read in ("at most 200.00 USD"), that being the half of the bound nobody typed:
the number came from the shopper, the currency from whatever the pages printed.

**Sorting by price sinks what cannot be placed**, alongside the products with no
price at all, for the reason those sink: ordering by a figure means ordering by
one that means the same thing all the way down the column.

This does not supersede ADR-0039. Nothing is converted, which is that record's
decision; what changes is the one consequence it noted in passing -- a bound and
a price from two currencies are not comparable, so they are no longer compared.

## Consequences

The blended score is arithmetic on one scale again, and the case that used to be
silently wrong is now visibly assumed: a euro listing among dollar ones shows
"price assumed" on its card and in the CLI's score parts, which is the honest
report of what the run knows.

`benchmark`'s `SLOPPY` run pays for this, correctly and immediately. It reports
the Bose at "349 EUR" -- a pairing the corpus never printed, which ADR-0022 is
about -- and that price now scores `NEUTRAL`, which is *better* than being the
priciest thing in the set, so the Bose rises above the Sony the answer key puts
ahead of it. The mistake was already marked under `figures`; it now costs
`order` too, and the scorecard is pinned to that.

Four obligations.

- **Nothing here converts.** A rate table, an exchange API or a hard-coded
  factor would each make this project report a figure no page printed. The
  answer to "these are not comparable" stays "then do not compare them".
- **A currency-blind comparison is a bug wherever it appears.** Two places read a
  price against another price today -- the ranking and the bounds -- and both go
  through `comparable_price`. A third would have to as well.
- **`NEUTRAL` now means one more thing.** It was "no rating was published" and
  "priced exactly mid-way"; it is now also "priced in a currency this set is not
  counted in". `ScoreParts.neutral` separates the measured from the assumed and
  does not separate the assumed from each other, which is a limit worth knowing
  before something is built on the distinction.
- **The dominant currency is a fact about the *set*.** It is worked out once, by
  whoever has the whole set -- `rank_products` and `Constraints.apply` -- and
  passed down. A per-product answer would depend on which products happened to
  come first.

The cost is a report that can score most of a set on nothing: a shopper whose
search returns five currencies gets a price criterion that is `NEUTRAL` for four
of them. That is the true state of what the run knows, and it says so, which is
better than the confident wrong ordering it replaces.
