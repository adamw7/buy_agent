# ADR-0014: Guard cross-module conventions with a test that reads the declarations

- **Status:** Accepted
- **Date:** 2026-08-25

## Context

Both suites cover essentially every line -- the Python floor is 99% against 100%
actual, the UI's 98%. At that point coverage stops telling anyone where the next
test should go, and it was never able to see the failure this codebase actually
produces, which is a list that exists in three places and only gets updated in
two.

Three of those lists are live:

- The three failure modes (ADR-0009), declared in `BuyAgent.run`'s docstring,
  the `except` tuple in `__main__.main`, and `api._STATUS`. Each is tested
  against its own idea of the list, so adding a fourth to two of them leaves the
  suite green while the user gets a traceback and the browser a 500.
- The sort criteria, offered by `ranking.SortBy`, `api.SORT_OPTIONS`, the CLI's
  `--sort-by` choices and the TypeScript `SortBy` union.
- The payload shapes, mirrored between `api.py` and `ui/src/app/agent.types.ts`
  across a language boundary neither suite can see across (ADR-0012).

A behavioural test cannot protect any of these, because it can only confirm the
entries it already knows to try. What matters is what is *not* in the list.

## Decision

`tests/test_conventions.py` asserts the rules that hold *between* modules by
reading the declarations themselves -- source, signatures, `typing.get_args`,
`ast` for the `except` tuple, and a parse of the TypeScript file -- rather than
by exercising behaviour.

The ADR log is checked the same way: that `docs/adr/README.md` indexes every
record in the directory and nothing that is not there, that numbers are unique,
and that each record carries the sections and the `Status` line ADR-0001 asks
for.

## Consequences

The failures this codebase is actually prone to become test failures at the point
of editing, with a message naming both places that now disagree. Adding a
failure mode, a sort criterion or a payload field means updating every place it
belongs, or the suite says which one was missed.

These tests are coupled to how the code is *written*, not only to what it does:
renaming `_STATUS` or restructuring `main` will break them, and that is the
trade. They are deliberately parsed loosely where possible -- reading the
documented failure modes without depending on indentation, for instance -- so
that formatting alone does not.
