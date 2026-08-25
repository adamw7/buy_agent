# ADR-0012: The browser decides nothing

- **Status:** Accepted
- **Date:** 2026-08-25

## Context

Once the products are JSON in a browser, it is tempting to let the client do
client things: re-sort the cards without another run, format an unknown price as
"--", compute a badge for the best value. Each is a few lines of TypeScript and
saves a round trip.

Each also moves a piece of the answer into the one place this project cannot
test with the rest. The Python suite cannot see it, and the same rule that put
`clean_products` in code rather than in the prompt (ADR-0002, ADR-0007) applies
here: whatever decides the answer belongs where it is testable.

## Decision

Ranking, grounding, ordering and even wording stay in Python. Concretely:

- `product_payload` sends `price_label` and `rating_label` alongside the raw
  figures, so "how an unknown price is written" is a Python decision with a
  Python test, and the component renders a string it was given.
- `sort_by` is a request parameter, not a client-side re-sort. Changing the sort
  runs the pipeline again with the new criterion.
- The API also sends `top_n`, so splitting the answer into "the top N" and "the
  rest" is a rule the server owns and the app applies.

The Angular app holds run state in signals, renders what arrives, and offers a
Stop button that unsubscribes from the stream. That is all it does.

`ui/src/app/agent.types.ts` mirrors the payloads for TypeScript, and a field
added to `api.py` is added there in the same change. `tests/test_conventions.py`
checks the two sides field for field (ADR-0014), because a field added on one
side and forgotten on the other is a runtime `undefined` in the browser that
neither suite can see on its own.

## Consequences

There is one implementation of every rule, tested once, and the CLI and the web
UI cannot drift into disagreeing about what the best product is. A reader
debugging a wrong answer never has to ask which side computed it.

The price is round trips for things a client could do instantly -- re-sorting
re-runs a search that takes a minute, which is a real cost and an accepted one --
and a payload that carries both raw figures and their rendered labels.
