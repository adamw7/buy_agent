# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A shopping agent: it takes a plain-language request ("wireless headphones under
$200"), searches the web, extracts up to 10 products, ranks them, and logs the
top 3. Built on LangChain with a local Ollama model. `ui/` is an Angular front
end onto the same pipeline, served by `buy_agent.server`. See `README.md` for
usage.

## Commands

Dependencies live in a `.venv` created with stdlib `venv`; there is no
`pyproject.toml` and no packaging step. Run everything from the repository root.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt          # runtime deps: requirements.txt

python -m pytest                              # whole suite (~2s)
python -m pytest tests/test_ranking.py        # one file
python -m pytest tests/test_ranking.py::test_cheaper_wins_when_rating_is_equal
python -m pytest -k verification              # by name
python -m coverage run -m pytest ; python -m coverage report   # with coverage

python -m buy_agent "gaming laptop under $1500"          # run the agent
python -m buy_agent "espresso machine" --model lfm2.5 -v
python -m buy_agent "running shoes" --sort-by price --json results.json

python -m buy_agent.server                    # the UI and its API on :8000
```

`$OLLAMA_MODEL` and `$OLLAMA_HOST` move the model and server defaults, and every
CLI flag defaults to the matching `AgentConfig` field, so a new setting is added
in `config.py` and picked up rather than repeated. `buy_agent.server` wants the
UI built first: without `ui/dist/ui/browser` the API still answers and the page
is a 503 saying how to build it (`--ui-dir` points at a build elsewhere).

The UI is a separate, ordinary Angular workspace in `ui/`, with its own
`package.json` and its own tests. Angular 22 on Node 22.22.3+, 24.15+ or 26+ --
older Node is refused by the Angular CLI, not by anything here. Nothing in the
Python side needs Node at all.

```powershell
cd ui
npm install
npm test                                      # vitest in jsdom
npm run test:coverage                         # the same, then the coverage floor
npm run build                                 # dist/ui/browser, what the server serves
npm start                                     # dev server on :4200, proxying /api to :8000
```

There is no Python linter; the UI has Prettier (`npx prettier --write "src/**/*"`).
CI (`.github/workflows/ci.yml`) runs two jobs for pushes to `main` and for every
pull request: `coverage run -m pytest` plus `coverage report` on Python 3.13, and
`npm run test:coverage && npm run build` in `ui/` on Node 22.22.3. `pytest.ini`
sets `pythonpath = .`, which is why the package imports without being installed,
plus `testpaths = tests` and `addopts = -q --strict-markers`. `.coveragerc` holds
the Python floor (99%, against 100% actual); `ui/scripts/check-coverage.mjs` holds
the UI's (98% of statements and lines) -- the Angular unit-test builder reads a
vitest config's coverage *reporters* but does not fail a run on its `thresholds`,
so the floor has to be checked separately or it is not a floor.

## Architecture

`docs/architecture.md` holds the same picture as C4 diagrams (context,
containers, components, and a streamed run end to end); keep it in step when
a module's responsibility or a boundary moves.

`docs/adr/` is the decision log: one numbered record per architectural decision,
with the context and the consequences, indexed by `docs/adr/README.md`. The
conventions below are the *rules*; the ADRs are why they exist and what was
rejected. A change that contradicts an accepted record gets a new record
superseding it rather than an edit to the old one -- numbers are never reused,
and accepted records are not rewritten. `tests/test_conventions.py` checks that
the index and the directory agree, so a new ADR is two edits: the file and its
row in the index. `docs/adr/0000-template.md` is the starting point.

The pipeline is deliberately **not** a tool-calling agent loop. The LLM is used
for the two steps it is reliable at, and ordinary Python does everything else,
because Ollama is typically run with small models that drive tool loops badly.

```
request -> refine query (LLM) -> DuckDuckGo -> fetch + condense pages
        -> extract products (LLM) -> clean_products -> ground -> deduplicate
        -> rank -> log top 3
