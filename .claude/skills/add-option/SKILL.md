---
name: add-option
description: Add, rename or remove a run setting (an AgentConfig field and the CLI flag, API key, form field and TypeScript type that carry it). Use when asked for a new option, flag, or setting for the agent -- "let the shopper set X", "add a --foo flag", "expose X in the form" -- or when changing the range, default or name of an existing one.
---

# Adding a run setting

A setting is one `AgentConfig` field reached through three doors: a Python
caller, the CLI, and the form over the JSON API. Nothing may decide it twice.
Work down this list in order.

## 1. `buy_agent/config.py` -- the field itself

- Add the field to `AgentConfig` with its default, documented in the class
  docstring's `Attributes`.
- **Numeric** -> a row in `LIMITS`, keyed by the field name, in whole numbers.
  The default must sit inside it.
- **Shaped or a closed set** (`region`, `currency`) -> a `parse_<field>` here,
  called from `__post_init__`. Read an existing table (`money.CODES`,
  `search.BACKENDS`) rather than listing it again, and name what would have
  worked in the refusal.
- A sentence `__post_init__` raises is shown in the browser too, so it names the
  **setting**, never the flag
  (`test_no_sentence_below_the_two_doors_tells_a_reader_to_type_a_flag`).
- **Provider-dependent** -> default `""`/`None` and resolve it in `__post_init__`
  off `self.model_server`, as `model`, `base_url` and `api_key` are (ADR-0012).
  Whether a server takes it is a provider-row field (`takes_num_ctx`), never a
  branch on the provider's name.

## 2. `buy_agent/api.py` -- one row for both doors

- One row in `OPTIONS`, in the order `--help` lists it. `parse_options`,
  `defaults_payload`, `limits_payload` and the CLI's flags all read it. Missing
  or empty means the default, never zero.
- Numeric -> `_number("<key>", int|float)`, adding the field when it is not the
  key; the range follows from the `LIMITS` row.
- A table row -> `_row("<key>", <TABLE>_OPTIONS)`. Shaped -> `Option("<key>",
  parse_<field>)`, which keeps the rule's own message. Boolean -> `Option("<key>",
  _as_bool)`, which the CLI makes a `--x` and `--no-x` pair.
- A parser raises `ValueError`; `_Unnamed` for one worded to follow the key
  ("must be ..."). `_read` marks the box, and the CLI prints it as a usage error.
- Provider- or rail-dependent -> `blank=True`, settled in `__post_init__`.
- A list has no row: follow `_read_sources` and `sources` in `parse_options` and
  `defaults_payload`, and add its flag by hand in `build_parser`.

## 3. `buy_agent/__main__.py` -- the flag's help

- An entry in `_written()` under the row's key: the help, which names the
  default (`test_every_flag_that_takes_a_value_names_the_default_it_has`), plus
  a `metavar` or `default` where the row's own would read wrong.
- `build_parser` and `main` need nothing else.
- A key named unlike its field (`think` for `reasoning`) is noted in `CLAUDE.md`.

## 4. `ui/src/app/agent.types.ts`

- Add the field to `AgentDefaults` and (optional) to `SearchOptions`, named as in
  Python.

## 5. `ui/src/app/search-form/`

- A signal, and the key in `options()`, the one place the payload is built.
  `agent.ts`'s `toQuery` drops blanks.
- **Numeric** -> one `field('<request key>', 'Label', signal, {step, hint, off,
  remembered, remembersBlank})` row in `numberFields` and nothing else: the
  template loops over it, and `settings` spreads
  `numberSettings(this.numberFields)`. No literal `min=`, no second block of
  markup, no `setting(...)` row for it.
- `remembersBlank: false` only where a cleared box should come back showing the
  default. `remembered: false` for a bound on what one search finds, which is
  never kept for the next visit (ADR-0077).
- **Anything else** -> a `setting(signal, (d) => d.<key>,
  asText|asBoolean|amongst(...))` row in `settings`.
- Keys are typed `NumberKey`; `placeholders()` needs nothing (a `null` default
  reads "No limit", ADR-0039).
- The page applies Python's rules and never invents one: ship a range or ask for
  a verdict (`GET /api/sources`) (ADR-0031, ADR-0033).

## 6. Tests and docs

- Nothing to add to `tests/test_conventions.py`: ranges against `numberFields`,
  `OPTIONS` against `SearchOptions` and `AgentDefaults` against
  `defaults_payload` fail by themselves.

- Unit tests in `tests/test_config.py`, `test_cli.py`, `test_api.py` and the
  form's spec; both suites cover every line.
- `docs/testing.md` is the one place the suite's counts are written down, and
  nothing checks them: correct them from a run.
- Update `README.md` and `CLAUDE.md` where they list the options.

Then run `/preflight`.
