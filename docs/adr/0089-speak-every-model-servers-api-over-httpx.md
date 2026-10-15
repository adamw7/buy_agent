# ADR-0089: Speak every model server's API over httpx, and drop the openai client

- **Status:** Accepted
- **Date:** 2026-10-15

## Context

[ADR-0088](0088-speak-ollamas-api-over-httpx.md) replaced the `ollama` package
with `providers.OllamaClient` over `httpx`, and kept the `openai` package for the
three rows that speak the OpenAI chat API: vLLM, a LiteLLM proxy and
TensorRT-LLM. It said that client earned its place.

Measured, it earned less than `ollama` had. The package was used for one call,
`chat.completions.create`, and for its error classes, which the hints read. Both
listings (`/models`, LiteLLM's `/model/info`) had always gone through
`httpx.get`. What it brought with it was five more packages: `httpx2` and
`httpcore2`, a second copy of the HTTP stack we already pin, plus `jiter`
(compiled), `sniffio` and `truststore`. Because `httpx2` logs under its own name,
`logging_setup` had to quiet it too, and a missed name was once a dozen lines per
call above the report. It also defaulted to two retries that ADR-0051 had to turn
off, and its errors stringified as `Error code: 404 - {...}`, a dict repr that
the hints then quoted to the shopper.

## Decision

`providers.OpenAIClient` speaks the OpenAI chat API over an `httpx.Client`, and
nothing imports `openai`. It makes one call, `POST {base_url}/chat/completions`,
with the body `_OpenAIChat` builds (model, messages, temperature,
`response_format`, and the row's thinking keys merged in at the top level, where
the package's `extra_body` put them). It sends the key as a bearer token only
when there is one, follows redirects and never retries. It answers the first
choice as a `Completion`, or `None` when there is none.

Both clients fail the same way. `chat.ServerError` carries what the server
answered in place of a reply, in its own words (Ollama's `error`, the OpenAI
API's `error.message`, vLLM's top-level `message`, a framework's `detail`, else
the body, else the reason phrase), plus the HTTP status. A success that is not a
JSON object is a `ServerError` too. It lives in `chat.py`, the model seam,
rather than in `providers.py`, because tests reload `providers` and a class
defined there would stop being the class raised. Refusals, timeouts and dropped
connections stay raw `httpx` errors. Every row's `transport_errors` is
`providers._TRANSPORT`.

A refused key is a `ServerError` with status 401 (`_refused_key`). A hint that
names a model still requires that the client raised the error
(`_answered_by(exc)`, now asking only whether it is a `ServerError`), so a bare
404 from a listing still points at the address.

Everything else ADR-0038 and ADR-0088 decided stands. `buy_agent/chat.py` is the
whole model-facing seam: a `Prompt` with `str.format` holes, a one-method
`ChatModel`, a `Chain`, and `read_answer` raising `UnreadableAnswerError`. A
model stand-in is a class with `answer`, and a chain stand-in one with `invoke`.
`OllamaClient` is unchanged except for the error it raises.

## Consequences

The runtime install falls from 21 packages to 15: `ddgs`, `pydantic`, `httpx`
and `lxml`, and what those need. One HTTP stack remains, so `logging_setup`
quiets `httpx` and holds `httpcore` down, and nothing else.

The wire format of all four servers is now this repository's to keep up with.
The OpenAI chat API is the most widely copied API there is, so the risk is low,
but a field a server renames now arrives as an empty or unreadable answer rather
than as a client upgrade. `integration/` against a real model is the check, and
it only covers Ollama. A vLLM, LiteLLM or TensorRT-LLM change shows up when
someone runs against one.

Obligations:

- **Tests patch `providers.OpenAIClient`** (its fake has `complete(body)` and
  `close`) and `providers.OllamaClient`, never a library's class. Both clients
  are tested against `httpx.MockTransport` in `tests/test_providers.py`,
  asserting on the requests.
- **A failure the clients invent is a `ServerError`**, and `_TRANSPORT` names
  it, or a misbehaving server reads as the shopper's bad request.
- **Hints quote `ServerError`**, whose text ends `(status code: N)`. A hint
  that wants only the server's words reads `exc.error`, as `_no_key_store` does.
- **Never retry** (ADR-0051). Nothing in `OpenAIClient` or `OllamaClient` may
  loop over a failed call.
