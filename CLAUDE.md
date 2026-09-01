# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A shopping agent: it takes a plain-language request ("wireless headphones under
$200"), searches the web, extracts up to 10 products along with what the pages
say about them, ranks them, and logs the top 3. Built on LangChain with a local
model, served by Ollama or by a vLLM behind its OpenAI-compatible API --
`AgentConfig.provider` chooses, and `buy_agent/providers.py` is the only module
that knows the difference (ADR-0028). `ui/` is an Angular front
end onto the same pipeline, served by `buy_agent.server`. See `README.md` for
usage -- it keeps the tour and links out to the longer technical sections, which
live beside it: `docs/models.md` (keeping Ollama's models current),
`docs/docker.md` (running the web tier as a container, and what a release publishes) and `docs/testing.md`
(both suites, the coverage floors, the nightly run against a real model and the
mutation run). `demo/` is the fourth, and documents itself in `demo/README.md`:
two recorded runs of the UI, the still the README shows above them, and the
harness that took all three.

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

ollama pull qwen3:0.6b ; python -m pytest integration   # against a real model

python -m buy_agent "gaming laptop under $1500"          # run the agent
python -m buy_agent "espresso machine" --model lfm2.5 -v
python -m buy_agent "gaming laptop" --provider vllm      # the other model server
python -m buy_agent "running shoes" --sort-by price --json results.json
python -m buy_agent "wireless earbuds" --source rtings.com --source @mkbhd

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
no `--ui-dir`. Neither model server is in the image or started by it (ADR-0015):
the container talks to the host's through `host.docker.internal` -- both
`$OLLAMA_HOST` and `$VLLM_HOST` are set to it, since either provider can be
chosen per run -- which needs
`--add-host=host.docker.internal:host-gateway` on Linux. `ENTRYPOINT` is `python`
and `CMD` is `-m buy_agent.server --host 0.0.0.0`, so the CLI is reachable from
the same image and `--host` stays out of the server's own default (it binds
loopback everywhere else). No pull request builds it -- `release.yml` does, once
per release, and publishes it to `ghcr.io` (ADR-0030); `tests/test_conventions.py`
is what keeps its version pins, its copy destination and its `EXPOSE` in step
the rest of the time.
`.dockerignore` narrows what the build even sees: `tests/`, `integration/`,
`docs/`, `scripts/`, `demo/`, `.github/`, every Markdown file, the dev and
mutation requirements with `setup.cfg` beside them, and every local build
artefact (`.venv/`, `ui/node_modules/`, `ui/dist/`, `ui/.angular/`, `mutants/`,
`__pycache__/`) stay out of the context, so the Node stage builds `ui/` from
source rather than copying a stale local `dist/` -- and `demo/`, which carries a
video the size of the rest put together, is not uploaded to the daemon at all.
Nothing tests that file -- the convention tests read the `Dockerfile` and not
`.dockerignore` -- so a path added to one of those directories is only kept out
of the image by keeping this list current.

```powershell
docker build -t buy-agent .
docker run --rm -p 8000:8000 buy-agent
docker run --rm buy-agent -m buy_agent "espresso machine"
```

`$BUY_AGENT_PROVIDER` moves which model server a run talks to, and each provider
has its own variables behind it -- `$OLLAMA_MODEL`/`$OLLAMA_HOST` and
`$VLLM_MODEL`/`$VLLM_HOST`/`$VLLM_API_KEY` -- read on that server's own row in
`providers.PROVIDERS` (ADR-0029). `AgentConfig.model`, `AgentConfig.base_url` and
`AgentConfig.api_key` therefore default to the *empty string* and are
resolved per provider in `__post_init__`, through `AgentConfig.model_server`:
which value is right depends on a
sibling field, so a plain field default could only ever be right for one of the
two, and "unset" is spelled the same way a blank form field is (ADR-0012). That
is why `--model` and `--base-url` default to `""` on the CLI and interpolate
every provider's pair into their help rather than one. `$VLLM_API_KEY` is the one
setting with no flag and no form field -- it is a secret, so it stays out of a
shell history, out of `defaults_payload` and out of `provider_options()`, and an
Ollama run never carries it at all. Every other CLI flag defaults to the
matching `AgentConfig` field, so a new setting is added
in `config.py` and picked up rather than repeated. One field is deliberately not
named the same on the way out: `AgentConfig.reasoning` is `--think`
(`BooleanOptionalAction`, so `--no-think` is the off switch) on the CLI and
`think` in both the JSON payloads and `agent.types.ts` -- the tri-state it carries
is Ollama's thinking mode, and `None` means "send nothing and leave the model
alone" rather than "off". It pairs with `num_ctx`: the extraction prompt runs to
~4.3k tokens, so on Ollama's default 4096 window a thinking model reasons until
the context is gone and never emits any JSON. Ollama's default model is
`gemma4:12b`, which thinks, so the defaults that make it answer travel with it -- `reasoning`
is `False` and `num_ctx` is `8192` rather than the `None` each used to be. A
model that cannot think ignores both; one that wants its own behaviour back is
given `num_ctx=None, reasoning=None`, which is the only way to send nothing and
is reachable from neither front end (ADR-0019). `num_ctx` is also the one setting the two providers do not share: vLLM fixes its
window with `--max-model-len` when it starts, so `Provider.takes_num_ctx` is
false there, the value is not sent, and the CLI's help and the form's field both
say so rather than accepting a number nothing reads. `reasoning` *is* shared --
Ollama's own `think` option, vLLM's `chat_template_kwargs.enable_thinking`.
`buy_agent.server` wants the
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
`.github/workflows/integration.yml` is the second workflow: `pytest integration`
against a real Ollama at 03:41 UTC nightly (and on `workflow_dispatch`), never on
a pull request, capped at `timeout-minutes: 5` -- which covers installing Ollama,
pulling the model and the inference. Linux only, unlike `ci.yml`: what it asks is
whether a real model still answers with the schema it was given, which is the
same question on either platform. Ollama and the model tag are the one pair this
project deliberately leaves unpinned, because noticing that a release changed
`method="json_schema"` decoding is half of what the job is for (ADR-0026).

