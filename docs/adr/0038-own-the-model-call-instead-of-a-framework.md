# ADR-0038: Call each model server's own client, and drop LangChain

- **Status:** Accepted
- **Date:** 2026-09-05

## Context

The pipeline was built on LangChain and used six things from it: two
`ChatPromptTemplate`s, `with_structured_output(..., method="json_schema")` twice,
`.invoke()`, `OutputParserException`, and `ChatOllama`/`ChatOpenAI`. Nothing else
-- no tools, no memory, no agent loop, the control flow being fixed Python by
ADR-0002 and the two model calls being the only places a model is asked anything
at all.

What that cost was measured rather than guessed. `langchain-core`,
`langchain-ollama` and `langchain-openai` resolved to twenty packages: `langsmith`
and its own HTTP stack (`httpx2`, `httpcore2`, `orjson`, `zstandard`,
`websockets`, `xxhash`, `uuid_utils`, `requests-toolbelt`) for tracing nothing
here switches on, `tiktoken` and `regex` for counting tokens nothing here counts,
plus `tenacity`, `jsonpatch` and the rest. Twenty-four packages become forty-four.
Every one is a version this project pins nothing about, in an image it ships, for
a surface of six symbols.

The wrappers were also the reason two direct imports went unpinned. `providers.py`
imports `ollama` and `openai` at module level -- the listing calls and the error
classes in `transport_errors` are theirs -- while `requirements.txt` named neither,
both arriving underneath a LangChain package that chose their versions.

And the surface was not free of the framework either. An answer the model fumbled
arrived as `OutputParserException`, whose message carries a docs link for a
library the shopper does not have installed, wrapped around the half-finished JSON
that is the actual symptom.

## Decision

The two clients are called directly, and the seam that was LangChain's is
`buy_agent/chat.py`: a `Prompt` (a system and a human turn with `{name}` holes,
filled by `str.format`), a `ChatModel` protocol of exactly one method
(`answer(messages, schema)`), a `Chain` binding a prompt to the model and the
schema, and `read_answer`, which parses a server's reply into that schema or
raises `UnreadableAnswerError`.

A `Message` is a `role`/`content` mapping, which is what both servers take over
the wire, so nothing is translated on the way out.

How a schema is *declared* stays the provider's, one row each, as everything that
differs between the two servers already is (ADR-0028, ADR-0029): Ollama takes it
as `format`, vLLM as `response_format`. ADR-0004 is unchanged by this -- both
calls are still constrained by a JSON schema compiled into a decoding grammar,
and extraction fields are still non-nullable with sentinels. Only the call
carrying the schema moved.

`ollama` and `openai` become the pinned direct dependencies they always were.

## Consequences

The install goes from forty-four packages to twenty-four, and the two clients this
project actually imports are pinned to the versions it was tested against rather
than to whatever a wrapper asked for.

`buy_agent/chat.py` is now this project's to maintain: about ninety lines, none of
it clever, but a prompt template and a JSON parser that were somebody else's
problem are this repository's. The bet is that six symbols' worth of surface is
cheaper to own than to depend on. If a later change wants tools, streaming
partial output, or a second prompt format, that bet is worth re-taking rather
than growing this module into a framework.

Three obligations follow.

**A stand-in for a model server is a class with one method.** `tests/conftest.py`,
`benchmark/scripted.py` and `demo/server.py` each have one, and they are what
`BuyAgent(config, llm=...)` takes. A stand-in for a *chain* is a class with
`invoke`, which is what `integration/conftest.py` wraps the real one in.

**Ollama is asked without streaming**, and its error vocabulary shifts with that.
The client converts exactly one failure -- a refused connection becomes a builtin
`ConnectionError` -- while a timeout and a dropped stream stay raw `httpx` errors.
Both kinds are still in the row's `transport_errors`, and the comment there says
which is which; a change to that call changes what a stopped server raises.

**A prompt's holes are `str.format`'s.** A template naming a key the payload does
not carry raises here rather than reaching the model with braces in it -- and a
value substituted in is never re-scanned, which is what keeps a fetched page free
to contain `{price}` without taking the run down between the search and the
ranking.

What this does not buy is any change in what the model is asked or what comes
back. The prompts are the same text, the schemas are the same schemas, and the
benchmark's floors are the check on that: a run scored the same before and after.
