---
name: add-row
description: Add a model server to providers.PROVIDERS, or a payment rail to rails.RAILS -- the two tables that carry everything differing between one backend and another. Use when asked to support another model server ("run this against LM Studio", "add a provider"), another way of paying ("add a rail", "support this merchant"), or when changing what one existing row declares.
---

# Adding a row

`buy_agent/providers.py` and `buy_agent/rails.py` are the same idea twice: one
table, one row per backend, and everything that differs between them on that row.
A third provider is a row in `PROVIDERS` and a row nowhere else; a third rail is a
row in `RAILS` and a row nowhere else. Adding one is mostly *not* editing things.

## 1. Write the row

- A `Provider` needs `name`, `label`, `model`, `base_url`, `api_key`,
  `takes_num_ctx`, `chat_model`, `installed`, `transport_errors` and `hint`; a
  `Rail` needs `name`, `label`, `endpoint`, `needs_endpoint`, `needs_key`,
  `moves_money`, `checkout`, `settle`, `transport_errors` and `hint`. The
  dataclass docstring says what each is for -- read it, and answer all of them.
- Defaults come from that backend's **own** environment variables, read on the
  row (`os.getenv("OLLAMA_MODEL", ...)`). A secret is read there and nowhere
  else: `api_key` has no flag and no form field, and neither
  `provider_options()` nor `rail_options()` may carry one -- those payloads go to
  a browser.
- `transport_errors` is read off what that client actually raises, not off any
  prose about it. The clients differ in what they convert: ollama's turns a
  refused connection into a builtin `ConnectionError` and lets `httpx` errors out
  raw, which is why its tuple is four entries wide.
- `hint` turns one of those into something the user can type. A sentence both
  backends would write belongs in `_too_slow_hint` / `_unreachable_hint` rather
  than in the row.

## 2. What must not happen

- No `if provider == ...` and no `if rail == ...` above these modules.
  `AgentConfig.model_server` and `AgentConfig.rail_used` are the only places a
  name becomes behaviour; everything else asks the row.
- A setting one backend takes and another does not is a **field on the row** --
  `takes_num_ctx`, `needs_endpoint`, `moves_money` are the pattern -- rather than
  a branch in the CLI, the API and the form.
- `providers.py` imports nothing from `config.py`; the dependency runs the other
  way.
- A per-backend address defaults to `""` and is resolved in
  `AgentConfig.__post_init__` off the row (ADR-0012). For a paying rail that
  needs one and has none, that is a `ValueError` -- and both doors translate it
  rather than letting it out, an `ApiError` naming `merchant_url` and argparse's
  own exit 2.

## 3. What comes free, and what does not

Free, because each is read off the table: the flag's `choices`,
`PROVIDER_OPTIONS` / `RAIL_OPTIONS`, and the rows the form's picker is built
from. `test_every_provider_is_offered_everywhere_it_can_be_asked_for` and
`test_every_rail_is_offered_everywhere_it_can_be_asked_for` check that they were.

Not free: the *shape* of a row, if it changed. A new field on `Provider` or
`Rail` that the picker needs is a field in `provider_options()` /
`rail_options()` and in `ui/src/app/agent.types.ts`, which
`test_a_provider_option_is_mirrored_field_for_field_in_typescript` and its rail
twin compare field for field.

## 4. Tests

- Patch the client where the module imported it -- `providers.Client`,
  `providers.openai.OpenAI`, `providers.httpx.get`, `rails.httpx.post` -- not
  where it is defined.
- Compare a row by identity only *through* the module
  (`providers_module.OLLAMA`, `rails.RAILS`). `tests/test_providers.py` and
  `tests/test_rails.py` reload their module to re-read environment-derived
  defaults, and a name bound at import time then holds a row nothing answers with
  (`test_the_module_that_reloads_providers_binds_nothing_a_reload_replaces`).
- `integration/` is Ollama's alone -- a second server needs a GPU a CPU runner
  cannot host honestly. A new provider's live half is a gap to name in an ADR,
  the way ADR-0028 names vLLM's, not a job to fake.
- A rail's tests sign real mandates and read them back through the SDK's own
  verifier: a chain that verifies only against a fake verifier is one no
  counterparty would take.

## 5. Docs

- `CLAUDE.md`: the first two of the fourteen conventions are these tables, and
  the paragraph naming each backend's environment variables is beside them.
- `README.md` where it enumerates what can be run against.
- `scripts/start.ps1` starts Ollama and nothing else on purpose -- anything else
  is waited for at its address and named rather than launched. A new row does not
  change that; if it should, say why in an ADR first (`/add-adr`).

## Finally

Run `/preflight`. `tests/test_conventions.py -k provider` and the same file
`-k rail` are the two that fail loudest if a row was added in one place only.