`.github/workflows/mutation.yml` is the third workflow: mutmut against
`buy_agent/` at 05:17 UTC on Saturdays (and on `workflow_dispatch`), never on a
pull request. Its settings live in `setup.cfg` -- which exists for that and is not
a packaging file -- and `scripts/mutation_report.py` turns a run into the job
summary and fails it if the score drops under 75% (ADR-0016). A run copies the
tree to `mutants/` and tests the copy, so anything the suite reads off disk or
imports from outside `buy_agent` has to be named in `also_copy`, or the whole run
dies at collection.

`.github/workflows/release.yml` is the fourth: it runs when a release is
*published* (and on `workflow_dispatch` with a tag, so a failed upload can be
retried without re-cutting the release) and puts two packages on GitHub --
`buy-agent-<version>.tar.gz`/`.zip` with a `SHA256SUMS.txt`, attached to the
release by `gh`, and `ghcr.io/<owner>/<repo>:<version>` built from the
`Dockerfile`, with `latest` following full releases only (ADR-0030). The archive
holds `buy_agent/`, the built UI at `ui/dist/ui/browser`, `requirements.txt`,
`README.md` and `docs/`: there is no wheel and no sdist, because the project is
still run from a directory. Both jobs check out `$TAG` rather than the branch the
workflow sits on -- assets built from `main` and attached to an older tag install
and serve perfectly and are the wrong code -- and neither package is published
unattended: the archive is unpacked, installed and started, the image is run as a
container, and both are asked for `/api/config` and for `/`, which is the Python
half answering and the UI being served from where the server looks. Linux only,
like the other two schedules: what is packaged is a Python package and static
files, identical whichever runner assembled them.

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
to ADR-0035 and every record is Accepted, so the next free number is 0036.

`.claude/skills/` holds the chores that span those files, one directory each:
`add-option` walks a new setting through `config.py`, both front doors,
`agent.types.ts` and the form; `add-adr` takes the next number off the directory
rather than off the sentence above, which has gone stale before; `preflight` is
the gate `ci.yml` applies, both suites and both coverage floors. They are
checklists over the rules written down here, not new rules -- a rule belongs in
this file or in a convention test, where it holds whether or not anybody invoked
a skill.

The pipeline is deliberately **not** a tool-calling agent loop. The LLM is used
for the two steps it is reliable at, and ordinary Python does everything else,
because these servers are typically run with small models that drive tool loops
badly.

```
request -> refine query (LLM) -> DuckDuckGo (once, or once per named
source) -> fetch + condense pages
        -> extract products and their opinions (LLM) -> clean_products
        -> ground -> deduplicate -> rank -> log top 3
```

That order is load-bearing in both joints. `clean_products` runs before `ground`
so a name still wearing its publisher suffix ("... Review | AudioSite") is not
failed by the coverage check for tokens the page never had to contain; `ground`
runs before `deduplicate` so `_combine` only ever merges figures the sources
back. That is necessary and not sufficient: a merge that took each field on its
own would still report a pairing no page printed, so `models.QUALIFIERS` says
which fields only qualify another and `_fill_gaps` moves the group.

| Module | Responsibility |
| --- | --- |
| `agent.py` | `BuyAgent.run()` -- orchestrates the pipeline, translates model-server errors |
| `extraction.py` | Both prompts, both chains, name cleaning, deduplication |
| `fetch.py` | Fetches result pages, keeps the lines quoting a figure or passing judgement, and tallies how the rest failed |
| `verification.py` | Drops products, figures and quotes absent from the sources; links what is left |
| `ranking.py` | Scoring and sorting; no LLM involved |
| `models.py` | `ExtractedProduct` (LLM-facing) vs `Product` (domain) |
| `search.py` | DuckDuckGo wrapper -- and nothing else (ADR-0021) |
| `sources.py` | What a trusted source is: domain, term, `site:` query, `covers` |
| `providers.py` | Everything that differs between Ollama and vLLM, and nothing else |
| `config.py`, `logging_setup.py`, `__main__.py` | Config, the report, the CLI |
| `api.py` | Request options in, ranked products out -- the web-facing half worth testing |
| `server.py` | A stdlib HTTP server: the JSON API, the event stream, the built UI |

Nine conventions matter when changing this code:

