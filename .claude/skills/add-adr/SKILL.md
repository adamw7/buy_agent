---
name: add-adr
description: Write a new architecture decision record in docs/adr/, or supersede an existing one. Use when a decision about how this codebase is shaped needs writing down, when asked to "add an ADR" or "record this decision", or when a change contradicts an accepted record and needs one superseding it.
---

# Adding an ADR

`docs/adr/` is the decision log: the conventions in `CLAUDE.md` are the rules,
the records are why they exist and what was rejected. Numbers are never reused
and accepted records are never rewritten -- a change that contradicts one gets a
new record superseding it.

## 1. Take the next number from the directory

Read it off the files, never off prose:

```bash
ls docs/adr/[0-9][0-9][0-9][0-9]-*.md | tail -1
```

The next free number is that one plus one, zero-padded to four. (The sentence in
`CLAUDE.md` naming the next free number is a copy, and copies go stale -- it has
before. Correct it in step 4, do not trust it in step 1.)

## 2. Write the record

Copy `docs/adr/0000-template.md` to `docs/adr/NNNN-slug.md`, where the slug is
the decision in the imperative, lower-cased and hyphenated -- match the voice of
the existing filenames (`0027-let-the-shopper-name-the-sources.md`), not a noun
phrase.

`tests/test_conventions.py` reads the shape, so it must be exactly:

- First line: `# ADR-NNNN: Title`, the number matching the filename, the title
  matching the index row exactly.
- `- **Status:** Accepted` (or `Proposed`, or
  `Superseded by [ADR-NNNN](NNNN-slug.md)` -- nothing else parses).
- `- **Date:** YYYY-MM-DD`, ISO.
- All three sections, spelled `## Context`, `## Decision`, `## Consequences`.
- Every `ADR-NNNN` it cites must exist.

What goes in them:

- **Context** -- the forces that made this a decision rather than a default, and
  what was actually observed to go wrong. Not what might in principle.
- **Decision** -- present tense, as a rule someone changing the code can apply:
  "extraction fields are non-nullable with a sentinel", not "we looked at
  sentinels".
- **Consequences** -- above all the *obligations*: which other place has to be
  edited in step, which invariant a future change must not break, and which
  failure returns if it does. Name the convention test that guards it, if there
  is one.

## 3. Index it

Add the row to the table in `docs/adr/README.md`, in numbered order:

```
| [NNNN](NNNN-slug.md) | Title, as the decision in the imperative | Accepted |
```

The title and status must match the record character for character --
`test_each_index_row_says_what_the_record_says` compares them.

## 4. Keep the prose in step

- `CLAUDE.md`: correct the sentence at the end of the `docs/adr/` paragraph that
  names how far the log runs and the next free number, and cite the new record
  `(ADR-NNNN)` beside whichever convention it explains.
- If the decision changes a rule, the rule's own paragraph in `CLAUDE.md` is the
  normative text -- update it too. The record explains; it does not instruct.

## Superseding

Do not edit the old record's argument. Set its `Status` to
`Superseded by [ADR-NNNN](NNNN-slug.md)`, update its row in the index to the same
string, and let the new record's Context say what changed since.

## Finally

`python -m pytest tests/test_conventions.py -k adr` checks all of the above.
