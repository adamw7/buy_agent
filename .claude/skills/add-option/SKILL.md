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

## 2. `buy_agent/__main__.py` -- the CLI

- `build_parser`: `add_argument` with `default=_DEFAULTS.<field>`.
- Numeric -> `type=_bounded(int, "<field>")`. Shaped -> `type=_checked(parse_region)`,
  which keeps the rule's own message; write no second wrapper.
- The help names the default
  (`test_every_flag_that_takes_a_value_names_the_default_it_has`).
- Boolean -> `BooleanOptionalAction` if it needs an off switch.
- `main` needs nothing: the config is built off `api.OPTIONS`.
- A flag named unlike its field (`--think` for `reasoning`) is noted in
  `CLAUDE.md`.

## 3. `buy_agent/api.py` -- the JSON door

- One row in `OPTIONS`: request key, field, and reader. `parse_options`,
  `defaults_payload` and `limits_payload` all read it. Missing or empty means
  the default, never zero.
- Numeric -> `_bounded(int|float)`; `_BOUNDED` follows from the `LIMITS` row.
- Provider- or rail-dependent -> `blank=True`, settled in `__post_init__`.
- A list has no row: follow `_read_sources` and `sources` in `parse_options` and
  `defaults_payload`.
- Raise `ApiError(..., field="<key>")` so the box is marked.

## 4. `ui/src/app/agent.types.ts`

- Add the field to `AgentDefaults` and (optional) to `SearchOptions`, named as in
  Python.

## 5. `ui/src/app/search-form/`

- A signal, and the key in `options()`, the one place the payload is built.
  `agent.ts`'s `toQuery` drops blanks.
- **Numeric** -> one `field('<request key>', 'Label', signal, {step, hint, off,
  remembersBlank})` row in `numberFields` and nothing else: the template loops
  over it, and `settings` spreads `numberSettings(this.numberFields)`. No literal
  `min=`, no second block of markup, no `setting(...)` row for it.
- `remembersBlank: false` only where a cleared box should come back showing the
  default.
- **Anything else** -> a `setting(signal, (d) => d.<key>,
  asText|asBoolean|amongst(...))` row in `settings`.
- Keys are typed `NumberKey`; `placeholders()` needs nothing (a `null` default
  reads "No limit", ADR-0039).
- The page applies Python's rules and never invents one: ship a range or ask for
  a verdict (`GET /api/sources`) (ADR-0031, ADR-0033).

## 6. `tests/test_conventions.py`

- Add the `(field, flag, key)` row to
  `test_both_front_doors_hold_a_number_to_the_same_range` for a numeric setting.
  The rest (ranges against `numberFields`, `OPTIONS` against `SearchOptions`,
  `AgentDefaults` against `defaults_payload`) fails by itself.

## 7. Tests and docs

- Unit tests in `tests/test_config.py`, `test_cli.py`, `test_api.py` and the
  form's spec; both suites cover every line.
- `docs/testing.md` is the one place the suite's counts are written down, and
  nothing checks them: correct them from a run.
- Update `README.md` and `CLAUDE.md` where they list the options.

Then run `/preflight`.
