# ADR-0001: Record architecture decisions in ADRs

- **Status:** Accepted
- **Date:** 2026-08-25

## Context

This codebase is small but unusually opinionated, and most of its opinions are
reactions to something that went wrong with a 1.2B model: sentinels instead of
nulls, a fixed pipeline instead of a tool loop, an event named `failure` instead
of `error`. `CLAUDE.md` and `docs/architecture.md` both describe the resulting
shape well, but they describe the *system as it is*. Neither has room for the
alternative that was tried and abandoned, and both are rewritten whenever the
code moves -- so the reasoning is quietly lost at exactly the moment someone is
about to undo it.

The recurring cost is real: a rule that looks arbitrary gets "simplified" away,
and the failure it prevented comes back a release later, now harder to diagnose
because the code no longer looks like it ever had that problem.

## Decision

Keep a numbered, append-only log of architecture decisions in `docs/adr/`, one
Markdown file per decision, in the style Michael Nygard described: context,
decision, consequences.

- Files are `NNNN-kebab-case-slug.md`, numbered from `0001` and never reused.
- Every ADR carries a `Status` and a `Date`.
- An ADR is never edited to say something different once accepted. A decision
  that changes gets a *new* ADR, and the old one is marked
  `Superseded by ADR-NNNN`. Fixing a typo or a broken link is fine.
- `docs/adr/README.md` indexes every record, and `tests/test_conventions.py`
  checks that the index and the directory agree.
- ADR-0002 onwards are retrospective: they record decisions already visible in
  the code as of this date, written from the reasons the modules' docstrings and
  `CLAUDE.md` already carry. Their `Date` is the date they were written down,
  not the date the decision was made.

## Consequences

The three documents divide up cleanly. `docs/architecture.md` says what the
system is, in C4 diagrams. `CLAUDE.md` says how to work in it. `docs/adr/` says
why it is this way and what was rejected -- the only one of the three that is a
log rather than a snapshot.

A change that contradicts an accepted ADR now has somewhere to argue: write the
superseding record, which forces the new decision's consequences to be stated
before the code lands. The cost is one file per decision, plus the discipline of
noticing that a decision is being made at all. Not every change is one: an ADR
is for choices that constrain later work, not for how a function is written.
