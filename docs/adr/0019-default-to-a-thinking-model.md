# ADR-0019: Default to a thinking model, and default the settings it needs to answer

- **Status:** Accepted
- **Date:** 2026-08-26

## Context

ADR-0003 decided the model is a local one served by Ollama, and named `llama3.2`
as the tag `AgentConfig.model` defaults to. Everything else in this codebase is
built to compensate for that being small: no tool loop (ADR-0002), a decoding
grammar and sentinels rather than nullable fields (ADR-0004), pages condensed so
the prompt fits (ADR-0005), every figure checked against the sources afterwards
(ADR-0006).

What has moved since is what people have pulled. The tags worth running now --
`gemma4`, `qwen3.5`, `lfm2.5`, anything listing the `thinking` capability --
think by default, and a thinking model fails this pipeline in a way that looks
like nothing at all. The extraction prompt runs to ~3.3k tokens; on Ollama's own
4096-token window the model spends the ~800 tokens left reasoning about what is a
copying task, and is cut off before it emits a single character of JSON. There is
no exception and no error line: extraction returns nothing, the run reports no
products, and the shopper is given no reason.

The two settings that fix it already existed -- `--num-ctx` and `--think`, both
added for exactly this (ADR-0003's consequences) -- and both defaulted to `None`,
which means "send nothing and leave the model alone". So the failure was reachable
by following the README: pull the tag it names, type the request it shows, and get
an empty answer until you find two flags nothing had told you to type.

A default is not just a value. It is the configuration a first run gets, and the
first run is the one that decides whether there is a second.

## Decision

`DEFAULT_MODEL` is `gemma4:12b`, still overridden by `$OLLAMA_MODEL`. Because
that model thinks, the two settings that make it answer travel with it as
defaults rather than as advice: `AgentConfig.reasoning` is `False` and
`AgentConfig.num_ctx` is `8192`, where each used to be `None`.

The tri-state that `reasoning` carries is unchanged. `None` still means "send
nothing and leave the model's own behaviour alone", `False` means "thinking off",
`True` means "thinking on" -- what moved is only which of the three is the
default. `None` is now something a caller asks for rather than something they
fall into, and it is reachable only through `AgentConfig(num_ctx=None,
reasoning=None)` in Python: `--think`/`--no-think` is a two-valued
`BooleanOptionalAction`, `--num-ctx` takes an int, and a blank field in the API
means "use the default" (ADR-0012's rule for empty values), so neither front end
can spell the third state. That is deliberate -- "leave the model alone" is a
library-level escape hatch for a model whose own behaviour is already right, not
a choice worth putting in a form.

Nothing the small-model decisions put in place is relaxed because the default tag
got bigger. Extraction stays schema-constrained, every figure stays grounded, and
every judgement stays in Python.

## Consequences

A cold start works with nothing typed: pull the tag the README names, ask for a
product, get an answer. That is what this buys, and it is the whole reason for it.

It costs a bigger download and a slower run than a 1B tag did, and it asks Ollama
for an 8192-token window even when serving a model that would have been fine at
4096. A model that cannot think ignores `reasoning` entirely, so the way back down
to a smaller tag is `--model` alone, with no second flag to remember.

What it obliges:

- **A default is written once, in `config.py`, and read everywhere else.** This
  decision is what makes that rule bite: `--help` interpolates
  `_DEFAULTS.num_ctx`, `api.defaults_payload` sends the dataclass's own values,
  and the UI's context field shows the default the server sent as its placeholder
  -- it previously hardcoded "Ollama's own (4096)", which this change silently
  made a lie. A default restated as a literal anywhere goes stale without failing
  anything.
- **Tests name `DEFAULT_MODEL`, not a tag.** Using the literal string as the
  marker that a defaults payload came back makes every future change of default a
  test edit; the server tests import the constant instead.
- **`None` must stay reachable and stay tested.** The tests that covered "send
  nothing" by being the default now ask for it explicitly. If a later change
  collapses the tri-state to a boolean, a model with the right behaviour of its
  own loses the only way to keep it.
- **The pipeline still has to work on a small model.** The compensating machinery
  in ADR-0002 and ADR-0004 through ADR-0006 is not softened by a larger default,
  because `--model` still points anywhere and the shopper's machine still decides
  what fits on it.

ADR-0003 is not superseded: the decision it records -- local Ollama, no accounts,
no keys -- stands unchanged, and only the tag it happened to name has moved.
