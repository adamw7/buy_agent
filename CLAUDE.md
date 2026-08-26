# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A shopping agent: it takes a plain-language request ("wireless headphones under
$200"), searches the web, extracts up to 10 products, ranks them, and logs the
top 3. Built on LangChain with a local Ollama model. `ui/` is an Angular front
end onto the same pipeline, served by `buy_agent.server`. See `README.md` for
usage -- it keeps the tour and links out to the longer technical sections, which
live beside it: `docs/models.md` (keeping Ollama's models current),
`docs/docker.md` (running the web tier as a container) and `docs/testing.md`
(both suites, the coverage floors and the mutation run).

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
.\scripts\start.ps1                           # ...or all of it from cold, no arguments

python -m scripts.update_ollama               # re-pull Ollama's models, report what moved

pip install -r requirements-mutation.txt      # mutmut, on top of the dev deps
python -m mutmut run                          # mutation testing; ~2 min, cached
python -m mutmut results --all true > mutation-results.txt
python scripts/mutation_report.py mutation-results.txt   # the report CI publishes
```

The `Dockerfile` is a third way to run that server, for someone who wants the page
rather than the source: a `node:22.22.3-bookworm-slim` stage builds `ui/`, a
`python:3.13-slim` stage installs `requirements.txt` and gets the build copied to
`ui/dist/ui/browser` beside the package -- where `server.DEFAULT_UI_DIR` looks, so
no `--ui-dir`. Ollama is not in the image and not started by it (ADR-0015): the
container talks to the host's through `host.docker.internal`, which needs
`--add-host=host.docker.internal:host-gateway` on Linux. `ENTRYPOINT` is `python`
and `CMD` is `-m buy_agent.server --host 0.0.0.0`, so the CLI is reachable from
the same image and `--host` stays out of the server's own default (it binds
loopback everywhere else). Nothing in CI builds it; `tests/test_conventions.py`
is what keeps its version pins, its copy destination and its `EXPOSE` in step.
`.dockerignore` narrows what the build even sees: `tests/`, `docs/`, `scripts/`,
the dev and mutation requirements and every local build artefact (`.venv/`,
`ui/node_modules/`, `ui/dist/`, `mutants/`) stay out of the context, so the Node
stage builds `ui/` from source rather than copying a stale local `dist/`. Nothing
tests that file -- the convention tests read the `Dockerfile` and not
`.dockerignore` -- so a path added to one of those directories is only kept out
of the image by keeping this list current.

```powershell
docker build -t buy-agent .
docker run --rm -p 8000:8000 buy-agent
docker run --rm buy-agent -m buy_agent "espresso machine"
```

`$OLLAMA_MODEL` and `$OLLAMA_HOST` move the model and server defaults, and every
CLI flag defaults to the matching `AgentConfig` field, so a new setting is added
in `config.py` and picked up rather than repeated. One field is deliberately not
named the same on the way out: `AgentConfig.reasoning` is `--think`
(`BooleanOptionalAction`, so `--no-think` is the off switch) on the CLI and
`think` in both the JSON payloads and `agent.types.ts` -- the tri-state it carries
is Ollama's thinking mode, and `None` means "send nothing and leave the model
alone" rather than "off". It pairs with `num_ctx`: the extraction prompt runs to
~3.3k tokens, so on Ollama's default 4096 window a thinking model reasons until
the context is gone and never emits any JSON. `DEFAULT_MODEL` is `gemma4:12b`,
which thinks, so the defaults that make it answer travel with it -- `reasoning`
is `False` and `num_ctx` is `8192` rather than the `None` each used to be. A
model that cannot think ignores both; one that wants its own behaviour back is
given `num_ctx=None, reasoning=None`, which is the only way to send nothing and
is reachable from neither front end (ADR-0019). `buy_agent.server` wants the
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
`npm run test:coverage && npm run build` in `ui/` on Node 22.22.3. Both are
matrixed over `ubuntu-latest` and `windows-latest` -- this is written on Windows
and the runners were Linux, so either alone leaves half the platform differences
unchecked (ADR-0020). `fail-fast` is off so one platform's failure still reports
the other, every step runs under `bash` because PowerShell carries on past a
failing command mid-step, and the matrix is over platforms only: one Python and
one Node, since the `Dockerfile`, `scripts/start.ps1` and `docs/testing.md` each
pin themselves to *the* version `ci.yml` names.
`.github/workflows/mutation.yml` is the second workflow: mutmut against
`buy_agent/` at 05:17 UTC on Saturdays (and on `workflow_dispatch`), never on a
pull request. Its settings live in `setup.cfg` -- which exists for that and is not
a packaging file -- and `scripts/mutation_report.py` turns a run into the job
summary and fails it if the score drops under 75% (ADR-0016). A run copies the
tree to `mutants/` and tests the copy, so anything the suite reads off disk or
imports from outside `buy_agent` has to be named in `also_copy`, or the whole run
dies at collection.

`pytest.ini` sets `pythonpath = .`, which is why the package imports without
being installed, plus `testpaths = tests` and `addopts = -q --strict-markers`.
`.coveragerc` holds the Python floor (99%, against 100% actual) and sets
`branch = true`, so that floor is over branches as well as lines; the one thing
it excludes is the `if __name__ == "__main__"` guard, which runs only under
`python -m buy_agent` and is covered by spawning a real interpreter instead.
`ui/scripts/check-coverage.mjs` holds the UI's (98% of statements and lines) --
the Angular unit-test builder reads a vitest config's coverage *reporters* but
does not fail a run on its `thresholds`, so the floor has to be checked
separately or it is not a floor. That floor is statements and lines *only* on
purpose: v8 attributes the branches inside a compiled Angular template to
positions no test can reach -- `search-form.html` reports every statement covered
and about 30% of its branches -- so a branch floor there would be a number about
the instrumentation rather than about the tests. Don't add one.

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
row in the index. `docs/adr/0000-template.md` is the starting point. The log runs
to ADR-0021 and every record is Accepted, so the next free number is 0022.

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
back. That is necessary and not sufficient: a merge that took each field on its
own would still report a pairing no page printed, so `_MERGEABLE_FIELDS` groups
a figure with whatever only qualifies it and `_fill_gaps` moves the group.

| Module | Responsibility |
| --- | --- |
| `agent.py` | `BuyAgent.run()` -- orchestrates the pipeline, translates Ollama errors |
| `extraction.py` | Both prompts, both chains, name cleaning, deduplication |
| `fetch.py` | Fetches result pages, keeps the lines quoting a price or rating |
| `verification.py` | Drops products and figures absent from the sources; links what is left |
| `ranking.py` | Scoring and sorting; no LLM involved |
| `models.py` | `ExtractedProduct` (LLM-facing) vs `Product` (domain) |
| `search.py` | DuckDuckGo wrapper -- and nothing else (ADR-0021) |
| `config.py`, `logging_setup.py`, `__main__.py` | Config, the report, the CLI |
| `api.py` | Request options in, ranked products out -- the web-facing half worth testing |
| `server.py` | A stdlib HTTP server: the JSON API, the event stream, the built UI |

Six conventions matter when changing this code:

- **`ExtractedProduct` uses sentinels, `Product` uses `None`.** The LLM-facing
  schema asks for `-1`/`""` rather than nullable fields: Ollama compiles the JSON
  schema into a decoding grammar, and a required `number` makes `"N/A"` -- which
  would fail validation for the entire batch -- structurally impossible. Keep new
  extraction fields non-nullable with a sentinel, and convert in `to_product()`.
- **Never rank on an unverified number, and never link to an unverified page.**
  `verification.ground()` drops products whose name is absent from the sources
  and blanks any price, rating or review count that is. Ratings need context
  ("4.3/5", "rated 4.3") because a bare `5` matches the "5" in "out of 5".
  Extraction and verification must be given the same text, or the check rejects
  everything -- this is why `fetch.enrich()` puts page content on `SearchResult`
  rather than passing it around separately. `attribute_sources()` then gives each
  product the URL of the first searched page that mentions it, keeping the
  model's own `url` only when it names a page that was searched (ADR-0017): a
  blanked figure still shows as "price unknown", but a made-up link is one the
  shopper clicks. It runs inside `ground`, so `deduplicate` only ever merges
  links the sources back.
- **A currency belongs to its price, and a review count to its rating.** Both are
  facts about the *listing* that printed them, not about the product, so
  `_MERGEABLE_FIELDS` pairs them up and `_fill_gaps` carries a qualifier over
  only where the figure it describes is carried over too, or where both listings
  quote the same one. Field-by-field merging passes grounding -- each half really
  is in the sources -- while reporting "129.00 EUR" for a page that said 129 and
  a page that said "249 EUR". A new field that only makes sense next to another
  belongs in that other's group rather than in one of its own.
- **`GENERIC_WORDS` is shared, and edits to it pull in two directions.**
  `verification.py` imports the set from `extraction.py` (along with
  `NAME_TOKENS`, so merging and grounding agree on what a name's words are).
  Adding a word makes `merge_variants` fold *more* names into one product, and at
  the same time makes `mentions_name` stricter -- ignored words leave fewer
  distinctive tokens to clear the 0.6 coverage bar. Both sides of that bar are
  split into words by `NAME_TOKENS` and compared word to word: a substring test
  would let "$1700" on the page vouch for an invented "Bose 700", the same way a
  bare `mentions_number` would have. Only ever add words that
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
model that is not there. What `BuyAgent._invoke` has to catch to produce that
error is wider than it looks: the ollama client turns a refused connection into a
builtin `ConnectionError` only on its *non*-streaming path, and `ChatOllama`
always chats over the streaming one, so a stopped server, a model too slow to
answer and a killed stream all arrive as raw `httpx` errors -- none of which is an
`OSError`. Hence `httpx.HTTPError` in the `except`, next to ollama's own
`RequestError`, which is a different class from httpx's identically named one.

The CLI and the API are two ways of filling in the same `AgentConfig`, and both
set `search_results = max(results, top)` -- searching for fewer pages than the
report intends to show would cap the report. A new option belongs in
`__main__.build_parser`, `api.parse_options` and `api.defaults_payload`, which is
what seeds the web form. `weights` is the one field neither of them fills in:
`RankingWeights` is reachable only by constructing an `AgentConfig` in Python, so
changing how the blended score is balanced is a code change and not a flag.

`buy_agent/__init__.py` re-exports the small surface a Python caller needs --
`BuyAgent`, `AgentConfig`, `Product`, `RankedProduct`, `RankingWeights` and
`rank_products` -- which is what `import buy_agent` is expected to offer; anything
else is reached by its module.

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
- **Loopback is not a boundary a browser respects, so every request is admitted
  first.** `BuyAgentHandler._admits()` runs at the top of `do_GET`, `do_POST` and
  `do_HEAD` -- a new method added without it is unguarded and nothing fails --
  and refuses `Sec-Fetch-Site: cross-site`, an `Origin` that is neither loopback
  nor equal to the request's own `Host`, and a `Host` outside `allowed_hosts`
  (ADR-0018). The first stops a page on another site starting a run whose answer
  it could never read; the last stops DNS rebinding, which is how that page would
  get to read one. `--allowed-host` names a further host; a bind to a public
  interface turns the `Host` check off and says so at startup.

`server._CONTENT_TYPES` spells out the types `ng build` emits rather than leaving
them to `mimetypes`, which reads the registry on Windows and can answer
`text/plain` for `.js` -- which a browser refuses to run as a module, leaving a
blank page and no error. `_resolve` is the other place the platform shows
through: it catches `OSError` and `ValueError` around `Path.resolve` because an
exception there escapes to socketserver, which drops the socket without a reply.
An encoded NUL raises there on POSIX and does not on Windows, where
`ntpath.realpath` returns the path unchanged and `is_file()` swallows the error
instead -- same answer, different line -- so the branch is tested by making
`resolve()` refuse outright rather than by an input only one platform rejects
(ADR-0020).

`_SECURITY_HEADERS` goes out on every response, and its CSP is `'self'` throughout
because the app is served whole from one origin -- `'unsafe-inline'` for styles
only, which is what Angular's per-component `<style>` blocks need. That policy and
the UI's build are coupled: `optimization.styles.inlineCritical` is off in
`ui/angular.json` because Angular's critical-CSS inliner defers the global
stylesheet with an inline `onload`, and `script-src 'self'` refuses to run it --
leaving the sheet at `media="print"` and the page unstyled. Neither suite can see
that; it takes a browser. Anything else that adds an inline handler, an inline
`<script>` or a request to another origin has the same shape of symptom.

`progress-log` offers a **Download log** button once a run has failed, and only
then -- a successful run is on the page in front of you, a failed one is a bug
report. `transcript()` writes what the panel was showing plus the failure
message, which the panel itself never has: a failure arrives as its own SSE
event rather than as a log line. It keeps whole logger names where the panel
trims them, since the fixed column on screen is worth a prefix and a bug report
is not. This is presentation, not judgement -- the browser is formatting lines
Python wrote, the way `shortName` already does.

`search-form` remembers the advanced settings in `localStorage` and the request
deliberately not -- what to shop for is a new question every time -- and every read
and write of it is wrapped, so a browser that refuses storage still gets a working
form.

Its model field is a `<select>` over `GET /api/models` -- what `ollama list`
prints -- and its two edge cases are the point. A name that is chosen but *not* in
that list (a remembered setting, or a default for a model nobody pulled) is kept
in the dropdown marked "not pulled" rather than dropped, because dropping it would
silently run the search on whichever model happened to sort first. A list that
came back empty -- Ollama unreachable, or nothing pulled -- falls back to the text
box it used to be, since a dropdown holding one unusable entry is worse than
typing. Because the list belongs to one server, editing the Ollama server field
emits `refresh` and `App.refreshModels` asks that one instead.

`create_server(agent_factory=...)` is the seam the server tests inject a stub
agent through, the way `BuyAgent(config, llm=...)` is for the pipeline; its
`allowed_hosts=` is the second seam, since `None` there means "answer any `Host`"
and is what a public bind gets. Angular
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
poll would otherwise cost half a second per test on shutdown. Four server tests
speak the protocol over a raw socket, because urllib will not build a request with
a malformed `Content-Length`; `raw()` reads until the declared body has arrived,
since the headers and the body are separate writes and so can land in separate
segments. The one asserting that a body refused unread ends the connection reads
to EOF instead -- what it checks is that nothing follows the reply. 602 tests
run in about three and a half seconds: most of that is the two
tests that spawn an interpreter -- one to check `python -m buy_agent` still runs
as a script, one PowerShell for the whole of `tests/test_start_script.py` -- plus
0.7s of deliberate `StubAgent.delay` in the two server tests that need a run to
still be going: the keepalive ping, and two streams overlapping.
Nothing else should sleep, so a run that takes much longer still means something
is reaching out. 602 is what a machine with PowerShell collects *and* runs; on
one with neither `pwsh` nor `powershell` the same 602 collect but 13 of the 15
in `tests/test_start_script.py` skip, so the summary reads `589 passed, 13
skipped` -- nothing is missing, and the two that still run are the ones reading
the script as text rather than through the probe. The UI's 62 tests
run in about two seconds, most of which is building the app first.
`docs/testing.md` quotes both counts, so a new test file is two edits.

Both suites cover essentially every line, which means coverage no longer tells
you where the next test should go. `tests/test_conventions.py` covers what it
cannot: the rules that hold *between* modules, read off the declarations
themselves rather than exercised. It asserts that `api._STATUS`, the `except`
tuple in `__main__.main` (parsed with `ast`) and `BuyAgent.run`'s documented
`Raises` name the same three failures; that `ranking.SortBy`, `api.SORT_OPTIONS`,
the CLI's `--sort-by` choices and the TypeScript `SortBy` union offer the same
criteria; that `agent.types.ts` mirrors `defaults_payload`, `product_payload`
and `run_search` field for field; that the `Dockerfile` pins the versions CI tests
against, copies the built UI where the server looks, exposes the port it binds and
installs the runtime dependencies only; that every job in `ci.yml` names both a
Windows and a Linux runner, and that it sets up exactly one Python and one Node
for the three files that pin themselves to those; that the two workflows pin the
same version of every action they both use, since an update that reached only one
of them leaves both files valid and the weekly run on the older action; that
every ADR is indexed, numbered to match its heading, carries the status, date and
sections ADR-0001 asks for, and cites only records that exist; and that the
Saturday mutation run mutates the package `.coveragerc` measures, on the Python
`ci.yml` pins, with every file these tests open -- or import from outside
`buy_agent` -- named in mutmut's `also_copy`. A field added on one side of the
language boundary and forgotten on the other is otherwise invisible to both
suites.

Both Python scripts in `scripts/` are tested like the rest, by the same rule as
`clean_products`: whatever decides an answer belongs where it is testable rather
than in a workflow's shell or a one-off run. `mutation_report.py`
(`tests/test_mutation_report.py`) decides whether a mutation run passes;
`update_ollama.py` (`tests/test_update_ollama.py`) decides what "updated" means --
a digest that moved between the listing before the pulls and the one after, since
`ollama pull` reports `success` whether it replaced anything or not. It is the one
thing in `scripts/` that imports from `buy_agent` (`config` for the `$OLLAMA_HOST`
defaults), which is why it is run as `python -m scripts.update_ollama` from the
repository root rather than by path.

`scripts/start.ps1` is the README's "Starting it on localhost" as one command
with no arguments -- venv, Ollama, `ollama pull`, `ng build`, the server, the
browser, each step skipped when it is already done. It decides nothing the rest
of the project decides: the model and the Ollama server are read out of
`buy_agent.config` with a `python -c`, so `$OLLAMA_MODEL` and `$OLLAMA_HOST`
still reach it and no default is written down twice. Its four agreements with the
rest of the project are in `tests/test_conventions.py` with the other cross-file
rules -- neither constant's value appears in the script, the URL it opens a
browser at is the one `server.build_parser` binds, the build it probes for is the
one `server.DEFAULT_UI_DIR` serves, and the Python and Node it sends you to
install are the ones `ci.yml` pins.

The script itself is tested by `tests/test_start_script.py`, which cannot run it
-- it installs, downloads, starts two servers and opens a browser -- and so does
everything short of that through `tests/start_script_probe.ps1`. The probe parses
the script into an AST, lifts the function definitions out of that AST and
dot-sources them on their own, leaving the body unrun, then writes what it found
and what those functions did as one JSON document: `Run` is given this suite's own
interpreter, so its `$LASTEXITCODE` check is exercised against a real process, and
`Answers` is given a stubbed `Invoke-WebRequest` and a clock that only moves when
it sleeps, so its polling loop is exercised without a network or a wait. The AST
is also what enforces the rule the script's own error handling rests on: every
program it runs goes through `Run`, since a native command that fails raises
nothing whatever `$ErrorActionPreference` says. One PowerShell process for the
whole module, because starting one costs about as long as the rest of the suite
takes; `pwsh` or `powershell`, whichever is on PATH, and the module skips where
there is neither -- which is not Windows and not the runner CI uses.

## Environment

Development happens on Windows with PowerShell as the default shell; prefer PowerShell syntax for terminal commands, or use the Bash tool explicitly for POSIX scripts.
