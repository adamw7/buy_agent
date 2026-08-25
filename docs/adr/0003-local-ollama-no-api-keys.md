# ADR-0003: Run against a local Ollama model, with no accounts or API keys

- **Status:** Accepted
- **Date:** 2026-08-25

## Context

A shopping request is personal -- a budget, a health condition implied by the
product, a gift for someone. Sending it to a hosted model means it leaves the
machine and is retained by somebody else. It also means an API key, an account,
and a per-run cost, which together are enough friction to stop anyone trying the
thing out.

A hosted frontier model would extract products more accurately than a 1.2B local
one. That is a genuine loss, and it is the trade being made.

## Decision

The model is whatever is served by Ollama, reached through `langchain-ollama`.
`AgentConfig.model` and `AgentConfig.base_url` default to `llama3.2` and
`http://localhost:11434`, overridable by `$OLLAMA_MODEL` and `$OLLAMA_HOST`.
Search is DuckDuckGo through `ddgs`, which needs no key either. Pages are
fetched directly with `httpx`.

Nothing about a run leaves the machine except the search terms and the page
fetches themselves. There is no telemetry, no account, and no configuration
that could acquire one.

The LLM stays behind LangChain's `BaseChatModel` interface rather than being
called through the `ollama` client directly, so the choice is not welded in: the
`llm=` argument to `BuyAgent` takes any chat model, which is also the seam the
tests inject a fake through. The one place that talks to `ollama` directly is the
error path that lists which models are actually pulled, to name them in the
message.

## Consequences

`pip install`, `ollama pull`, run. That is the whole setup, and it is why the
README can open with a transcript rather than a signup.

Everything the model is asked to do has to work with a small one. That constraint
propagates into ADR-0002 (no tool loop), ADR-0004 (schema-constrained decoding
and sentinels), ADR-0005 (condense pages so the prompt fits) and ADR-0006
(assume the model invents figures and check every one). The `--num-ctx` and
`--think` flags exist for the same reason: a thinking model on Ollama's default
4096-token window spends the window reasoning about a copying task and is cut off
before it emits any JSON.

Ollama being down or the model not being pulled is the single most likely failure,
so it gets its own exception -- `OllamaUnavailableError` -- carrying an actionable
message rather than a transport traceback (ADR-0009).