- **A model server is one row in one table, reached one way.**
  `providers.PROVIDERS` holds each server whole -- what it defaults to
  (`model`, `base_url`, `api_key`, read from its own environment variables) next
  to how it is talked to (the chat model, the listing, the transport errors that
  mean "not there", the sentence that failure carries) plus `takes_num_ctx`
  (ADR-0029). The listing answers `InstalledModel`s rather than names, because
  what a server holds and what a run can use are the same question only on vLLM:
  Ollama's `installed` asks `ollama show` per tag for its `capabilities`, so an
  embedding-only pull is marked in the picker rather than offered (ADR-0032), and
  a probe that fails leaves the tag usable -- hiding a working model on a failed
  probe is the worse mistake. It still imports nothing from `config`; the dependency runs the
  other way, and `AgentConfig.model_server` is the *only* place a provider name
  becomes behaviour. So `agent.py` reads
  `config.model_server.chat_model(config)`, catches
  `config.model_server.transport_errors` and raises with
  `config.model_server.hint(config, exc)`, and `api.installed_models` asks
  `config.model_server.installed(config)` -- there are no module-level wrappers
  to go through and no `if provider == ...` anywhere above the table. A setting
  one server takes and the other does not gets a declaration on the row rather
  than a branch in the CLI, the API and the form; a hint sentence both servers
  would write goes in `_too_slow_hint` or `_unreachable_hint` rather than in each
  provider's own (ADR-0028, ADR-0029).

- **The sources are whatever was searched, and the shopper may narrow them.**
  `AgentConfig.sources` is empty by default, which is the whole web. Given any,
  `BuyAgent._search` runs one search per source (`site:` takes one domain), puts
  every result through `Source.covers()` before keeping it, pools them
  deduplicated by URL and cuts the pool back to `search_results` -- so the
  fetching does not multiply with the sources even though the searching does.
  Nothing downstream knows: the pool is what gets fetched, extracted from and
  grounded against either way, which is how "every figure and every quote was
  printed by a page the shopper named" holds by construction (ADR-0027). What is
  enforced is the **domain**; a handle or a section only narrows the query,
  because a URL cannot carry it -- a video's address says which video it is and
  not who published it, so filtering on one would empty the pool instead of
  narrowing it. There is no fall back to the wider web when the named sources
  find nothing: that would report facts from pages the shopper refused.
  `sources.py` does no I/O -- it decides what a source *is* and `agent.py` does
  the searching, which is also what keeps `search.py` a DuckDuckGo wrapper.
- **`ExtractedProduct` uses sentinels, `Product` uses `None`.** The LLM-facing
  schema asks for `-1`/`""` rather than nullable fields: Ollama compiles the JSON
  schema into a decoding grammar, and a required `number` makes `"N/A"` -- which
  would fail validation for the entire batch -- structurally impossible. Keep new
  extraction fields non-nullable with a sentinel, and convert in `to_product()`.
  `opinions` is the one field whose sentinel survives into `Product` as itself:
  the empty list already spells "nothing was said", and a `None` beside it would
  be a second spelling every caller had to handle.
- **Never rank on an unverified number, never link to an unverified page, and
  never quote what nobody said.**
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
- **A quote is checked as running text, on the page it came from** (ADR-0024,
  ADR-0025). `fetch.py`
  sweeps each page twice -- once for the lines quoting a figure, once for the
  lines passing judgement, each on a budget of its own (`page_chars` and
  `opinion_chars`), so neither kind crowds the other out -- and
  `verify_opinions()` then drops every quote the sources do not contain as
  overlapping runs of five consecutive words, most of which must be found. A
  word-by-word check would pass any sentence assembled out of vocabulary the
  pages share, which is what a small model paraphrasing produces: an invented
  price is a number nobody wrote, an invented quote is words in a reviewer's
  mouth. The tolerance is deliberately at the ends and not in the middle -- a
  word the model added in front breaks only the runs at that end, a word changed
  in the middle breaks every run spanning it and fails. `_OPINION` is a
  vocabulary of judgement ("reviewers found", "the downside is",
  "disappointing"), never of subject matter: "wireless" or "battery" would take
  every line on the page. Unlike the figures, a quote is checked against one page
  at a time and only a page `mentions_name` says is about the product, which is
  why `verify_opinions()` takes the results and not the pooled haystack: pooled,
  a real verdict on the electric kettle three results down was evidence about
  these headphones. That makes `mentions_name` the decider of three things --
  whether a product is real, where it links, and what may be quoted for it -- so
  a word added to `GENERIC_WORDS` loosens all three.
- **A currency belongs to its price, and a review count to its rating**
  (ADR-0022). Both are facts about the *listing* that printed them, not about the
  product, so `models.QUALIFIERS` pairs them up and `_fill_gaps` carries a
  qualifier over only where the figure it describes is carried over too, or where
  both listings quote the same one. The same rule binds at both earlier stages:
  in `verification.verify_numbers`, where a figure the sources do not back takes
  its qualifiers down with it, and in `ExtractedProduct.to_product`, where a
  review count the model reported with no rating beside it never becomes one at
  all. Either way a count left standing alone describes nothing, reads "unrated"
  on the card, and still feeds the popularity half of the score. That
  is why the pairing is declared once beside the fields it names rather than
  restated in the merge's table and the grounding's. Field-by-field merging passes grounding --
  each half really is in the sources -- while reporting "129.00 EUR" for a page
  that said 129 and a page that said "249 EUR". A new field that only makes sense
  next to another belongs in that other's group rather than in one of its own.
  `opinions` is deliberately outside that scheme, in `_merge_opinions`: two
  listings' quotes are both kept, because two reviewers -- unlike two prices --
  are not in conflict, and each quote was grounded on its own before the merge.
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
- **The report is output; the progress is narration.** `logging_setup` splits
  them by handler rather than by logger: `log_top_products` marks its records and
  they go to stdout, everything else goes to the stderr handler `basicConfig`
  installed, and both still reach every other handler -- which is what keeps the
  browser's progress panel showing one stream and a `caplog` seeing the whole
  run. Only the *console* handler is told to skip the report; a handler writing
  anywhere else is nobody's stream to take lines out of. Without the split
  `python -m buy_agent ... > top.txt` wrote an empty file, all of it having gone
  to stderr.
