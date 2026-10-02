---
name: add-row
description: Add a model server to providers.PROVIDERS, a payment rail to rails.RAILS, or a search backend to search.BACKENDS -- the three tables that carry everything differing between one backend and another. Use when asked to support another model server ("run this against LM Studio", "add a provider"), another way of paying ("add a rail", "support this merchant"), another way of searching ("add a search engine", "use Kagi"), or when changing what one existing row declares.
---

# Adding a row

`buy_agent/providers.py`, `buy_agent/rails.py` and `buy_agent/search.py` are one
idea three times: a table with one row per backend, holding everything that
differs. A new backend is a row in `PROVIDERS`, `RAILS` or `BACKENDS` and nowhere
else.

## 1. Write the row

- Answer every field the dataclass docstring describes. A `Provider` has `name`,
  `label`, `model`, `base_url`, `api_key`, `takes_num_ctx`, `chat_model`,
  `installed`, `transport_errors` and `hint`; a `Rail` has `name`, `label`,
  `endpoint`, `needs_endpoint`, `needs_key`, `moves_money`, `checkout`, `settle`,
  `transport_errors` and `hint`; a `Backend` has `name`, `label`, `endpoint`,
  `api_key`, `needs_key`, `find`, `transport_errors` and `hint`.
- A `Provider`'s and a `Rail`'s functions get the `AgentConfig`; a `Backend`'s get
  **the row itself**, so `search.py` imports nothing from `config` (ADR-0057).
- Defaults come from the backend's own environment variables, read on the row
  (`os.getenv("OLLAMA_MODEL", ...)`). `api_key` has no flag or form field and is
  never in `provider_options()` or `rail_options()`.
- `transport_errors` is what the client actually raises (ollama's lets `httpx`
  errors out raw beside a builtin `ConnectionError`). A row that dials an address
  also lists `UnicodeError`: the socket raises it, beneath every client and
  wrapped by none, for a host it cannot IDNA-encode (`192.168.1..5`). A row that
  builds its URL per call with `httpx` (a search backend, a rail) lists
  `httpx.InvalidURL` too, which is outside httpx's root.
- `hint` covers only this backend's own failures. Shared sentences live in
  `_too_slow_hint`/`_unreachable_hint`, and `_hint` decides the failures every
  provider shares. A hint names the **setting**, never the flag
  (`test_no_sentence_below_the_two_doors_tells_a_reader_to_type_a_flag`).

## 2. What must not happen

- No `if provider == ...`, `if rail == ...` or `if backend == ...` anywhere.
  `AgentConfig.model_server`, `AgentConfig.rail_used` and
  `AgentConfig.search_backend` are the only places a name becomes behaviour.
- A setting one backend takes and another does not is a field on the row
  (`takes_num_ctx`, `needs_endpoint`, `moves_money`).
- `providers.py` imports nothing from `config.py`.
- A per-backend address defaults to `""` and is resolved in
  `AgentConfig.__post_init__` (ADR-0012). A paying rail with none raises
  `ValueError`, which each door translates (an `ApiError` on `merchant_url`, exit
  2).
- A search backend's address and key are read on the row only; a missing key is
  the row's own `SearchError`, and `Backend.configured` marks the picker
  (ADR-0057).

## 3. What comes free, and what does not

Free: the flag's `choices`, `PROVIDER_OPTIONS` / `RAIL_OPTIONS` /
`BACKEND_OPTIONS` and the picker rows, checked by
`test_every_provider_is_offered_everywhere_it_can_be_asked_for`,
`test_every_rail_is_offered_everywhere_it_can_be_asked_for` and
`test_every_backend_is_offered_everywhere_it_can_be_asked_for`.

Not free: a new field the picker needs goes in `provider_options()` /
`rail_options()` / `backend_options()` and `ui/src/app/agent.types.ts`
(`test_a_provider_option_is_mirrored_field_for_field_in_typescript` and its
twins).

## 4. Tests

- Patch clients where the module imports them: `providers.Client`,
  `providers.openai.OpenAI`, `providers.httpx.get`, `rails.httpx.post`,
  `search.DDGS`, `search.httpx.get`.
- Build the row you mean rather than reading an environment-derived field off a
  shipped one.
- Compare rows by identity only through the module (`providers_module.OLLAMA`,
  `rails.RAILS`), since `tests/test_providers.py` and `tests/test_rails.py` reload
  them (`test_the_module_that_reloads_providers_binds_nothing_a_reload_replaces`).
- `integration/` is Ollama's alone (ADR-0026); another server's live half is a gap
  to name in an ADR, as ADR-0028 and ADR-0057 do.
- A rail's tests sign real mandates and verify them with the SDK.

## 5. Docs

- `CLAUDE.md`: the first three conventions and the environment-variable
  paragraph.
- `docs/testing.md` is the one place the suite's counts are written down, and
  nothing checks them: correct them from a run.
- `README.md` where it lists what can be run against.
- `scripts/start.ps1` starts only Ollama; changing that needs an ADR
  (`/add-adr`).

Then run `/preflight`; `tests/test_conventions.py -k provider`, `-k rail` and
`-k backend` fail loudest when a row was added in one place only.
