# ADR-0081: Reach TensorRT-LLM as a fourth model server, through vLLM's client

- **Status:** Accepted
- **Date:** 2026-10-10

## Context

An NVIDIA box may already run [TensorRT-LLM](https://nvidia.github.io/TensorRT-LLM/)
behind `trtllm-serve`, an OpenAI-compatible server for one model, chosen when it
starts. A LiteLLM proxy can already reach it
([ADR-0068](0068-reach-a-litellm-proxy-as-a-third-model-server.md)), but putting a
router in front of one server just to translate a protocol it already speaks
adds a process without adding anything.

`trtllm-serve` takes the same request vLLM does: the OpenAI chat API,
`response_format` with a JSON schema, `chat_template_kwargs` for the template's
`enable_thinking`, and `/v1/models` for a listing. One difference matters here.
It constrains decoding only when started with a guided-decoding backend
(`guided_decoding_backend: xgrammar` in the file handed to
`--extra_llm_api_options`). Without one, the schema
[ADR-0004](0004-json-schema-and-sentinels.md) relies on is either refused or not
enforced.

Hugging Face's TGI was the other candidate. It was turned down because it is
in maintenance mode and has historically spelled its grammar its own way, so it
would need a client of its own.

## Decision

`providers.PROVIDERS` gains a row, `trtllm`, built almost wholly from vLLM's
parts:

- The chat call is vLLM's, renamed `_templated_chat_model` now that two rows
  share it. The listing is `_openai_models`.
- The defaults come from `$TRTLLM_MODEL`, `$TRTLLM_HOST` and `$TRTLLM_API_KEY`:
  `Qwen/Qwen3-8B`, `http://localhost:8000/v1` (vLLM's port too) and no key.
  `trtllm-serve` checks no key itself, so the key is for whatever stands in
  front of it.
- `takes_num_ctx` and `takes_cpu_only` are false, since both are fixed when it
  starts.
- vLLM's hint becomes `_one_model_hint(label, serve, key)`, which both rows
  build. TensorRT-LLM's adds one sentence ahead of it: an answer from the server
  that mentions guided decoding is told how to turn it on. Its `more_room` also
  names guided decoding, because a server without it answers in free text, and
  that free text lands on the unreadable-answer hint.

## Consequences

`--provider trtllm` works without changing `requirements.txt` or the UI. The UI
gets the row from `provider_options()`. What it obliges:

- `__main__`'s help, `scripts/start.ps1` and the `Dockerfile` now name four sets
  of variables, as ADR-0068 asks.
- The guided-decoding sentence matches the server's wording ("guided"), not a
  status code. If a release words that refusal differently, it falls through to
  the shared hints, and the unreadable-answer hint still names the remedy.
- The live suite stays Ollama's
  ([ADR-0026](0026-integration-tests-against-a-tiny-cpu-model.md)). TensorRT-LLM
  needs an NVIDIA GPU, so this row is asserted in `tests/test_providers.py`
  alone, the same gap ADR-0028 and ADR-0068 name for vLLM and LiteLLM.
