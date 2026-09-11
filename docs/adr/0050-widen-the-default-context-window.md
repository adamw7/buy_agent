# ADR-0050: Widen the default context window to hold the answer as well as the prompt

- **Status:** Accepted
- **Date:** 2026-09-11

## Context

[ADR-0019](0019-default-to-a-thinking-model.md) made the settings a thinking
model needs the defaults: `reasoning=False`, and a window of 8192 tokens where
Ollama's own is 4096. The number was chosen against an extraction prompt of
~3.3k tokens, which left roughly 4.9k for whatever the model wrote back.

Both halves have moved since, and only one of them upward in the record.
[ADR-0024](0024-read-and-quote-what-the-sources-say.md) added the opinion sweep,
taking the prompt to ~4.3k -- it says so, and says the 8192 still leaves room to
answer. What it did not count is that the same change grew the *answer*: a
product is no longer four figures and a name but a name, a price, a currency, a
rating, a review count, a URL and the sentences a page printed about it, each
quoted verbatim. Ten of those -- `num_products` ships as 10 -- is comfortably a
couple of thousand tokens of JSON. So the window now has to hold ~4.3k of prompt
and ~2k of answer, and at 8192 what is left is under 2k.

That margin is spent by anything ordinary. Pages denser than the fabricated
corpus, a request whose results are long-form reviews, a shopper who asked for
`--think` back on a model that reasons before it copies: each ends the stream
part-way through an object. The failure is the one ADR-0019 was written about
and is no better for being diagnosed -- `chat.UnreadableAnswerError` becomes a
`ModelUnavailableError` carrying `_unreadable_hint`, which tells the shopper to
type a larger `--num-ctx`. A default that works only until the answer gets long
is a default that teaches the shopper a flag.

Nothing about that is the browser's to fix. The form's context field shows the
default the server sent as its placeholder and sends nothing when it is blank
(ADR-0019, ADR-0012), so what the UI offers is whatever `config.py` says.

## Decision

`AgentConfig.num_ctx` defaults to `16384`, where it was `8192`.

Everything else about the setting is unchanged. It is still Ollama's alone --
vLLM fixes its window with `--max-model-len` at startup, which
`Provider.takes_num_ctx` declares -- still overridable by `--num-ctx` and by the
form's Context window box, and `None` is still the third state a Python caller
asks for to leave the server's own behaviour alone.

The number is written once, in `config.py`. `--help` interpolates it,
`api.defaults_payload` sends it, and the form's placeholder names it, so no
front end is edited to widen a window.

## Consequences

A run of ten dense pages has room for the JSON describing all ten, and a model
asked to think has room to think before it writes them. That is what this buys.

It costs memory on the shopper's machine: Ollama sizes the KV cache from
`num_ctx`, so the default asks for twice the cache it did, on a 12B model that
was already the bigger half of ADR-0019's bill. The way down is the flag that
was always there -- `--num-ctx 8192`, or lower for a model that cannot think and
does not need the room.

What it obliges:

- **ADR-0019 is not superseded.** The decision it records -- default to a
  thinking model and ship the settings it needs -- is what this record applies,
  exactly as ADR-0019 moved the tag ADR-0003 happened to name without
  superseding it. Only the number has moved.
- **The default stays written once.** The tests that assert it are the ones
  that are *about* the default, in `tests/test_config.py`, `tests/test_cli.py`
  and `tests/test_agent.py`; every other test naming a window passes its own. A
  place restating the number goes stale without failing anything.
- **The prose that quotes it is edited in step.** `README.md`'s flag table and
  its "Thinking models" section, `CLAUDE.md`, `docs/testing.md` and
  `integration/conftest.py`'s arithmetic each name the number, because what they
  are explaining is why it is that number and not another.
- **The room is not a substitute for the prompt fitting.** `fetch.condense`
  still cuts pages to `page_chars` and `opinion_chars`, and extraction is still
  schema-constrained (ADR-0004). A window wide enough to hide a prompt that grew
  by another kilobyte would only move this record's problem to the next one.
