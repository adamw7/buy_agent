# ADR-0029: Keep everything about a model server in one table row

- **Status:** Accepted
- **Date:** 2026-08-28

## Context

[ADR-0028](0028-serve-the-model-from-ollama-or-vllm.md) put Ollama and vLLM
behind one seam and split each of them across two modules: `config.PROVIDER_DEFAULTS`
held the model and the address, because reading the environment is the config's
job, and `providers.PROVIDERS` held the behaviour, because acting on a config is
the provider's. Neither could import the other's half, so every server's name was
written twice and `tests/test_conventions.py` existed to check the two agreed.

That split was defended on layering, and the layering was never actually at risk.
The dependency runs one way -- `config` imports `providers` -- so a default read
in `providers.py` breaks nothing; what a server falls back to is a fact about that
server, the same kind of fact as which exceptions mean it is down. Written twice,
it cost a table, a convention test, and an index-based read
(`PROVIDER_DEFAULTS[name][0]` is the model) in `--help`.

The four module-level functions the seam was reached through --
`build_chat_model`, `list_models`, `unavailable_hint`, `transport_errors` -- had
the same shape: look the provider up by the name on the config, then call one of
its fields with that config. Four public names, four docstrings, and four places
a fifth question would have to be added, for one lookup.

## Decision

**One row per model server, in `providers.PROVIDERS`, holding both what it
defaults to and how it is talked to.** `Provider` gains `model`, `base_url` and
`api_key`, read from that server's own environment variables
(`$OLLAMA_MODEL`/`$OLLAMA_HOST`, `$VLLM_MODEL`/`$VLLM_HOST`/`$VLLM_API_KEY`)
beside the behaviour that uses them. `config.PROVIDER_DEFAULTS`,
`config.DEFAULT_MODEL`, `config.DEFAULT_BASE_URL`, `config.VLLM_MODEL`,
`config.VLLM_BASE_URL` and `config.VLLM_API_KEY` are gone. `provider_options()`
moves to `providers.py`, next to the rows it renders. `$BUY_AGENT_PROVIDER` stays
in `config.py`, because which server to use is not one server's fact.

**`AgentConfig.model_server` is the one way anything reaches a provider.** It
resolves `config.provider` through `provider_for`, and `__post_init__` fills the
model, the address and the key from it -- three fields that could not be plain
defaults for the reason [ADR-0012](0012-the-browser-decides-nothing.md) gives, an
empty one being "unset" and the right value depending on a sibling field. The
four pass-through functions are deleted: `BuyAgent` reads
`config.model_server.chat_model(config)`, catches
`config.model_server.transport_errors` and raises with
`config.model_server.hint(config, exc)`; `api.installed_models` asks
`config.model_server.installed(config)`.

**The key is the provider's, not the run's.** `AgentConfig.api_key` defaults to
the empty string and takes vLLM's `$VLLM_API_KEY` only when the run is a vLLM run,
so an Ollama config no longer carries a secret it has no notion of. It is still
read from the environment and from nowhere else -- no flag, no form field, and
not in `provider_options()`.

**A hint that is the same sentence on both servers is written once.**
`_too_slow_hint` and `_unreachable_hint` are shared; each provider's own function
keeps only the cases that really are its own -- a tag to pull for Ollama, a
refused key and a server serving one other model for vLLM. The remedy a timeout
suggests is read off `takes_num_ctx`, because that is exactly the difference:
where the window is a per-request setting there is a smaller one to ask for, and
where the server fixed it at startup there is only a shorter prompt to send.

ADR-0028 otherwise stands. The seam, the four questions a provider answers, the
tri-state `reasoning`, `takes_num_ctx` and `ModelUnavailableError` are unchanged;
what this record revises is where a provider's defaults live and how the seam is
reached.

## Consequences

A third model server is one row in one table, plus the two functions its row
points at. Nothing else has to be edited: `--provider`'s choices,
`api.PROVIDER_OPTIONS`, the form's picker, `--help`'s per-provider defaults and
`_default_base_url` all read `PROVIDERS`, and the convention test that the two
tables agreed is deleted because there is no second table to disagree with.

The cost is that `providers.py` now reads `os.getenv` at import, which `config.py`
used to be the only module to do. That is a real narrowing of a rule that was easy
to state; it buys a name written once, and the module still imports nothing from
`config`.

What this obliges:

- **Nothing outside `providers.py` may branch on a provider's name.**
  `config.model_server` is the only lookup, and everything a caller needs is a
  field on the row it returns. An `if provider == "vllm"` anywhere above that line
  is the thing this record exists to prevent -- add a field to `Provider` instead,
  the way `takes_num_ctx` was added.
- **A row's defaults are read once, at import.** Tests that move
  `$OLLAMA_MODEL` or `$VLLM_HOST` reload `buy_agent.providers`, not
  `buy_agent.config`. Reloading the table is enough: `provider_for` reads it out
  of the module's own globals, so a config built afterwards sees the new rows.
- **`api_key` stays out of anything a browser is handed.** It is a field on a
  provider row now, which is also what `provider_options()` renders from, so the
  test that the payload never carries it is the one that keeps the secret in.
- **A shared hint must stay true of both servers.** `_too_slow_hint` reads the
  label and `takes_num_ctx` off the row rather than naming a server, and
  `_unreachable_hint` is given the command to print. A third provider whose
  timeout needs different advice puts that advice on its row, not an `if` in the
  shared helper.
