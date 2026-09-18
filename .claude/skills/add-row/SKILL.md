---
name: add-row
description: Add a model server to providers.PROVIDERS, a payment rail to rails.RAILS, or a search backend to search.BACKENDS -- the three tables that carry everything differing between one backend and another. Use when asked to support another model server ("run this against LM Studio", "add a provider"), another way of paying ("add a rail", "support this merchant"), another way of searching ("add a search engine", "use Kagi"), or when changing what one existing row declares.
---

# Adding a row

`buy_agent/providers.py`, `buy_agent/rails.py` and `buy_agent/search.py` are the
same idea three times: one table, one row per backend, and everything that differs
between them on that row. A third provider is a row in `PROVIDERS` and a row
nowhere else; a third rail is a row in `RAILS` and a row nowhere else; a fourth
search backend is a row in `BACKENDS` and a row nowhere else. Adding one is mostly
*not* editing things.

## 1. Write the row

- A `Provider` needs `name`, `label`, `model`, `base_url`, `api_key`,
  `takes_num_ctx`, `chat_model`, `installed`, `transport_errors` and `hint`; a
  `Rail` needs `name`, `label`, `endpoint`, `needs_endpoint`, `needs_key`,
  `moves_money`, `checkout`, `settle`, `transport_errors` and `hint`; a `Backend`
  needs `name`, `label`, `endpoint`, `api_key`, `needs_key`, `find`,
  `transport_errors` and `hint`. The dataclass docstring says what each is for --
  read it, and answer all of them.
- A `Provider`'s and a `Rail`'s functions are handed the `AgentConfig` they were
  resolved from; a `Backend`'s are handed **the row itself**. A search takes a
  query and answers results, and the address and key a row needs are on the row --
  so `search.py` imports nothing from `config` in either direction, and a new row
  must not need one either (ADR-0057).
- Defaults come from that backend's **own** environment variables, read on the
  row (`os.getenv("OLLAMA_MODEL", ...)`). A secret is read there and nowhere
  else: `api_key` has no flag and no form field, and neither
  `provider_options()` nor `rail_options()` may carry one -- those payloads go to
  a browser.
- `transport_errors` is read off what that client actually raises, not off any
  prose about it. The clients differ in what they convert: ollama's turns a
  refused connection into a builtin `ConnectionError` and lets `httpx` errors out
  raw, which is why its tuple is four entries wide.
- `hint` turns one of those into something the user can type, and only what is
  *this* backend's own: a sentence both would write belongs in `_too_slow_hint` /
  `_unreachable_hint`, and in `providers.py` the two failures both servers answer
  the same way -- an unreadable answer and a timeout -- are already decided above
  the rows by `_hint`, which every row's own function is wrapped in.
- Whatever that sentence says, it names the **setting** and never the flag: it is
  read once on a terminal and once under a labelled box in the form, where nobody
  can type one. Every module below the doors is read for one by
  `test_no_sentence_below_the_two_doors_tells_a_reader_to_type_a_flag`.

## 2. What must not happen

- No `if provider == ...`, no `if rail == ...` and no `if backend == ...` above
  these modules. `AgentConfig.model_server`, `AgentConfig.rail_used` and
  `AgentConfig.search_backend` are the only places a name becomes behaviour;
  everything else asks the row.
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
- A **search** backend's address and key are the exception, and deliberately:
  both are read on the row from that backend's own environment variable, with no
  flag and no form field. The config refuses a *name* nothing can search; a key
  the machine has not got is the row's own `SearchError` naming the variable, and
  `Backend.configured` is what the picker marks the row with (ADR-0057).

## 3. What comes free, and what does not

Free, because each is read off the table: the flag's `choices`,
`PROVIDER_OPTIONS` / `RAIL_OPTIONS` / `BACKEND_OPTIONS`, and the rows the form's
picker is built from. `test_every_provider_is_offered_everywhere_it_can_be_asked_for`,
`test_every_rail_is_offered_everywhere_it_can_be_asked_for` and
`test_every_backend_is_offered_everywhere_it_can_be_asked_for` check that they were.

Not free: the *shape* of a row, if it changed. A new field on `Provider`, `Rail`
or `Backend` that the picker needs is a field in `provider_options()` /
`rail_options()` / `backend_options()` and in `ui/src/app/agent.types.ts`, which
`test_a_provider_option_is_mirrored_field_for_field_in_typescript` and its rail
and backend twins compare field for field.

## 4. Tests

- Patch the client where the module imported it -- `providers.Client`,
  `providers.openai.OpenAI`, `providers.httpx.get`, `rails.httpx.post`,
  `search.DDGS`, `search.httpx.get` -- not where it is defined.
- Do not read a row's environment-derived field off the shipped row in a test: it
  holds whatever this process started with, so a developer with `$BRAVE_API_KEY`
  set would be testing the other answer. Build the row you mean.
- Compare a row by identity only *through* the module
  (`providers_module.OLLAMA`, `rails.RAILS`). `tests/test_providers.py` and
  `tests/test_rails.py` reload their module to re-read environment-derived
  defaults, and a name bound at import time then holds a row nothing answers with
  (`test_the_module_that_reloads_providers_binds_nothing_a_reload_replaces`).
- `integration/` is Ollama's alone -- a second server needs a GPU a CPU runner
  cannot host honestly, and the web there is faked on purpose (ADR-0026). A new
  provider's or backend's live half is a gap to name in an ADR, the way ADR-0028
  names vLLM's and ADR-0057 names SearXNG's and Brave's, not a job to fake.
- A rail's tests sign real mandates and read them back through the SDK's own
  verifier: a chain that verifies only against a fake verifier is one no
  counterparty would take.

## 5. Docs

- `CLAUDE.md`: the first three conventions in the list are these tables, and the
  paragraph naming each backend's environment variables is beside them. Do not
  copy the number the heading counts them by -- a count restated here is one more
  place to correct, and it has been wrong before.
- `docs/testing.md` is the one place the suite's counts are written down, and
  nothing checks them: run the suite and correct what it says a run reads. Read
  the numbers off the run, for the same reason the count above is not repeated
  here.
- `README.md` where it enumerates what can be run against.
- `scripts/start.ps1` starts Ollama and nothing else on purpose -- anything else
  is waited for at its address and named rather than launched. A new row does not
  change that; if it should, say why in an ADR first (`/add-adr`).

## Finally

Run `/preflight`. `tests/test_conventions.py -k provider`, the same file
`-k rail` and `-k backend` are the three that fail loudest if a row was added in
one place only.
