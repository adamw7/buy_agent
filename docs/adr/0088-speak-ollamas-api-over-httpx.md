# ADR-0088: Speak Ollama's API over httpx, and drop its client

- **Status:** Accepted
- **Date:** 2026-10-15

## Context

[ADR-0038](0038-own-the-model-call-instead-of-a-framework.md) dropped LangChain
and called each model server's own client directly: `ollama` for Ollama, `openai`
for vLLM, a LiteLLM proxy and TensorRT-LLM. The second half of that held up. The
OpenAI client is how three rows speak one API, with typed errors this project's
hints read and a request shape that is genuinely fiddly to get right by hand.

The `ollama` package earns far less. Four calls are made of it -- `chat` without
streaming, `show` for a tag's capabilities, `pull` and `list` in
`scripts/update_ollama.py` -- and each is one HTTP request to Ollama's own API.
The model listing had already moved off it: `_ollama_tags` asks `/api/tags`
through `httpx.get`, so the address is parsed by `_ollama_url` either way. What
the package added on top of `httpx`, which it is built on and which we pin
anyway, was a pydantic model per response and one translated failure (a refused
connection became a builtin `ConnectionError` with a download link in it).

So it was one more pin to audit, and one more release line whose changes reach
the default provider, for roughly sixty lines of code that are ours to read.

## Decision

`providers.OllamaClient` speaks Ollama's API over an `httpx.Client`, and nothing
imports `ollama`. It makes exactly the calls this project needs: `chat` (one
`POST /api/chat` with `stream: false`, sending `think` only when it is not
`None`), `show`, `tags` and a streaming `pull`, plus `close`. It follows
redirects and has no deadline unless given one, as the package's client did.

Whatever answers in place of a reply is `providers.OllamaError`, carrying
Ollama's own words (its JSON `error`, else the body, else the reason phrase) and
the HTTP status, and written as the package wrote `ResponseError` so hints read
the same. A success that is not a JSON object -- a captive portal, a proxy's
page -- is an `OllamaError` too, never a `ValueError` that would escape a run as
the shopper's mistake. Refusals, timeouts and dropped connections stay raw
`httpx` errors. `OLLAMA.transport_errors` names both.

Everything else ADR-0038 decided stands unchanged: `buy_agent/chat.py` is the
whole model-facing seam (a `Prompt` with `str.format` holes, a one-method
`ChatModel`, a `Chain`, and `read_answer` raising `UnreadableAnswerError`); a
model stand-in is a class with `answer`, a chain stand-in one with `invoke`;
the vLLM, LiteLLM and TensorRT-LLM rows call the `openai` client directly.

## Consequences

One package fewer in `requirements.txt` and in the image, and none fewer in
practice beneath it: `httpx` and `pydantic` were already ours.

The wire format is now this repository's to keep up with. Ollama's API is
stable and versioned by the server, but a renamed field in a chat or `show`
answer now surfaces as an empty reply or an unreadable answer rather than as a
client upgrade. The `integration/` suite against a real Ollama is the check.

Obligations:

- **Tests patch `providers.OllamaClient`**, not a library's class, and fakes
  answer what it answers: `chat` an `OllamaReply`, `show` a list of capability
  names or `None`. The client itself is tested against `httpx.MockTransport` in
  `tests/test_providers.py`, asserting on the request bodies.
- **A failure the client invents is an `OllamaError`.** Anything that would
  otherwise raise out of `_read` or `_decoded` must stay inside
  `transport_errors`, or a misbehaving server reads as a bad request.
- **`scripts/update_ollama.py` uses the same client**, with no deadline, so a
  pull's error vocabulary is the run's.
