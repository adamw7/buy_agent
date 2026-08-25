# ADR-0002: Fix the pipeline instead of letting the model drive a tool loop

- **Status:** Accepted
- **Date:** 2026-08-25

## Context

The obvious shape for "an agent that shops for you" is a tool-calling loop: give
the model a search tool and a fetch tool, and let it decide what to do next until
it is satisfied. LangChain makes that shape the path of least resistance, and it
is what the word "agent" usually means.

It assumes a model that reliably emits well-formed tool calls, notices when a
result is useless, and stops. This project targets whatever the shopper has
pulled into Ollama, which in practice is a small model -- `lfm2.5` at 1.2B is the
one measured. Small models drive tool loops badly: they re-issue the same search,
answer in prose where a call was required, or never decide they are done. Every
one of those failures costs a full model round trip, on a run that already takes
about 75 seconds.

What the same models do well is two narrow things: rewording a request, and
copying facts out of prose they were given.

## Decision

The control flow is ordinary Python and does not change from run to run:

```
request -> refine query (LLM) -> DuckDuckGo -> fetch + condense pages
        -> extract products (LLM) -> clean -> ground -> deduplicate
        -> rank -> log top N
```

The model is called exactly twice, at the two steps it is good at, and never
gets to choose what happens next. `BuyAgent.run()` is the whole loop, and it is
readable top to bottom.

`search.py` still exposes a LangChain `@tool` wrapper, because search is useful
as a tool elsewhere -- but `BuyAgent` calls `search_web()` directly rather than
offering the tool to the model.

## Consequences

A run costs two model calls, which is what makes it viable on a laptop, and its
cost is predictable before it starts. Every step is a plain function, so every
step has a unit test that never touches a model.

The price is that the agent cannot adapt: it will not search a second time with
better words after seeing thin results, and it will not go deeper on a promising
page. Anything like that has to be written as another pipeline stage, deliberately,
rather than emerging from the model's judgement.

This decision is the reason for most of the others here. Because the model is not
in charge, everything that decides the answer -- filtering, grounding, ranking,
ordering -- has to live in code (ADR-0007, ADR-0012), and the two calls it does
make have to be constrained hard enough to be trusted (ADR-0004, ADR-0006).
