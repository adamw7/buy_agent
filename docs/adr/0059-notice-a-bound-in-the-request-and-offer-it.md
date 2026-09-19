# ADR-0059: Notice a bound in the request, and offer it rather than enforcing it

- **Status:** Accepted
- **Date:** 2026-09-19

## Context

"wireless headphones under $200" is how a shopper writes the request, and until now
those three words shaped the search query and nothing else: `--max-price 200` is what
enforced them. The README lists it as a limitation -- *a bound has to be typed, not
implied* -- and the limitation is there because the obvious fix is worse than the
problem.

The obvious fix is to ask the model for the bounds. A small model asked to read a budget
out of "headphones with 200 hours of battery" answers 200, every product in the run is
then outside it, and the report says only that nothing was found. That failure is
silent, it looks exactly like an empty web, and ADR-0002 already rules it out: the model
is used for the two steps it is reliable at and ordinary Python decides everything else.

What is left once that is rejected is not nothing. Reading "under $200" out of a string
is the same kind of work `clean_products` does, and the half of the idea that makes it
safe is not the reading but what is done with the answer.

## Decision

**The request is read in Python, and what comes out of it is only ever offered.**
`buy_agent/bounds.py` scans for the shapes a person writes -- `under $200`,
`less than 200`, `at least 4 stars`, `4+ stars`, `over 500 reviews` -- and answers a
`Noticed` per bound: the setting that would enforce it, the figure, and the shopper's own
words. Nothing in the pipeline reads it. `Constraints` is untouched, ADR-0039 stands: a
bound is still enforced after grounding, still in Python, and still only when it was set.

**Each door offers it in the one way it can, and neither applies one.** The CLI logs one
INFO line per bound, quoting the words it read and naming the flag that would enforce
them -- `__main__` being one of the two modules allowed to name a flag at all. The
browser asks `GET /api/bounds`, which runs nothing and refuses nothing, and the form
puts the figure in the box that would enforce it, once, only where that box is empty,
under Python's own sentence saying where the number came from. A hint and never a mark:
nothing is wrong, and marking a box the shopper did not type in is a refusal nobody can
act on (ADR-0033).

**The scan is anchored on the unit, not on the phrase.** A rating is read only beside
its scale and a review count only beside somebody being counted, which is the rule
`verification.py` already holds for a figure off a page. A budget is read beside a
currency mark, or beside nothing that starts another word, or beside one of the handful
of joins a sentence carries on in -- so "under 1500 and at least 4 stars" is a budget and
"under 200 hours of battery" is not. `money`'s own tables are what a currency mark is
read off, as everywhere else (ADR-0054).

**The currency is not carried over.** "under $200" names USD and the run may be counted
in something else (ADR-0043). The bare number is offered and the existing rule places it:
`max_price` is read in the currency the run's prices are counted in, and the box says so.

**A figure the setting would not take is dropped at the door.** `bounds.py` knows
nothing about `config.LIMITS` -- it is a domain module and the ranges live at the doors
(ADR-0033) -- so `api.bounds_payload` is what leaves out a budget of fifty million
rather than pre-filling a box the form would then mark.

## Consequences

The "200 hours of battery" case now costs a line of narration and a box somebody clears,
rather than an empty report with nothing to explain it. That is the whole trade, and it
is only a good one for as long as nothing acts on the reading.

The obligations.

- **Nothing may ever apply a noticed bound.** Not the CLI, not `parse_options`, not the
  form on submit. The moment one is applied without being seen, the failure this ADR
  exists to avoid is back, and it is silent.
- **`bounds.py` names no flag.** The same sentence is read on a terminal and in a
  browser, so the module answers the words it read and the figure; each door writes its
  own sentence around them, and `tests/test_conventions.py` holds every module below the
  two doors to it.
- **The form fills a box once, and only an empty one.** A box the shopper has typed in
  or cleared is theirs. Re-offering a figure they deleted is enforcing it slowly.
- **A new shape is a pattern in that module and nowhere else.** Both doors read
  `notice()`; a phrasing added anywhere else would be a second answer to what the
  request says.