```

That order is load-bearing in both joints. `clean_products` runs before `ground`
so a name still wearing its publisher suffix ("... Review | AudioSite") is not
failed by the coverage check for tokens the page never had to contain; `ground`
runs before `deduplicate` so `_combine` only ever merges figures the sources
back.

| Module | Responsibility |
| --- | --- |
| `agent.py` | `BuyAgent.run()` -- orchestrates the pipeline, translates Ollama errors |
| `extraction.py` | Both prompts, both chains, name cleaning, deduplication |
| `fetch.py` | Fetches result pages, keeps the lines quoting a price or rating |
| `verification.py` | Drops products and figures absent from the sources |
| `ranking.py` | Scoring and sorting; no LLM involved |
| `models.py` | `ExtractedProduct` (LLM-facing) vs `Product` (domain) |
| `search.py` | DuckDuckGo wrapper plus a LangChain `@tool` version |
| `config.py`, `logging_setup.py`, `__main__.py` | Config, the report, the CLI |
| `api.py` | Request options in, ranked products out -- the web-facing half worth testing |
| `server.py` | A stdlib HTTP server: the JSON API, the event stream, the built UI |

Five conventions matter when changing this code:

- **`ExtractedProduct` uses sentinels, `Product` uses `None`.** The LLM-facing
  schema asks for `-1`/`""` rather than nullable fields: Ollama compiles the JSON
  schema into a decoding grammar, and a required `number` makes `"N/A"` -- which
  would fail validation for the entire batch -- structurally impossible. Keep new
  extraction fields non-nullable with a sentinel, and convert in `to_product()`.
- **Never rank on an unverified number.** `verification.ground()` drops products
  whose name is absent from the sources and blanks any price, rating or review
  count that is. Ratings need context ("4.3/5", "rated 4.3") because a bare `5`
  matches the "5" in "out of 5". Extraction and verification must be given the
  same text, or the check rejects everything -- this is why `fetch.enrich()` puts
  page content on `SearchResult` rather than passing it around separately.
- **`GENERIC_WORDS` is shared, and edits to it pull in two directions.**
  `verification.py` imports the set from `extraction.py` (along with
  `NAME_TOKENS`, so merging and grounding agree on what a name's words are).
  Adding a word makes `merge_variants` fold *more* names into one product, and at
  the same time makes `mentions_name` stricter -- ignored words leave fewer
  distinctive tokens to clear the 0.6 coverage bar. Only ever add words that
  identify nothing ("wireless", "black"); a brand or a model number there would
  let an invented product pass grounding.
- **Missing data scores neutral, not zero.** `ranking.NEUTRAL` is 0.5, and an
  unknown rating, review count or price scores that. Grounding blanks figures the
  sources did not back, so scoring a blank as 0 would punish a product for the
  extractor's misses rather than for anything about the product. For the same
  reason `sort_by="price"` and `"rating"` sink products missing that field to the
  bottom instead of dropping them.
- **Model output is never trusted as judgement.** The model reports article
  headlines as products; `clean_products` filters them. Anything that decides the
  answer -- filtering, scoring, ordering -- belongs in Python, where it is testable.

`BuyAgent.run()` raises exactly three things -- `ValueError`,
`OllamaUnavailableError`, `SearchError` -- and `__main__.main()` catches exactly
those, logging them and returning 1 (130 on Ctrl-C). `api._STATUS` maps the same
three onto HTTP statuses (400, 503, 502). A new failure mode needs handling in
all three places, or it reaches the user as a traceback and the browser as a 500.
Within the agent only query refinement is recoverable: it falls back to the raw
request, but lets `OllamaUnavailableError` through rather than searching with a
model that is not there.

The CLI and the API are two ways of filling in the same `AgentConfig`, and both
set `search_results = max(results, top)` -- searching for fewer pages than the
report intends to show would cap the report. A new option belongs in
`__main__.build_parser`, `api.parse_options` and `api.defaults_payload`, which is
what seeds the web form.

## The UI and its server

`buy_agent.server` is stdlib-only on purpose -- the dependency list is already
the interesting part of this project, and a run that takes a minute and serves
one person does not need a framework under it. It hands `/api` to `api.py` and
everything else to the built Angular app, with unknown paths falling back to
`index.html` so the app keeps its own routing.

| Endpoint | Answers with |
| --- | --- |
| `GET /api/config` | The form's defaults -- the same ones `--help` prints |
| `GET /api/models` | Which models Ollama has pulled, or why it could not be asked |
| `POST /api/search` | One run, as JSON |
| `GET /api/search/stream` | One run, as SSE: `log` lines, then `result` or `failure` |

Four things there are load-bearing:

- **A run is streamed, not requested.** A search takes tens of seconds, so
  `GET /api/search/stream` runs the agent in a worker thread and relays its log
  lines as Server-Sent Events while it works. `_LogRelay` routes records by the
  thread that produced them, which is what keeps two concurrent runs from seeing
  each other's progress. Extraction is slow and logs nothing while it runs, so a
  `ping` event goes out every 15s to keep browsers and proxies from timing the
  stream out. `POST /api/search` is the same run in one response.
- **The stream's failure event is called `failure`, not `error`.** A browser's
  `EventSource` delivers transport errors under `error` and then reconnects; a
  named `error` event would be indistinguishable from a dropped connection, and
  the reconnect would silently start the whole search again. For the same reason
  `HEAD /api/search/stream` answers 405 rather than starting a run nobody reads.
- **The browser decides nothing.** Ranking, grounding and even the wording of an
  unknown price stay in Python: `product_payload` sends `price_label` and
  `rating_label` next to the raw figures, and `sort_by` is a request parameter
  rather than a client-side re-sort. The same rule as `clean_products` -- whatever
  decides the answer belongs where it is testable. `ui/src/app/agent.types.ts`
  mirrors those payloads for TypeScript, so a field added to `api.py` is added
  there too.
- **A blank value means "use the default".** `api.parse_options` treats a missing
  key and an empty string alike, because an empty form field means "unset", not
  "zero" -- and the UI's `toQuery` drops blanks for the same reason. Values that
  are present but unusable raise `ApiError` with the status the client deserves.

`server._CONTENT_TYPES` spells out the types `ng build` emits rather than leaving
them to `mimetypes`, which reads the registry on Windows and can answer
`text/plain` for `.js` -- which a browser refuses to run as a module, leaving a
blank page and no error.

`create_server(agent_factory=...)` is the seam the server tests inject a stub
agent through, the way `BuyAgent(config, llm=...)` is for the pipeline. Angular
components are tested in jsdom with `TestBed`; `AgentService` is tested against a
fake `EventSource` rather than a live one.

## Tests

`BuyAgent(config, llm=...)` is the injection seam: `tests/conftest.py` provides a
`FakeLLM` exposing only `with_structured_output`. The network is monkeypatched
in three places: `buy_agent.agent.search_web` and `buy_agent.agent.enrich` for
pipeline tests, and `buy_agent.search.DDGS` / `buy_agent.fetch.httpx.Client` for
the wrappers' own tests. `ollama.Client` is patched too, for the one path that
lists the installed models to name them in an error. Patching `DDGS.text` does
*not* work -- the name `ddgs` exports is a wrapper that constructs a different
class. No test touches the network or Ollama; keep it that way. The server tests are
the one exception to "no sockets": they bind loopback, because routing and status
codes are what they are about. They pass `serve_forever(0.01)` -- the default 0.5s
poll would otherwise cost half a second per test on shutdown. Three server tests
speak the protocol over a raw socket, because urllib will not build a request with
a malformed `Content-Length`; `raw()` reads until the declared body has arrived,
since the headers and the body are separate writes and so can land in separate
segments. 425 tests run in about three seconds: most of that is the one
test that spawns an interpreter to check `python -m buy_agent` still runs as a
script, plus 0.7s of deliberate `StubAgent.delay` in the two server tests that
need a run to still be going -- the keepalive ping, and two streams overlapping.
Nothing else should sleep, so a run that takes much longer still means something
is reaching out. The UI's 46 tests
run in about two seconds, most of which is building the app first. `README.md`
quotes both counts, so a new test file is two edits.

Both suites cover essentially every line, which means coverage no longer tells
you where the next test should go. `tests/test_conventions.py` covers what it
cannot: the rules that hold *between* modules, read off the declarations
themselves rather than exercised. It asserts that `api._STATUS`, the `except`
tuple in `__main__.main` (parsed with `ast`) and `BuyAgent.run`'s documented
`Raises` name the same three failures; that `ranking.SortBy`, `api.SORT_OPTIONS`,
the CLI's `--sort-by` choices and the TypeScript `SortBy` union offer the same
criteria; that `agent.types.ts` mirrors `defaults_payload`, `product_payload`
and `run_search` field for field; and that every ADR is indexed, numbered to
match its heading, and carries the status, date and sections ADR-0001 asks for. A field added on one side of the language
boundary and forgotten on the other is otherwise invisible to both suites.

## Environment

Development happens on Windows with PowerShell as the default shell; prefer PowerShell syntax for terminal commands, or use the Bash tool explicitly for POSIX scripts.
