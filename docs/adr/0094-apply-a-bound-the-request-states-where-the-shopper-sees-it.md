# ADR-0094: Apply a bound the request states, where the shopper sees it and can undo it

- **Status:** Accepted
- **Date:** 2026-10-31

## Context

ADR-0059 read "under $200" out of the request in Python and *offered* it: the CLI
logged the flag that would enforce it, and the form put the figure in its box. Its
one hard rule was that nothing apply a noticed bound unseen, because a misreading
applied silently -- "200 hours of battery" read as a budget -- empties the report
and leaves nothing to explain it.

Using both doors with the headline request showed what that cost.

- **The form swallowed the first press.** The request is read on `change`, which
  Enter (and leaving the box for Find products) fires on the way to submitting, so
  every submit of a request naming a budget was held for its reading. The reading
  filled the box, and to keep the figure from being applied unseen the form sent
  nothing: it threw open the Settings panel -- twenty fields -- moved the focus into
  Max price, and said nothing had been searched yet. "wireless headphones under
  $200", typed and entered, always took two presses, and two of the four example
  requests name a budget. The panel then stayed open above the results.
- **The two doors answered the same words differently.** The form applied the figure
  on the second press; the CLI never did, and reported products over the budget the
  shopper wrote.

The danger ADR-0059 guarded against is a figure applied *unseen*, not a figure
applied. Seeing it does not need a held submit or an open panel: it needs the figure
said where the shopper is looking, in their own words, with a way to undo it -- and
a report that, when the figure removed everything, says so (ADR-0055's `dropped`).
The scan itself is unchanged and stays conservative: anchored on the unit, so
"under 200 hours" is never read.

## Decision

**A bound the request states is applied by both doors, and said where it is applied.**
`bounds.notice` reads it as before and still names no flag; `bounds.py` is still
imported by the two doors alone, and nothing below them reads it.

- **The CLI applies each takeable bound whose flag was not given**
  (`__main__._apply_noticed_bounds`), and logs one INFO line per bound quoting the
  words it read, the flag it stands for, and how to change it: give the flag, or
  leave the words out of the request. A flag the shopper gave always wins.
- **The form fills the box once, only while empty, and never opens the panel to do
  it.** A line beside the request (`.from-request`, a `role="status"`) quotes each
  bound's phrase while its box still holds the figure, with one button: *Don't apply
  it* clears those boxes; once the results on screen came from them (the last search
  sent was this request with these figures), it reads *Search again without it* and
  searches again. A cleared box is not refilled.
- **A submit still waits for a reading on its way, and then goes, with what it
  filled in.** No press is swallowed; the box under Python's note and the line beside
  the request both say where the figure came from.
- `GET /api/bounds` answers each bound's `phrase` as well as its `note`, so the line
  quotes the shopper's own words.

ADR-0059's other rules carry over unchanged: the scan is anchored on the unit, the
currency is not carried over (`max_price` is read in the run's currency), and a
figure outside its setting's range is dropped at the door (`api.takeable`).

## Consequences

"wireless headphones under $200" is one press at both doors and the same run at
both. The "200 hours of battery" case would now cost a run, not a line of narration
-- but the scan does not read it, and a misreading it does make is quoted back
before the results and named in `dropped` after them.

The obligations.

- **Nothing applies a noticed bound without saying so at the door that applied it.**
  The CLI's line and the form's `.from-request` line are the "seen"; a third place
  applying one (a scheduled run, `parse_options`) must say so too, or the silent
  empty report ADR-0059 feared is back.
- **A given setting outranks the request.** The CLI skips a bound whose flag was
  given; the form fills only an empty box and never one the shopper typed in or
  cleared (`offered`, `filled`).
- **The undo is one click and says what it does.** Its wording follows
  `searchedWithIt`; a button that searched when nothing had been searched yet, or
  only cleared when the results came from the figure, would be a surprise either way.
- **`bounds.py` still names no flag and is still read only by `__main__.py` and
  `api.py`** (`tests/test_architecture.py`); `NoticedBound` mirrors the payload field
  for field (`tests/test_conventions.py`).
