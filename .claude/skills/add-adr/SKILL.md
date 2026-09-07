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

The template *is* the shape `tests/test_conventions.py` reads -- the heading, the
status vocabulary, the ISO date, the three sections -- and its prompts are the
brief for what goes in each. Keep them all; replace the prose. Two things it
cannot say for itself:

- The number in the heading must match the filename, and the title must match
  the index row character for character.
- Every `ADR-NNNN` the record cites must exist.

Of the three sections, **Consequences** is the one that earns the record: the
*obligations*. Which other place has to be edited in step, which invariant a
future change must not break, which failure returns if it does, and the name of
the convention test that guards it.

## 3. Index it

Add the row to the table in `docs/adr/README.md`, in numbered order and in the
shape the rows above it already have.
`test_each_index_row_says_what_the_record_says` compares the title and the
status against the record character for character.

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