- **Model output is never trusted as judgement.** The model reports article
  headlines as products; `clean_products` filters them. Anything that decides the
  answer -- filtering, scoring, ordering -- belongs in Python, where it is testable.

`BuyAgent.run()` raises exactly three things -- `ValueError`,
`ModelUnavailableError`, `SearchError` -- and `__main__.main()` catches exactly
those around the run, logging them and returning 1 (130 on Ctrl-C, and
`NOTHING_FOUND` -- 3 -- for a run that worked and found nothing, which a shell
told 1 could not tell from a stopped model server; 2 is argparse's own, so the
codes a script branches on are the five `--help` now ends by listing). It has a
second, unrelated `except` further down, for an `OSError` from writing the
`--json` file, which is why `tests/test_conventions.py` reads the handlers of
the `try` holding the `.run()` call rather than every handler in the function. `api._STATUS` maps the same
three onto HTTP statuses (400, 503, 502). A new failure mode needs handling in
all three places, or it reaches the user as a traceback and the browser as a 500.
Within the agent only query refinement is recoverable: it falls back to the raw
request, but lets `ModelUnavailableError` through rather than searching with a
model that is not there. What has to be caught to produce that error is the
provider's to say -- `BuyAgent._invoke` catches
`self.config.model_server.transport_errors` and nothing written down locally -- and for Ollama the tuple is wider than it
looks: the ollama client turns a refused connection into a
builtin `ConnectionError` only on its *non*-streaming path, and `ChatOllama`
always chats over the streaming one, so a stopped server, a model too slow to
answer and a killed stream all arrive as raw `httpx` errors -- none of which is an
`OSError`. Hence `httpx.HTTPError` in the tuple, next to ollama's own
`RequestError`, which is a different class from httpx's identically named one.
vLLM's is `openai.OpenAIError`, the root of that client's hierarchy, plus the two
above for the listing, which goes over `httpx` directly.

The CLI and the API are two ways of filling in the same `AgentConfig`, and both
set `search_results = max(results, top)` -- searching for fewer pages than the
report intends to show would cap the report. A new option belongs in
`__main__.build_parser`, `api.parse_options` and `api.defaults_payload`, which is
what seeds the web form. A numeric one belongs in `config.LIMITS` as well, which
is where the range it is held to is declared once and read by both doors:
written down on each of them, the CLI comes to accept what the API refuses, and
`--results 0` then searches the web, reads ten pages and asks the model for no
products at all. On the CLI the check is a `type` function, so an out-of-range
number is a usage error rather than a minute wasted; `tests/test_conventions.py`
asserts the two doors refuse the same numbers. `region` is the same rule for a
shape rather than a range: `config.REGION` is a country and then a language
(`us-en`, `pl-pl`, and the three-letter `hk-tzh`), `config.parse_region` is the
only place it is checked, and both doors go through it -- the CLI as a `type`
function, the API as `_as_region` -- with `AgentConfig.__post_init__` behind them
for a Python caller. A shape and not the list of codes that exist, because `ddgs`
asks several engines and each reads the two halves its own way, so DuckDuckGo's
own list would refuse `de-de` which Google takes (ADR-0031). The shape is not the
whole story -- `en-us` is the right shape the wrong way round -- so
`BuyAgent._region_note` also names the region in the "Search returned nothing"
warning, unless it is `config.DEFAULT_REGION`: that one is known to work, and
naming it would send a shopper to correct a setting that is correct. `provider` is the one option that is offered in *four*
places -- those three plus the `ProviderOption` rows `defaults_payload` sends the
picker -- and every one of them reads `providers.PROVIDERS` rather than listing
the names again. It is also the one option that changes what two others mean, so
both front ends pass `model` and `base_url` through as `""` when they were not
given: the config resolves the pair, and the form fills both fields in when the
picker changes so neither is left holding the other server's answer. `sources` is the one option that is a list, and so the
one that does not go through `api._read`: that renders every value with `str`
first, which turns a JSON array into its Python repr, so `_read_sources` takes
either an array or the separated string a query string can carry. On the CLI it
is `--source`, repeatable, and its `type` checks the spec but hands back the
text: checking there is what makes a bad source a usage error carrying the
shapes that work (argparse throws a type function's `ValueError` away and prints
"invalid value" instead), while parsing every flag together in `main` is what
makes two flags naming one site one source. `weights` is the one field neither of them fills in:
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
| `GET /api/models` | What a named server is serving, or why it could not be asked and what to do about it |
| `GET /api/sources` | Whether a Trusted sources field names sites -- the one endpoint that runs nothing |
| `POST /api/search` | One run, as JSON |
| `POST /api/rank` | A finished run's products in another order -- the only endpoint that runs no pipeline |
| `GET /api/search/stream` | One run, as SSE: `log` lines, then `result` or `failure` |

Six things there are load-bearing:

