# ADR-0009: Raise exactly three failure modes, and list them in three places

- **Status:** Accepted
- **Date:** 2026-08-25

## Context

The pipeline can fail in many ways underneath -- a connection refused by Ollama,
a model tag that was never pulled, a DuckDuckGo rate limit, a page timing out, a
model answering nonsense. Left as they are, those reach the CLI as a traceback
and the browser as a 500, and neither tells the user the one thing that would
help: start Ollama, pull the model, wait a minute and try again.

## Decision

`BuyAgent.run()` raises exactly three exceptions, and each names a different
thing for the user to do:

| Exception | Means | HTTP |
| --- | --- | --- |
| `ValueError` | the request itself is unusable | 400 |
| `OllamaUnavailableError` | the model server is down, or the model is not pulled | 503 |
| `SearchError` | the search backend could not be reached | 502 |

Ollama's `RequestError` and `ResponseError` are translated at the agent boundary
into `OllamaUnavailableError`, with the models that *are* installed listed in the
message. Everything below that is either recovered or allowed to be a bug.

The same three are listed in three places, and a fourth failure mode has to be
added to all of them: `BuyAgent.run`'s documented `Raises`, the `except` tuple in
`__main__.main` (which logs and returns 1; 130 on Ctrl-C), and `api._STATUS`
(which maps them onto the statuses above). `tests/test_conventions.py` checks
that the three agree -- see ADR-0014.

Within the agent, **only query refinement is recoverable.** It falls back to the
raw request if the model returns something unusable, but lets
`OllamaUnavailableError` through rather than quietly searching with a model that
is not there: a degraded search is worse than an honest failure, because the user
gets results and never learns they were produced without the model.

## Consequences

Both front ends handle failure completely, in a few lines each, and the browser
gets a status that means something rather than a generic 500. The error message
for the most common failure is actionable enough to fix the problem without
reading the code.

The cost is a list that exists in three places -- exactly the shape this codebase
gets wrong twice, which is why it is one of the things the conventions test reads
off the source rather than trusting.
