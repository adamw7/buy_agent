# ADR-0071: Build the CLI's flags off the options table

- **Status:** Accepted
- **Date:** 2026-10-04

## Context

`api.OPTIONS` was already the one list of settings both doors filled a config
from (ADR-0012, ADR-0033): `parse_options`, `defaults_payload`, the ranges and
`__main__.main` all read it. The flags were not built from it. `build_parser`
wrote out every setting again by hand -- its type, its choices and its default,
around 280 lines -- and the checks were written twice as well:
`api._bounded`/`_as_number` and `__main__._bounded` held a number to its range
in the same words, and `api._checked` and `__main__._checked` wrapped the same
`config` rule, one raising `ApiError` and the other `ArgumentTypeError`.

Convention tests kept the two copies in step: a range test per number setting,
a test that every row had a flag, and the "same words" test. A new setting
was a row, a flag and a test row, and the `add-option` skill listed all three.

## Decision

Each `api.Option` row carries a door-neutral parser that takes stripped text and
raises `ValueError` -- `_Unnamed` when the words are written to follow the key
("must be a number; got 'hot'"). `api._read` turns a refusal into an `ApiError`
marking the key's box. `__main__._flag` turns it into argparse's usage error.

`build_parser` makes one flag per row: `--<key>`, a `BooleanOptionalAction` for
a switch, the row's `choices` for a table row, and the row's default.
`__main__._written()` holds what only the terminal says, keyed like the rows:
the help, and the few places a flag is not what its row makes it (a `metavar`,
the raw `$BUY_AGENT_*` default so a misspelt one is refused by name, and
`--num-ctx`'s `None`). `--fetch` therefore gains its `--fetch` half beside
`--no-fetch`.

## Consequences

- A new setting is a row in `api.OPTIONS` and its help in `_written()`. A row
  without help is a `KeyError` the first time the parser is built, which every
  CLI test does, so no test is needed to catch it.
- `--help` lists the rows in `OPTIONS`' order, followed by the flags that are not
  settings (`--sort-by`, `--source`, `--compare`, `--json`, `-v`).
- A parser must raise `ValueError` and nothing else; raising `ApiError` itself
  would skip the CLI's wrapping and surface as a traceback there.
- `test_both_front_doors_refuse_text_in_a_number_in_the_same_words` stays, as
  the check that `_flag` keeps the parser's words rather than argparse's "invalid
  float value".