- **A run is streamed, not requested.** A search takes tens of seconds, so
  `GET /api/search/stream` runs the agent in a worker thread and relays its log
  lines as Server-Sent Events while it works. `_LogRelay` routes records by the
  thread that produced them, which is what keeps two concurrent runs from seeing
  each other's progress. Extraction is slow and logs nothing while it runs, so a
  `ping` event goes out every 15s to keep browsers and proxies from timing the
  stream out -- and every relayed line carries the `time` Python logged it at, in
  the CLI's own `%H:%M:%S`, since the gap between two lines is the only thing
  that tells a four-minute extraction from a four-second one. `POST /api/search`
  is the same run in one response.
- **Closing the stream stops the run, at the next step and not at the click**
  (ADR-0034). `BuyAgent.run` calls its `checkpoint` with the name of each step
  about to start -- `search`, `fetch`, `extract`, `rank` -- and nothing in the
  pipeline catches what that raises, which is how a run ends. The first frame
  `_stream_search` cannot write (a log line, or the ping 15s later) sets the
  flag `server._stop_when` reads, and the worker's `_Stopped` goes no further
  than the worker: a stopped run is not a failure, so it stays out of
  `api._STATUS`, out of `__main__.main` and out of `run`'s `Raises:`, and the
  three-failure agreement holds. What this cannot do is cancel a chat call
  already in flight -- there is no way into either client -- so the granularity
  is a step, and the browser's Stop line is what says so rather than a promise
  nothing can keep. A step added to `run` announces itself or a stopped run pays
  for it anyway; every stand-in for `BuyAgent` takes the keyword, since
  `run_search` always passes it.
- **The stream's failure event is called `failure`, not `error`.** A browser's
  `EventSource` delivers transport errors under `error` and then reconnects; a
  named `error` event would be indistinguishable from a dropped connection, and
  the reconnect would silently start the whole search again. For the same reason
  `HEAD /api/search/stream` answers 405 rather than starting a run nobody reads.
- **The browser decides nothing.** Ranking, grounding and even the wording of an
  unknown price stay in Python: `product_payload` sends `price_label` and
  `rating_label` next to the raw figures, `sort_by` is a request parameter
  rather than a client-side re-sort -- for a finished run too, which posts its
  products back to `POST /api/rank` rather than sorting the array it is holding
  (ADR-0035) -- `installed_models` sends each model's
  `completion` beside its name so the dropdown marks what it could not have
  worked out, and it sends the provider's
  `label` next to the models so the header pill never has to decide what to call
  the server it is reporting on -- and, where it could not be reached, the `hint`
  that provider's own row would have raised a run with, so the page says
  "Start it with:  ollama serve" without a second wording in TypeScript. The same
  rule as `clean_products` -- whatever decides the answer belongs where it is
  testable. `ui/src/app/agent.types.ts` mirrors those payloads for TypeScript, so
  a field added to `api.py` is added there too.
- **Re-ordering a finished run is a request, not a re-run** (ADR-0035).
  `rank_again` is `rank_products` and nothing else -- no agent is built, no page
  is fetched, no model is asked -- and it answers the shape `run_search` answers
  with, so the page shows a re-sorted run through the same view. The products
  travel in the body rather than being kept server-side under a run id: a
  session store is a lifetime, an eviction policy and a leak on a server that is
  stdlib on purpose, and the browser is already holding them. What it sends is
  what it was sent, and every product is scored again from the set, so an edited
  figure changes nothing. `api.results_payload` is the one shaping of a run's
  products -- the API's answer, the file `--json` writes and the file the page's
  Download results button hands over -- so the browser saves the answer rather
  than composing a document of its own. A re-sort that fails is said beside the
  results it left alone, not in the banner that means the *run* failed, which is
  also what keeps the log panel from offering a bug report about a search that
  worked.
- **A blank value means "use the default".** `api.parse_options` treats a missing
  key and an empty string alike, because an empty form field means "unset", not
  "zero" -- and the UI's `toQuery` drops blanks for the same reason. Values that
  are present but unusable raise `ApiError` with the status the client deserves.
- **The form refuses first, and never on a rule of its own** (ADR-0033). Every
  setting the page holds is one the server can judge without a model, a network
  or a minute of waiting, so it is judged before a run is opened -- but the rule
  is always Python's. `defaults_payload` ships `limits` (`limits_payload`, off
  `config.LIMITS` through `api._BOUNDED`, which is the one table saying which
  config field bounds each request key), and the form binds `[min]`/`[max]` from
  it and holds each number to it; a source is not a range, so the form asks
  `GET /api/sources`, which reads the field with the same `parse_sources` a run
  would and answers `{"sources", "error"}` -- 200 either way, and naming the spec
  it was about so an answer for text since typed over is dropped rather than
  shown against what replaced it. What the page cannot judge is still marked
  where it belongs: `ApiError` carries `field`, the request key the value arrived
  under, `payload()` sends it, and the `failure` event carries it to the box.
  `parse_options` is untouched -- this is the earlier line, not the only one, and
  a CLI-shaped client that never sees a form is refused exactly as before. A
  region is deliberately *not* checked here: its shape stays in Python
  (ADR-0031), and what it gains is the mark.
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

The header pill says whether the model server answered; `App.unreachable` is
what says why and what to type. It reads the `hint` `installed_models` sent and
shows it as a line under the pill, because a `title` is hover-only -- unavailable
on a touch screen, easy to miss with a mouse, and inconsistently announced by
screen readers -- and this is the one failure a shopper meets before anything
else whose fix is a single command. It is `null` for a server that answered, and
also when the *agent* server is the one that did not: nothing came back to ask,
and a sentence written here rather than in `providers.py` would be a second
wording to keep true. The sentence is `white-space: pre-wrap`, which keeps the
run of spaces Python puts in front of the command, and a **Check again** button
sits beside it: the pill re-asks when it is clicked, but nothing about a status
pill says so, and the moment someone has just run that command is the moment they
need to say they have -- without it the only way back is reloading the page.

