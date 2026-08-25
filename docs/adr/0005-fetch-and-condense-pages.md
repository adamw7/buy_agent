# ADR-0005: Extract from fetched pages, condensed to their priced lines, not from search snippets

- **Status:** Accepted
- **Date:** 2026-08-25

## Context

The cheap design is to extract products straight from DuckDuckGo's result
snippets: no fetching, no HTML parsing, no per-page timeouts, and a prompt that
comfortably fits any context window.

It does not work. A snippet for "headphones under $200" typically contains
exactly one number -- the `$200` from the query itself. Extracting from ten such
snippets yields ten products with no prices, no ratings and nothing to rank; and
a model asked for a price it cannot see does not answer "unknown", it fills the
gap with something plausible.

Feeding whole pages instead is the other extreme. A shop page runs to tens of
thousands of tokens of navigation and boilerplate, and a small model's context
window holds a fraction of one.

## Decision

`fetch.enrich()` fetches each result page in parallel (a `ThreadPoolExecutor`,
`fetch_timeout` seconds each, a browser-ish `User-Agent` because many shops
answer `python-httpx` with a 403), then condenses it: the extracted text is cut
to the lines that actually quote a price or a rating, up to `page_chars` (1200)
per page.

The condensed text is stored on `SearchResult.content` -- on the result object
itself, not passed around alongside it. That is what guarantees extraction and
verification are handed the *same* text; see ADR-0006, where the two diverging
would reject everything.

A page that cannot be read -- a timeout, a 403, a JavaScript-rendered shell --
falls back to its snippet rather than failing the run. `fetch_pages=False`
(`--no-fetch`) turns the whole stage off for a faster run that rarely finds a
price.

## Consequences

The prompt stays small enough for a local model while containing real figures,
which is what makes grounding find anything to confirm. Fetching is the second
slowest stage after extraction, and it is the stage most exposed to the open web:
timeouts and 403s are normal, expected, and never fatal.

The condensing heuristic decides what the model can possibly see. A price
rendered as an image, or split across elements so it never lands on one line, is
invisible to extraction *and* to grounding -- the product survives with a blank
price and scores neutral, which is the right failure but a silent one.
