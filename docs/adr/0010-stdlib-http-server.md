# ADR-0010: Serve the web tier from the standard library

- **Status:** Accepted
- **Date:** 2026-08-25

## Context

The UI needs something to serve it and an API to call. The reflex is FastAPI or
Flask, and either would work.

The load it has to carry is unusual, though: one user, on their own laptop,
running a search that takes tens of seconds against a model on the same machine.
There is no concurrency to manage, no scaling story, no auth. What a framework
would contribute -- routing, validation, dependency injection, an ASGI server --
is small next to what it would add: another dependency tree in a project whose
dependency list is already the most interesting thing about it, and a second
place (Pydantic models for HTTP, on top of the Pydantic models for the LLM
schema) where request shapes are declared.

## Decision

`buy_agent.server` is `http.server` and nothing else. `BuyAgentHandler` routes
`/api/*` to `api.py` and everything else to the built Angular app, with unknown
paths falling back to `index.html` so the app keeps its own routing. It binds
loopback by default: it drives a model on your own machine and is not meant to
be exposed.

`api.py` is framework-free and separately testable: options in, ranked products
out, plus `_STATUS` mapping the three failures onto HTTP statuses (ADR-0009).
The server is the transport; the API is the logic.

`create_server(agent_factory=...)` is the injection seam, the way `llm=` is for
the pipeline -- the server tests drive real sockets against a stub agent.

`server._CONTENT_TYPES` spells out the types `ng build` emits rather than
delegating to `mimetypes`, which reads the registry on Windows and can answer
`text/plain` for `.js`. A browser refuses to run that as a module, and the
symptom is a blank page with no error anywhere.

## Consequences

The runtime dependency list stays about the agent. The server is a few hundred
readable lines with no magic, and its tests are about HTTP because they speak
HTTP.

What is given up is everything a framework would have handed over: no automatic
validation (`api.parse_options` coerces by hand), no OpenAPI, no middleware
ecosystem, and a threading model that is `ThreadingHTTPServer` and nothing more.
Streaming had to be built rather than configured (ADR-0011). If this ever needs
to serve more than one person over a network, this decision is the first one to
revisit -- and `api.py` being framework-free is what would make that a small
change.
