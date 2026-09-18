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
- **Shaped, not bounded** (like `region`, or `currency`, whose rule is a closed
  set rather than a shape) -> write a `parse_<field>` function here and call it
  from `__post_init__`, so a Python caller is refused the same way the doors
  refuse. Do not put the rule in the CLI or in `api.py`. Where the set the rule
  reads is already a table somewhere (`money.CODES`, `search.BACKENDS`), read it
  rather than listing it again -- and let the refusal name what would have worked.
- A sentence `__post_init__` raises is read on a terminal *and* under a labelled
  box in the form, so it names the **setting** and never the flag --
  `test_no_sentence_below_the_two_doors_tells_a_reader_to_type_a_flag` reads every
  module below the doors for one.
- **Provider-dependent** -> do not give it a plain default. Default it to `""` /
  `None` and resolve it in `__post_init__` off `self.model_server`, the way
  `model`, `base_url` and `api_key` are (ADR-0012). If one server takes it and the
  other does not, declare that on the provider row (`takes_num_ctx` is the
  pattern) rather than branching on the provider's name anywhere.

## 2. `buy_agent/__main__.py` -- the CLI

- `build_parser`: `add_argument` with `default=_DEFAULTS.<field>` -- never a
  literal repeat of the default.
- Numeric -> `type=_bounded(int, "<field>")`, so an out-of-range value is a usage
  error rather than a minute of waiting. Shaped -> `type=_checked(parse_region)`,
  the one wrapper every fixed-set and shaped flag goes through: it calls the
  rule, catches `ValueError` and re-raises `argparse.ArgumentTypeError` **with the
  original message** (argparse throws a plain `ValueError` away and prints
  "invalid value", losing the whole point). Do not write a second wrapper beside
  it -- the rule it calls is the part that is new.
- The help text names the default, whatever the flag is: `--help` is the CLI's
  only documentation, so a default left out is a fact with nowhere to be read.
  `test_every_flag_that_takes_a_value_names_the_default_it_has` holds both
  parsers to it.
- Boolean -> `BooleanOptionalAction` if it needs an off switch; a tri-state whose
  `None` means "send nothing" is not reachable from the CLI on purpose.
- `main`: pass it into the `AgentConfig(...)` call.
- If the flag's name differs from the field's (`--think` for `reasoning`), that
  is a deliberate exception -- add it to the note in `CLAUDE.md` rather than
  inventing a second one silently.

## 3. `buy_agent/api.py` -- the JSON door

- `parse_options`: `_read(data, "<key>", defaults.<field>, <coercion>)`. A missing
  key and an empty string both mean "use the default" -- never "zero".
- Numeric -> `_bounded(int|float)` **and** a row in `_BOUNDED` mapping the
  request key to the config field (`results` -> `num_products`). The parser is
  given the key it arrives under, so the range comes off that row and the key is
  written once on the line. That one table is read twice: to hold an incoming
  value, and by `limits_payload` to ship the range to the form.
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

- A signal for the field, and the key in `options()`, the one place the request
  payload is built. The request itself is deliberately not remembered;
  `agent.ts`'s `toQuery` drops blanks on the way out.
- **Numeric** -> one `field('<request key>', 'Label', signal, {step, hint, off,
  remembersBlank})` row in the `numberFields` table and **nothing else**: not a
  line in the template, which loops over that table, and not a row in
  `settings`, which spreads `numberSettings(this.numberFields)`. The row is what
  draws the box, binds `[min]`/`[max]` from the range the server shipped, takes
  its placeholder off `defaults_payload` under the same key, carries the refusal
  mark, and is seeded, remembered and restored under that same key. A literal
  `min="1"` in the markup, a second block of number-box markup beside the loop,
  or a `setting(...)` row for a box already in that table, is the mistake this
  table exists to make impossible.
- Set `remembersBlank: false` only where a cleared box should come back showing
  the served default rather than cleared -- it is the one thing about a number
  box its key cannot say, `num_ctx` defaulting to a number and still having to
  remember a blank while `temperature` defaults to 0 and must not.
- **Anything else** -> a row in the `settings` table, `setting(signal,
  (d) => d.<key>, asText|asBoolean|amongst(...))`, which is what seeds it from
  the defaults and remembers and restores it. It is remembered under the key you
  write there; a number box is remembered under the camel case of its request
  key, so a browser already holding one keeps it.
- A number box's key is typed `NumberKey` -- the `AgentDefaults` keys whose
  value really is a number -- so a box the server has no default, no range or no
  number for does not compile. `placeholders()` needs nothing: a `null` default
  reads "No limit" (ADR-0039), a number reads itself.
- The page applies rules; it never invents one. If the page cannot judge the
  value without a model, a network or a minute of waiting, either ship it a rule
  from Python (a range) or ask the server for a verdict (`GET /api/sources`) --
  do not reimplement the rule in TypeScript (ADR-0033, ADR-0031).

## 6. `tests/test_conventions.py`

- Add the `(field, flag, key)` row to
  `test_both_front_doors_hold_a_number_to_the_same_range` for a numeric setting.
- The rest is automatic and will fail on its own if a step above was skipped:
  the shipped ranges against the form's `numberFields` table, every key
  `parse_options` reads against `SearchOptions`, `AgentDefaults` against
  `defaults_payload` field for field, and this file's own name for both of the
  form's tables against what `search-form.ts` declares.

## 7. Tests and docs

- Unit tests for the new behaviour in `tests/test_config.py`, `test_cli.py`,
  `test_api.py` and the form's spec -- both suites cover every line, so a new
  branch with no test drops the floor.
- `docs/testing.md` is the one place the suite's counts are written down, and
  nothing checks them: run the suite and correct what it says a run reads. Read
  the numbers off the run, not off this file -- a count copied here would be one
  more to correct.
- Update `README.md` and `CLAUDE.md` where they enumerate the options.

## Finally

Run `/preflight` -- the whole gate CI applies, both suites and both coverage
floors.
