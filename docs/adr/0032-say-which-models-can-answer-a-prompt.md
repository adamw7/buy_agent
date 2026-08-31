# ADR-0032: Ask Ollama what each model can do, and mark the ones that cannot answer

- **Status:** Accepted
- **Date:** 2026-08-31

## Context

The model picker exists so that a shopper does not have to know what is
installed. `GET /api/models` asks the provider's row what the server is serving,
and the form turns the answer into a `<select>`.

For vLLM that answer is complete: the process serves the one model it was
started for, so everything `/v1/models` reports is something a run can use.

For Ollama it was not. `_ollama_installed` returned every tag `ollama list`
prints, and a machine doing anything with local RAG has embedding models pulled
alongside the chat ones -- `nomic-embed-text`, `mxbai-embed-large`, `all-minilm`.
A listing of bare names cannot tell those apart from a chat model, so the picker
offered them. Choosing one cost a whole run: the search, the fetches and the
condensing all happen before the first prompt, and the failure that came back
was a sentence from the model server that nothing on the form had predicted.

Worse, that sentence reached the shopper wearing the wrong remedy. Ollama refuses
with `"nomic-embed-text" does not support chat`; `_ollama_hint` matched neither
its timeout branch nor its "not found" branch and fell through to
`_unreachable_hint`, which says "Could not reach Ollama at ... Start it with:
ollama serve". The server had answered. Starting another one is no help, and it
sends someone to look at the wrong thing.

What makes this awkward is that `ollama list` does not say what a tag can do.
`ollama show <tag>` does -- its `capabilities` are `completion`, `embedding`,
`tools`, `vision`, `thinking` -- but that is one call per tag, on a listing asked
for while a form renders, against `_LIST_TIMEOUT`'s deliberate five seconds.

Three answers were considered.

**Ask lazily, on change.** Keep the listing as it is and check only the chosen
model. One call instead of N, and it moves the check to where the choice is made.
Rejected because it does not fix the dropdown: the shopper still reads a list in
which nothing distinguishes a model that works from one that cannot, and the
answer arrives after they have picked. It also puts a second question on the
address the form is pointed at, on a keystroke rather than on a render.

**Say it only in the failure.** Cheapest, and it is half of what was taken --
but on its own it still spends a run to say it, which is the cost this is about.

**Guess from `ollama list`.** `details.family` reads `nomic-bert` and `bert` for
the usual embedding models. Rejected: a list of families this project maintains
by hand is a rule about this project rather than about the server, and it would
be wrong in whichever direction is not tested -- silently, on a tag nobody here
has seen.

## Decision

**`Provider.installed` answers with models rather than names.**
`providers.InstalledModel` carries a `name` and a `completion` flag, and the two
rows fill it in their own way: vLLM's listing sets `completion=True` for
everything it reports, because a served model was chosen to be served; Ollama's
asks `ollama show` per tag and reads its `capabilities`. That keeps the
difference between the two servers where ADR-0029 put it, on the row, rather than
in the caller.

The N extra calls go out **together**, through a `ThreadPoolExecutor` capped at
`_PROBES`, and the client is built with `timeout=_LIST_TIMEOUT` -- which Ollama's
listing did not have before, having only ever made one call. The budget is for
the whole listing, since it is one form field's worth of waiting either way.

**A probe that fails means "cannot say", never "cannot run".** A `show` that
raises, and a `capabilities` that is absent because the Ollama is older than the
field, both leave `completion=True`. The point of asking is to keep a model that
cannot possibly work out of the shopper's way; hiding a working one on the
strength of a failed probe is the worse mistake, and this is the call that fills
the picker.

**The form marks rather than hides.** `api.model_payload` sends `{name,
completion}`, `agent.types.ts` mirrors it, and `ModelOption` gains a `note` --
the empty string for an ordinary choice, `— not served` for a chosen name the
server does not have, `— embedding only` for one it has that cannot answer. This
is the shape the dropdown already had for "not served", and it keeps a pull made
by mistake visible: dropped from the list, a model someone deliberately pulled
would simply be missing, with nothing to say why. Which entries get which note is
Python's answer, not the browser's (ADR-0012); the wording of the suffix sits in
the template beside the one that was already there.

**`_ollama_hint` gains the branch that failure deserves.** A message containing
"does not support" is Ollama saying the tag is there and has no completion to
give, so the hint says exactly that and offers the installed models that *can*
answer -- `_listed(config, completing=True)`, narrowed for this one message only.
Listing the embedding models back to someone whose run just failed on one would
repeat the mistake in the sentence written to explain it.

## Consequences

**Every Ollama listing is now N+1 calls rather than one.** They are local, they
are concurrent and they are capped by one timeout, but the picker is no longer a
single round trip. A machine with many tags pays a little more to render the
form; that is the price of the form being right.

**`installed` is a typed shape now, and three callers had to follow.**
`api.installed_models` sends objects, `providers._listed` joins `model.name`, and
`integration/conftest.py` reads the names out before checking whether the tiny
model is pulled. `demo/server.py`'s scripted answer is shaped the same way, since
it stands in for the same endpoint.

**`ModelStatus.models` changed shape on the wire.** It is `InstalledModel[]`
rather than `string[]`, and
`test_an_installed_model_is_mirrored_field_for_field_in_typescript` is what keeps
the two sides of that boundary together. A page from an older build talking to a
newer server would render `[object Object]`; both are served from the same
origin, built together, so there is no version of that pairing to support.

**The capability vocabulary is Ollama's, and this depends on two words of it.**
`completion` in `capabilities`, and `does not support` in a refusal. Either could
be renamed by an Ollama release, and no unit test would notice -- the fakes would
go on saying what they were told to say. `integration/`'s
`test_a_live_listing_says_which_models_can_answer_a_prompt` is the one that
would, which is the same bargain ADR-0026 makes for `method="json_schema"`
decoding: the nightly run against a real model is where a moved server contract
shows up.

**A model that can be prompted but is bad at this is still offered.** Nothing
here judges quality -- a vision-only tag that reports `completion`, or a 0.5B
model that cannot hold the extraction prompt, is a choice the shopper is left to
make. The line drawn is "cannot answer a prompt at all", because that one is a
fact the server states rather than an opinion this project would be forming.