`progress-log` follows the tail the way a terminal does, but only while the
reader is at it: the scroll handler sets `sticking` from how far the panel is
from the bottom, and a reader who has scrolled up to re-read a finished step is
left where they are rather than dragged back down by the next line. It offers a
**Download log** button once a run has failed, and only
then -- a successful run is on the page in front of you, a failed one is a bug
report. `transcript()` writes what the panel was showing plus the failure
message, which the panel itself never has: a failure arrives as its own SSE
event rather than as a log line. It keeps whole logger names where the panel
trims them, since the fixed column on screen is worth a prefix and a bug report
is not. This is presentation, not judgement -- the browser is formatting lines
Python wrote, the way `shortName` already does.

It also counts the wait out, beside the working pill and then as the total in
place of it. Extraction is the slow step and it logs nothing at all while it
runs, so the panel is otherwise a frozen list of lines under a pulsing dot, with
no way to tell a model still thinking from one that has stopped answering -- the
timestamps say that afterwards, and `elapsed()` says it while it is happening.
The clock starts and stops on the `running` input, redraws once a second and is
cleared on destroy; `duration()` writes it in seconds and minutes (`8s`,
`2m 14s`) rather than as a `0:08` clock, since this is how long something took
and not what time it is.

`search-form` remembers the advanced settings in `localStorage` and the request
deliberately not -- what to shop for is a new question every time -- and every read
and write of it is wrapped, so a browser that refuses storage still gets a working
form.

It also refuses what the server would, before the run rather than a minute into
it (ADR-0033). `problems()` is what the page worked out -- each number against
the range that came down with the defaults, and the sources field against
whatever `GET /api/sources` last said about the text it currently holds -- and it
is what gates `canSubmit`, since none of it costs anything to know. `notes()` is
what is shown under each field: `problems()`, plus the `rejected` input for a
field the page has no rule for, which is the `field` a `failure` event named. The
server's mark does not gate the button -- it is about what was sent, and what is
in the box now may well be right -- and it clears when the next run starts. The
sources check goes out on `change` rather than on every keystroke (half a spec is
not a mistake, and clicking Find products leaves the field first) and once more
after `restore`, since a remembered bad source is one nobody is about to retype.
The `numbers` table, keyed by the name each field is sent under, is the one place
that mapping lives, and `tests/test_conventions.py` holds it against
`limits_payload`.

Every one of those marks is on a field inside the Settings panel, which is shut
until somebody opens it -- so the form opens it itself the first time `flagged()`
is not zero, and the summary carries the count for a reader who has shut it
again. Left alone, a marked box says nothing and a run refused for a setting
leaves a greyed-out Find products button with no visible reason anywhere on the
page; the case that needs no keystroke at all is a remembered source or a
remembered number the server would refuse, restored, checked and marked before
the form is first drawn. The effect fires on the marks changing and not on the
panel's state, so closing it again stays the reader's to do. `placeholders()` is
the smaller half of the same idea: a cleared number box means "use the default"
(ADR-0012), which is an answer rather than a mistake, so each box names the
number it falls back to the way the context field already named its own.

Its model field is a `<select>` over `GET /api/models` -- what `ollama list`
prints, or the one model a vLLM reports -- and its three edge cases are the point. A
name that is chosen but *not* in
that list (a remembered setting, or a default for a model nobody pulled) is kept
in the dropdown marked "not served" rather than dropped, because dropping it would
silently run the search on whichever model happened to sort first. A model that
*is* there and reports no `completion` capability is marked "embedding only" for
the same reason turned around: it is a pull someone made on purpose, and hiding
it would leave nothing to explain why the tag they remember is gone (ADR-0032).
Which of the two an entry gets is `ModelOption.note`, filled from the
`completion` Python sent -- the browser writes the suffix, not the judgement. A list that
came back empty -- the server unreachable, or nothing loaded -- falls back to the
text box it used to be, since a dropdown holding one unusable entry is worse than
typing. Because the list belongs to one server, editing the address field
emits `refresh` and `App.refreshModels` asks that one instead. `refresh` carries
a `ModelSource` -- the provider *and* the address -- because the address alone is
not the question: a vLLM asked Ollama's question answers 404. Changing the
provider picker emits the same event, after filling the model and address fields
from that provider's row, since leaving them would ask a vLLM for an Ollama tag on
Ollama's port. `takes_num_ctx` on that row is what disables the context field and
replaces its note, rather than the form testing the provider's name.

`create_server(agent_factory=...)` is the seam the server tests inject a stub
agent through, the way `BuyAgent(config, llm=...)` is for the pipeline; its
`allowed_hosts=` is the second seam, since `None` there means "answer any `Host`"
and is what a public bind gets. Angular
components are tested in jsdom with `TestBed`; `AgentService` is tested against a
fake `EventSource` rather than a live one.

