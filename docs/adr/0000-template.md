# ADR-0000: Title, as the decision in the imperative

- **Status:** Proposed | Accepted | Superseded by [ADR-NNNN](NNNN-slug.md)
- **Date:** YYYY-MM-DD

## Context

What forces made this a decision rather than a default. Constraints that were
actually binding -- a small local model, a run that takes a minute, one user --
and what was observed to go wrong, not what might in principle.

## Decision

What was decided, in the present tense and as a rule someone changing the code
can apply: "extraction fields are non-nullable with a sentinel", not "we looked
at sentinels".

## Consequences

What this buys, what it costs, and above all what it obliges. The obligations
are the part worth writing down: which other place has to be edited in step,
which invariant a future change must not quietly break, which failure returns
if it does.
