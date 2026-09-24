# ADR-0068: Reach a LiteLLM proxy as a third model server, and never its SDK

- **Status:** Accepted
- **Date:** 2026-09-24

## Context

Teams that run a GPU box often put a [LiteLLM](https://docs.litellm.ai) proxy in
front of it: one OpenAI-compatible address routing aliases to Ollama, vLLM or
hosted APIs, with fallbacks and virtual keys. Pointing this agent past the
router, at the server behind it, throws away what the router is for.

LiteLLM has two forms. The **proxy** is a server that speaks the OpenAI chat API
and translates `response_format` for whatever it routes to. The `openai` client
already used for vLLM ([ADR-0028](0028-serve-the-model-from-ollama-or-vllm.md))
talks to it unchanged. The **SDK** is `litellm.completion(...)` in-process,
bringing `tiktoken`, `tokenizers`, `aiohttp` and more: the framework between
prompt and answer that
[ADR-0038](0038-own-the-model-call-instead-of-a-framework.md) removed.

A proxy may also forward a request to a hosted API, and a shopping request is
the personal text [ADR-0003](0003-local-ollama-no-api-keys.md) kept local.

## Decision

`providers.PROVIDERS` gains a row, `litellm`, reached through the `openai`
client. Its chat call and `/models` listing are shared with vLLM's row. The SDK
is not used.

- The defaults come from `$LITELLM_MODEL`, `$LITELLM_HOST` and
  `$LITELLM_API_KEY`: `local_model`, a placeholder for an alias in somebody's
  `model_list`, then `http://localhost:4000/v1`, and no key.
- `takes_num_ctx` and `takes_cpu_only` are false, since both belong to what the
  proxy routes to. `reasoning` is sent as LiteLLM's `reasoning_effort`, and
  `None` sends nothing.
- The listing reads each alias's `mode` off `/model/info`
  ([ADR-0032](0032-say-which-models-can-answer-a-prompt.md)). If that endpoint
  refuses, the listing falls back to `/v1/models`.
- The hint separates four failures: a refused key, an alias the proxy does not
  route, a failure it relays, and nothing answering.
- `Provider.more_room` is a new field on every row: each server's own ending for
  the unreadable-answer hint, which had named vLLM's `--max-model-len` for every
  server without a per-run window.

ADR-0003 stands. Nothing here defaults to a hosted model: where a proxy
forwards a request is decided by its owner's `config.yaml`, as with pointing
`$OLLAMA_HOST` at another machine.

## Consequences

A LiteLLM proxy is reachable with `--provider litellm`, and `requirements.txt`
does not change. What it obliges:

- A sentence one server words its own way belongs on the row, as `more_room`
  does, not in a shared hint that branches on a declared capability.
- Every place that names the servers' variables by hand names all three:
  `__main__`'s help, `scripts/start.ps1` and the `Dockerfile`.
- Structured output depends on what the proxy routes to. Where that model cannot
  honour `response_format`, the unreadable-answer hint is what reports it.
- The live suite stays Ollama's
  ([ADR-0026](0026-integration-tests-against-a-tiny-cpu-model.md)): a proxy
  there would test the proxy's translation, not this code. The row is asserted
  in `tests/test_providers.py` alone, a gap named here as ADR-0028 named vLLM's.
