# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with
code in this repository.

## What this is

A shopping agent: it takes a plain-language request ("wireless headphones under
$200"), searches the web, extracts up to 10 products along with what the pages
say about them, ranks them, and logs the top 3. Built on a local model, served
by Ollama or by a vLLM behind its OpenAI-compatible API --
`AgentConfig.provider` chooses, `buy_agent/providers.py` is the only module that
knows the difference (ADR-0028), and each server's own client is called
directly: there is no framework between the prompt and the answer,
`buy_agent/chat.py` being all of one there is (ADR-0038). `ui/` is an Angular
front end onto the same pipeline, served by `buy_agent.server`. Optionally --
off by default, and off unless an extra dependency is installed -- it can also
*buy* what it found, authorised by signed [AP2](https://ap2-protocol.org)
mandates rather than a stored card (ADR-0046).

`README.md` keeps the tour and links out to the longer sections beside it:
`docs/models.md` (keeping Ollama's models current), `docs/docker.md` (the web
tier as a container, and what a release publishes), `docs/testing.md` (both
suites, the coverage floors, the nightly run, the benchmark and the mutation
run) and `demo/README.md` (two recorded runs of the UI, the still the README
shows, and the harness that took all three).

The rules below are the *rules*. `docs/adr/` is why each exists and what was
rejected; the module docstrings carry the local detail. Prefer adding a rule
here or a convention test over restating an ADR.

## Commands

Dependencies live in a `.venv` created with stdlib `venv`; there is no
`pyproject.toml` and no packaging step. Run everything from the repository root.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt          # runtime deps: requirements.txt

python -m pytest                              # whole suite (~6s)
python -m pytest tests/test_ranking.py        # one file
python -m pytest tests/test_ranking.py::test_cheaper_wins_when_rating_is_equal
python -m pytest -k verification              # by name
python -m coverage run -m pytest ; python -m coverage report   # with coverage
python -m pylint buy_agent                    # the linter, from the root (ADR-0048)

ollama pull qwen3:0.6b ; python -m pytest integration   # against a real model

python -m benchmark --scripted perfect        # score the pipeline, no model needed
python -m benchmark -v --json score.json      # ...and against whatever is serving

python -m buy_agent "gaming laptop under $1500"          # run the agent
python -m buy_agent "espresso machine" --model lfm2.5 -v
python -m buy_agent "gaming laptop" --provider vllm      # the other model server
python -m buy_agent "running shoes" --sort-by price --json results.json
python -m buy_agent "headphones" --max-price 200 --min-rating 4.5   # bounds, enforced
python -m buy_agent "headphones" --cache-ttl 0                      # every page fresh
python -m buy_agent "wireless earbuds" --source rtings.com --source @mkbhd

pip install -r requirements-ap2-deps.txt        # what the AP2 SDK imports
pip install --no-deps -r requirements-ap2.txt   # ...and the SDK, for paying only
python -m buy_agent "headphones" --pay                       # asks, then signs; charges nobody
python -m buy_agent "headphones" --pay --spend-limit 250      # ...and not a penny more
python -m buy_agent "headphones" --pay --rail http --merchant-url https://pay.example

python -m buy_agent.server                    # the UI and its API on :8000
.\scripts\start.ps1                           # ...or all of it from cold, no arguments

python -m scripts.update_ollama               # re-pull Ollama's models, report what moved

pip install -r requirements-mutation.txt      # mutmut, on top of the dev deps
python -m mutmut run                          # mutation testing; ~2 min, cached
python -m mutmut results --all true > mutation-results.txt
python scripts/mutation_report.py mutation-results.txt   # the report CI publishes
```

```powershell
cd ui
npm install
npm test                                      # vitest in jsdom
npm run test:coverage                         # the same, then the coverage floor
npm run build                                 # dist/ui/browser, what the server serves
npm start                                     # dev server on :4200, proxying /api to :8000
```

`ui/` is a separate, ordinary Angular workspace with its own `package.json` and
tests: Angular 22 on Node 22.22.3+, 24.15+ or 26+ (older Node is refused by the
Angular CLI, not by anything here), and nothing on the Python side needs Node.
The Python half has a linter and no formatter -- pylint, over the package and
from the repository root, where it finds `.pylintrc` (ADR-0048) -- and the UI
has it the other way about: Prettier (`npx prettier --write "src/**/*"`) and
nothing linting it. Without `ui/dist/ui/browser` the API still answers and the
page is a 503 saying how to build it (`--ui-dir` points at a build elsewhere) --
as a small HTML page for a client whose `Accept` says it is a browser, which is
who reads that message, and as the same sentence in JSON for everyone else.

### The container

```powershell
docker build -t buy-agent .
docker run --rm -p 8000:8000 buy-agent
docker run --rm buy-agent -m buy_agent "espresso machine"
```

A `node:22.22.3-bookworm-slim` stage builds `ui/`; a `python:3.13-slim` stage
installs `requirements.txt` and gets the build copied to `ui/dist/ui/browser`
beside the package, where `server.DEFAULT_UI_DIR` looks. Neither model server is
in the image or started by it (ADR-0015): the container talks to the host's
through `host.docker.internal`, which both `$OLLAMA_HOST` and `$VLLM_HOST` are
set to, and which needs `--add-host=host.docker.internal:host-gateway` on Linux.
`ENTRYPOINT` is `python` and `CMD` is `-m buy_agent.server --host 0.0.0.0`, so
the CLI is reachable from the same image and `--host` stays out of the server's
own default. No pull request builds it -- `release.yml` does, once per release
(ADR-0030); the rest of the time `tests/test_conventions.py` keeps its version
pins, its copy destination and its `EXPOSE` in step.

`.dockerignore` narrows what the build sees: `tests/`, `integration/`,
`benchmark/`, `docs/`, `scripts/`, `demo/`, `.github/`, `.claude/`, every
Markdown file, the dev and mutation requirements with `setup.cfg` and
`.pylintrc`, whatever working here leaves behind -- `.gitignore`'s own list,
every spelling of the virtualenv included -- and `.env`, which is the one thing
kept out on purpose rather than for its size. So the Node stage builds from
source rather than copying a stale local `dist/`, and `demo/` -- a video the
size of the rest put together -- never reaches the daemon.
`tests/test_conventions.py` reads it from both sides: everything `.gitignore`
names is named here too, which is the sentence the file opens with, and nothing
the `Dockerfile` copies is caught by any of it, which is the mistake that stops
a build rather than quietly fattening one. Neither says anything about a *new*
top-level directory, so one added without a line here is still uploaded whole.
Patterns match from the root, so the UI's own leavings are written out
(`ui/dist/`, `ui/coverage/`): `ui/` is the one directory copied whole, and a
`coverage/` matched at the root reaches nothing inside it.

### Settings and their environment

`$BUY_AGENT_CACHE_DIR` moves where a run keeps what it can reuse -- `pages/` for
the text the fetch read (ADR-0040) and `answers/` for what the model said about
it (ADR-0044) -- defaulting to the platform's own cache directory under
`buy-agent/`. Like `$VLLM_API_KEY` it has no flag and no form field -- a path on
the server's disk is not a browser's to choose -- while *how long* an entry
lasts is the ordinary `cache_ttl` setting, one number for both kinds, 0 meaning
"read every page off the web and ask the model every question". A sampled run
(`temperature` above 0) is never remembered whatever the setting says: it has no
one answer to remember.

Paying adds two settings of the same kind, and for the same reason.
`$BUY_AGENT_AP2_KEY` is the EC P-256 key mandates are signed with -- a secret,
so it stays out of a shell history, out of `defaults_payload` and out of any
form -- and `$BUY_AGENT_AP2_MANDATE` names a pre-signed *open* mandate, a path
on the server's disk. That second one is not merely a setting: its presence is
what puts a run into AP2's human-not-present mode, because the open mandate *is*
the authorisation and a second switch saying "run unattended" would only fail
without the file anyway (ADR-0046).

`$BUY_AGENT_PROVIDER` moves which model server a run talks to, and each provider
has its own variables behind it -- `$OLLAMA_MODEL`/`$OLLAMA_HOST` and
`$VLLM_MODEL`/`$VLLM_HOST`/`$VLLM_API_KEY` -- read on that server's own row in
`providers.PROVIDERS` (ADR-0029). `AgentConfig.model`, `base_url` and `api_key`
therefore default to the *empty string* and are resolved per provider in
`__post_init__`: which value is right depends on a sibling field, so a plain
default could only ever be right for one of the two, and "unset" is spelled the
way a blank form field is (ADR-0012). Hence `--model` and `--base-url` default
to `""` and interpolate every provider's pair into their help. `$VLLM_API_KEY`
is the one setting with no flag and no form field -- a secret, so it stays out
of a shell history, out of `defaults_payload` and out of `provider_options()`.

Every other CLI flag defaults to the matching `AgentConfig` field, so a new
setting is added in `config.py` and picked up rather than repeated. One field is
deliberately renamed on the way out: `AgentConfig.reasoning` is `--think`
(`BooleanOptionalAction`) on the CLI and `think` in the JSON payloads and
`agent.types.ts`. Its tri-state is Ollama's thinking mode, where `None` means
"send nothing and leave the model alone" rather than "off".

It pairs with `num_ctx`: the extraction prompt runs to ~4.3k tokens, so on
Ollama's default 4096 window a thinking model reasons until the context is gone
and never emits any JSON. Ollama's default model is `gemma4:12b`, which thinks,
so `reasoning` defaults to `False` and `num_ctx` to `8192`. A model that cannot
think ignores both; one that wants its own behaviour back is given
`num_ctx=None, reasoning=None`, reachable from neither front end (ADR-0019).
`num_ctx` is the one setting the two providers do not share -- vLLM fixes its
window with `--max-model-len` at startup, so `Provider.takes_num_ctx` is false
there, the value is not sent, and both front ends say so rather than accepting a
number nothing reads. `reasoning` *is* shared: Ollama's `think`, vLLM's
`chat_template_kwargs.enable_thinking`.

### CI and the three workflows beside it

`.github/workflows/ci.yml` runs two jobs for pushes to `main` and every pull
request: `coverage run -m pytest`, `coverage report` and then `pylint buy_agent`
on Python 3.13, and `npm run test:coverage && npm run build` in `ui/` on Node
22.22.3. The lint is last in its job on purpose: a job stops at its first
failing step, and of the two the tests are what a change is about. Either
platform alone leaves half the platform differences unchecked (ADR-0020), so
both jobs are still matrixed over `ubuntu-latest` and `windows-latest`. Not on
the same trigger, though (ADR-0037): a push and a pull request are gated on
Linux, and Windows joins the matrix at 04:09 UTC on Saturdays and on
`workflow_dispatch`. That dispatch is how a branch that touched a path, an
encoding, a socket or `start.ps1` asks for Windows before it is merged rather
than a week after. The matrix is one expression over `github.event_name`, in one
workflow, rather than a second file copying both jobs' steps and both version
pins. Four details hold it together. `fail-fast` is off, so one platform's
failure still reports the other. The concurrency group names the event: the
schedule fires on `main` where pushes land, and `cancel-in-progress` otherwise
would let a Saturday morning merge drop the week's only Windows run. Every step
runs under `bash`, PowerShell carrying on past a failing command mid-step. And
the matrix is over platforms only, one Python and one Node, since the
`Dockerfile`, `scripts/start.ps1` and `docs/testing.md` each pin themselves to
*the* version `ci.yml` names.

- **`integration.yml`** runs `pytest integration` against a real Ollama at 03:41
  UTC nightly (and on `workflow_dispatch`), never on a pull request, capped at
  `timeout-minutes: 5` -- which covers installing Ollama, pulling the model and
  the inference. Linux only: what it asks is the same question on either
  platform. Ollama and the model tag are the one pair deliberately left
  unpinned, noticing that a release changed `method="json_schema"` decoding
  being half of what the job is for (ADR-0026).
- **`mutation.yml`** runs mutmut against `buy_agent/` at 05:17 UTC on Saturdays
  (and on `workflow_dispatch`), never on a pull request. Its settings live in
  `setup.cfg` -- which exists for that and is not a packaging file -- and
  `scripts/mutation_report.py` turns a run into the job summary and fails it
  under 75% (ADR-0016). A run copies the tree to `mutants/` and tests the copy,
  so anything the suite reads off disk or imports from outside `buy_agent` has
  to be named in `also_copy`, or the run dies at collection.
- **`release.yml`** runs when a release is *published* (and on
  `workflow_dispatch` with a tag, so a failed upload can be retried without
  re-cutting the release) and puts two packages on GitHub:
  `buy-agent-<version>.tar.gz`/`.zip` with a `SHA256SUMS.txt`, attached by `gh`,
  and `ghcr.io/<owner>/<repo>:<version>` from the `Dockerfile`, `latest`
  following full releases only (ADR-0030). The archive holds `buy_agent/`, the
  built UI, `requirements.txt`, `README.md` and `docs/` -- no wheel and no
  sdist, the project still being run from a directory. Both jobs check out
  `$TAG` rather than the branch the workflow sits on, and neither package is
  published unattended: each is installed or run and asked for `/api/config` and
  `/`. Linux only, like the other two schedules.

Four files configure all of that, and `docs/testing.md` says why each is set the
way it is.

- `pytest.ini` sets `pythonpath = .`, which is why the package imports without
  being installed, plus `testpaths = tests`, `addopts = -q --strict-markers`,
  `filterwarnings` (a `DeprecationWarning` or a `PendingDeprecationWarning` is an
  error, `integration/` included) and `timeout = 60` per test. A warning a
  dependency raises about itself gets an `ignore` line there naming the message
  and the module. A minute is the wrong number for a real model, so
  `integration/conftest.py` marks every test there with
  `integration.LIVE_TIMEOUT_SECONDS` instead, which a convention test holds
  between that cap and the five minutes `integration.yml` gives the whole job.
- `.coveragerc` holds the Python floor -- 99% against 100% actual, with
  `branch = true`, so it is over branches as well as lines. The one exclusion is
  the `if __name__ == "__main__"` guard, covered instead by spawning a real
  interpreter.
- `.pylintrc` is the one of the four that holds no number: the linter has to come
  out with no message at all. Every check this project has answered differently
  is turned off there with the answer, and every line the tool misreads carries a
  `# pylint: disable` and the sentence saying why (ADR-0048).
- `ui/scripts/check-coverage.mjs` holds the UI's floor, 98% of statements and
  lines. The Angular unit-test builder reads a vitest config's coverage
  *reporters* but does not fail a run on its `thresholds`, so the floor has to be
  checked separately or it is not a floor. Statements and lines only, on purpose:
  v8 attributes the branches inside a compiled Angular template to positions no
  test can reach, so a branch floor there would measure the instrumentation.
  Don't add one.

## Architecture

`docs/architecture.md` holds the same picture as C4 diagrams; keep it in step
when a module's responsibility or a boundary moves. `docs/adr/` is the decision
log, indexed by `docs/adr/README.md`. A change that contradicts an accepted
record gets a new record superseding it rather than an edit to the old one --
numbers are never reused, and accepted records are not rewritten.
`tests/test_conventions.py` checks that the index and the directory agree, so a
new ADR is two edits: the file and its row in the index.
`docs/adr/0000-template.md` is the starting point. Every record is Accepted but
ADR-0020, which ADR-0037 supersedes; the next free number is the one after the
last file in that directory, which is where `add-adr` reads it from.

`.claude/skills/` holds the chores that span those files: `add-option` walks a
new setting through `config.py`, both front doors, `agent.types.ts` and the
form; `add-row` is that walk for a model server or a payment rail, each of which
is one row in one table and a row nowhere else; `add-adr` takes the next number
off the directory rather than off the sentence above, which has gone stale
before; `preflight` is the gate `ci.yml` applies. They are checklists over the
rules written down here, not new rules -- a rule belongs in this file or in a
convention test, where it holds whether or not anybody invoked a skill. They are
checked like everything else that names the code from outside it:
`tests/test_conventions.py` holds every file, test and table a skill names
against what is there, since nothing else in either suite reads them and a step
pointing at a renamed table stays green for as long as nobody follows it.

`.claude/hooks/session-start.sh` is the other thing in there, and it runs rather
than being read: the images Claude Code on the web starts a session in ship a
Node below the one `ci.yml` pins, which the Angular CLI refuses outright, so
every session used to open by hunting for another interpreter and finding none.
The hook fetches the pinned build into `/opt/node-<version>`, leaves it on
`$PATH` through `$CLAUDE_ENV_FILE` -- the Bash tool starting a fresh shell per
call, so exporting it is not enough -- and runs `npm install` in `ui/`. It reads
the version out of `ci.yml` rather than writing it down again, by the rule
`scripts/start.ps1` follows: that file is the one pin the `Dockerfile`, the
start script and `docs/testing.md` already chase, and a fourth copy is a fourth
thing to bump. It is a no-op outside a remote session (`$CLAUDE_CODE_REMOTE`), a
no-op once the interpreter is unpacked, and every failure in it is a warning
rather than a stop -- a session that starts with the old Node is the situation
it was written for, not worse than it.

The pipeline is deliberately **not** a tool-calling agent loop. The LLM is used
for the two steps it is reliable at, and ordinary Python does everything else,
because these servers are typically run with small models that drive tool loops
badly.

```
request -> refine query (LLM) -> DuckDuckGo (once, or once per named
source) -> fetch + condense pages
        -> extract products and their opinions (LLM) -> clean_products
        -> ground -> deduplicate -> the shopper's bounds -> rank -> log top 3
```

That order is load-bearing in three joints. `clean_products` runs before
`ground` so a name still wearing its publisher suffix ("... Review | AudioSite")
is not failed by the coverage check for tokens the page never had to contain;
`ground` runs before `deduplicate` so `_combine` only ever merges figures the
sources back. That is necessary and not sufficient: a merge taking each field on
its own would still report a pairing no page printed, so `models.QUALIFIERS`
says which fields only qualify another and `_fill_gaps` moves the group. The
third is the shopper's bounds (ADR-0039): after `deduplicate`, since
`_fill_gaps` may be what supplies the price they are judged on, and before
`rank_products`, since price scores relative to the candidate set and the set
that matters is the one being reported.

| Module | Responsibility |
| --- | --- |
| `agent.py` | `BuyAgent.run()` -- orchestrates the pipeline, translates model-server errors |
| `chat.py` | The whole model-facing seam: a prompt, a chain, an answer read back as its schema (ADR-0038) |
| `extraction.py` | Both prompts, both chains, name cleaning, deduplication |
| `fetch.py` | Streams result pages up to a ceiling, keeps the lines quoting a figure or passing judgement, and tallies how the rest failed |
| `cache.py` | What a run can reuse from the last one: the page text it read (ADR-0040) and the answers it got (ADR-0044) -- and nothing else |
| `verification.py` | Drops products, figures and quotes absent from the sources; links what is left |
| `constraints.py` | The bounds the shopper set, applied to the products before they are ranked |
| `ranking.py` | Scoring and sorting, and what each score is made of; no LLM involved |
| `models.py` | `ExtractedProduct` (LLM-facing) vs `Product` (domain) |
| `search.py` | DuckDuckGo wrapper -- and nothing else (ADR-0021) |
| `sources.py` | What a trusted source is: domain, term, `site:` query, `covers` |
| `providers.py` | Everything that differs between Ollama and vLLM, and nothing else |
| `payment.py` | What may be bought and for how much: a cart out of a grounded product, the spend limit, the receipt -- and one failure |
| `mandates.py` | The AP2 seam, and the only module that imports `ap2` (ADR-0046) |
| `rails.py` | Everything that differs between one counterparty and another, one row each |
| `config.py`, `logging_setup.py`, `__main__.py` | Config, the report, the CLI |
| `api.py` | Request options in, ranked products out -- the web-facing half worth testing |
| `server.py` | A stdlib HTTP server: the JSON API, the event stream, the built UI |

### Fourteen conventions

- **A model server is one row in one table, reached one way.**
  `providers.PROVIDERS` holds each server whole -- its defaults (`model`,
  `base_url`, `api_key`, from its own environment variables) beside how it is
  talked to (the client, how it declares a schema, the listing, the transport
  errors meaning "not there", the sentence that failure carries) plus
  `takes_num_ctx` (ADR-0029). The listing answers `InstalledModel`s rather than
  names, since what a server holds and what a run can use are the same question
  only on vLLM: Ollama's `installed` asks `ollama show` per tag, so an
  embedding-only pull is marked in the picker rather than offered (ADR-0032),
  and a failed probe leaves the tag usable. The module imports nothing from
  `config`; the dependency runs the other way, and `AgentConfig.model_server` is
  the *only* place a provider name becomes behaviour -- so `agent.py` reads
  `config.model_server.chat_model(config)`, catches `.transport_errors` and
  raises with `.hint(config, exc)`, and `api.installed_models` asks
  `.installed(config)`. No module-level wrappers, and no `if provider == ...`
  anywhere above the table. A setting one server takes and the other does not
  gets a declaration on the row rather than a branch in the CLI, the API and the
  form; a hint sentence both servers would write goes in `_too_slow_hint` or
  `_unreachable_hint` (ADR-0028, ADR-0029). A row's client holds a connection
  pool, and letting go of it is the row's own too: each chat model has a
  `close`, `chat.release` is who asks for one where there is one to ask, and
  `BuyAgent.close` is when -- so both front doors build one agent per request
  and release it, rather than leaving a pool to whenever the last reference to
  it happens to fall. A stand-in is unaffected, which is the point of asking
  rather than requiring: `ChatModel` is still a class with one method.
- **A payment rail is one row in one table too, and the default one spends
  nothing.** `rails.RAILS` is `providers.PROVIDERS` for counterparties: each row
  carries where it listens (from its own environment variable), whether it needs
  an address and an *enrolled* key, whether it `moves_money`, how a cart becomes
  a merchant-signed checkout, how an authorisation is presented, the transport
  errors meaning "not there" and the sentence one carries. AP2 secures *what* is
  authorised and says nothing about who it is sent to, so the counterparty is a
  choice -- and no merchant, wallet, processor or cloud is named anywhere here
  (ADR-0046). `AgentConfig.rail_used` is the only place a rail's name becomes
  behaviour; no `if rail == ...` above that module, and a third rail is a row
  there and a row nowhere else. The default is `dry-run`, which plays every
  role, signs a chain that really verifies and charges nobody, so `--pay` on its
  own is never a way to spend money.
- **Never pay on an unverified number, and never on one this run cannot place.**
  The ranking rule (ADR-0006) turned around. `payment._check` refuses a product
  whose price grounding blanked, whose currency no page printed, whose price is
  in a currency outside the run's own (ADR-0043), or which has no source page to
  name a merchant from -- every one of those already being the answer to "did a
  source say so". It is the deliberate opposite of the shopper's bounds, which
  *keep* a product they cannot judge (ADR-0039): a filter that drops a candidate
  over a missing figure punishes the extractor's miss, while an amount nobody
  can place is simply not an amount to send. That one function is asked by the
  CLI's prompt, by the card's button (through `cannot_pay` on every product) and
  by the payment itself, so a button is never offered for something the server
  would refuse.
- **The sources are whatever was searched, and the shopper may narrow them.**
  `AgentConfig.sources` is empty by default, which is the whole web. Given any,
  `BuyAgent._search` runs one search per source (`site:` takes one domain), puts
  every result through `Source.covers()` before keeping it, pools them
  deduplicated by URL and cuts the pool back to `search_results` -- so the
  fetching does not multiply with the sources even though the searching does.
  Nothing downstream knows, which is how "every figure and every quote was
  printed by a page the shopper named" holds by construction (ADR-0027). What is
  enforced is the **domain**; a handle or a section only narrows the query, a
  URL being unable to carry it. Every shape is checked for naming something -- a
  host against `_HOSTNAME`, a handle against `_HANDLE` -- because a spec that
  parses without identifying anything searches for a phrase no page contains,
  and with no fall back to the wider web that is an empty report with nothing to
  explain it. There is no fall back to the wider web when the named sources find
  nothing: that would report facts from pages the shopper refused. `sources.py`
  does no I/O -- it decides what a source *is* and `agent.py` does the
  searching, which is also what keeps `search.py` a DuckDuckGo wrapper.
- **`ExtractedProduct` uses sentinels, `Product` uses `None`.** The LLM-facing
  schema asks for `-1`/`""` rather than nullable fields: Ollama compiles the
  JSON schema into a decoding grammar, and a required `number` makes `"N/A"` --
  which would fail validation for the entire batch -- structurally impossible.
  Keep new extraction fields non-nullable with a sentinel, and convert in
  `to_product()`. `opinions` is the one field whose sentinel survives into
  `Product` as itself: the empty list already spells "nothing was said".
- **Never rank on an unverified number, never link to an unverified page, and
  never quote what nobody said.** `verification.ground()` drops products whose
  name is absent from the sources and blanks any price, rating or review count
  that is. Only the price is checked as a bare number. The other two are small
  whole numbers a page prints for a hundred other reasons, so each is checked as
  *itself*: a rating needs its scale ("4.3/5", "rated 4.3"), a bare `5` matching
  the "5" in "out of 5", and a review count needs somebody to be counted ("3,200
  ratings", "from 12,500 shoppers"), a bare `720` matching the model number in
  "WH-CH720N", the year in a release date, or the price beside it. A figure
  added here that a page could print by accident needs a `mentions_*` of its own
  rather than `mentions_number`. Extraction and verification must be given the
  same text or the check rejects everything, which is why `fetch.enrich()` puts
  page content on `SearchResult` rather than passing it around separately.
  `attribute_sources()` then gives each product the URL of the first searched
  page that mentions it, keeping the model's own `url` only when it names a page
  that was searched (ADR-0017): a blanked figure shows as "price unknown", but a
  made-up link is one the shopper clicks. It runs inside `ground`, so
  `deduplicate` only ever merges links the sources back.
- **A quote is checked as running text, on the page it came from** (ADR-0024,
  ADR-0025). `fetch.py` sweeps each page twice -- once for the lines quoting a
  figure, once for the lines passing judgement, each on its own budget
  (`page_chars`, `opinion_chars`) so neither crowds the other out -- and
  `verify_opinions()` drops every quote the sources do not contain as
  overlapping runs of five consecutive words, most of which must be found. A
  word-by-word check would pass any sentence assembled out of shared vocabulary,
  which is what a small model paraphrasing produces. The tolerance is
  deliberately at the ends and not in the middle. `_OPINION` is a vocabulary of
  judgement ("reviewers found", "the downside is", "disappointing"), never of
  subject matter: "wireless" or "battery" would take every line on the page.
  Unlike the figures, a quote is checked against one page at a time and only one
  `mentions_name` says is about the product, which is why `verify_opinions()`
  takes the results and not the pooled haystack. That makes `mentions_name` the
  decider of three things -- whether a product is real, where it links, and what
  may be quoted for it -- so a word added to `GENERIC_WORDS` loosens all three.
- **A quote carries the page that printed it** (ADR-0042). `verify_opinions()`
  already has to find that page to keep the quote at all, so `Product.opinions`
  are `Opinion` objects -- the words and the URL of the first result that both
  mentions the product and prints them, which is the rule `attribute_sources`
  picks the product's own link by. The model is asked for the words and never
  for the page, exactly as it is never trusted with a link (ADR-0017). `url` is
  nullable and `None` is *not* "no page printed it": it is a result the search
  returned without one, and collapsing the two would drop every quote off such a
  page. The pair travels as one object -- through `_merge_opinions`,
  `distinct_quotes`, `product_payload` and the card -- which is why it needs
  none of the qualifier care below: neither half can move without the other.
- **A currency belongs to its price, and a review count to its rating**
  (ADR-0022). Both are facts about the *listing* that printed them, so
  `models.QUALIFIERS` pairs them up and `_fill_gaps` carries a qualifier over
  only where the figure it describes is carried over too, or where both listings
  quote the same one. The same rule binds at both earlier stages:
  `verification.verify_numbers`, where a figure the sources do not back takes
  its qualifiers down with it, and `ExtractedProduct.to_product`, where neither
  a review count reported with no rating beside it nor a currency reported with
  no price ever becomes one. Either way a count left standing alone describes
  nothing, reads "unrated" on the card, and still feeds the popularity half of
  the score -- which is why the pairing is declared once beside the fields it
  names rather than restated in the merge's table and the grounding's.
  Field-by-field merging passes grounding -- each half really is in the sources
  -- while reporting "129.00 EUR" for a page that said 129 and a page that said
  "249 EUR". A new field that only makes sense next to another belongs in that
  other's group. `opinions` is deliberately outside the scheme, in
  `_merge_opinions`: two listings' quotes are both kept, two reviewers being no
  conflict, and each was grounded on its own before the merge.
- **`GENERIC_WORDS` is shared, and edits to it pull in two directions.**
  `verification.py` imports the set from `extraction.py`, along with
  `NAME_TOKENS`, so merging and grounding agree on what a name's words are.
  `SUPERLATIVES` comes across too -- the words a roundup ranks with. There they
  open a headline the model reported as a product; here they mark the figure
  beside them as a count of products rather than a rating. A word in one copy
  only used to drop a "cheapest" headline while still grounding the rating
  printed next to it. Adding a word makes `merge_variants` fold *more* names
  into one product and at the same time makes `mentions_name` stricter, ignored
  words leaving fewer distinctive tokens to clear the 0.6 coverage bar. Both
  sides of that bar are split by `NAME_TOKENS` and compared word to word: a
  substring test would let "$1700" on the page vouch for an invented "Bose 700".
  Only ever add words that identify nothing ("wireless", "black"); a brand or a
  model number there would let an invented product pass grounding.
- **Missing data scores neutral, not zero -- and says that it did.**
  `ranking.NEUTRAL` is 0.5, and an unknown rating, review count or price scores
  that. Grounding blanks figures the sources did not back, so scoring a blank as
  0 would punish a product for the extractor's misses. For the same reason
  `sort_by="price"` and `"rating"` sink products missing that field to the
  bottom instead of dropping them, and the shopper's bounds keep a product whose
  figure is unknown rather than dropping it (ADR-0039). A price the run cannot
  *place* is one of those blanks: prices are compared inside one currency and
  converted never, so `models.dominant_currency` says which currency a set is
  counted in and `models.comparable_price` answers `None` for anything outside
  it, which scores neutral, sinks in a price sort and passes every bound
  (ADR-0043). A bare price is taken as the set's own. Both places that hold one
  price against another -- `rank_products` and `Constraints` -- go through that
  one function; a third would have to. Which currency a listing named is settled
  once, in `models._currency`: the schema asks for a code and a small model
  hands back the sign the page printed, so `$` and `USD` are folded together
  there rather than counted as two currencies half a set is then unplaceable in.
  Only the spellings that name one currency are folded -- `¥` is the yen's and
  the yuan's alike, and an ambiguous one left as written is a price the run
  cannot place, which is what the rule above already has an answer for. The cost
  of that rule is that 0.5 means two different things, so `score_product`
  answers a `ScoreParts` whose `neutral` names the criteria that were assumed
  rather than read, and both front ends show it (ADR-0041). It is decided there
  and nowhere else: a share that *equals* 0.5 may have been measured, a product
  priced mid-way through the set scoring exactly that. The shares stay
  *unweighted*, so a run reports the weights beside them --
  `RankingWeights.fractions` on the run payload and on the score line,
  `ranking.CRITERIA` pairing each with the share it weighs (ADR-0045). Three
  numbers under a total they do not add up to are otherwise unreadable, and a
  product carried by its price looks exactly like one carried by its rating.
- **The report is output; the progress is narration.** `logging_setup` splits
  them by handler rather than by logger: `log_top_products` marks its records
  and they go to stdout, everything else to the stderr handler `basicConfig`
  installed, and both still reach every other handler -- which keeps the
  browser's progress panel showing one stream and a `caplog` seeing the whole
  run. Only the *console* handler is told to skip the report; a handler writing
  anywhere else is nobody's stream to take lines out of. `configure_logging`
  sets the level itself rather than leaving it to `basicConfig`, which does
  nothing at all where the root logger already has a handler -- and the level is
  what it silently skips, so `--verbose` asked for DEBUG and got INFO. It
  quietens libraries in two tiers: `_NOISY_LIBRARIES` log a line per call and go
  quiet until somebody asks for detail, while `_TRACE_LIBRARIES` -- httpcore, a
  dozen DEBUG lines per request -- are held down at `--verbose` too, being what
  asking for detail would otherwise be spent on.
- **A heuristic that takes something away says how many at INFO and which at
  DEBUG**, and `tests/test_logging_contract.py` drives all eight to say so, each
  step's own file pinning its wording. All eight do: `clean_products`,
  `drop_ungrounded`, `merge_variants`, `deduplicate`'s nameless drop and
  `Constraints.apply` drop a whole product; `verify_numbers` blanks a figure,
  `verify_opinions` a quote and `attribute_sources` a link. The count is what
  says a short report is a filtered one rather than a thin web; the name is what
  makes a wrong drop arguable, and `-v` is the only place a name per product can
  be afforded. The merge is the case to remember, since nothing was dropped at
  all: the folded entry keeps the shorter of the two names and the other is
  simply gone.
- **Model output is never trusted as judgement.** The model reports article
  headlines as products; `clean_products` filters them. Anything that decides
  the answer -- filtering, scoring, ordering -- belongs in Python, where it is
  testable.

### Failures

`BuyAgent.run()` raises exactly three things -- `ValueError`,
`ModelUnavailableError`, `SearchError` -- and `__main__.main()` catches exactly
those around the run, logging them and returning 1. Four other codes are not
failures of that kind. 130 is Ctrl-C, at the payment's approval prompt as well
as during the run, that being where a shopper hesitates and the one place a
traceback reads as money having moved. `NOTHING_FOUND` (3) is a run that worked
and found nothing, which a shell told 1 could not tell from a stopped model
server. `PAYMENT_FAILED` (4) is a run that was asked to pay and did not. 2 is
argparse's own. So the codes a script branches on are the six `--help` ends by
listing. `main` has a second, unrelated `except` for an `OSError` from writing
the `--json` file, which is why `tests/test_conventions.py` reads the handlers
of the `try` holding the `.run()` call rather than every handler in the
function. `api._STATUS` maps the same three onto HTTP statuses (400, 503, 502).
A new failure mode needs handling in all three places, or it reaches the user as
a traceback and the browser as a 500.

A *payment* fails at its own door and is deliberately not a fourth row there
(ADR-0046). `payment.PaymentError` is the one thing paying raises --
`RailUnreachableError` subclasses it, so the API can answer 502 for the one
failure that is nothing to do with the request while the CLI still catches both
by catching the parent -- and it is mapped by `api.PAY_STATUS` and caught in
`__main__._bought`, each read by a convention test of its own. Adding it to
`_STATUS` would make `run` promise something it does not raise.

Within the agent only query refinement is recoverable: it falls back to the raw
request but lets `ModelUnavailableError` through rather than searching with a
model that is not there. What has to be caught is the provider's to say --
`_invoke` catches `self.config.model_server.transport_errors` and nothing
written down locally. For Ollama that tuple is wider than it looks: the ollama
client converts exactly one of its transport failures, a refused connection
becoming a builtin `ConnectionError`, while a model too slow to answer and a
stream the server drops mid-object arrive as raw `httpx` errors, neither an
`OSError`. Hence `httpx.HTTPError` beside ollama's own `RequestError`, a
different class from httpx's identically named one. Which failure is which moved
with ADR-0038 -- the chat call is the plain path now, not the streaming one --
so the tuple is read off what the client actually raises rather than off this
paragraph. vLLM's is `openai.OpenAIError`, the root of that client's hierarchy,
plus the two above for the listing.

A server that answers with something that is not the JSON asked for is the third
of those three and not a fourth: `chat.UnreadableAnswerError` *is* a
`ValueError` -- deliberately, so an uncaught one lands in the three `run`
documents rather than a fourth -- so left alone it arrives as the one `run`
documents for an empty request, telling a shopper whose model ran out of room
that their request was bad. `_extract_products` turns it into a
`ModelUnavailableError` carrying `hint(config, exc)`, which names the room the
model may have run out of (ADR-0019). Caught there rather than in `_invoke`,
which the recoverable step goes through too: a fumbled query still falls back to
the raw request.

### Options, and the seven that are special

The CLI and the API are two ways of filling in the same `AgentConfig`, and both
set `search_results = max(results, top)` -- searching for fewer pages than the
report intends to show would cap the report. A new option belongs in
`__main__.build_parser`, `api.parse_options` and `api.defaults_payload`, which
seeds the web form.

- **Numbers** belong in `config.LIMITS` too, where the range is declared once
  and read by both doors: written on each of them, the CLI comes to accept what
  the API refuses. On the CLI the check is a `type` function, so an out-of-range
  number is a usage error rather than a minute wasted;
  `tests/test_conventions.py` asserts the two doors refuse the same numbers.
- **`region`** is the same rule for a shape rather than a range: `config.REGION`
  is a country and then a language (`us-en`, `pl-pl`, three-letter `hk-tzh`),
  `config.parse_region` is the only place it is checked, and both doors go
  through it -- the CLI as a `type` function, the API as `_as_region` -- with
  `__post_init__` behind them for a Python caller. A shape and not the list of
  codes that exist, because `ddgs` asks several engines that each read the
  halves their own way (ADR-0031). The shape is not the whole story -- `en-us`
  is the right shape the wrong way round -- so `BuyAgent._region_note` names the
  region in the "Search returned nothing" warning unless it is `DEFAULT_REGION`,
  which is known to work.
- **`provider`** is offered in *four* places -- those three plus the
  `ProviderOption` rows `defaults_payload` sends the picker -- each reading
  `providers.PROVIDERS` rather than listing the names again. It also changes
  what two other options mean, so both front ends pass `model` and `base_url`
  through as `""` when they were not given, and the form fills both fields in
  when the picker changes.
- **`sources`** is the one option that is a list, and so the one that does not
  go through `api._read`, which renders every value with `str` and would turn a
  JSON array into its Python repr; `_read_sources` takes either an array or the
  separated string a query string can carry. On the CLI it is `--source`,
  repeatable, and its `type` checks the spec but hands back the text: checking
  there makes a bad source a usage error carrying the shapes that work, while
  parsing every flag together in `main` is what makes two flags naming one site
  one source. It is the one option whose two doors judge a *blank* differently,
  and they have to. Over the wire an empty value is how "unset" is spelled
  (ADR-0012), so `api._present` reads an empty string and a list of blanks alike
  as the whole web. On a command line "unset" is spelled by leaving the flag
  off, so `parse_named_sources` refuses a flag naming nothing rather than
  letting it come back empty and widen the search to everything (ADR-0027).
- **The three bounds** -- `max_price`, `min_rating`, `min_reviews` -- are
  ordinary numbers with one rule of their own: they default to `None`, so a
  blank is not "the default value" but "no bound at all", and a product whose
  figure is unknown passes every one of them (ADR-0039). The form says so rather
  than showing a fallback number: their placeholder is "No limit". `max_price`
  is read in the currency the run's own prices are counted in, and a price
  outside it is a figure the bound cannot judge -- so it passes too, and the
  line the run logs names the currency (ADR-0043).
- **The four paying settings.** `pay` is the master switch and defaults to
  `False`, so nothing about a run changes without it. `rail` is checked against
  `rails.RAILS` at both doors, the way `provider` is checked against `PROVIDERS`
  -- and it is offered in *four* places for the same reason, the fourth being
  the `rail_options()` rows the form's picker is built from. `merchant_url`
  defaults to `""` and is resolved per rail in `__post_init__`, exactly as
  `base_url` is resolved per provider (ADR-0012). A *paying* rail that needs an
  address and has none is a `ValueError` there, which is the one thing about
  these settings a range cannot say. It is also the only one a config raises
  that neither door has already refused, so each door translates it rather than
  letting it out: an `ApiError` naming `merchant_url`, so the form marks that
  box (ADR-0033), and argparse's own exit 2, so it reads like the refusals
  `_checked` writes. Both doors build the config outside every guard they have,
  so an escaping one is a 500 and a traceback for a mistake the form can make.
  `spend_limit` is an ordinary bounded number whose range is `max_price`'s, and
  a different promise: that one filters what is reported and admits a product it
  cannot judge, this one has to be cleared before money moves and refuses what
  it cannot judge.
- **`weights`** is the one field neither door fills in: `RankingWeights` is
  reachable only by constructing an `AgentConfig` in Python, so rebalancing the
  blended score is a code change and not a flag.

`buy_agent/__init__.py` re-exports the small surface a Python caller needs --
`BuyAgent`, `AgentConfig`, `Product`, `RankedProduct`, `RankingWeights`,
`rank_products`; anything else is reached by its module.

## The UI and its server

`buy_agent.server` is stdlib-only on purpose -- the dependency list is already
the interesting part of this project, and a run that takes a minute and serves
one person does not need a framework under it. It hands `/api` to `api.py` and
everything else to the built Angular app, unknown paths falling back to
`index.html` so the app keeps its own routing.

| Endpoint | Answers with |
| --- | --- |
| `GET /api/config` | The form's defaults -- the same ones `--help` prints |
| `GET /api/models` | What a named server is serving, or why it could not be asked and what to do about it |
| `GET /api/sources` | Whether a Trusted sources field names sites -- the one endpoint that runs nothing |
| `POST /api/search` | One run, as JSON |
| `POST /api/rank` | A finished run's products in another order -- runs no pipeline |
| `POST /api/pay` | One of those products bought, given the approval the page witnessed -- runs no pipeline either |
| `GET /api/search/stream` | One run, as SSE: `log` lines, then `result` or `failure` |

- **A run is streamed, not requested.** `GET /api/search/stream` runs the agent
  in a worker thread and relays its log lines as Server-Sent Events while it
  works. `_LogRelay` routes records by the thread that produced them, which
  keeps two concurrent runs from seeing each other's progress. Extraction is
  slow and logs nothing while it runs, so a `ping` goes out every 15s to keep
  browsers and proxies from timing the stream out, and every relayed line
  carries the `time` Python logged it at in the CLI's own `%H:%M:%S` -- the gap
  between two lines being the only thing that tells a four-minute extraction
  from a four-second one. `POST /api/search` is the same run in one response.
- **Closing the stream stops the run, at the next step and not at the click**
  (ADR-0034). `BuyAgent.run` calls its `checkpoint` with the name of each step
  about to start -- `search`, `fetch`, `extract`, `rank` -- and nothing in the
  pipeline catches what that raises. The first frame `_stream_search` cannot
  write sets the flag `server._stop_when` reads, and the worker's `_Stopped`
  goes no further than the worker: a stopped run is not a failure, so it stays
  out of `api._STATUS`, out of `__main__.main` and out of `run`'s `Raises:`, and
  the three-failure agreement holds. It cannot cancel a chat call already in
  flight -- there is no way into either client -- so the granularity is a step,
  and the browser's Stop line says so rather than promising what nothing can
  keep. A step added to `run` announces itself or a stopped run pays for it
  anyway; every stand-in for `BuyAgent` takes the keyword, since `run_search`
  always passes it.
- **The stream's failure event is called `failure`, not `error`.** A browser's
  `EventSource` delivers transport errors under `error` and then reconnects, so
  a named `error` event would be indistinguishable from a dropped connection and
  the reconnect would silently restart the search. For the same reason `HEAD
  /api/search/stream` answers 405 rather than starting a run nobody reads.
- **The browser decides nothing.** Ranking, grounding, whether a product may be
  bought at all, and even the wording of an unknown price all stay in Python.
  `cannot_pay` is Python's sentence, from the same check the payment goes
  through. `pay_currency` and `pay_label` beside it are what that purchase would
  be *for*, which is frequently not the product's own figures: a page that
  printed a bare "329.00" is priced in the run's currency (ADR-0043), so
  `currency` is null while the cart is in USD. `product_payload` sends
  `price_label` and `rating_label` next to the raw figures. `sort_by` is a
  request parameter rather than a client-side re-sort, for a finished run too
  (ADR-0035). `installed_models` sends each model's `completion` beside its
  name, so the dropdown marks what it could not have worked out; the provider's
  `label`, so the header pill never decides what to call the server; and --
  where it could not be reached -- the `hint` that provider's row would have
  raised a run with, so the page says "Start it with:  ollama serve" without a
  second wording in TypeScript. `ui/src/app/agent.types.ts` mirrors those
  payloads, so a field added to `api.py` is added there too.
- **Paying is witnessed, not asserted** (ADR-0046). `POST /api/pay` runs no
  pipeline, the way a re-sort runs none, and the products travel in the body for
  the same reason -- the browser is already holding them. What it must not send
  is a cart: it sends the run, which product of it, and `approved`, an echo of
  the title, price and currency it put in front of a person -- the `pay_label`
  and `pay_currency` that came down with the product, since what a person is
  shown has to be the cart and not the listing. The cart is built
  here from those products and the echo has to match it, so a page showing a
  stale price cannot buy at that price and a page that asked nobody cannot guess
  the right echo. Where a pre-signed open mandate authorises the run there is no
  echo to send: the mandate is the authority, and its own constraints are what
  the cart is held to. A receipt never carries the mandate chain -- that is a
  credential, and this payload reaches a browser; `reference` is the hash that
  points back at it.
- **Re-ordering a finished run is a request, not a re-run** (ADR-0035).
  `rank_again` is `rank_products` and nothing else -- no agent built, no page
  fetched, no model asked -- and it answers the shape `run_search` answers with,
  so the page shows a re-sorted run through the same view. The products travel
  in the body rather than being kept server-side under a run id: a session store
  is a lifetime, an eviction policy and a leak on a server that is stdlib on
  purpose, and the browser is already holding them. Every product is scored
  again from the set, so an edited figure changes nothing. `api.results_payload`
  is the one shaping of a run's products -- the API's answer, the file `--json`
  writes, and the file Download results hands over. A re-sort that fails is said
  beside the results it left alone, not in the banner that means the *run*
  failed.
- **A blank value means "use the default".** `api.parse_options` treats a
  missing key and an empty string alike, an empty form field meaning "unset" and
  not "zero" -- and the UI's `toQuery` drops blanks for the same reason. Values
  present but unusable raise `ApiError` with the status the client deserves.
- **The form refuses first, and never on a rule of its own** (ADR-0033). Every
  setting the page holds is one the server can judge without a model, a network
  or a minute of waiting, so it is judged before a run is opened -- but the rule
  is always Python's. `defaults_payload` ships `limits` (`limits_payload`, off
  `config.LIMITS` through `api._BOUNDED`, the one table saying which config
  field bounds each request key), and the form binds `[min]`/`[max]` from it. A
  source is not a range, so the form asks `GET /api/sources`, which reads the
  field with the same `parse_sources` a run would and answers `{"sources",
  "error"}` -- 200 either way, and naming the spec it was about so an answer for
  text since typed over is dropped. What the page cannot judge is still marked
  where it belongs: `ApiError` carries `field`, `payload()` sends it, and the
  `failure` event carries it to the box. `parse_options` is untouched -- this is
  the earlier line, not the only one. A region is deliberately *not* checked
  here: its shape stays in Python (ADR-0031), and what it gains is the mark.
- **Loopback is not a boundary a browser respects, so every request is admitted
  first.** `BuyAgentHandler._admits()` runs at the top of `do_GET`, `do_POST`
  and `do_HEAD` -- a new method added without it is unguarded and nothing fails
  -- and refuses `Sec-Fetch-Site: cross-site`, an `Origin` that is neither
  loopback nor equal to the request's own `Host`, and a `Host` outside
  `allowed_hosts` (ADR-0018). The first stops a page on another site starting a
  run whose answer it could never read; the last stops DNS rebinding, which is
  how that page would get to read one. `--allowed-host` names a further host; a
  bind to a public interface turns the `Host` check off and says so at startup.
- **Every request is answered, including the ones that go wrong.** `do_GET` and
  `do_POST` each end in a catch-all that logs and sends a 500, because an
  exception out of a handler escapes to socketserver, which closes the socket
  unanswered -- and a browser reads that as the server having gone, which is the
  one thing it did not do. `GET /api/config` is the reminder: it builds an
  `AgentConfig`, so `$BUY_AGENT_PROVIDER=olama` made every page load a dropped
  connection under a banner blaming the agent server. `server.main` refuses that
  name before it binds a port -- and `$BUY_AGENT_RAIL` beside it, a config
  resolving both -- for the same reason `__main__` makes either a usage error:
  it is not worth a server that starts and then 500s at its own form. The stream
  sits outside the guard and answers its own failures with a `failure` event,
  having spent the status line already.

### Two platform traps and one coupling

`server._CONTENT_TYPES` spells out the types `ng build` emits rather than
leaving them to `mimetypes`, which reads the registry on Windows and can answer
`text/plain` for `.js` -- which a browser refuses to run as a module, leaving a
blank page and no error. `_resolve` catches `OSError` and `ValueError` around
`Path.resolve` and falls back to the app, an unreadable path naming nothing to
serve; an encoded NUL raises on POSIX and does not on Windows, where
`ntpath.realpath` returns the path unchanged, so the branch is tested by making
`resolve()` refuse outright rather than by an input only one platform rejects
(ADR-0020).

`_SECURITY_HEADERS` goes out on every response, its CSP `'self'` throughout
because the app is served whole from one origin -- `'unsafe-inline'` for styles
only, which Angular's per-component `<style>` blocks need. That policy and the
UI's build are coupled: `optimization.styles.inlineCritical` is off in
`ui/angular.json` because Angular's critical-CSS inliner defers the global
stylesheet with an inline `onload`, and `script-src 'self'` refuses to run it,
leaving the sheet at `media="print"` and the page unstyled. Neither suite can
see that; it takes a browser. Anything else adding an inline handler, an inline
`<script>` or a request to another origin has the same shape of symptom.

### The components

Four of them, and `ui/README.md` is where each is drawn and argued. These are the
rules a change to them may not break.

- **No sentence about the model server is written here.** `App.unreachable` is
  whatever `installed_models` sent as `hint`, and it is `null` when the agent
  server itself did not answer, there being nothing to have asked. `App.asking`
  holds the `ModelSource` in flight, so the pill names the server being asked and
  not the one still on screen; `checking` stands the remedy and the picker down.
- **`progress-log` is presentation, not judgement.** Download log is offered for a
  failed run and a stopped one only. `transcript()` appends the failure message,
  which never reached the panel as a log line.
- **Buying takes two clicks, and the second restates the cart.** Title, the cart's
  `pay_label`, merchant, rail, and whether anybody is charged -- the cart the
  mandates will carry, never the product's own figures (ADR-0043). The card emits
  those three fields for the server to check against the cart it builds itself,
  and shows Python's `cannot_pay` where there is no button to offer.
- **A receipt is keyed by product name, in `App.receipts` and in both loops.** A
  re-sort ranks the same products again from 1 (ADR-0035), so tracking by index
  moves a purchase onto whatever lands at that rank next.
- **`pay` is the one setting `localStorage` does not remember.** The rest are
  standing answers about this machine; that one would arm a run nobody asked for.
  Every storage call is wrapped, so a browser refusing storage still has a form.
- **The form refuses on the server's rules and invents none of its own**
  (ADR-0033). `problems()` gates `canSubmit` off the ranges that came down with
  the defaults and off what `GET /api/sources` last said. A field whose `off()` is
  true is neither held to a range nor sent, a mark on a disabled box being one
  nobody can act on. `notes()` adds the server's `rejected` field, which does not
  gate the button and is shown only while the box still holds what `submitted`
  recorded. `options()` is the single place that payload is built.
  `numberTyped` reads `validity.badInput`, without which a box full of text is
  sent as the `null` a cleared box means (ADR-0012).
- **A number box is declared once, in `numberFields`**, under the key that also
  carries its range and names its refusal; `placeholders()` reads its fallback off
  `defaults_payload` by that key. `tests/test_conventions.py` holds those keys
  against `limits_payload`.
- **The model field marks what it cannot offer and never hides it** -- "not
  served" for a name the server does not have, "embedding only" for a pull that
  cannot answer a prompt (ADR-0032). `ModelOption.note` is filled from Python's
  `completion`: the browser writes the suffix, not the judgement. `refresh`
  carries a `ModelSource`, provider and address both, a vLLM asked Ollama's
  question answering 404, and `takes_num_ctx` off the provider's row is what
  disables the context field.
- **A mark opens the panel it is in.** The form opens Settings itself the first
  time `flagged()` is non-zero, on the marks changing and not on the panel's
  state, so shutting it again stays the reader's to do.

`create_server(agent_factory=...)` is the seam the server tests inject a stub
agent through, the way `BuyAgent(config, llm=...)` is for the pipeline;
`allowed_hosts=` is the second, `None` meaning "answer any `Host`", which is what
a public bind gets. Angular components are tested in jsdom with `TestBed`,
`AgentService` against a fake `EventSource`.

### demo/

`demo/README.md` says what the two recordings show, what is real in them and how
to take them again. Three things about the directory hold here.

- `demo/server.py` starts the *real* `buy_agent.server` with only `search_web`,
  `enrich` and the chat model replaced, so everything between the search and the
  ranking is the real pipeline and a recording needs neither Ollama nor the
  network. The scripts beside it make the fake model wrong in the six ways a
  small model is wrong, which is what puts `clean_products`, `ground`,
  `verify_opinions`, `attribute_sources` and `deduplicate` each catching one in
  the progress panel.
- A third demo is a module offering the same five names `books.py` and
  `laptops.py` do, plus a row in `server.SCRIPTS`. `docs/ui.png` is taken off
  this server rather than off `buy_agent.server`, the model dropdown and the
  header pill being answers from an Ollama, and it is clipped to the form card,
  so a field added to the settings makes it taller rather than falling off the
  bottom.
- Nothing here is imported by `buy_agent/` or by either suite, so it is not
  covered, not mutated and, per `.dockerignore`, not in the image.

## Tests

`docs/testing.md` is the long form: both suites, the counts, the coverage
floors, the nightly run, the benchmark and the mutation run. What is written
here is what a change has to obey.

**The seams.** `BuyAgent(config, llm=...)` injects the model, and
`tests/conftest.py` provides a `FakeLLM` with the one `answer` method
`chat.ChatModel` asks for. A stand-in for a *chain* is a class with `invoke`,
which is what `integration/conftest.py` wraps the real one in.
`create_server(agent_factory=...)` is the same seam for the server, and
`allowed_hosts=` is the second one.

**Where the network is patched.** Three places: `buy_agent.agent.search_web` and
`buy_agent.agent.enrich` for pipeline tests, `buy_agent.search.DDGS` and
`buy_agent.fetch.httpx.Client` for the wrappers' own tests. `search_web` is
patched on `agent` and only there, which is why the fan-out over named sources
lives in `agent.py` rather than beside the rest of `sources.py`: a second call
site would be a second thing to patch, and a test that forgot it would reach the
real DuckDuckGo silently. The HTTP rail's transport is patched at
`rails.httpx.post`, where that module imported it.

**Where the model clients are patched.** Both are patched where
`buy_agent.providers` imported them -- `providers.Client` for Ollama's chat and
for the `show` a listing asks per tag, `providers.openai.OpenAI` for vLLM's
chat. Both listings are patched at `providers.httpx.get`, Ollama's `/api/tags`
beside vLLM's `/v1/models`. The tags are read off the endpoint rather than
through the client's typed listing, which declares one of the two spellings that
endpoint names a model by and discards the other: a tag arriving as `name` alone
reached the picker as nothing at all. What a setting reaches is asserted on the
*request*, not on a wrapper read back, since ADR-0038 sends the window, the
thinking switch and the schema per call.

**Two patches that no longer work.** `ollama.Client` is not the name to patch:
`providers.py` imports it at module level, which is also the only place either
client is named. `DDGS.text` is not either, the name `ddgs` exports being a
wrapper that constructs a different class.

**A table row is compared by identity only through its module** --
`providers_module.OLLAMA`, never a name imported from it, and `rails.RAILS` the
same. `tests/test_providers.py`, `tests/test_rails.py` and
`tests/test_config.py` each reload those modules to re-read their
environment-derived defaults, and a reload re-runs the module over its own
globals. `provider_for` then answers with the new rows while a name bound at
import time holds the old ones. The teardown puts the *values* back, which is
why comparing those is safe anywhere.

**No test in `tests/` touches the network, a model server or the machine's own
cache, and all three stay that way.** `conftest.py` points
`$BUY_AGENT_CACHE_DIR` at a scratch directory per test, autouse, so a test that
builds a real `BuyAgent` remembers its answers somewhere disposable (ADR-0044)
and no test can answer another test's question. A second autouse fixture unsets
`$BUY_AGENT_AP2_KEY`, `$BUY_AGENT_AP2_MANDATE` and `$BUY_AGENT_MERCHANT_URL`: a
developer who has configured paying would otherwise have a suite signing with
their key and buying on their budget.

**The payment tests sign real mandates** -- keys generated in the test, read
back through the AP2 SDK's own verifier, because a mandate that verifies only
against a fake verifier is one nobody else would take. That needs the optional
SDK, which `ci.yml` and `mutation.yml` each install in a step of their own.

**The server tests are the one exception to "no sockets".** They bind loopback,
routing and status codes being what they are about, and pass
`serve_forever(0.01)`, the default 0.5s poll costing half a second per test on
shutdown. Four speak the protocol over a raw socket, urllib refusing to build a
request with a malformed `Content-Length`. `raw()` reads until the declared body
has arrived, the headers and the body being separate writes that can land in
separate segments; the one asserting that a body refused unread ends the
connection reads to EOF instead.

**`integration/` is where a real model goes**, outside `testpaths` so a bare
`pytest` cannot reach it.

**Nothing else should sleep.** The suite takes about eight seconds, most of it
the three tests that spawn an interpreter -- two for what only a real import can
answer (`python -m buy_agent` still runs as a script, and still imports with
`$BUY_AGENT_RAIL` misspelt), one PowerShell for the whole of
`tests/test_start_script.py` -- plus 1.0s of deliberate `StubAgent.delay` in the
three server tests that need a run to still be going. A run that takes much
longer means something is reaching out.

**Two optional prerequisites decide how many of them run, and neither is a
failure when it is absent.** With neither `pwsh` nor `powershell`, most of
`tests/test_start_script.py` skips on `needs_powershell`; without the AP2 SDK,
the tests that sign or verify a mandate skip on `needs_ap2`, which asks
`mandates.available()` once at import. Both markers live in `tests/conftest.py`.
Skipping is only ever the local convenience: both workflows install the SDK, and
the 100% the coverage floor is set just under cannot be reached with those tests
sitting out. So a marker on a test that does not need the SDK fails the run that
matters, and a test that needs one and carries none is red on every checkout the
marker exists for. `needs_ap2` sits on the parametrised *case* that reaches the
signing stack, not on a whole function whose other half fakes the import it is
about. `docs/testing.md` is the one place all three counts are written down, so
a new test file is one edit there.

### The convention tests

Both suites cover essentially every line, so coverage no longer says where the
next test should go. `tests/test_conventions.py` covers what it cannot: the
rules that hold *between* modules, read off the declarations rather than
exercised. A field added on one side of the language boundary and forgotten on
the other is otherwise invisible to both suites. It asserts that

- `api._STATUS`, the `except` tuple in `__main__.main` (parsed with `ast`) and
  `BuyAgent.run`'s documented `Raises` name the same three failures;
- `ranking.SortBy`, `api.SORT_OPTIONS`, `--sort-by`'s choices and the TypeScript
  `SortBy` union offer the same criteria;
- every provider in `providers.PROVIDERS` is offered by `--provider`, by
  `api.PROVIDER_OPTIONS` and in the rows the form's picker is built from, and
  `ProviderOption` is mirrored in TypeScript;
- every rail in `rails.RAILS` is offered by `--rail`, by `api.RAIL_OPTIONS` and
  in the form's picker; `RailOption`, `Receipt` and `PayOptions` are mirrored in
  TypeScript; every key `pay_now` reads is one the page sends; the payment's
  failures are `api.PAY_STATUS` and what `__main__._bought` catches, ordered
  subclass-first, and deliberately *not* the three in `_STATUS`; and
  `buy_agent.mandates` is the only module in the package that imports `ap2`;
- `agent.types.ts` mirrors `defaults_payload`, `product_payload`, the
  `breakdown` a product carries, the `Opinion`s it quotes and `run_search` field
  for field;
- the form holds a number to a range for every range `limits_payload` ships and
  writes no `min` or `max` of its own into its template, and every key
  `parse_options` reads is one `SearchOptions` sends -- a key it reads and the
  form never sends is a refusal marking a box that is not there (ADR-0033);
- the `Dockerfile` pins the versions CI tests against, copies the built UI where
  the server looks, exposes the port it binds and installs the runtime
  dependencies only, and `.dockerignore` keeps out everything `.gitignore` does
  while keeping in everything those `COPY` lines ask for;
- every job in `ci.yml` names both a Windows and a Linux runner between them and
  holds a merge up for neither, over the events the workflow actually runs on
  (ADR-0037), and sets up exactly one Python and one Node for the three files
  that pin themselves to those; every workflow sets up that same Python and
  builds with that same Node; the workflows pin the same version of every action
  they share, an update reaching only one leaving every file valid and a
  scheduled run on the older action; and every one of them declares a read-only
  token, the two jobs that publish anything widening that on their own job
  instead;
- the release archive puts the built UI where `server.DEFAULT_UI_DIR` looks, and
  both of its jobs check out the tag being released rather than a branch;
- the nightly run pulls the tag `integration.TINY_MODEL` names, names the
  directory `testpaths` leaves out, sets `$BUY_AGENT_REQUIRE_OLLAMA` so an
  absent Ollama fails instead of skipping, caps itself at the five minutes the
  docs quote, and leaves `integration.LIVE_TIMEOUT_SECONDS` room inside that cap
  to fail a stopped model first;
- every ADR is indexed, numbered to match its heading, carries the status, date
  and sections ADR-0001 asks for, and cites only records that exist;
- the Saturday mutation run mutates the package `.coveragerc` measures, on the
  Python `ci.yml` pins, with every file these tests open -- or import from
  outside `buy_agent`, `benchmark/` and `integration/` included -- named in
  mutmut's `also_copy`;
- the linter reads that same package, `.pylintrc` sits where every command that
  runs pylint is run from, and no line of the package takes a check away without
  saying why: a `# pylint: disable` with no prose above it is a suppression
  nobody can date, which is what the `# noqa` codes it replaced had become
  (ADR-0048);
- every skill in `.claude/skills/` is named after its own directory, is
  described where this file introduces them, and names only files, tests and
  tables that exist -- `add-option` the two the form declares, `preflight` the
  checking commands `ci.yml` runs and the toolchains it pins;
- every module in the package takes its logger off the package's own name,
  leaves its formatting to the logger, marks nothing as the report, configures
  logging nowhere but `logging_setup`, and writes to stdout not at all -- and
  each entry point wires its `--verbose` flag to the level. Every one of those
  is invisible where it is broken: the line still reaches a terminal, and only
  the browser's progress panel, a `> top.txt` or a `-v` nobody ran is any the
  wiser.

### The architecture tests

Those are the rules that span a *declaration*. `tests/test_architecture.py` is
the other half -- the rules that span an *import* -- asserted against the import
graph with [ArchUnitPython](https://github.com/LukasNiessen/ArchUnitPython)
(ADR-0047). Which module may know about which is what this file says most often,
and an import in the wrong direction runs perfectly: it passes that module's own
tests, keeps the coverage floor and survives the mutation run. Nineteen rules,
each the executable form of a sentence written down here or in a record, and a
twentieth test that keeps them honest:

- the package has **no import cycles**, and imports **none of the five trees
  that import it** -- `tests/`, `integration/`, `benchmark/`, `demo/`,
  `scripts/`, none of which is in the image or the release archive;
- every module sits in a **layer that reaches only downward** -- entry points,
  web, orchestration, pipeline, paying, model access, settings, domain -- with
  the four edges that are decisions named in the test: the pipeline never reads
  the config (which is what lets `rank_products`, `ground` and `Constraints` be
  tested with three arguments and no environment), the pipeline never pays
  (ADR-0046), paying never asks the model, and the model seam knows nothing
  about products (ADR-0038);
- **one seam, one module**: `mandates.py` alone imports `ap2` (ADR-0046),
  `providers.py` alone a model client (ADR-0029), `search.py` alone the search
  backend (ADR-0021), `fetch.py` alone the HTML parser, the three that speak
  HTTP -- `fetch`, `providers`, `rails` -- are the three the suite patches, so a
  fourth is a request from a module nobody thought made any, and `argparse`
  belongs to the two modules handed an `argv`: a parser below them is a third
  set of defaults, and one that answers a bad value by exiting the process;
- **`buy_agent/__init__.py` imports the four modules it re-exports from** and no
  others. It is the file both rules above let off, so it is the one that needs a
  rule of its own -- and importing any submodule runs it first, so a `from` line
  here is paid by every caller: one naming `payment` would put the optional AP2
  stack behind `import buy_agent`, one naming `server` a socket module behind
  `python -m buy_agent`;
- `server.py` imports **nothing outside the standard library** (ADR-0010), read
  off the graph rather than off `requirements.txt`, so a dependency added
  tomorrow is covered without anybody writing it down again;
- the **tables know nothing about the config** resolved from them (ADR-0029),
  and `search.py`, `sources.py` and `mandates.py` know nothing about any other
  module -- deciding is not fetching, and the AP2 seam translates between two
  vocabularies without speaking either back (ADR-0046);
- **the web tier is split at the payload**: `api.py` reaches no socket, no
  thread and no queue, which is what leaves every one of its rules assertable by
  calling a function while the status line and the stream stay in `server.py`;
- **the steps take values and answer values**: nothing in the pipeline or the
  domain reads an environment variable, a file, a clock or a random number --
  the half of "the pipeline never reads the config" no layer can state, and what
  says a remembered answer (ADR-0044) is the same answer;
- **the steps do not chain themselves**: the order of the pipeline is
  `BuyAgent.run`'s to know, since a joint argued in one place is a joint that
  can be moved, and the one edge inside that layer is `verification.py` sharing
  `extraction.py`'s vocabulary;
- **nothing that decides the answer asks the model**: `ranking`, `constraints`,
  `verification` and `models` may not reach the chat seam, the fetcher or the
  search (ADR-0002).

Three things about how those are written. Every rule is checked with
`ignore_type_checking_imports=True`: an import under that guard never runs, so
it is a name and not a dependency, and it is how this package already spells "I
use this type and not this module". `buy_agent/__init__.py` is in no layer and
outside the cycle rule, because `from buy_agent import mandates` -- the deferred
import `api`, `payment` and `rails` each use -- reads as an edge onto the
package rather than onto the module. And a negated rule whose subject matches
nothing *passes*, which is the one way this file could be worse than no file:
`only()` and `every_module_but()` therefore check that every filename they name
is really a module of the package, so a rename fails the rule about that module
rather than quietly making it a no-op. A module in no layer is exempt in that
same silent way, and one named in *two* is free to reach whatever either row
allows, so the twentieth test collects the placings and counts them. Size is
deliberately not asserted: a ceiling on lines, methods or cohesion would be a
policy nobody has decided.

### The live suite

`integration/` is the second Python suite and the only place a real model is
involved (ADR-0026). It is Ollama's alone: vLLM needs a GPU and a CPU runner
cannot host one honestly, so that provider's half is asserted in
`tests/test_providers.py` and named as a gap in ADR-0028. A directory rather
than a marker, because `pytest.ini` keeps `testpaths = tests`: "nothing in the
suite touches Ollama" is then a property of where a file sits, not of anyone
remembering an annotation. Five things there are load-bearing:

- **The model is real; the web is not.** `search_web` and `enrich` are still
  faked, over the ten fabricated pages `benchmark/corpus.py` owns and
  `benchmark.runner.serving_the_corpus` installs, so a nightly failure caused by
  DuckDuckGo rate-limiting says nothing about this code. The fake stops at the
  transport: `enrich` reads the fabricated text and then runs the real
  `fetch.condense` over it, so the prompt is shaped as a production prompt is,
  wide enough for ADR-0019's `num_ctx` question to arise.
- **One run, many assertions.** A session-scoped `live_run` fixture runs the
  pipeline once and each test reads something different off it. The extraction
  chain is *wrapped* rather than replaced -- a `Recording` delegating `invoke`,
  which is the whole of a chain's surface, keeps the raw `ProductList` on the
  way past -- so what runs underneath is the run `BuyAgent` would have made.
- **Almost nothing asserts the model was right.** The assertions are the
  invariants: every name, figure and quote in the sources, every link a page
  that was searched, no repeats, the ranking ordered. A 0.6B model is not held
  to an answer. The exceptions are a smoke test that something was extracted and
  a second that something was quoted, since every other assertion passes
  vacuously on an empty list.
- **An absent Ollama skips locally and fails on the schedule.**
  `$BUY_AGENT_REQUIRE_OLLAMA`, set by the workflow and nothing else, flips it --
  a nightly job that skipped every test it has is a green job that checked
  nothing. `$BUY_AGENT_TEST_MODEL` moves the tag; `$OLLAMA_MODEL` deliberately
  does not, that one moving the default the agent ships with.
- **The same run is also scored.** `integration/test_benchmark.py` puts it
  through `benchmark.scoring.score_run` and fails under `FLOORS`, one test per
  metric so a red job names which half slipped. That is the other question --
  not "did the promises hold" but "how well did it do" -- and it needs the
  answer key the four points above deliberately do without.

### The benchmark

`benchmark/` is the answer key the invariants above cannot have, and the scorer
over it. It owns the corpus, which is why `integration/` reads it back: one
corpus and one model call answer both questions. ADR-0036 has the reasoning and
`docs/testing.md` the metric table; four rules hold here.

- **The key is per-product sets, not one right answer.** `answers.py` records
  every `(price, currency)` and every `(rating, review_count)` a page prints for
  each product; the canonical value beside each set exists only to build the
  ranking the run should have produced. `329 USD` is a pairing no page printed
  and so one wrong price, not two right halves (ADR-0022).
- **`scoring.METRICS` is the one place a metric is declared** -- its weight and
  what it scores on an empty denominator -- and a `Scorecard` is `right out of`
  per name, so nothing recomputes a ratio. Where the pipeline has a rule the
  scorer uses it: `verification.distinctive_words`, `word_coverage` and
  `NAME_COVERAGE` for names -- the bar `mentions_name` sets, applied both ways
  -- the *condensed* page text for quotes, `rank_products` for the ideal order.
- **The floors are a tripwire, not a target.** Set where a 0.6B model happens to
  sit today, the nightly would fail for a reworded prompt, which is how a
  scheduled run gets ignored. Raising one is a commit of its own quoting the
  runs that justify it.
- **Editing the corpus means re-running both scripted answers.** `PERFECT` must
  score exactly 1.000 -- which is what says the key is *reachable* rather than a
  silent ceiling under every number the nightly reports -- and `SLOPPY` is wrong
  in eight ways, pinned to the exact counts each mistake should produce.

### The scripts

Both Python scripts in `scripts/` are tested like the rest, by the same rule as
`clean_products`: whatever decides an answer belongs where it is testable rather
than in a workflow's shell. `mutation_report.py` decides whether a mutation run
passes; `update_ollama.py` decides what "updated" means -- a digest that moved
between the listing before the pulls and the one after, since `ollama pull`
reports `success` whether it replaced anything or not. It is the one thing in
`scripts/` that imports from `buy_agent` (`providers.OLLAMA` for the
`$OLLAMA_HOST` defaults), which is why it runs as `python -m
scripts.update_ollama` from the repository root rather than by path.

`scripts/start.ps1` is the README's "Starting it on localhost" as one command
with no arguments (ADR-0023) -- venv, Ollama, `ollama pull`, `ng build`, the
server, the browser, each step skipped when already done. It decides nothing the
rest of the project decides: the provider, model and address are read off one
`AgentConfig()` with a `python -c`, so `$BUY_AGENT_PROVIDER`,
`$OLLAMA_MODEL`/`$OLLAMA_HOST` and `$VLLM_MODEL`/`$VLLM_HOST` still reach it and
no default is written down twice. Off one config rather than three constants,
because the pair belongs to the provider. Ollama is the only server it starts --
the install-and-pull half is behind a provider check, and anything else is
waited for at `/models` and named rather than launched, a vLLM needing a GPU, a
served model and flags this script has no business choosing. Paying is the same
shape one step further: the AP2 SDK is an optional install and somebody else's
git repository, so it is fetched only where the environment already names a
rail, a merchant, a key or a mandate -- the settings nothing but a payment reads
-- installed with the two commands `mandates.INSTALL` spells out, and then asked
for again, pip exiting 0 for an install that cannot be imported being this
dependency's documented failure. A run that skips it says which variable to set,
since a page that silently never offers to buy anything is the confusing half of
optional. Its seven agreements with the rest of the project are in
`tests/test_conventions.py`. No default's value appears in the script. The URL
it opens a browser at is the one `server.build_parser` binds, and the build it
probes for is the one `server.DEFAULT_UI_DIR` serves. The Python and Node it
sends you to install are the ones `ci.yml` pins. The SDK is installed the way
`mandates.INSTALL` says, and reported by asking `mandates.available()` rather
than by looking. And every `$BUY_AGENT_*` it reads is one the package reads too.

`tests/test_start_script.py` cannot run the script -- it installs, downloads,
starts two servers and opens a browser -- so it does everything short of that
through `tests/start_script_probe.ps1`. The probe parses the script into an AST,
lifts the function definitions out and dot-sources them on their own, leaving
the body unrun, then writes what it found and what those functions did as one
JSON document: `Run` is given this suite's own interpreter, so its
`$LASTEXITCODE` check is exercised against a real process, and `Answers` is
given a stubbed `Invoke-WebRequest` and a clock that only moves when it sleeps,
so its polling loop is exercised without a network or a wait. The AST also
enforces the rule the script's error handling rests on: every program it runs
goes through `Run`, since a native command that fails raises nothing whatever
`$ErrorActionPreference` says. One PowerShell process for the whole module,
starting one costing about as long as the rest of the suite; `pwsh` or
`powershell`, whichever is on PATH, and the module skips where there is neither.

## Environment

Development happens on Windows with PowerShell as the default shell; prefer
PowerShell syntax for terminal commands, or use the Bash tool explicitly for
POSIX scripts.
