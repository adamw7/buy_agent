# ADR-0058: Keep every listing a product was priced at, and pay the one being bought

- **Status:** Accepted
- **Date:** 2026-09-19

## Context

The agent reads up to ten pages per run and several of them price the same product.
`extraction._combine` picked a winner by `_completeness` and `_fill_gaps` filled only
the winner's *blanks*, so two pages quoting 129 and 149 left one figure in the report
and no sign anywhere that the other had existed. The shopper saw one price and could
not tell it from the only one there was.

That is the most shopping-shaped thing the pipeline was throwing away, and it was
weakening the payment too. `payment.merchant_for` fell back to the host of whatever page
the product ended up linked to, because nothing in the run remembered *which listing*
the surviving price came off -- which is the second half of the README's "Paying is only
as good as the page it read". Worse, `seller` is one of `_MERGEABLE_FIELDS`, so a winner
that printed 129 and named no shop took the loser's shop name: the report could name the
seller of the 149 beside the price of the 129, and the cart would have been built on it.

The project already has the answer written down twice. ADR-0022 says a figure and its
qualifier travel together, because a merge taking each field on its own reports a
pairing no page printed. ADR-0042 says a quote carries the page that printed it, and
`_merge_opinions` keeps *both* listings' quotes, because two reviewers are no conflict.
Two shops are no conflict either.

## Decision

**A `Product` carries the offers the sources printed for it**, each one a price, the
currency it was written in, the shop quoting it and the page it was on. `models.Offer`
holds the four; a merge keeps both listings' offers whole, the way it keeps both
listings' quotes, and `_MERGEABLE_FIELDS` deliberately does not gain a row.

**They are seeded in `deduplicate` and nowhere earlier.** That is the last point at
which one `Product` is still one listing, and it is after `ground`, so no offer can
carry a price the sources do not back -- the same argument ADR-0006 makes about every
other figure, applied once per listing rather than once per product.

**The headline price does not move.** `Product.price` is still what `_combine` chose;
ranking, `Constraints` and `dominant_currency` read that one figure and nothing else.
The offers are a record, not a second opinion: giving the ranking two answers to "what
is this priced at" would leave ADR-0043 with nothing to settle.

**The cart names the offer being bought.** `payment.offer_for` finds the listing whose
price *and* currency are the headline's -- the pair, per ADR-0022 -- and the merchant and
the page the cart carries come off that listing. Where there is no such offer, which is a
product reaching `cart_for` off a re-sort's payload, the product's own fields answer as
they always did.

**The spread is Python's sentence.** `Product.offers_label` writes "3 listings,
129.00-149.00 USD", and it is measured over the offers priced the way the headline is,
since two prices in two currencies have nothing between them and nothing is converted
(ADR-0043). One outside that scale is counted and not measured. The report prints the
sentence under the price and the card puts the listings under it; neither formats an
amount of its own (ADR-0012).

## Consequences

The card gains something a shopper actually wants -- what else this was going for, and
where -- out of pages the run had already read and was already throwing away. The
merchant on the approval prompt is now the one that quoted the price being approved,
which is the half of the payment limitation that was fixable without a better model.

What it does **not** fix, and deliberately: `Product.seller` is still one of
`_MERGEABLE_FIELDS`, so a merged product can still *display* the shop name that
came off the other listing. The offers are what make that visible rather than
invisible -- the spread is right there under the price, and the confirmation and
the receipt read `pay_merchant`, which is the offer's. Moving `seller` into
`QUALIFIERS` beside `currency` was the obvious next step and is wrong: a
qualifier describes a figure and dies with it (ADR-0022), and a shop named with
no price beside it still says where the thing is sold. Making the *displayed*
seller the headline offer's is a separate change to what a merged `Product` is,
and this one is about what a merge stopped throwing away.

The obligations.

- **The offers must never become a second way to rank.** `rank_products`,
  `Constraints` and `dominant_currency` read `Product.price` and `Product.currency`.
  A criterion reading the offers would make "what is this priced at" a question with
  two answers, and ADR-0043 settles exactly one.
- **`_MERGEABLE_FIELDS` gains no row for them.** It is the table of fields a *weaker*
  listing can fill a gap with. `offers` and `opinions` are the two fields where the
  listings do not conflict, so both are merged whole and both have a function of their
  own; a row here would fill the winner's empty list from the loser's and lose the
  winner's own listing.
- **An offer is grounded because the listing it came from was.** Seeding them anywhere
  before `ground` would put a price the sources do not back on a card, under a heading
  that says the opposite.
- **Both ends of the language boundary move together.** `Offer` is mirrored in
  `agent.types.ts` and checked by `tests/test_conventions.py`, as `Opinion` is.
