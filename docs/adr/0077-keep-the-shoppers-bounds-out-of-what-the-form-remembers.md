# ADR-0077: Keep the shopper's bounds out of what the form remembers

- **Status:** Accepted
- **Date:** 2026-10-06

## Context

The form remembers its settings in `localStorage` and restores them on the next visit,
and the request is not among them: what to shop for is a new question every time.
Until now the three bounds were remembered with the rest -- Max price, Min rating and
Min reviews (ADR-0039) -- on the reasoning, written into the form's spec, that a budget
is a standing answer, shopped under for weeks rather than retyped per search.

What that bought was observed in a browser. 650 and 4.4, set for a light laptop, came
back on the next visit and filtered a search for running shoes to one pair. Nothing on
the page said a limit was in force: the Settings panel opens shut, its summary counts
only the boxes it marks as wrong, and a bound is never marked. The run did say so, as
"1 of 7 product(s) are within the limits" in the progress panel and as "Outside the
limits you set" under the results, which is the reader finding out after the run what
they could not see before it. A bound read off the request was already kept out of
storage for the same reason -- "a figure read off this request is part of the
question, not a standing answer" -- and a typed one is no less part of it.

The standing answer is not lost by forgetting it. The request is retyped on every
visit anyway, and "running shoes under $120" typed into it fills Max price again,
under a note saying where the figure came from (ADR-0059).

## Decision

**A bound belongs to the request it was set for, and is not remembered.** Each number
box in the form's `numberFields` declares `remembered`; the three bounds declare it
`false`, and `numberSettings` carries it onto their rows of `settings`. `remember()`
writes no row that is not remembered, and `restore()` reads none -- not even one an
older build stored, since that is the filter this exists to stop restoring.

The bounds are still seeded from the server's defaults, refused on Python's ranges
(ADR-0033) and filled from the request (ADR-0059); only the storage changes. `pay` is
still the one other setting the form forgets, for its own reason (ADR-0046). The spend
limit, though it shares `max_price`'s range, is remembered: it is a ceiling on paying
at all, not a judgement about what one search should find.

## Consequences

A shopper who did shop under one budget for weeks types it again, or writes it into
the request. That is the cost, and it is paid where the figure is visible, rather than
by a search for something else filtered by a number nobody can see.

The obligations.

- **A new bound declares `remembered: false`.** It is one more row in `numberFields`
  (the `add-option` skill), and the form's spec holds the three to it twice: no
  bound in the blob a run writes, and none restored from a blob an older build wrote.
  A bound left remembered brings this back silently -- a closed panel never shows it.
- **The stored keys stay the promise they were.** A remembered setting is still
  written under the name a browser already holds it by, and the spec's list of those
  names no longer includes `maxPrice`, `minRating` or `minReviews`.
- **Remembering a bound again needs a way to see it.** Whatever brings one back must
  also say on the page, before a run, that a limit is in force -- which the shut
  panel does not.
