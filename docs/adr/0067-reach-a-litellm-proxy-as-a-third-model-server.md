# ADR-0067: Reach a LiteLLM proxy as a third model server, and never its SDK

- **Status:** Accepted
- **Date:** 2026-09-24

## Context

[ADR-0028](0028-serve-the-model-from-ollama-or-vllm.md) let a run talk to a vLLM
somebody already runs, and [ADR-0029](0029-one-table-per-model-server.md) made
each server one row in one table. The next server people already run is not
another inference engine but a router: [LiteLLM](https://docs.litellm.ai), which
puts Ollama, vLLM, llama.cpp, LM Studio and a hundred hosted APIs behind one
address, with fallbacks, load balancing and per-person virtual keys. A team with
a GPU box often has one in front of it, and asking that team to point this agent
past their router at the server behind it throws away what the router is for.

LiteLLM comes in two shapes, and they are different decisions:

- **The proxy** is a server. It speaks the OpenAI chat API at `/v1`, routes by
  the alias a request names, and translates `response_format` into whatever
  structured output the model it routes to understands. The `openai` client this
  project already imports for vLLM talks to it unchanged.
- **The SDK** is a library, `litellm.completion(...)`, doing that routing inside
  the calling process. It installs `tiktoken`, `tokenizers`, `aiohttp`, `jinja2`,
  `click` and more -- the shape of dependency
  [ADR-0038](0038-own-the-model-call-instead-of-a-framework.md) removed
  LangChain for, a framework between the prompt and the answer, for a surface of
  one call.

The proxy also reopens a question ADR-0028 could close in one sentence. A vLLM
serves a model on a machine somebody controls. A LiteLLM proxy may forward a
request to a hosted API, and a shopping request is exactly the personal text
[ADR-0003](0003-local-ollama-no-api-keys.md) kept on the machine.

Two settings are also shaped differently behind a router. The context window and
the device belong to whatever the proxy routes to, which the proxy does not pass
on as a per-request option every backend reads. And the thinking switch vLLM
takes as `chat_template_kwargs.enable_thinking` means nothing to most of what a
proxy can route to; LiteLLM's own provider-neutral spelling is
`reasoning_effort`.

## Decision

`providers.PROVIDERS` gains a third row, `LITELLM`, named `litellm`. It reaches
the proxy through the same `openai` client vLLM's row uses, so the chat call and
the plain `/models` listing are shared code (`_OpenAIChat`, `_openai_models`)
rather than a copy. **The SDK is not used**, and adding it would need a record
superseding this one and ADR-0038.

- Its defaults come from its own variables: `$LITELLM_MODEL`, `$LITELLM_HOST`
  (`http://localhost:4000/v1`, which is what `litellm --config` binds) and
  `$LITELLM_API_KEY`, a master key or a virtual key. The key has no flag and no
  form field, for the reason `$VLLM_API_KEY` has neither.
- The model default is `local_model`, a placeholder. A LiteLLM model name is an
  alias out of somebody's own `model_list`, so any real name would be a guess at
  somebody's config.
- `takes_num_ctx` and `takes_cpu_only` are false. `reasoning` is sent as
  `reasoning_effort` (`"medium"` for on, `"none"` for off), and nothing is sent
  for `None`.
- The listing asks `/model/info`, which reports each alias's `mode`. So an
  embedding alias is marked in the picker, which is the question
  [ADR-0032](0032-say-which-models-can-answer-a-prompt.md) answers for Ollama's
  tags. A proxy that will not answer it falls back to `/v1/models`, every entry
  offered, since "cannot say" is not "cannot run".
- The hint separates four failures. A refused key names `$LITELLM_API_KEY`. An
  alias the proxy does not route names what it does route and its
  `config.yaml`. A failure the proxy relays is blamed on the server behind it.
  Nothing answering at all says `litellm --config config.yaml`. The model-naming
  branches need the OpenAI client to have raised the error, as
  `_answered_by` already requires.
- `Provider.more_room` is a new field on every row. It holds the tail of the
  sentence `_unreadable_hint` writes, which used to decide between two wordings
  by `takes_num_ctx` and named vLLM's `--max-model-len` for every server that
  took no window.

**On ADR-0003:** it is not superseded. Nothing here defaults to a hosted model
or ships a key to anybody's service: the default address is a proxy on this
machine, and where that proxy forwards a request is decided by the `config.yaml`
its owner wrote, not by this project. A proxy routing to a local Ollama or vLLM
is inside ADR-0003 for the reason ADR-0028 gave. A proxy routing to a hosted API
is a choice that person made about their own requests, which is the same
position as pointing `$OLLAMA_HOST` at somebody else's machine.

## Consequences

Somebody who already runs a LiteLLM proxy points this agent at it with
`--provider litellm` and gets whatever that proxy routes to, with its fallbacks
and its keys, at no cost in dependencies: `requirements.txt` does not change.

What it obliges:

- **A server-specific sentence belongs on the row.** `more_room` is the pattern.
  A shared hint that branches on a row's declared capability has to be re-read
  when a third row gives the same declaration a different meaning.
- **Every door that names the servers' variables by hand names all three.**
  `--model` and `--base-url` help in `__main__`, `scripts/start.ps1` and the
  `Dockerfile`'s `LITELLM_HOST`. The `--num-ctx` and `--cpu-only` help also has
  to say that a proxy leaves both to the server it routes to.
- **Structured output depends on what the proxy routes to.** Where that model
  cannot honour `response_format`, the answer comes back unreadable and
  `_unreadable_hint` says so, as it does for any server. This project does not
  check the proxy's routing ahead of time.
- **The live suite stays Ollama's**
  ([ADR-0026](0026-integration-tests-against-a-tiny-cpu-model.md)). A proxy in
  that job would test the proxy's translation of a schema to Ollama, which is not
  this project's code. LiteLLM's half is asserted in `tests/test_providers.py`
  alone, which is a gap and is named here as one, as ADR-0028 named vLLM's.
