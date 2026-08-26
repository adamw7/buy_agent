# ADR-0025: Check a quote against the page it came from, not against all of them

- **Status:** Accepted
- **Date:** 2026-08-26

## Context

ADR-0024 added quoted opinions and grounded them: `verify_opinions()` drops
every quote the sources do not contain as overlapping runs of five consecutive
words. It also recorded, in its consequences, what that check does *not* do:

> **Attribution is still unchecked**, as in ADR-0006: that a quote appears in
> the sources does not prove the reviewer was talking about the product it was
> filed under.

For a figure that limitation is tolerable, and ADR-0006 argues why: two products
sharing a review count is a wrong number beside a right name, and the report
already tells the shopper the figures are a shortlist and not a price quote.

A quote is not that. It is a sentence a named reviewer wrote about one thing,
and the whole reason ADR-0024 checks it so strictly is that being *nearly* right
is being wrong. Yet the check was run against `build_haystack(results)` -- ten
pages joined into one string -- which made every verdict on any of them
available to every product on all of them. So the strictest bar in the codebase
passed the failure it was least able to afford: not an invented quote, which it
catches, but a real reviewer's real sentence about a different product. A search
for headphones that also returned an electric kettle could file "we loved the
precise temperature control" under the headphones, and every word of that is
running text of a page that was really searched.

The prompt asks the model not to move a verdict between products. Small models
are exactly the reason nothing else in this pipeline takes the prompt's word for
anything (ADR-0002).

## Decision

**A quote is checked against one page, and only a page that mentions the
product.**

`verify_opinions()` takes the `SearchResult` sequence rather than the pooled
haystack, builds each page's text on its own, and keeps a quote only where some
single page both mentions the product and contains the quote as running text.

Which pages count as "about this product" is not a new rule: it is
`mentions_name`, the 0.6 coverage bar that decided the product was real
(ADR-0006) and that `attribute_sources` already picks a product's link by
(ADR-0017). A product no page mentions keeps no quotes, the same way it keeps no
link.

The run bar itself is unchanged -- five-word runs, most of which must be found,
tolerant at the ends and strict in the middle (ADR-0024). What narrows is the
haystack each run is looked for in.

## Consequences

The quotes under a product are sentences from a page that names it. That is not
the whole of attribution and does not claim to be: a review page covering eight
headphones mentions all of them, so a verdict can still move between products
*within* one page. What is closed is the larger and likelier hole, where a quote
moves between pages about unrelated things -- which is the case a search for one
category returning another produces, and the one the prompt's own example
invites.

The obligations:

- **A quote costs more than it did.** A page that mentions the product only in
  passing, below the coverage bar, can no longer vouch for a verdict printed on
  it. That is recall for precision, the direction every grounded field here
  fails in.
- **`mentions_name` now decides three things**, not two: whether a product is
  real, which page it links to, and which pages may be quoted for it. Loosening
  `GENERIC_WORDS` therefore loosens quote attribution too -- one more pull on a
  set that already pulls in two directions (ADR-0008).
- **The per-page text is built twice** in one `ground()`, once here and once in
  `attribute_sources`. That is a join and a regex per page, and keeping both
  functions callable on their own is worth more than the microseconds.
- **ADR-0024's figures half is untouched.** `verify_numbers` still checks a
  price, a rating and a review count against all the sources pooled, and
  ADR-0006's attribution limitation still stands for them. The README records it
  for figures alone now.