`demo/` records that page running, as the two `.mpg` files `demo/README.md`
describes. It is the same seams again from the other side: `demo/server.py`
starts the real `buy_agent.server` with `search_web`, `enrich` and the chat model
replaced by one of the scripts beside it (`books.py`, `laptops.py`, chosen with
`--script`, and a third is a module offering the same five names plus a row in
`server.SCRIPTS`), so everything between the search and the ranking is the real
pipeline and neither Ollama nor the network is needed to reproduce a recording.
The scripts make the fake model wrong in the six ways a small model is wrong, so
the progress panel shows `clean_products`, `ground`, `verify_opinions`,
`attribute_sources` and `deduplicate` each catching one. `demo/record.mjs` drives
Chromium through Playwright and encodes with ffmpeg; `--pace` scales the
scripted delays, since a real run's two silent model calls are dead air on tape.
`demo/screenshot.mjs` is the same drive for one frame -- it takes `docs/ui.png`,
the form the README shows, off this server rather than off `buy_agent.server`
because the model dropdown and the header pill are answers from an Ollama, and a
picture taken without one says "Ollama unreachable" over a text box. It is
clipped to the form card, so a field added to the settings makes it taller
rather than falling off the bottom.
Nothing here is imported by `buy_agent/` or by either suite -- `testpaths` never
reaches it -- so it is not covered, not mutated and, per `.dockerignore`, not in
the image.

## Tests

`BuyAgent(config, llm=...)` is the injection seam: `tests/conftest.py` provides a
`FakeLLM` exposing only `with_structured_output`. The network is monkeypatched
in three places: `buy_agent.agent.search_web` and `buy_agent.agent.enrich` for
pipeline tests, and `buy_agent.search.DDGS` / `buy_agent.fetch.httpx.Client` for
the wrappers' own tests. `search_web` is patched on `agent` and only there, which
is why the fan-out over named sources lives in `agent.py` rather than beside the
rest of `sources.py`: a second call site elsewhere would be a second thing to
patch, and a test that forgot it would reach the real DuckDuckGo silently. Both model clients are patched
where `buy_agent.providers` imported them -- `buy_agent.providers.ChatOllama` for
the kwargs a real `ChatOllama` would be built with, `buy_agent.providers.Client`
for the listing that names the installed models in an error -- that fake answers
both `list` and `show`, since an Ollama listing is now a call per tag -- and
`buy_agent.providers.httpx.get` for vLLM's `/v1/models`. Patching `ollama.Client`
no longer works: `providers.py` imports the name at module level, which is also
the only place either client is named. A row of `providers.PROVIDERS` is
compared by identity only through the module -- `providers_module.OLLAMA`, never
a name imported from it -- because `tests/test_providers.py` reloads that module
to re-read its environment-derived defaults: a reload re-runs it over its own
globals, so `provider_for` answers with the new rows while a name bound at import
time still holds the old ones. The teardown puts the *values* back, which is why
comparing those is safe anywhere; the objects are new either way, so an `is`
against an imported name passes or fails on which file pytest ran first -- every
ordinary run one way, the Saturday mutation job's clean-test pass the other.
Patching `DDGS.text` does *not* work -- the name `ddgs` exports is a wrapper that
constructs a different class. No test in `tests/` touches the network or a model
server; keep it that way -- `integration/` is where a real model goes, and it is
outside `testpaths` so that a bare `pytest` cannot reach it (see below). The
server tests are the one exception to "no sockets": they bind loopback, because
routing and status
codes are what they are about. They pass `serve_forever(0.01)` -- the default 0.5s
poll would otherwise cost half a second per test on shutdown. Four server tests
speak the protocol over a raw socket, because urllib will not build a request with
a malformed `Content-Length`; `raw()` reads until the declared body has arrived,
since the headers and the body are separate writes and so can land in separate
segments. The one asserting that a body refused unread ends the connection reads
to EOF instead -- what it checks is that nothing follows the reply. 1026 tests
run in about four seconds: most of that is the two
tests that spawn an interpreter -- one to check `python -m buy_agent` still runs
as a script, one PowerShell for the whole of `tests/test_start_script.py` -- plus
1.0s of deliberate `StubAgent.delay` in the three server tests that need a run to
still be going: the keepalive ping, two streams overlapping, and the run a reader
who has gone stops at its next step.
Nothing else should sleep, so a run that takes much longer still means something
is reaching out. 1026 is what a machine with PowerShell collects *and* runs; on
one with neither `pwsh` nor `powershell` the same 1026 collect but 13 of the 17
in `tests/test_start_script.py` skip, so the summary reads `1013 passed, 13
skipped` -- nothing is missing, and the four that still run are the ones reading
the script as text rather than through the probe. The UI's 121 tests
run in about two seconds, most of which is building the app first. The 20 in
`integration/` are counted separately and collected only by being named.
`docs/testing.md` quotes all three counts, so a new test file is two edits.

