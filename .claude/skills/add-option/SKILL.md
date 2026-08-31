---
name: add-option
description: Add, rename or remove a run setting (an AgentConfig field and the CLI flag, API key, form field and TypeScript type that carry it). Use when asked for a new option, flag, or setting for the agent -- "let the shopper set X", "add a --foo flag", "expose X in the form" -- or when changing the range, default or name of an existing one.
---

# Adding a run setting

A setting is one field in `AgentConfig` reached through three doors: a Python
caller, the CLI, and the web form over the JSON API. Nothing may decide it twice
-- a default, a range or a shape written down on two doors is how the CLI comes
to accept what the API refuses. Work down this list in order; each step names the
convention test that fails if it is skipped.

## 1. `buy_agent/config.py` -- the field itself

- Add the field to `AgentConfig` with its default, and document it in the class
  docstring's `Attributes` (that docstring is what `--help` and the form's
  wording are written from).
- **Numeric** -> add a row to `LIMITS`, keyed by the *field* name, whole numbers
  even for a decimal field (the refusal quotes them back). Check the default is
  inside its own range.
- **Shaped, not bounded** (like `region`) -> write a `parse_x` function here and
  call it from `__post_init__`, so a Python caller is refused the same way the
  doors refuse. Do not put the rule in the CLI or in `api.py`.
- **Provider-dependent** -> do not give it a plain default. Default it to `""` /
  `None` and resolve it in `__post_init__` off `self.model_server`, the way
  `model`, `base_url` and `api_key` are (ADR-0012). If one server takes it and the
  other does not, declare that on the provider row (`takes_num_ctx` is the
  pattern) rather than branching on the provider's name anywhere.

## 2. `buy_agent/__main__.py` -- the CLI

- `build_parser`: `add_argument` with `default=_DEFAULTS.<field>` -- never a
  literal repeat of the default.
- Numeric -> `type=_bounded(int, "<field>")`, so an out-of-range value is a usage
  error rather than a minute of waiting. Shaped -> a `type=` wrapper like
  `_region` / `_source` that catches `ValueError` and re-raises
  `argparse.ArgumentTypeError` **with the original message** (argparse throws a
  plain `ValueError` away and prints "invalid value", losing the whole point).
- Boolean -> `BooleanOptionalAction` if it needs an off switch; a tri-state whose
  `None` means "send nothing" is not reachable from the CLI on purpose.
- `main`: pass it into the `AgentConfig(...)` call.
- If the flag's name differs from the field's (`--think` for `reasoning`), that
  is a deliberate exception -- add it to the note in `CLAUDE.md` rather than
  inventing a second one silently.

## 3. `buy_agent/api.py` -- the JSON door

- `parse_options`: `_read(data, "<key>", defaults.<field>, <coercion>)`. A missing
  key and an empty string both mean "use the default" -- never "zero".
- Numeric -> `_bounded(int|float, "<key>")` **and** a row in `_BOUNDED` mapping
  the request key to the config field (`results` -> `num_products`). That one
  table is read twice: to hold an incoming value, and by `limits_payload` to ship
  the range to the form.
- A list-valued option does not go through `_read` (it renders values with `str`,
  turning a JSON array into a Python repr) -- follow `_read_sources`, which takes
  an array or a separated string.
- Raise `ApiError(..., field="<key>")` for anything unusable, naming the request
  key: that is what marks the box in the browser.
- `defaults_payload`: add the key, so the form is seeded with it.

## 4. `ui/src/app/agent.types.ts`

- Add the field to `AgentDefaults` and to `SearchOptions` (optional there --
  blanks mean default). Mirror the Python name exactly.

## 5. `ui/src/app/search-form/`

- A signal for the field, a row in the `settings` table -- `setting(signal,
  (d) => d.<key>, asText|asNumber)`, which is what seeds it from the defaults and
  remembers and restores it -- and the key in what `submit()` emits. The request
  itself is deliberately not remembered; `agent.ts`'s `toQuery` drops blanks on
  the way out.
- Numeric -> add it to the `numbers` table keyed by the *request key*, and bind
  `[min]="limits()['<key>']?.min"` / `[max]` in the template. Never write a
  literal `min="1"` into the markup.
- The page applies rules; it never invents one. If the page cannot judge the
  value without a model, a network or a minute of waiting, either ship it a rule
  from Python (a range) or ask the server for a verdict (`GET /api/sources`) --
  do not reimplement the rule in TypeScript (ADR-0033, ADR-0031).

## 6. `tests/test_conventions.py`

- Add the `(field, flag, key)` row to
  `test_both_front_doors_hold_a_number_to_the_same_range` for a numeric setting.
- The rest is automatic and will fail on its own if a step above was skipped:
  the shipped ranges against the form's `numbers` table, every key
  `parse_options` reads against `SearchOptions`, and `AgentDefaults` against
  `defaults_payload` field for field.

## 7. Tests and docs

- Unit tests for the new behaviour in `tests/test_config.py`, `test_cli.py`,
  `test_api.py` and the form's spec -- both suites cover every line, so a new
  branch with no test drops the floor.
- Update `README.md` and `CLAUDE.md` where they enumerate the options.

## Finally

Run `/preflight` -- the whole gate CI applies, both suites and both coverage
floors.
