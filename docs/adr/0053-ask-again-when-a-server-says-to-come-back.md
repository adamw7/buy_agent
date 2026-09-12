# ADR-0053: Ask again when a server says to come back, with a clock that is handed in

- **Status:** Accepted
- **Date:** 2026-09-12

## Context

Nothing in this package ever asked a second time, and the two places that talk to
the web fail differently for it:

- A shop that answers `429` was a page with no content. Grounding then blanks
  every figure that page would have backed (ADR-0006), so the report reads "price
  unknown" -- which is indistinguishable from a model that missed one, except for
  the tally line `enrich` logs.
- A search that fails is the whole run. `ddgs` asks several engines and raises
  only when every one of them failed, so a `DDGSException` that is not
  "No results found." is the one failure that is about *this minute* rather than
  about this query -- and it reached the shopper as `SearchError`, exit 1, nothing
  found. README has listed "DuckDuckGo rate-limits heavy use" as a limitation
  throughout.

The obvious fix is blocked by an architecture rule, and the rule is right.
`fetch.py` and `search.py` are in the pipeline layer, and
`test_the_steps_take_values_and_answer_values` forbids a step from importing
`time`: "an answer that moves on its own is one the cache would keep wrongly"
(ADR-0044). A step that read the clock to decide *what to answer* would poison a
remembered answer; waiting is not that, but the rule is enforced as an import ban
and an exemption would have to be argued per module rather than per use.

## Decision

The waiting is a value the step is handed, not a module it imports.
`search_web(..., wait=...)` and `enrich(..., wait=...)` each take a
`Callable[[float], None]`, defaulting to `None`, which is "ask once". `BuyAgent`
-- orchestration, which may hold a clock -- passes `time.sleep` to both, the way
it passes `checkpoint` down for ADR-0034. Nothing else does, so every other caller
and every test gets the single-attempt behaviour by default.

What is worth a second attempt is narrow on each side:

- **A page**: `429` and `503`, the two statuses that mean "later" rather than
  "no", after `Retry-After` seconds if the answer named them -- capped at
  `_MAX_RETRY_WAIT`, floored at zero, and `_RETRY_WAIT` where the header was
  absent or unreadable. The `HTTP-date` form of that header counts as unreadable
  on purpose: reading it means subtracting a clock this module does not hold. A
  403, a 404 and a transport failure are answers, and a timeout is already the
  whole of `--fetch-timeout` spent.
- **A search**: any `DDGSException` that is not `_NO_RESULTS`, after
  `_RETRY_WAIT`. A search that matched nothing is never asked again -- it worked,
  and it would match nothing twice.

Once each, either way. The second answer is final.

## Consequences

- A rate-limited shop costs a second rather than a page, and a rate-limited
  search costs two seconds rather than the run. That is the whole of what this
  buys, and it is bought at the only price anyone notices: a run against a wall
  of 429s is now slower before it is as empty as it was.
- The pipeline's "takes values and answers values" rule holds unchanged, and it
  is why the parameter exists in this shape. A future step that wants to wait gets
  the same treatment; one that wants to read the clock to *decide* something is
  the thing the rule still refuses.
- Every stand-in for `search_web` takes the keyword, the way every stand-in for
  `enrich` already absorbed its settings -- `benchmark/runner.py`,
  `demo/server.py` and the suite's own fakes. One that does not raises `TypeError`
  out of `BuyAgent.run`.
- The waiting is the one per-page line logged at INFO rather than DEBUG: it is
  time the shopper is spending rather than a page they are not getting. The
  search's is a WARNING, being the failure the run would have ended on.
- A page is asked at most twice and a search at most twice, so the worst case a
  shopper waits is bounded by what they already set: `--fetch-timeout` twice plus
  the cap per page, and two searches plus two seconds per source.
- `fetch.enrich` gained its seventh setting for this, which is what moved
  `.pylintrc`'s `max-args` (ADR-0051 notes the other half).