Both suites cover essentially every line, which means coverage no longer tells
you where the next test should go. `tests/test_conventions.py` covers what it
cannot: the rules that hold *between* modules, read off the declarations
themselves rather than exercised. It asserts that `api._STATUS`, the `except`
tuple in `__main__.main` (parsed with `ast`) and `BuyAgent.run`'s documented
`Raises` name the same three failures; that `ranking.SortBy`, `api.SORT_OPTIONS`,
the CLI's `--sort-by` choices and the TypeScript `SortBy` union offer the same
criteria; that every provider in `providers.PROVIDERS` is offered by
`--provider`, by `api.PROVIDER_OPTIONS` and in the rows the form's picker is
built from, and that `ProviderOption` is mirrored in TypeScript; that `agent.types.ts` mirrors `defaults_payload`, `product_payload`
and `run_search` field for field; that the form holds a number to a range for
every range `limits_payload` ships and writes no `min` or `max` of its own into
its template, and that every key `parse_options` reads is one `SearchOptions`
sends -- a key it reads and the form never sends is a refusal marking a box that
is not there (ADR-0033); that the `Dockerfile` pins the versions CI tests
against, copies the built UI where the server looks, exposes the port it binds and
installs the runtime dependencies only; that every job in `ci.yml` names both a
Windows and a Linux runner, and that it sets up exactly one Python and one Node
for the three files that pin themselves to those; that every workflow sets up
that same Python and builds with that same Node, and that the workflows pin the
same version of every action they
share, since an update that reached only one of them leaves every file valid and
a scheduled run on the older action; that the release archive puts the built UI
where `server.DEFAULT_UI_DIR` looks and that both of its jobs check out the tag
being released rather than a branch; that the nightly run pulls the tag
`integration.TINY_MODEL` names, names the directory `testpaths` leaves out, sets
`$BUY_AGENT_REQUIRE_OLLAMA` so an absent Ollama fails instead of skipping, and
caps itself at the five minutes the docs quote; that
every ADR is indexed, numbered to match its heading, carries the status, date and
sections ADR-0001 asks for, and cites only records that exist; and that the
Saturday mutation run mutates the package `.coveragerc` measures, on the Python
`ci.yml` pins, with every file these tests open -- or import from outside
`buy_agent` -- named in mutmut's `also_copy`. A field added on one side of the
language boundary and forgotten on the other is otherwise invisible to both
suites.

`integration/` is the second Python suite and the only place a real model is
involved (ADR-0026), and it is Ollama's alone: vLLM needs a GPU and a CPU runner
cannot host one honestly, so that provider's half is asserted in
`tests/test_providers.py` and named as a gap in ADR-0028. It is a directory rather than a marker because `pytest.ini`
keeps `testpaths = tests`: "nothing in the suite touches Ollama" is then a
property of where a file sits, not of anyone remembering an annotation. Four
things there are load-bearing:

- **The model is real; the web is not.** `search_web` and `enrich` are still
  faked, over ten fabricated pages `integration/conftest.py` owns. A nightly
  failure caused by DuckDuckGo rate-limiting says nothing about this code. The
  fake stops at the transport: `enrich` reads the fabricated text rather than a
  URL and then runs the real `fetch.condense` over it, so the prompt is shaped
  as a production prompt is and is wide enough for ADR-0019's `num_ctx` question
  to arise.
- **One run, many assertions.** A CPU model answers in seconds, so a
  session-scoped `live_run` fixture runs the pipeline once and each test reads
  something different off it. The extraction chain is *wrapped* rather than
  replaced -- an appended `RunnableLambda` records the raw `ProductList` and
  hands it on -- so what runs underneath is the run `BuyAgent` would have made.
- **Almost nothing asserts the model was right.** The assertions are the
  invariants: every name, figure and quote in the sources, every link a page
  that was searched, no repeats, the ranking ordered. A 0.6B model is not held
  to an answer. The one exception is a smoke test that something was extracted,
  since every other assertion passes vacuously on an empty list -- and a second
  that something was quoted, for the same reason.
- **An absent Ollama skips locally and fails on the schedule.**
  `$BUY_AGENT_REQUIRE_OLLAMA`, set by the workflow and nothing else, flips it --
  a nightly job that skipped every test it has is a green job that checked
  nothing. `$BUY_AGENT_TEST_MODEL` moves the tag; `$OLLAMA_MODEL` deliberately
  does not, since that one moves the default the agent ships with.

Both Python scripts in `scripts/` are tested like the rest, by the same rule as
`clean_products`: whatever decides an answer belongs where it is testable rather
than in a workflow's shell or a one-off run. `mutation_report.py`
(`tests/test_mutation_report.py`) decides whether a mutation run passes;
`update_ollama.py` (`tests/test_update_ollama.py`) decides what "updated" means --
a digest that moved between the listing before the pulls and the one after, since
`ollama pull` reports `success` whether it replaced anything or not. It is the one
thing in `scripts/` that imports from `buy_agent` (`providers.OLLAMA` for the
`$OLLAMA_HOST` defaults), which is why it is run as `python -m scripts.update_ollama` from the
repository root rather than by path.

`scripts/start.ps1` is the README's "Starting it on localhost" as one command
with no arguments (ADR-0023) -- venv, Ollama, `ollama pull`, `ng build`, the
server, the browser, each step skipped when it is already done. It decides nothing the rest
of the project decides: the provider, the model and the server address are read
off one `AgentConfig()` with a `python -c`, so `$BUY_AGENT_PROVIDER`,
`$OLLAMA_MODEL`/`$OLLAMA_HOST` and `$VLLM_MODEL`/`$VLLM_HOST`
still reach it and no default is written down twice. Off one config rather
than out of three constants, because the pair belongs to the provider: a model
read from one variable and an address from another is how the two come to
disagree. Ollama is the only server it starts -- the install-and-pull half is
behind a provider check, and anything else is waited for at `/models` and named
rather than launched, because a vLLM needs a GPU, a served model and flags this
script has no business choosing. Its four agreements with the
rest of the project are in `tests/test_conventions.py` with the other cross-file
rules -- no default's value appears in the script, the URL it opens a
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
