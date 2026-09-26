# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with
code in this repository.

## What this is

A shopping agent: it takes a plain-language request ("wireless headphones under
$200"), searches the web, extracts up to 10 products along with what the pages
say about them, ranks them, and logs the top 3. Built on a local model, served
by Ollama, by a vLLM behind its OpenAI-compatible API, or by whatever a LiteLLM
proxy routes to behind that same API -- `AgentConfig.provider` chooses,
`buy_agent/providers.py` is the only module that knows the difference (ADR-0028,
ADR-0068), and each server's own client is called
directly: there is no framework between the prompt and the answer,
`buy_agent/chat.py` being all of one there is (ADR-0038). `ui/` is an Angular
front end onto the same pipeline, served by `buy_agent.server`. Optionally --
off by default, and off unless an extra dependency is installed -- it can also
*buy* what it found, authorised by signed [AP2](https://ap2-protocol.org)
mandates rather than a stored card (ADR-0046). And optionally again, the same
way: a server bound to this machine, with Playwright installed, draws each card
beside a picture of the page it links to (ADR-0065).

`README.md` keeps the tour and links out to the longer sections beside it:
`docs/models.md` (keeping Ollama's models current), `docs/docker.md` (the web
tier as a container, and what a release publishes), `docs/testing.md` (both
suites, the coverage floors, the nightly run, the benchmark, the mutation run
and the nightly audit of both dependency lists) and `demo/README.md` (three recorded runs of the UI, one of them with a
synthesised soundtrack, the two stills the README shows, the harness that
took all five, and a local merchant for the `http` rail).

The rules below are the *rules*. `docs/adr/` is why each exists and what was
rejected; the module docstrings carry the local detail. Prefer adding a rule
here or a convention test over restating an ADR.

## Commands

Dependencies live in a `.venv` created with stdlib `venv`; there is no
`pyproject.toml` and no packaging step. Run everything from the repository root.

```powershell
.\scripts\setup.ps1                           # all of the setup below, both halves (ADR-0067)
.\scripts\preflight.ps1                       # the whole gate ci.yml applies; -Only python|ui

python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt          # runtime deps: requirements.txt

python -m pytest                              # whole suite (~6s)
python -m pytest tests/test_ranking.py        # one file
python -m pytest tests/test_ranking.py::test_cheaper_wins_when_rating_is_equal
python -m pytest -k verification              # by name
python -m coverage run -m pytest ; python -m coverage report   # with coverage
python -m pylint buy_agent                    # the linter, from the root (ADR-0048)
python -m mypy buy_agent                      # the type checker, from there too (ADR-0063)

ollama pull qwen3:0.6b ; python -m pytest integration   # against a real model

python -m benchmark --scripted perfect        # score the pipeline, no model needed
python -m benchmark -v --json score.json      # ...and against whatever is serving

python -m buy_agent "gaming laptop under $1500"          # run the agent
python -m buy_agent "espresso machine" --model lfm2.5 -v
python -m buy_agent "gaming laptop" --provider vllm      # another model server
python -m buy_agent "gaming laptop" --provider litellm   # ...or a LiteLLM proxy
python -m buy_agent "running shoes" --sort-by price --json results.json
python -m buy_agent "headphones" --max-price 200 --min-rating 4.5   # bounds, enforced
python -m buy_agent "headphones" --cache-ttl 0                      # every page fresh
python -m buy_agent "espresso machine" --compare                   # ...and what moved since
python -m buy_agent "wireless earbuds" --source rtings.com --source @mkbhd

pip install -r requirements-ap2-deps.txt        # what the AP2 SDK imports
pip install --no-deps -r requirements-ap2.txt   # ...and the SDK, for paying only
python -m buy_agent "headphones" --pay                       # asks, then signs; charges nobody
python -m buy_agent "headphones" --pay --spend-limit 250      # ...and not a penny more
python -m buy_agent "headphones" --pay --rail http --merchant-url https://pay.example

pip install -r requirements-screenshots.txt   # Playwright, for a picture of each page
python -m playwright install --only-shell chromium   # ...and the browser it drives

python -m buy_agent.server                    # the UI and its API on :8000
.\scripts\start.ps1                           # ...or all of it from cold, no arguments

python -m scripts.update_ollama               # re-pull Ollama's models, report what moved

pip install -r requirements-audit.txt         # pip-audit, and nothing else
python -m pip_audit -r requirements.txt -r requirements-dev.txt   # what the nightly audit asks
cd ui; npm audit --audit-level=high           # ...and the other half of it

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
npm run lint                                  # ESLint and angular-eslint, templates included
npm run format:check                          # Prettier reading; `npm run format` writes
npm start                                     # dev server on :4200, proxying /api to :8000

npx stryker run                               # mutation testing; ~90 min, one `ng test` each
cd ..; python scripts/mutation_report.py ui/reports/mutation/mutation.json
```

`ui/` is a separate, ordinary Angular workspace with its own `package.json` and
tests: Angular 22 on Node 22.22.3+, 24.15+ or 26+ (older Node is refused by the
Angular CLI, not by anything here), and nothing on the Python side needs Node.
The Python half has a linter and a type checker and no formatter -- pylint and
mypy, both over the package and both from the repository root, where one finds
`.pylintrc` and the other the `[mypy]` section of `setup.cfg` (ADR-0048,
ADR-0063) -- and the UI has a linter and a formatter: `npm run lint`, ESLint with
`angular-eslint` over `src/` and its templates, configured by `.pylintrc`'s rule
in `ui/eslint.config.mjs` (ADR-0066), and Prettier, its own type check being half
of `npm run build`. All of them are gates rather than habits: the linter runs in
`ci.yml` after the tests and the build for the reason pylint runs after the tests
in the other job, and has to come out with no message, warnings and unused
suppressions included; `npm run format:check` is the same glob as `npm run
format` reading rather than writing, runs last, and `npm run format` is what to
run when it goes red. `npm run build` is the
UI's other check and is a type check before it is a build: `ui/tsconfig.json`
sets `strict` and `strictTemplates` for both halves of the workspace and
`ui/tsconfig.app.json` adds `noUncheckedIndexedAccess` for the shipped one,
that being the member `strict` leaves out and the one this app needs, since
every lookup a component makes is by a key that came off a payload and without
it a miss is typed as a hit -- which is how a `?? null` written for a real
`undefined` reads to the compiler as one that can be deleted. It stops at the
specs on purpose: a test indexing past the end of a list it built itself is a
failing assertion on the next line, and the `!` per subscript it would take
there says nothing about the code that
ships. `strictTemplates` is the same promise over the *bindings*, which is where
a payload actually lands and the one place a label Python wrote can be misused
with no `.ts` file saying so -- and being shared, it is what decides how the
three maps a template looks up by a payload key are typed: `App.receipts`, the
form's `limits` and its `placeholders` each say `| undefined` themselves rather
than leaning on the setting above, which the specs are compiled without and
which would have the `?? null` each is read through reported *there* as a `??`
to delete. That report is a warning and nothing fails on a warning, so a lookup
left typed as a hit is this check saying something and stopping nothing.

Without `ui/dist/ui/browser` the API still answers and the page is a 503 saying
how to build it (`--ui-dir` points at a build elsewhere) -- as a small HTML
page for a client whose `Accept` says it is a browser, which is who reads that
message, and as the same sentence in JSON for everyone else. `_unbuilt_remedy`
writes that sentence and there are two of it, because a `--ui-dir` with no
Angular workspace three levels above it -- a release archive, a copy, a typo --
has nowhere to run `npm install` and was told to run it in the build's own
directory anyway. A remedy nobody can follow is worse than none, so
where `_workspace_for` answers `None` the message says there is nothing there to
build and names `--ui-dir` instead. One remedy, three places: the page, the JSON
and the warning `main` logs at startup.

### The container

```powershell
docker build -t buy-agent .
docker run --rm -p 8000:8000 buy-agent
docker run --rm buy-agent -m buy_agent "espresso machine"
```

A `node:22.23.3-bookworm-slim` stage builds `ui/`; a `python:3.14-slim` stage
installs `requirements.txt` and gets the build copied to `ui/dist/ui/browser`
beside the package, where `server.DEFAULT_UI_DIR` looks. Neither model server is
in the image or started by it (ADR-0015): the container talks to the host's
through `host.docker.internal`, which `$OLLAMA_HOST`, `$VLLM_HOST` and
`$LITELLM_HOST` are all set to, and which needs `--add-host=host.docker.internal:host-gateway` on Linux.
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
a build rather than quietly fattening one. A third reads the gap between those
two: every directory at the top of the tree is either named by a `COPY` line or
matched by a pattern here, so the next `demo/` is a failing test rather than
somebody's memory. Directories only -- a stray file at the root is a few
kilobytes and is what the documented commands leave behind (`--json score.json`),
while a stray directory is the whole of itself. Patterns match from the root, so
the UI's own leavings are written out (`ui/dist/`, `ui/coverage/`, the sandbox
and the report a mutation run leaves): `ui/` is the one directory copied whole, and a `coverage/` matched at the root reaches nothing
inside it.

### Settings and their environment

`$BUY_AGENT_CACHE_DIR` moves where a run keeps what it can reuse -- `pages/` for
the text the fetch read (ADR-0040) and `answers/` for what the model said about
it (ADR-0044) -- defaulting to the platform's own cache directory under
`buy-agent/`. Like `$VLLM_API_KEY` it has no flag and no form field -- a path on
the server's disk is not a browser's to choose -- while *how long* an entry
lasts is the ordinary `cache_ttl` setting, one number for both kinds, 0 meaning
"read every page off the web and ask the model every question". A sampled run
(`temperature` above 0) is never remembered whatever the setting says: it has no
one answer to remember. *How much* it may hold is `cache.MAX_BYTES`, per kind and
per directory, enforced on the way in by `prune` deleting the oldest first
(ADR-0052): age is no bound on size, `cache_ttl` may be set to the thirty days
`LIMITS` allows, and what is stored is the whole visible text of a page rather
than the excerpt a prompt saw. Expiry is asked first because it is free. The cap
is a constant and not a setting, for the reason the directory is not one.

Paying adds two settings of the same kind, and for the same reason.
`$BUY_AGENT_AP2_KEY` is the EC P-256 key mandates are signed with -- a secret,
so it stays out of a shell history, out of `defaults_payload` and out of any
form -- and `$BUY_AGENT_AP2_MANDATE` names a pre-signed *open* mandate, a path
on the server's disk. That second one is not merely a setting: its presence is
what puts a run into AP2's human-not-present mode, because the open mandate *is*
the authorisation and a second switch saying "run unattended" would only fail
without the file anyway (ADR-0046).

`runs/` is the third directory under that same root and is deliberately *not* a
third kind of cache entry (ADR-0060). `cache.py` holds what a run can *reuse* and
expires all of it on one clock, because a stale page is not evidence; a journal is
a record kept for a person to read, and an expiry would take away the one question
it answers. So `journal.py` owns it, it never expires, and it is bounded by a
count instead -- `MAX_RUNS` runs of one search and `MAX_SEARCHES` searches, the
least recently *run* one out first, which is ADR-0052 turned around because
pruning oldest-first deletes exactly the entry a comparison wants. What it holds
is a name, a price and a currency per product: what a comparison needs and nothing
else, a shopping history on disk being a different object from a page cache. What
the two *do* share is how a file is put there: `cache.write_atomically` and
`cache.file_for` are one temporary-file dance and one hashed name, and the split
being argued here is a policy one -- what is kept, and for how long -- not a second
copy of the code that keeps it, which was the half either module could have got
subtly wrong on its own.
`journal` is the ordinary setting that turns it off, `--compare` is the CLI
reading it, and the run payload carries `changes` and `compared_with` for the
panel. `agent.journal_for` is the only place a config becomes a key, and that key
is what was *asked* -- the request, the region, the scale, the backend, the
sources, the three bounds and the count -- and never how it was answered: keying
on the model would make every change of model a search with no history at all,
which is the opposite reading from `_asks_the_same_question`'s and deliberately
so.

`$BUY_AGENT_BACKEND` moves which search backend a run asks, and each backend has
its own variables behind it -- `$SEARXNG_HOST`, and `$BRAVE_HOST`/`$BRAVE_API_KEY`
-- read on that backend's own row in `search.BACKENDS` (ADR-0057). Neither the
address nor the key has a flag or a form field: one is a property of the machine
the server runs on and the other is a secret, and the browser has no box for
either. A backend that needs a key and has none fails at its own row with a
sentence naming the variable, rather than at the config: `AgentConfig` refuses a
*name* nothing can search, exactly as it refuses a provider, but what is
configured on the machine is not what the request got wrong.

`$BUY_AGENT_PROVIDER` moves which model server a run talks to, and each provider
has its own variables behind it -- `$OLLAMA_MODEL`/`$OLLAMA_HOST` and
`$VLLM_MODEL`/`$VLLM_HOST`/`$VLLM_API_KEY` and
`$LITELLM_MODEL`/`$LITELLM_HOST`/`$LITELLM_API_KEY` -- read on that server's own row in
`providers.PROVIDERS` (ADR-0029). `AgentConfig.model`, `base_url` and `api_key`
therefore default to the *empty string* and are resolved per provider in
`__post_init__`: which value is right depends on a sibling field, so a plain
default could only ever be right for one of them, and "unset" is spelled the
way a blank form field is (ADR-0012). Hence `--model` and `--base-url` default
to `""` and interpolate every provider's pair into their help. `$VLLM_API_KEY`
and `$LITELLM_API_KEY` are the settings with no flag and no form field -- secrets,
so they stay out of a shell history, out of `defaults_payload` and out of
`provider_options()`. `$LITELLM_MODEL` defaults to `local_model`, a placeholder:
a proxy's model is an alias out of its owner's `model_list`, which nothing here
can know (ADR-0068).

Every other CLI flag defaults to the matching `AgentConfig` field, so a new
setting is added in `config.py` and picked up rather than repeated. One field is
deliberately renamed on the way out: `AgentConfig.reasoning` is `--think`
(`BooleanOptionalAction`) on the CLI and `think` in the JSON payloads and
`agent.types.ts`. Its tri-state is Ollama's thinking mode, where `None` means
"send nothing and leave the model alone" rather than "off".

It pairs with `num_ctx`: the extraction prompt runs to ~4.3k tokens, so on
Ollama's default 4096 window a thinking model reasons until the context is gone
and never emits any JSON. Ollama's default model is `gemma4:12b`, which thinks,
so `reasoning` defaults to `False` and `num_ctx` to `16384` -- the prompt being
only half of what has to fit, the JSON for ten products the other (ADR-0050). A
model that cannot think ignores both; one that wants its own behaviour back is
given `num_ctx=None, reasoning=None`, reachable from neither front end
(ADR-0019).
`num_ctx` and `cpu_only` are the two settings the providers do not share, and
they are the same shape: vLLM fixes its window with `--max-model-len` at startup
and picks its device with `--device` there, and a LiteLLM proxy leaves both to
whatever it routes to, so `Provider.takes_num_ctx` and `Provider.takes_cpu_only`
are both false for those two, neither value is sent, and both
front ends say so rather than accepting a setting nothing reads. `cpu_only` is
Ollama's `num_gpu: 0` -- no layers on the card -- sent only when it was asked
for, `False` meaning "offload whatever you would have" rather than a number to
send, exactly as `num_ctx`'s `None` does. It is deliberately not in the
fingerprint a remembered answer is filed under, for `model_timeout`'s reason:
*where* a run computed an answer decides nothing about what the model said.
`reasoning` *is* shared: Ollama's `think`, vLLM's
`chat_template_kwargs.enable_thinking`, LiteLLM's own `reasoning_effort`
(`"medium"` or `"none"`). So is `model_timeout`, the longest one question may
take: every row sets it on the client they build and neither asks
twice -- the OpenAI client is given `max_retries=0`, a client retrying behind the
number making it mean three times itself (ADR-0051). Left unset it was not a long
wait but no wait at all on Ollama, whose client disables httpx's own, so a server
that took the prompt and went quiet hung the run and `_too_slow_hint` was a
sentence nothing could reach. It is deliberately not in the fingerprint a
remembered answer is filed under: how long a run would have waited decides
nothing about what the model said. The listing keeps its own five seconds, a form
waiting on it (ADR-0032).

### CI and the five workflows beside it

`.github/workflows/ci.yml` runs two jobs for pushes to `main` and every pull
request: `coverage run -m pytest`, `coverage report` and then `pylint buy_agent`
and `mypy buy_agent` on Python 3.14, and `npm run test:coverage`, `npm run build`
`npm run lint` and `npm run format:check` in `ui/` on Node 22.23.3. The lint and
the type check are last in that job on purpose: a job stops at its first failing
step, and of the three the tests are what a change is about -- and the lint and
the formatting check are last in the other for that same reason, the tests and
the build being what a change is about there. Either
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

- **`audit.yml`** asks the question Renovate does not: not whether a pin has
  moved but whether what is pinned is known to be broken today (ADR-0062). Two
  halves on two events. `pip-audit` over every requirements file but
  `requirements-ap2.txt` -- resolved with their transitives and none of them
  installed -- and `npm audit --audit-level=high` off `ui/package-lock.json`,
  both at 02:47 UTC nightly and on `workflow_dispatch`, never on a pull request:
  an advisory published on a Tuesday is not news that a Tuesday push made true.
  The file left out is left out for the reason it is installed `--no-deps` --
  resolving it audits the SDK's own metadata pins rather than anything this
  project has -- and `requirements-ap2-deps.txt`, which is what paying really
  signs with, is audited. The thresholds are the other half of the decision:
  none on the Python side, where every line is a direct pin of something that is
  installed, and `high` on the npm side, where the lockfile is two megabytes of
  transitive build-time packages and a run that goes red nightly for a moderate
  advisory in a mutation tester's HTTP client is a run somebody turns off
  (ADR-0016's failure mode, one step further along). The other half runs on a
  pull request and is the only thing here that gates a merge:
  `actions/dependency-review-action` at that same `high`, reading what the branch
  *adds* to either list rather than the whole of what is pinned -- so it cannot go
  red for something published since the branch was cut.
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
  to be named in `also_copy`, or the run dies at collection. What the copy holds
  of the package is the code as *run*: every module of it opens with the
  tester's trampoline and carries every mutant of every line at once. So the two
  files made of rules read off the source rather than exercised --
  `tests/test_conventions.py` and `tests/test_architecture.py` -- read the
  package from the tree the copy was made of (`conftest.SOURCE_ROOT`), and
  everything else, which the copy carries unchanged, where it sits. That
  trampoline is a wrapper, so none of the declarations under a `def` are on the
  object the suite imports there: a default read off `__kwdefaults__` is `None`
  on the copy and stops the run at its baseline, a week after the pull request
  that passed. What a function was declared with is asked of a run that was told
  nothing, which `tests/test_conventions.py` holds every test in both suites to.
- **`mutation-ui.yml`** is that same question asked of the front end, an hour
  later at 06:23 UTC and in a workflow of its own (ADR-0061): a run is 1031
  mutants and a whole `ng test` each, over an hour and a half where mutmut's whole
  run is two minutes, so stacking it into the job above would make the package's
  report wait on this one. [Stryker](https://stryker-mutator.io/) runs the
  project's own test command per mutant -- `ui/stryker.config.mjs` is the
  `setup.cfg` of that half -- because the only faster runner would need a second
  Angular compiler beside the one `ci.yml` runs, and a score about a program
  compiled differently from the one that ships is not this project's score.
  `ui/tsconfig.mutation.json` is the one thing given way on: instrumented code
  widens the types a template reads, so the run turns the *template* checks off
  and holds `strict` and `noUncheckedIndexedAccess` exactly where the build has
  them. The report is `scripts/mutation_report.py` again -- one report, two
  readers, the file it is handed saying which tester wrote it -- and the floor
  under the front end is its own number, set where the thing stands.
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
  `/`. Linux only, like every schedule beside it.

Six files configure all of that, and `docs/testing.md` says why each is set the
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
  interpreter. What it measures is three trees and not one: `source` is the
  package, and `source_dirs` adds `benchmark/` and `scripts/`, which the suite
  tests and no number here was about -- the answer key the nightly is scored
  against and the two scripts that decide whether the Saturday run passes and
  what "updated" means (ADR-0064). They are in the second setting and not the
  first because `source` is what three other tools read to mean the package:
  mutating the answer key is a different question from mutating the code it
  scores, and `scripts/start.ps1` is not Python at all -- its gate is
  `tests/test_start_script.py`.
- `.pylintrc` is the one of the four that holds no number: the linter has to come
  out with no message at all. Every check this project has answered differently
  is turned off there with the answer, and every line the tool misreads carries a
  `# pylint: disable` and the sentence saying why (ADR-0048). Which checks *run*
  is the same decision the other way about: fifteen of pylint's twenty-five
  optional checkers are loaded there, each one stating a rule this package
  already holds in every module, and the ten left out carry their reason beside
  the ones that are in (ADR-0049). A checker is run over the package
  before it is added and what it finds is fixed rather than configured around,
  which is the difference between a check and a preference. The type checker
  beside it holds no number either and is configured in `setup.cfg` rather than a
  file of its own, beside mutmut's settings and for the same reason there is no
  `pyproject.toml` for either: the default checks rather than `strict`,
  `warn_unused_ignores` beside them -- `useless-suppression` one tool over, and
  what makes a `# type: ignore` written for a checker nothing ran fail rather than
  sit there -- and `ignore_missing_imports` for the five libraries neither tool can
  read, which is `.pylintrc`'s own `ignored-modules` and `extension-pkg-allow-list`
  said in mypy's vocabulary and held against it from both sides (ADR-0063).
- `ui/angular.json` holds the UI's floor on the test target, 98% of statements
  and lines: `coverageThresholds` is the builder's own, and a run under it exits
  with an error. Statements and lines only, on purpose: v8 attributes the
  branches inside a compiled Angular template to positions no test can reach, so
  a branch floor there would measure the instrumentation. Don't add one -- and
  the two it is silent about, `branches` and `functions`, are left out rather
  than set low, an omitted threshold being the only one that cannot drift.
- `ui/tsconfig.json` says how much `npm run build` checks, which is one of the
  two of the six that are not a number: `strict` is the family, shared by the app and
  the specs, `strictTemplates` beside it is that family over the bindings and is
  shared for the same reason -- a component's template is checked from the
  outside, so a spec rendering it is compiled against the same class -- and
  `noUncheckedIndexedAccess` in `ui/tsconfig.app.json` is the
  member `strict` leaves out and this app needs -- every lookup a component makes
  is by a key that came off a payload (`limits()[number.key]`,
  `receipts()[product.name]`), and typed as a hit a miss makes the `?? null`
  written for it read as one that can be deleted. It is on the app's own config
  and not the shared one because a test indexing past the end of a list it just
  built is a failing assertion on the next line rather than something a shopper
  is shown. Turned off,
  the nullable halves of a payload are assignable to everything and
  `agent.types.ts` stops being a promise the components are held to, while every
  mirror test above goes on passing. `tests/test_conventions.py` holds all three
  settings on for that reason: nothing else here would notice a build that
  quietly checks less.
- `ui/eslint.config.mjs` is `.pylintrc` for the front end and holds no number
  either (ADR-0066): the presets `typescript-eslint` and `angular-eslint`
  recommend, plus the rules this UI already holds everywhere -- `OnPush`,
  signals, `inject()`, no `$any` in a template -- each turned on beside the
  sentence saying so, and the rules that were run and left off each carrying its
  answer. A rule is run over `ui/src` before it is turned on and what it finds is
  fixed, and a line it misreads is suppressed in a comment directly above it with
  the reason; `reportUnusedDisableDirectives` fails one nothing needs any more.
  Specs are linted by the same rules, having needed no override. Its one rule
  that is not a preset's is the CSP below as `no-restricted-syntax`: an `on*`
  attribute or a `javascript:` URL in a template is refused at build time rather
  than silently by the browser.

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
call, so exporting it is not enough -- and runs `npm ci` in `ui/`, the command
`ci.yml` runs, since `npm install` under that Node's npm rewrites the lockfile and
left every session a diff in a file nobody touched. Those
images ship no Python dependencies at all, so it does the other half too, which
is `ci.yml`'s Python job and not a second opinion about it: a `.venv`,
`requirements-dev.txt`, and then the AP2 SDK in an install of its own for the
reasons `requirements-ap2.txt` gives -- optional to a *run* but not to the
suite, where without it the payment tests skip and the coverage floor cannot be
reached, so a session without it reports a red gate for a checkout CI would
pass. That venv goes on `$PATH` the same way the interpreter does. It reads both
versions out of `ci.yml` rather than writing them down again, by the rule
`scripts/start.ps1` follows: that file is the one pin the `Dockerfile`, the
start script and `docs/testing.md` already chase, and a fourth copy is a fourth
thing to bump. Only Node's pin is *fetched*, though: there is no portable Python
build to fetch, so the venv is built from the newest interpreter on `$PATH` --
not whichever one `python3` names, which on those images is a 3.11 that cannot
parse the suite, beside a 3.12 and a 3.13 -- a final release ahead of a release
candidate, never one of the venv's own, and the venv rebuilt when a session finds
it older than that; `tests/test_session_hook.py` runs that choice against
stand-ins. One under the pin is named at startup and used anyway, which is the
platform difference `ci.yml` matrixes for rather than a session that cannot run
the suite. It is a no-op outside a remote session
(`$CLAUDE_CODE_REMOTE`), a no-op once the interpreter is unpacked and the venv
carries a stamp newer than every requirements file, and every failure in it is a
warning rather than a stop -- a session that starts with the old Node is the
situation it was written for, not worse than it. Each half stands alone for that
reason: Node failing to download leaves the venv installed, and the line it
prints at the end says which of the two actually happened.

The pipeline is deliberately **not** a tool-calling agent loop. The LLM is used
for the two steps it is reliable at, and ordinary Python does everything else,
because these servers are typically run with small models that drive tool loops
badly.

```
request -> refine query (LLM) -> one search (once, or once per named
source), through whatever backend was named -> fetch + condense pages
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
that matters is the one being reported. Which is also why the budget is settled
against the set it *leaves*: the currency is a fact about the set (ADR-0043) and
this is the step that changes the set, so `Constraints.apply` re-reads the bound
until the two agree, rather than applying it in a currency nothing that survived
was ever held to.

| Module | Responsibility |
| --- | --- |
| `agent.py` | `BuyAgent.run()` -- orchestrates the pipeline, translates model-server errors |
| `chat.py` | The whole model-facing seam: a prompt, a chain, an answer read back as its schema (ADR-0038) |
| `extraction.py` | Both prompts, both chains, name cleaning, deduplication |
| `fetch.py` | Streams result pages up to a ceiling, keeps the lines quoting a figure or passing judgement, and tallies how the rest failed |
| `cache.py` | What a run can reuse from the last one: the page text it read (ADR-0040) and the answers it got (ADR-0044) -- and nothing else |
| `journal.py` | What past runs of this same search reported, and what moved since (ADR-0060) -- a record read by a person, which is why it is not a third cache kind, though it is written to disk by the same two functions |
| `verification.py` | Drops products, figures and quotes absent from the sources; links what is left |
| `constraints.py` | The bounds the shopper set, applied to the products before they are ranked |
| `bounds.py` | What the request itself asks for, read in Python and offered at both doors (ADR-0059) -- never applied |
| `ranking.py` | Scoring and sorting, and what each score is made of; no LLM involved |
| `models.py` | `ExtractedProduct` (LLM-facing) vs `Product` (domain), and which currency a set is counted in |
| `money.py` | Every currency table: how a spelling is placed, which ones a page is scanned for, how an amount is written and counted (ADR-0054) |
| `search.py` | Which backend a search is asked through, one row each -- and nothing else (ADR-0021, ADR-0057) |
| `sources.py` | What a trusted source is: domain, term, `site:` query, `covers` |
| `providers.py` | Everything that differs between Ollama, vLLM and a LiteLLM proxy, and nothing else |
| `screenshots.py` | The browser seam: a picture of a page, in a headless Chromium one thread owns -- and the only module that imports `playwright` (ADR-0065) |
| `payment.py` | What may be bought and for how much: a cart out of a grounded product, the spend limit, the receipt -- and one failure |
| `mandates.py` | The AP2 seam, and the only module that imports `ap2` (ADR-0046) |
| `rails.py` | Everything that differs between one counterparty and another, one row each |
| `config.py`, `logging_setup.py`, `__main__.py` | Config, the report, the CLI |
| `api.py` | Request options in, ranked products out -- the web-facing half worth testing |
| `server.py` | A stdlib HTTP server: the JSON API, the event stream, the built UI |

### Nineteen conventions

- **A model server is one row in one table, reached one way.**
  `providers.PROVIDERS` holds each server whole -- its defaults (`model`,
  `base_url`, `api_key`, from its own environment variables) beside how it is
  talked to (the client, how it declares a schema, the listing, the transport
  errors meaning "not there", the sentence that failure carries) plus
  `takes_num_ctx` and `takes_cpu_only`, and `more_room` -- the tail of the
  sentence an unreadable answer is explained by, being the one piece of that
  shared hint each server words its own way (ADR-0029, ADR-0068). The listing
  answers `InstalledModel`s rather than names, since what a server holds and what
  a run can use are the same question only on vLLM: Ollama's `installed` asks
  `ollama show` per tag and a LiteLLM proxy's reads each alias's `mode` off
  `/model/info`, so an embedding-only model is marked in the picker rather than
  offered (ADR-0032), and a failed probe leaves the model usable -- a proxy that
  will not say falling back to its `/v1/models`. vLLM's and LiteLLM's rows share
  the one OpenAI-compatible chat call and listing rather than each keeping a copy. The module imports nothing from
  `config`; the dependency runs the other way, and `AgentConfig.model_server` is
  the *only* place a provider name becomes behaviour -- so `agent.py` reads
  `config.model_server.chat_model(config)`, catches `.transport_errors` and
  raises with `.hint(config, exc)`, and `api.installed_models` asks
  `.installed(config)`. No module-level wrappers, and no `if provider == ...`
  anywhere above the table. A setting one server takes and the other does not
  gets a declaration on the row rather than a branch in the CLI, the API and the
  form; a hint sentence both servers would write goes in `_too_slow_hint` or
  `_unreachable_hint`, and a *failure* both would answer with one of those -- an
  unreadable answer, a timeout -- is decided once in `_hint`, above the rows,
  rather than on each of them (ADR-0028, ADR-0029). A hint that names a *model* --
  pull it, serve it -- needs the row's own client to have raised it, which is what
  `_answered_by` asks: read off the message alone, a bare 404 from whatever else is
  listening at that address says "not found" too, and an Ollama address pointed at a
  vLLM was answered with `ollama pull`, which succeeds against the real Ollama and
  changes nothing. The address is what is wrong, and `_unreachable_hint` says so.
  A row's client holds a connection
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
- **A search backend is one row in one table, and the default one needs nothing.**
  `search.BACKENDS` is that same table for the web: each row carries where it
  listens and its key (each from its own environment variable), whether it needs
  one, how it is asked, how its answer becomes `SearchResult`s, the transport
  errors meaning "not there" and the sentence one of those carries (ADR-0057).
  `AgentConfig.search_backend` is the only place a backend's name becomes
  behaviour; no `if backend == ...` above that module, and a fourth backend is a
  row there and a row nowhere else. Unlike the other two, a row is handed *itself*
  rather than a config -- a search takes a query and answers results, and the
  address and the key it needs are on the row -- which is what keeps this table
  the one that imports nothing from `config` in either direction, even deferred.
  The table lives in `search.py` rather than beside it because `config.py` has to
  read the rows and `SearchResult` is what they answer with; one module is also
  what keeps `buy_agent.search.DDGS` the one name the suite patches for a library.
  What is *shared* stays above the rows: the retry ADR-0053 decided is about a
  search and not about a backend, while what "nothing matched" looks like is each
  backend's own -- DuckDuckGo raises where the others answer an empty list. A
  missing key is the row's failure and not the config's: `AgentConfig` refuses a
  name nothing can search, exactly as it refuses a provider, but a key is a fact
  about the machine and not about the request, so the row raises a `SearchError`
  naming the variable and `Backend.configured` is what the picker marks the row
  with.
- **Never pay on an unverified number, and never on one this run cannot place.**
  The ranking rule (ADR-0006) turned around. `payment._check` refuses a product
  whose price grounding blanked, whose currency no page printed, whose run counts
  in a scale `money.placeable` cannot place, whose price is in a currency outside
  the run's own (ADR-0043), or which has no source page to
  name a merchant from -- every one of those already being the answer to "did a
  source say so". The third is the one a *vote* can reach: what a shopper names
  is a code out of the table (ADR-0056) and what the set votes for is whatever
  the pages spelled, so an ambiguous `¥` or a small model's "bucks" becomes the
  run's scale, scores `NEUTRAL` in the ranking as any unplaceable price does, and
  is refused here rather than sent as an amount counted in hundredths of a unit
  nobody has said are hundredths. It is the deliberate opposite of the shopper's bounds, which
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
  searching, which is also what keeps `search.py` the backend table and nothing
  else.
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
- **Every listing a product was priced at is kept, and the cart is for one of
  them** (ADR-0058). `models.Offer` is a price, the currency it was written in,
  the shop quoting it and the page it was on; `deduplicate` seeds one per listing
  -- the last point at which one `Product` is still one listing, and after
  `ground`, so no offer carries a figure the sources do not back -- and `_combine`
  keeps both listings' offers whole through `_merge_offers`. `offers` is therefore
  deliberately *not* a row in `_MERGEABLE_FIELDS`, which is the table of fields a
  *weaker* listing fills a gap with: two shops are no conflict, exactly as two
  reviewers are not, which is `_merge_opinions`' argument one field over. The
  headline price does not move and nothing downstream reads the offers --
  `rank_products`, `Constraints` and `dominant_currency` read `Product.price`, or
  ADR-0043 would have two answers to "what is this priced at". What they do decide
  is *who is paid*: `payment.offer_for` matches the headline price and its currency
  (the pair, ADR-0022) and the cart's merchant and page come off that listing, so a
  merge that filled a blank seller from another listing can no longer name the shop
  selling at a different figure. `Product.offers_label` writes the spread -- over
  the offers on the run's own scale, since two currencies have nothing between them
  -- and both front ends read that sentence rather than formatting an amount.

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
  counted in -- the shopper's `currency` where they named one, and otherwise the
  vote (ADR-0056) -- and `models.comparable_price` answers `None` for anything outside
  it, which scores neutral, sinks in a price sort and passes every bound
  (ADR-0043). A bare price is taken as the set's own. Both places that hold one
  price against another -- `rank_products` and `Constraints` -- go through that
  one function; a third would have to. Which currency a listing named is settled
  once, in `money.code_for`: the schema asks for a code and a small model
  hands back the sign the page printed, so `$` and `USD` are folded together
  there rather than counted as two currencies half a set is then unplaceable in.
  That table is `money.py`'s whole reason for existing (ADR-0054), and
  `money.placeable` is its second question: `code_for` reads what a page wrote and
  hands an unknown spelling back as written, while a *scale* nothing can place is
  a report with no price criterion at all, so what a shopper may name is a code
  out of the table and nothing else (ADR-0056). Which
  spellings are folded and which spellings make `fetch` keep a price *line* are
  one rule -- a currency `fetch` cannot see is every price on that shop dropped
  before the model sees it, which is what `--region pl-pl` was until `zł` was
  added to one table and not the other -- so `fetch` scans with `money.SIGNS`,
  `money.WORDS` and `money.SCANNED_CODES`, all three *derived* from the one table
  rather than written beside it, and a currency is added there and nowhere else.
  Each of the three is read on *either side* of the figure, since "€129" and
  "129,99 €" are one price written the way English writes it and the way most of
  the continent does: read one way round only, the sign form kept every English
  shop's prices and dropped every German, French and Spanish one before the model
  saw them, which is `zł`'s failure again a sweep further along.
  The last two are one alternation split at the case it is read in: a word
  spelling is folded, a page writing "129 Dollars" as readily as "129 dollars",
  and an ISO code is read as written, a page writing "129 TRY" and never
  "129 try". Folded in with the words, `TRY` is the Turkish lira and the English
  verb alike, so "Try 3 of these" was a price line charged against `page_chars`;
  the collision is a property of the table rather than of that one row, every
  code being three letters that may spell something. Two exemptions are named,
  each with its sentence and each held from both sides by
  `tests/test_conventions.py`: `UNPLACEABLE` is read off a page and never placed
  (`¥` is the yen's and the yuan's alike, and an ambiguous one left as written is
  a price the run cannot place, which is what the rule above already has an
  answer for), and `UNSCANNED` is placed and never read off a page ("pounds" is a
  unit of mass, and scanning for it would keep a line per weight). The cost
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
- **A bound written in the request is offered and never applied** (ADR-0059).
  `bounds.notice` reads "under $200", "at least 4 stars", "over 500 reviews" out of
  the request in ordinary Python -- the work `clean_products` does, and never the
  model's, because a model asked to read "200 hours of battery" as a budget drops
  every product in the run and the report then says only that nothing was found.
  Each shape is anchored on the unit that makes it a bound: a rating beside its
  scale and a count beside somebody counted, which is `verification`'s rule for a
  figure off a page, and a budget beside a currency mark off `money`'s own tables,
  or beside nothing that starts another word, or beside one of the joins a sentence
  carries on in. Both doors *offer* it: `__main__` logs one line naming the flag,
  being one of the two modules allowed to name one, and `api.bounds_payload`
  answers `GET /api/bounds` for the form to pre-fill the box with -- once, only
  where the box is empty, under Python's own sentence and as a hint rather than a
  mark, nothing here being wrong (ADR-0033). `bounds.py` knows nothing of
  `config.LIMITS`, so a figure the setting would refuse is dropped at the door
  rather than pre-filled into a box the form would then mark. Nothing may ever
  apply one: the moment one is applied unseen, the silent empty report is back.

- **The report says what it is ordered by**, in its heading, for every criterion
  and not only the surprising ones. `ranking.ORDERINGS` holds the phrase per
  `SortBy` -- "cheapest first", not "by price", the direction being the half a
  field name cannot say -- and `BuyAgent.run` hands `log_top_products` the
  `sort_by` it ranked with, so the heading cannot drift from the order beneath
  it. Sorted by rating the block reads 0.68, 0.98, 0.83 down the left edge, which
  is a ranking that looks broken until the heading explains it; a `> top.txt` had
  nothing at all. Named for `score` too, since a report is read by whoever was
  handed it and not only by whoever typed the command. The browser's control
  beside the results is its heading, and it said "price" -- so `defaults_payload`
  sends the same phrases as `sort_labels`, both of the form's ordering pickers
  list those rather than the names, and `--sort-by`'s help spells each one out
  where `choices` shows only the names. A fourth criterion needs a phrase there,
  which `tests/test_conventions.py` holds against `SortBy` and against both doors.
- **The report is output; the progress is narration.** `logging_setup` splits
  them by handler rather than by logger: `log_top_products` marks its records
  and they go to stdout, everything else to the stderr handler `basicConfig`
  installed, and both still reach every other handler -- which keeps the
  browser's progress panel showing one stream and a `caplog` seeing the whole
  run. Only the *console* handler is told to skip the report; a handler writing
  anywhere else is nobody's stream to take lines out of. The two console
  handlers write the same records differently, which is the other half of the
  split: the narration keeps `_FORMAT`'s clock, level and step, since the gap
  between two lines is what tells a four-minute extraction from a four-second
  one, and the report is written plainly (`_REPORT_FORMAT`), since every line of
  it shares one timestamp, one level and one logger -- thirty columns
  distinguishing nothing, wrapping the quotes, and turning the `> top.txt`
  `--help` offers into a log of the answer rather than the answer. Formatting
  and not filtering, so the relay behind the browser's panel still builds its
  own line off `record.created` and `record.name`. `configure_logging`
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
  **The five that drop a whole product also hand it over as data** (ADR-0055):
  `record`, a keyword defaulting to `models.nothing_recorded` and handed down the
  way `checkpoint` and `wait` are, called with a `models.Removal` -- the name it
  went under, the step that took it, and the reason as a finished sentence.
  `BuyAgent.run` passes it down, `api.run_search` collects, and the run payload
  carries them as `dropped`, which is what lets the page answer "why is the one I
  had in mind not in there?" once the progress panel has been replaced by the
  results. The three that blank a field record nothing: the product is still in
  the report and its own card says so. The sentence is written beside the log line
  that already says the same thing -- `Constraints.apply` builds its own off the
  same `describe`, currency and all -- since two wordings for one judgement is how
  the panel and the progress come to disagree. The default stays "nobody is
  recording" and changes nothing: every step is called without one throughout both
  suites, and `tests/test_logging_contract.py` partitions the eight by which side
  of this they are on.
- **The web is asked twice and the model once, and the clock is handed in.** A
  step of the pipeline holds no clock: `fetch.py` and `search.py` take a `wait`
  and `BuyAgent` passes `time.sleep`, the way it passes `checkpoint` down
  (ADR-0034, ADR-0053). `None` -- every other caller, and every test -- asks once.
  What is worth asking twice is narrow and on each side of it: a page answering
  429 or 503, after `Retry-After` seconds where it named them, capped and floored
  and never read as a date, since that would mean subtracting a clock this module
  does not hold; and any search failure that is not "matched nothing", `ddgs`
  raising only when every engine it asked failed. A 403, a 404, a timeout and a
  search that worked are answers, and asking again buys two of the same. The model
  is the other way about: `model_timeout` bounds one question and nothing retries
  it (ADR-0051), a repeated 4.3k-token prompt being the most expensive thing here.
  Every stand-in for `search_web` and `enrich` therefore takes the keyword, as
  every stand-in for `BuyAgent` takes `checkpoint`.
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
`RailUnreachableError` subclasses it, so the API can answer 502 for the failures
that are nothing to do with the request while the CLI still catches both by
catching the parent. The line is drawn at whose failure it is and not at the
transport: a counterparty that could not be reached is one of those, and so is
one that answered with nothing an answer can be read out of -- HTML where JSON
was asked for, an array rather than an object, a checkout with no signed token
in it -- since 400 would mark a form with nothing wrong with it. A counterparty
that understood the request and declined it is the other thing, and stays the
plain `PaymentError` that reads as 400. It is mapped by `api.PAY_STATUS` and
caught in `__main__._bought`, each read by a convention test of its own. Adding
it to `_STATUS` would make `run` promise something it does not raise.

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
paragraph. vLLM's and LiteLLM's are `openai.OpenAIError`, the root of that
client's hierarchy, plus the two above for the listing.

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

### Options, and the nine that are special

**A setting is one row in one table too**, and the same table for both doors:
`api.OPTIONS` says which request key each carries, which `AgentConfig` field it
fills in and how its text is read, and everything that has to agree about that
reads it -- `parse_options` for the value, `defaults_payload` for the form's
seed, `_BOUNDED` and `limits_payload` for the range, and `main`, whose flags land
under those same keys, for which field to pass each on as. So a new option is a
row there, a flag with its help in `__main__.build_parser`, and nothing else on
either door. What is left on both is the one thing no row can say: `sources` is a
list, which `_read` would render as a Python repr; the CLI's `--num-ctx` sentinel
is not a value to pass on; and `search_results = max(results, top)` is a setting
neither door is given, searching for fewer pages than the report intends to show
being a capped report.

Two of them being two, **a sentence written below either door names the setting
and never the flag.** `providers.hint`, `rails.hint` and
`AgentConfig.__post_init__` are read twice: once on a terminal and once in the
page, where the same words land in a banner or under a labelled box. "Give it
more room with a larger `--num-ctx`" *is* the form's Context window field, named
as something nobody looking at the form can type, and "give `--merchant-url`"
was printed under the box labelled Payment endpoint -- the remedy sitting where
the sentence was pointing away from. So the shared wording is the setting's
name, each door shows the setting under that name, and `--help` uses the same
nouns, which is what leaves the CLI reader a word to look up. Flags of *other*
programs stay as they are (`vllm serve --max-model-len`, `pip --no-deps`): those
are the same thing to type at either door. `tests/test_conventions.py` reads the
flags off both parsers and holds every module below them to it, docstrings
excepted -- there the flag is the right name for the flag.

- **Numbers** belong in `config.LIMITS` too, where the range is declared once
  and read by both doors: written on each of them, the CLI comes to accept what
  the API refuses. On the CLI the check is a `type` function, so an out-of-range
  number is a usage error rather than a minute wasted;
  `tests/test_conventions.py` asserts the two doors refuse the same numbers.
- **`region`** is the same rule for a shape rather than a range: `config.REGION`
  is a country and then a language (`us-en`, `pl-pl`, three-letter `hk-tzh`),
  `config.parse_region` is the only place it is checked, and both doors go
  through it -- each as its own `_checked`, wrapping that one function in the
  refusal its door answers with -- with `__post_init__` behind them for a Python
  caller. A shape and not the list of
  codes that exist, because `ddgs` asks several engines that each read the
  halves their own way (ADR-0031). The shape is not the whole story -- `en-us`
  is the right shape the wrong way round -- so `BuyAgent._region_note` names the
  region in the "Search returned nothing" warning unless it is `DEFAULT_REGION`,
  which is known to work. It is one of two notes that warning carries, composed
  by `_empty_search_note` because the punctuation between them depends on which
  are in play: `_sources_note` is the other, and the stronger suspect. A named
  source is enforced by construction with deliberately no falling back to the
  wider web (ADR-0027), so one that does not cover the request is an empty report
  and nothing else -- and the "Ignored N result(s) from outside ..." lines that
  say so scroll past a step earlier, at INFO, above a warning that used to name
  the query and the region and never them.
- **`currency`** is `region`'s rule again, for a code rather than a shape:
  `config.parse_currency` is the only place it is checked and both doors go
  through it -- each through its own `_checked`, as the region does, so what a door
  adds to that one function is written once per door rather than once per setting --
  with `__post_init__` behind them. What it checks is `money.placeable`, so a spelling
  is folded the way a page's is (`$`, `usd` and `USD` are one answer) and the
  refusal names the codes that would have worked -- and so does `--currency`'s own
  help, off the same table. It is the one flag whose value comes from a closed set
  and cannot carry argparse's `choices`, the folding meaning two spellings in three
  are not choices; left to the refusal, the only way to read the set was to name a
  code that does not work and be told, while the form has had a picker over it all
  along. A *closed* set here where the
  region is a shape, because this one is a choice among the codes `money` already
  holds rather than a hint passed to somebody else's engine. Blank is the default
  and means the vote ADR-0043 settled the scale by (ADR-0056), so the form's
  picker offers the blank as a value rather than leaving the field empty. Four
  places settle a run's scale and each takes the override -- `rank_products`,
  `Constraints`, `payment.cart_for` and `api.results_payload` -- and the last two
  are why a *finished* run carries its currency: a re-sort and a payment are both
  handed the products by the browser (ADR-0035), so letting the set vote again
  there would answer a different ordering, and a different cart, for one run.
- **`backend`** is `provider`'s rule, one table over: checked against
  `search.BACKENDS` at both doors and offered in *four* places, the fourth being
  the `backend_options()` rows the form's picker is built from. Unlike
  `provider` it changes what no other option means -- a backend's address and its
  key are read on its own row and have no flag and no form field, the way
  `$VLLM_API_KEY` has neither.
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
- **`journal`** is an ordinary boolean with one thing worth saying at both doors:
  what it writes down is a shopping history, so `--journal`'s help names the
  directory and says deleting it throws the whole of it away, and the form's box
  says the same in a sentence. `--compare` beside it is not an `AgentConfig` field
  at all -- it decides what the *report* prints, the way `--json` does -- so it
  lives on the CLI alone, while the browser is handed `changes` on every run and
  draws the panel when there is one (ADR-0060).
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
  it cannot judge. The other three mean nothing at all without `pay`, and the two
  doors say so differently because only one of them can: the form draws none of
  them until the box is ticked, while the CLI has no panel to hide and names
  whichever were given in one line (`_idle_paying_flags`), the way `--num-ctx` is
  called out on a server that fixes its window. A rail is measured against
  `DEFAULT_RAIL` rather than against a sentinel, `$BUY_AGENT_RAIL` being how a
  machine is pointed at one counterparty for good -- and a sentinel there would
  cost the `_checked` refusal that makes a misspelt one a usage error.
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
| `GET /api/sources` | Whether a Trusted sources field names sites -- one of the two endpoints that run nothing |
| `GET /api/bounds` | What the request itself asks for, offered for the form to fill in and never applied |
| `GET /api/screenshot` | A JPEG of the page a card links to, from a server with a camera -- the one answer that is not JSON |
| `POST /api/search` | One run, as JSON |
| `POST /api/rank` | A finished run's products in another order -- runs no pipeline |
| `POST /api/pay` | One of those products bought, given the approval the page witnessed -- runs no pipeline either |
| `GET /api/search/stream` | One run, as SSE: `log` lines, then `result` or `failure` |

- **A run is streamed, not requested.** `GET /api/search/stream` runs the agent
  in a worker thread and relays its log lines as Server-Sent Events while it
  works. `_LogRelay` routes records by the context the run is being watched
  through, which keeps two concurrent runs from seeing each other's progress --
  a thread begins in a context of its own, so the two worker threads are already
  apart, and a step that fans out into threads of *its* own still reaches the
  right stream. `fetch.enrich` is the one that does: it reads the result pages in
  a pool, and `_as_the_caller` is what starts each worker in the context the run
  is in. Routed by thread id instead, the one INFO line that step writes -- how
  long a rate-limited page is asking the shopper to wait -- reached the terminal
  and never the page. Extraction is
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
  through. `pay_currency`, `pay_label` and `pay_merchant` beside it are what that
  purchase would be *for* and who it would go *to*, which is frequently not the
  product's own figures: a page that printed a bare "329.00" is priced in the
  run's currency (ADR-0043), so `currency` is null while the cart is in USD, and
  a page that printed no seller is paid at the site it is on, so `seller` is null
  while the cart names `audiosite.example`. All three come out of the same check
  `cannot_pay` does, so all three are null together and `payment.merchant_for` is
  asked by the payload and by `cart_for` alike. `product_payload` sends
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
  failed. It reports no removals of its own either -- it ran no pipeline, so it
  took nothing out, and the page carries the run's own `dropped` across rather
  than taking the empty one (ADR-0055). Answering with the run's list here would
  be this endpoint speaking for a run it never saw.
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
  first.** `BuyAgentHandler._refused()` runs at the top of `do_GET`, `do_POST`
  and `do_HEAD` -- a new method added without it is unguarded and nothing fails
  -- and refuses `Sec-Fetch-Site: cross-site`, an `Origin` that is neither
  loopback nor equal to the request's own `Host`, and a `Host` outside
  `allowed_hosts` (ADR-0018). The first stops a page on another site starting a
  run whose answer it could never read; the last stops DNS rebinding, which is
  how that page would get to read one. `--allowed-host` names a further host; a
  bind to a public interface turns the `Host` check off and says so at startup.
  Both of those read an address somebody typed, which is not the spelling a browser
  writes, so they go through `_bound_host` and not `_hostname`: an address bar
  brackets an IPv6 literal and a command line does not, and split at the first colon
  `::1` named nothing -- so the one loopback address `_LOOPBACK_HOSTS` spells out was
  the one bind classed as public, and an `--allowed-host` naming one allowed `""`,
  which is what a request sending no `Host` at all arrives as. Whatever names nothing
  is dropped rather than allowed. The family is that same address read once more:
  `_family_for` binds an IPv6 one on `AF_INET6`, `ThreadingHTTPServer` being `AF_INET`
  and nothing else and every IPv6 bind having failed outright.
- **Only a server bound to this machine takes pictures of pages** (ADR-0065). A
  browser that draws anything will draw the router's page as readily as a shop's,
  and the picture is that page handed back -- so `server.camera_for` gives a camera
  to a loopback bind with Playwright installed and to nothing else, saying at
  startup which of the two it was missing. `defaults_payload` carries whether it
  has one as `screenshots`, which is not a setting: no door can ask for a camera.
  `GET /api/screenshot` is asked by an `<img>`, never by the run, so nothing in
  the pipeline, the payload, `--json` or the journal knows a picture exists, and
  a re-sort's body -- the products, under 64 KB -- carries none. Only `http` and
  `https` are photographed; a page that will not be is a 502, the request having
  been fine. `screenshots.Camera` is one browser on one thread, because
  Playwright's synchronous objects are the thread's that made them: every request
  queues and waits, the browser launches on the first picture and closes once
  nobody has asked for a minute.
- **Every request is answered, including the ones that go wrong.** `do_GET` and
  `do_POST` each end in a catch-all that logs and sends a 500, because an
  exception out of a handler escapes to socketserver, which closes the socket
  unanswered -- and a browser reads that as the server having gone, which is the
  one thing it did not do. `GET /api/config` is the reminder: it builds an
  `AgentConfig`, so `$BUY_AGENT_PROVIDER=olama` made every page load a dropped
  connection under a banner blaming the agent server. `server.main` refuses that
  name before it binds a port -- and `$BUY_AGENT_RAIL` and `$BUY_AGENT_BACKEND`
  beside it, a config resolving all three -- for the same reason `__main__` makes
  any of them a usage error:
  it is not worth a server that starts and then 500s at its own form. The stream
  sits outside the guard and answers its own failures with a `failure` event,
  having spent the status line already. `--port` is refused at that same door and
  for that same reason, by the rule `__main__._bounded` holds for every number the
  agent takes: out of `_PORTS`, `socket.bind` raises an `OverflowError`, which goes
  straight past the `OSError` `main` reports a refused bind with -- so a mistyped
  port was the one thing all of the above exists to prevent, a traceback. And the
  sentence that refusal carries is a remedy for one failure and is printed for one:
  `_clashing_provider` answers a port already taken, reading `EADDRINUSE` off the
  error rather than the port off the command line, since a `--host` that names
  nothing fails the same bind with the same port in the message and was told to
  "serve the UI somewhere else" -- the address being what is wrong, which is the
  reading `providers._answered_by` already makes of a hint naming a model.

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
`<script>` or a request to another origin has the same shape of symptom -- and
the first of those is the one the linter sees: `ui/eslint.config.mjs` refuses an
`on*` attribute and a `javascript:` URL in any template (ADR-0066). An inline
`<script>` is dropped by Angular's compiler from a component template and by its
parser before a rule could look, so in `src/index.html` it stays this paragraph.

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
- **What a run took out is listed under what it found, in Python's words**
  (ADR-0055). The panel groups `dropped` and counts it and composes no sentence of
  its own, which is ADR-0012 on this payload. It is drawn under the "Nothing came
  back" banner too, that run being the one the question is loudest on -- and it
  survives a re-sort, which answers an empty list because it removed nothing.
- **What moved since the last run is listed the same way** (ADR-0060). `changes`
  and `compared_with` travel with `dropped` and are carried across a re-sort for
  the same reason: a re-sort ran no pipeline, so it compared nothing and answers
  empty rather than speaking for a run it never saw. The panel counts and groups;
  every sentence in it is the journal's own, and `movement` is a word to colour by
  and never one to compose from.
- **What each page priced a product at is under the price it is a spread of**
  (ADR-0058). `offers_label` is Python's sentence and so is each listing's
  `price_label` -- the card formats no amount, exactly as it formats no unknown
  one.
- **A card's picture of its page is asked for and never decided on** (ADR-0065).
  The card draws a frame where the server said `screenshots` and the product links
  somewhere, and `agent.screenshotUrl` is the address the `<img>` asks; the
  picture links where the title does, is `loading="lazy"` so the cards under
  "more the agent found" ask for nothing until they are opened, and reserves its
  640 x 400 before it lands. A picture that does not come drops the frame -- by
  address, so a card handed a product on another page asks again -- rather than
  drawing a broken image beside a title that still works.
- **Buying takes two clicks, and the second restates the cart.** Title, the cart's
  `pay_label`, its `pay_merchant`, rail, and whether anybody is charged -- the cart
  the mandates will carry, never the product's own figures (ADR-0043), which is why
  the merchant is read off `pay_merchant` and not off `seller`: most pages print no
  seller, and a confirmation reading that one named nobody at all for most products.
  The card emits three of those fields -- title, price, currency -- for the server
  to check against the cart it builds itself, and shows Python's `cannot_pay` where
  there is no button to offer.
- **A receipt is keyed by product name, in `App.receipts` and in both loops.** A
  re-sort ranks the same products again from 1 (ADR-0035), so tracking by index
  moves a purchase onto whatever lands at that rank next.
- **`pay` is the one setting `localStorage` does not remember.** The rest are
  standing answers about this machine; that one would arm a run nobody asked for.
  Every storage call is wrapped, so a browser refusing storage still has a form.
- **A bound the request asked for in words fills its box once, and marks
  nothing** (ADR-0059). `noticedNow` drops an answer about a request the box no
  longer holds, the way the sources check does; `offered` is what keeps a box the
  shopper then cleared from being filled in again, since re-offering a figure
  somebody deleted is enforcing it slowly. The note under the box is Python's and
  is a hint rather than a problem: nothing is wrong, so `canSubmit` is untouched.
- **The form refuses on the server's rules and invents none of its own**
  (ADR-0033). `problems()` gates `canSubmit` off the ranges that came down with
  the defaults and off what `GET /api/sources` last said. A field whose `off()` is
  true is neither held to a range nor sent, a mark on a box nobody can type into
  being one nobody can act on. `notes()` adds the server's `rejected` field, which
  does not gate the button and is shown only while the box still holds what
  `submitted` recorded -- and `moved` says when it stops, so `App` drops the
  banner repeating that same sentence rather than leaving the page refusing a
  value the form has already stopped marking. `options()` is the single place that
  payload is built. A mark is two things and one answer: `aria-invalid` on the
  box and the sentence it points at with `aria-describedby`, both read off
  `problemId`, since `aria-invalid` alone says something is wrong and never what
  and a live `role="alert"` says it once and is then a paragraph beside a box.
  One answer for both ends, so a box this run does not take loses the pointer
  with the mark rather than naming a sentence nothing is drawing -- which is the
  rule `duplicate-id-aria` and `aria-valid-attr-value` hold from the other side.
  `numberTyped` reads `validity.badInput`, without which a box full of text is
  sent as the `null` a cleared box means (ADR-0012).
- **A number box is declared once, in `numberFields`**, under the key that also
  carries its range and names its refusal; `placeholders()` reads its fallback off
  `defaults_payload` by that key. `tests/test_conventions.py` holds those keys
  against `limits_payload`. That one key is also what the box is *remembered* by:
  `settings` spreads `numberSettings(this.numberFields)` rather than repeating the
  ten of them, the seed being the default the server answers under that same key
  and the storage name its camel case, which is what the signal beside it is
  called and what a browser holding a saved blob already wrote. So only what the
  key cannot say is written on the row -- `remembersBlank`, since `num_ctx`
  defaults to a number and still has to remember a cleared box while
  `temperature` defaults to 0 and must not. The row also says *where* it is
  drawn: `payingFields` and `settingFields` partition that one table, so the
  spend limit sits in the paying block with the rail and its address -- the three
  settings the form draws none of until the box is ticked -- rather than four
  rows above the tick under a sentence naming a control off the reader's screen.
- **The model field marks what it cannot offer and never hides it** -- "not
  served" for a name the server does not have, "embedding only" for a pull that
  cannot answer a prompt (ADR-0032). `ModelOption.note` is filled from Python's
  `completion`: the browser writes the suffix, not the judgement. `refresh`
  carries a `ModelSource`, provider and address both, a vLLM asked Ollama's
  question answering 404, and `takes_num_ctx` and `takes_cpu_only` off the
  provider's row are what disable the context field and the CPU-only box.
- **A mark opens the panel it is in.** The form opens Settings itself the first
  time `flagged()` is non-zero, on the marks changing and not on the panel's
  state, so shutting it again stays the reader's to do.
- **Every one of those rules is a claim about what somebody can perceive, so
  each component is held to an accessibility check.** A refusal on the box it is
  about (ADR-0033), a bound offered and never marked (ADR-0059), a `movement`
  that is a word to colour by and never one to compose from: a spec asserting a
  CSS class passes all three for a mark rendered as a colour alone, a control
  with no accessible name and an error that reaches no assistive technology. So
  `ui/src/app/a11y.ts` runs `axe-core` over each of the four in the jsdom
  `TestBed`, on rules turned on one at a time -- each carrying the sentence
  saying which promise it holds, and what is left out carrying its reason beside
  them, which is `.pylintrc`'s argument (ADR-0049) one language over. A blanket
  run passes vacuously on what jsdom cannot answer and fails on rules nobody has
  decided about. A rule that ran and could not decide counts as one that did not
  pass, which is what caught an `aria-label` on a `<div>`, an element whose role
  forbids it to carry a name. What the check cannot state here is asserted where
  it is made: a colour is never the only carrier, so a level the progress panel
  colours is a level it names, and the panel's lines land in a live region --
  contrast and target size want pixels and layout, and stay a browser's job the
  way the CSP and the critical-CSS inliner do. `ui/README.md` argues the whole
  of it beside the components it is about.

`create_server(agent_factory=...)` is the seam the server tests inject a stub
agent through, the way `BuyAgent(config, llm=...)` is for the pipeline;
`allowed_hosts=` is the second, `None` meaning "answer any `Host`", which is what
a public bind gets, and `camera=` the third, `None` -- the default -- being a server
that takes no pictures. Angular components are tested in jsdom with `TestBed`,
`AgentService` against a fake `EventSource`.

### demo/

`demo/README.md` says what the three recordings show, what is real in them and
how to take them again, and how to run the merchant beside them. Six things
about the directory hold here.

- `demo/server.py` starts the *real* `buy_agent.server` with only `search_web`,
  `enrich` and the chat model replaced, so everything between the search and the
  ranking is the real pipeline and a recording needs neither Ollama nor the
  network. The scripts beside it make the fake model wrong in the six ways a
  small model is wrong, which is what puts `clean_products`, `ground`,
  `verify_opinions`, `attribute_sources` and `deduplicate` each catching one in
  the progress panel.
- The picture is MPEG-2 in a program stream, stating its own rate and buffer.
  MPEG-1 is what the first two recordings used and it is the wrong format for
  1280x720: the encoder declares a video buffer smaller than its own keyframes
  and every pack violates the system target decoder, which a player showing
  video alone ignores and one scheduling an audio track against it cannot. So a
  take with sound in it opened nowhere until the codec moved. `VIDEO` in
  `record.mjs` is that decision whole, and it is the one thing in the directory
  a new recording may not quietly go back on.
- The sound is synthesised, because there is none to record: Chromium captures
  no audio, so `record.mjs` writes down a cue per thing that happened and
  `sound.py` turns those into the WAV that is muxed in. Which means the two
  halves are decided in different places and have to agree about one thing --
  `record.mjs` says which log lines *took something away* and so earn a note of
  their own, matching the verb rather than the count, and `sound.py` says what
  that note is. Nothing is sampled or licensed: every voice there is a few sine
  waves under an envelope, so a recording taken again comes out the same.
- A third demo is a module offering the same five names `books.py` and
  `laptops.py` do, plus a row in `server.SCRIPTS`. `docs/ui.png` is taken off
  this server rather than off `buy_agent.server`, the model dropdown and the
  header pill being answers from an Ollama, and it is clipped to the form card,
  so a field added to the settings makes it taller rather than falling off the
  bottom. `docs/results.png` is the same script given `--script`, which runs
  that script's request and clips to the results section instead.
- `demo/merchant.py` is the counterparty the `http` rail was driven against:
  it answers `{url}/checkout` and `{url}/payment`, signs the checkout itself and
  accepts a payment only when both mandates bind to that checkout, at its
  amount, currency and payee, once. It verifies with the AP2 SDK's own verifier
  and never with `buy_agent.mandates` -- the agent's code checking the agent's
  work being a mirror -- and a merchant that accepted anything would prove
  nothing, so `--once` presents a tampered and a replayed authorisation too and
  fails if either is taken. It names no real merchant (ADR-0046) and charges
  nobody.
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
`create_server(agent_factory=...)` is the same seam for the server,
`allowed_hosts=` is the second one, and `camera=` the third, where
`tests/conftest.py`'s `Photographer` stands in for the browser the way `FakeLLM`
stands in for the model. No test starts a browser, whether or not Playwright is
installed: `screenshots.Camera` takes `launch=`, and the tests of `Chromium`
install a `playwright` of their own in `sys.modules` (ADR-0065).

**Where the network is patched.** Four places: `buy_agent.agent.search_web` and
`buy_agent.agent.enrich` for pipeline tests, and -- for the tables' own tests --
`buy_agent.search.DDGS` beside `buy_agent.search.httpx.get`, which are the two
ways a backend row reaches out, and `buy_agent.fetch.httpx.Client`. `search_web` is
patched on `agent` and only there, which is why the fan-out over named sources
lives in `agent.py` rather than beside the rest of `sources.py`: a second call
site would be a second thing to patch, and a test that forgot it would reach the
real DuckDuckGo silently. The HTTP rail's transport is patched at
`rails.httpx.post`, where that module imported it.

**Where the model clients are patched.** Both are patched where
`buy_agent.providers` imported them -- `providers.Client` for Ollama's chat and
for the `show` a listing asks per tag, `providers.openai.OpenAI` for vLLM's and
LiteLLM's chat. Every listing is patched at `providers.httpx.get`, Ollama's
`/api/tags` beside vLLM's `/v1/models` and a LiteLLM proxy's `/model/info`. The tags are read off the endpoint rather than
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
longer means something is reaching out. `tests/test_conventions.py` holds that
file to being the only one that sleeps, since a tenth of a second added anywhere
else is paid by everybody and noticed by nobody.

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
about. `tests/test_session_hook.py` has a marker of its own, and it is the
platform rather than a prerequisite: it runs the hook's choice of interpreter
under bash with `#!/bin/sh` stand-ins, so it skips on Windows, where the hook
never runs. `docs/testing.md` is the one place all three counts are written down,
so a new test file is one edit there.

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
- every currency `fetch` will keep a price line for is one `money.code_for` can
  place, and every currency it can place is one `fetch` keeps a line for -- asked
  through `quotes_a_figure`, since the split into signs, words and codes is a
  derivation and `US$` is reached by no one of the three on its own -- with both
  exemptions checked from the other side too, so neither can outlive its reason,
  and every sign held to being read on either side of the figure, one order alone
  passing that rule while dropping half of Europe's prices (ADR-0043, ADR-0054);
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
  `breakdown` a product carries, the `Opinion`s it quotes, the `Offer`s each page
  priced it at (ADR-0058), the `Removal`s the run reports, the `Change`s it says
  moved since the last one and what `bounds_payload` read out of the request
  (ADR-0059, ADR-0060), and `run_search` field for field -- and every bound a
  request can be read as asking for is one the form draws a box for;
- the form holds a number to a range for every range `limits_payload` ships and
  writes no `min` or `max` of its own into its template, and every key
  `parse_options` reads is one `SearchOptions` sends -- a key it reads and the
  form never sends is a refusal marking a box that is not there (ADR-0033);
- no module below the two modules handed an `argv` puts one of their flags in a
  sentence, docstrings excepted and other programs' flags allowed: a hint below
  the doors is read at both, and the browser has no command line to type
  `--num-ctx` into;
- every flag of either parser that takes a value and has a default names it in
  its help, `--help` being the CLI's only documentation and a default left out a
  fact with nowhere else to be read; and every `SortBy` has an `ORDERINGS` phrase
  naming a direction, which `--sort-by`'s help and the form's pickers both say;
- the `Dockerfile` pins the versions CI tests against, copies the built UI where
  the server looks, exposes the port it binds and installs the runtime
  dependencies only, and `.dockerignore` keeps out everything `.gitignore` does
  while keeping in everything those `COPY` lines ask for, and no directory at the
  top of the tree is left for neither to say anything about;
- every job in `ci.yml` names both a Windows and a Linux runner between them and
  holds a merge up for neither, over the events the workflow actually runs on
  (ADR-0037), and sets up exactly one Python and one Node for the three files
  that pin themselves to those; every workflow sets up that same Python and
  builds with that same Node, and keys its pip cache on every requirements file
  it hands pip -- the `-r` lines inside those included, since a key naming fewer
  files than the job installs is a cache that quietly stops covering a step; the
  workflows pin the same version of every action
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
- every requirements file at the top of the tree is one the nightly audit
  resolves -- the `-r` lines inside those included, as with the pip cache -- or is
  the one named in the test with its reason, which is read from the other side
  too so neither the exemption nor the file it names can outlive the other; the
  two halves that can fail over a severity agree on which one, a threshold
  written in two tools that read none of each other's configuration; the audit
  itself never gates a pull request and the review of what one *adds* only ever
  does (ADR-0062);
- every ADR is indexed, numbered to match its heading, carries the status, date
  and sections ADR-0001 asks for, and cites only records that exist;
- the floor measures every tree the suite tests -- the two beside the package
  through `source_dirs`, so a test deleted from either is a red run, and the
  package alone through the `source` that three other tools read (ADR-0064);
- the Saturday mutation run mutates the package `.coveragerc` names in `source`
  and none of the trees measured beside it, on the
  Python `ci.yml` pins, with every file these tests open -- or import from
  outside `buy_agent`, `benchmark/` and `integration/` included, plus the files
  at the top of the tree they name and the paths the skills point at, neither of
  which any constant carries -- named in mutmut's `also_copy`;
- the Saturday run over the front end mutates every source `ui/src/app` holds
  unless the config leaves it out by name, and every name it leaves out is a
  file that is there; its tsconfig relaxes the templates and declares no
  `compilerOptions`, that being the one place the checks `npm run build` makes
  could be turned down where nothing else would notice; each of the two runs
  hands the report the file its own tester wrote, which is what picks the reader
  and the floor; Stryker's own `thresholds.break` stays off, a second floor
  being a run that fails with no report published; and no two of the four
  schedules are waiting on the same runners (ADR-0061);
- every dependency `requirements.txt` pins is one the package imports, and every
  third-party module the package imports is pinned in some requirements file --
  read off the source with `ast` and mapped to a distribution by
  `importlib.metadata`, so a deferred import counts and no table of the names
  that differ has to be kept. Neither half is visible to either suite: a pin
  nothing imports still passes every module's tests, keeps the coverage floor and
  survives the mutation run, while an import pinned nowhere installs here and on
  nobody else's machine. The dev and mutation files are outside it, being run
  over the package rather than imported by it; the two paying files and the
  screenshot one are outside the first half only, an optional install being
  absent on a checkout that pins it;
- the linter and the type checker read that same package, `.pylintrc` sits where
  every command that runs pylint is run from, the `[mypy]` section of `setup.cfg`
  keeps the one check it was added for, the two files name the same five libraries
  as ones neither tool can read -- read from both sides, so neither list can
  outlive the other (ADR-0063) -- and no line of the package takes a check away
  without saying why: a `# pylint: disable` with no prose above it is a suppression
  nobody can date, which is what the `# noqa` codes it replaced had become
  (ADR-0048) -- nor does that file name a check by its code, `W0718` in its own
  `enable` or `disable` being the thing `use-symbolic-message-instead` is
  switched on to stop, which pylint applies to the pragmas and not to the file
  that switches it on (ADR-0049);
- every skill in `.claude/skills/` is named after its own directory, is
  described where this file introduces them, and names only files, tests and
  tables that exist -- `add-option` the two the form declares, `preflight` the
  checking commands `ci.yml` runs and the toolchains it pins;
- every link in every Markdown file here points at something that is there,
  which is the rule the decision log already keeps for the records it cites and
  the skills for the paths they name, applied to the prose a reader actually
  starts from: `README.md` hands off to `docs/`, this file to the record behind
  each rule, `docs/testing.md` to both suites. A renamed file leaves every one of
  those valid Markdown and every one of them a dead end, and nothing else in
  either suite opens them -- the link still renders, and only somebody following
  it finds out. Two placeholders are exempt and are the same ones a skill's paths
  are: `docs/adr/NNNN-slug.md` is a file the template and `add-adr` are telling
  the reader to create;
- `ui/tsconfig.json` keeps `strict` and `strictTemplates` on and
  `ui/tsconfig.app.json` `noUncheckedIndexedAccess`, since how much `npm run
  build` checks is a setting rather than a property of the build and a weaker one
  fails nothing;
- every module in the package takes its logger off the package's own name,
  leaves its formatting to the logger, marks nothing as the report, configures
  logging nowhere but `logging_setup`, and writes to stdout not at all -- and
  each entry point wires its `--verbose` flag to the level. Every one of those
  is invisible where it is broken: the line still reaches a terminal, and only
  the browser's progress panel, a `> top.txt` or a `-v` nobody ran is any the
  wiser;
- every type named for a failure is one -- a class whose name ends in `Error`
  reaches `BaseException` -- and nothing but the `if __name__` guard ends the
  process. `main` answers a code and the guard spends it; a `sys.exit` further
  in is the one failure the three-failure agreement cannot catch, since
  `SystemExit` is a `BaseException` and goes past every `except Exception`
  above it -- in a worker thread silently, which is the stream stopping mid-run
  with no `failure` event to say why. The converse of the naming rule is
  deliberately not asserted: `server._Stopped` is raisable and is not a failure
  (ADR-0034);
- and the suite keeps its own four: no test is switched off outright -- both
  markers are `skipif`, which names what is missing rather than saying a test is
  off -- nothing sleeps but `tests/test_server.py`, where a run has to still be
  going while a second request arrives, the environment is changed through
  `monkeypatch` rather than written, `os.environ` being one dictionary for the
  whole process and the leak landing on a later test rather than the one that
  caused it, and nothing reads a declaration off a function object, mutmut's
  trampoline carrying none of them and the Saturday run being where that is
  found out.

### The architecture tests

Those are the rules that span a *declaration*. `tests/test_architecture.py` is
the other half -- the rules that span an *import* -- asserted against the import
graph with [ArchUnitPython](https://github.com/LukasNiessen/ArchUnitPython)
(ADR-0047). Which module may know about which is what this file says most often,
and an import in the wrong direction runs perfectly: it passes that module's own
tests, keeps the coverage floor and survives the mutation run. Thirty-two rules,
each the executable form of a sentence written down here or in a record, and a
thirty-third test that keeps them honest:

- the package has **no import cycles**, and imports **none of the five trees
  that import it** -- `tests/`, `integration/`, `benchmark/`, `demo/`,
  `scripts/`, none of which is in the image or the release archive;
- the package **starts no process**: `subprocess`, `multiprocessing` and
  `webbrowser` are nobody's here. Installing Ollama, pulling a model, building
  the UI and opening the page are `scripts/start.ps1`'s (ADR-0023), and the
  container starts neither model server either (ADR-0015). A child process is
  the one way out of this one that no fake in the suite could answer: it would
  not see the `FakeLLM`, the faked `search_web` or the scratch cache directory,
  and a server that opened a browser would open it where nobody is sitting. The
  one exception is named rather than hidden: the headless Chromium Playwright
  launches for `screenshots.py`, asked for by the server alone and faked at the
  camera's `launch` in the suite (ADR-0065);
- **nothing here awaits, and the threads are the four that wait**: no
  `asyncio`, `anyio` or `trio` anywhere. The server ADR-0010 settled is a
  `ThreadingHTTPServer`, and an `async def` below it would want a loop under
  everything that imports this package -- the CLI, the suite and the Python
  caller who imported `BuyAgent` included. So `threading`, `concurrent.futures`,
  `queue` and `contextvars` belong to the four modules that wait on somebody
  else: `fetch` reading the result pages in a pool, `providers` asking `ollama
  show` once per tag, `server` running a request in a worker thread and
  routing its log lines by the context that thread began in, and `screenshots`
  handing every picture to the one thread its browser belongs to. Playwright
  runs a loop of its own under its synchronous API, inside that thread, and
  nothing here awaits it (ADR-0065);
- every module sits in a **layer that reaches only downward** -- entry points,
  web, orchestration, pipeline, paying, seams, settings, domain -- with three
  of the four edges that are decisions named in the test: the pipeline never reads
  the config (which is what lets `rank_products`, `ground` and `Constraints` be
  tested with three arguments and no environment), the pipeline never pays
  (ADR-0046), and paying never asks the model. The fourth is no longer this
  rule's to state: the seams reach the domain and nothing else above them, which
  is `journal.py`'s doing and worth saying -- what it writes down is products,
  so it names the domain types every layer already passes around (ADR-0060) --
  so the model seam's own edge (ADR-0038) is a rule of its own below. The rule
  has two blind spots besides, and they are why so many of the rules below name
  one module: an edge to or from a file in **no** layer is skipped, and so is one
  *inside* a layer, so the two doors, the three tables and the seams under the
  journal each keep a stricter rule of their own;
- **one seam, one module**: `mandates.py` alone imports `ap2` (ADR-0046),
  `providers.py` alone a model client (ADR-0029), `search.py` alone a search
  library (ADR-0021, ADR-0057), `fetch.py` alone the HTML parser,
  `screenshots.py` alone Playwright (ADR-0065), the four that
  speak HTTP -- `fetch`, `providers`, `rails`, `search` -- are the four the suite
  patches, so a fifth is a request from a module nobody thought made any --
  the browser's requests are Chromium's, and its seam is the camera's `launch`
  rather than a patched transport -- and `argparse`
  belongs to the two modules handed an `argv`: a parser below them is a third
  set of defaults, and one that answers a bad value by exiting the process;
- **one temporary-file dance and one hashed name**: `tempfile` and `hashlib`
  are `cache.py`'s, and `journal.py` imports `write_atomically` and `file_for`
  rather than keeping a second copy of the half either module could have got
  subtly wrong on its own (ADR-0060);
- **the environment is read where a setting is declared**: `config.py`, for what
  a door can fill in too; the three tables, for the address and the key on each
  row; `cache.py`, for the directory a run may reuse; and `mandates.py`, for the
  key and the open mandate that authorise a payment. Neither door is on that
  list, which is the rule -- every flag defaults to the matching `AgentConfig`
  field, so a second reading of the environment below one door is a setting the
  other one does not have;
- **the standard library's network is `server.py`'s alone** -- a socket, an
  `ssl`, an `http.client`, a `urllib.request`. The rule above is about a
  distribution and this one is about the machine underneath it: a module that
  opened its own socket would pass that one while making a request no fake could
  answer. `server.py` *is* a socket on purpose (ADR-0010) and only listens on
  it; `urllib.parse` is string handling and is deliberately not on the list;
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
  and `search.py`, `sources.py`, `mandates.py` and `screenshots.py` know nothing
  about any other module -- deciding is not fetching, the AP2 seam translates
  between two vocabularies without speaking either back (ADR-0046), and the
  browser seam is an address in and a JPEG out (ADR-0065);
- **the web tier is split at the payload**: `api.py` reaches no socket, no
  thread and no queue, which is what leaves every one of its rules assertable by
  calling a function while the status line and the stream stay in `server.py`;
- **the two doors do not know about each other**: the CLI and the API are two
  ways of filling in one `AgentConfig`, and both being entry points is exactly
  why the layer rule cannot say so. `server.py` imported from `__main__.py` is a
  socket module behind `python -m buy_agent`, and `__main__.py` imported from
  the server is an `argv`'s worth of defaults behind a form;
- **nothing above the orchestrator runs a step of its own**: the two doors, the
  payload and the settings reach none of `extraction`, `verification`,
  `constraints` or `fetch`. `ranking` is the exception at all five and the same
  exception -- a finished run put in another order without being run again
  (ADR-0035), the criteria `--sort-by` offers, and the `RankingWeights` a config
  carries;
- **the model seam knows nothing about products**: `chat.py` is a prompt, a
  chain and an answer read back as its schema, and the schema is the caller's
  (ADR-0038) -- which is what lets a stand-in for the model be a class with one
  `answer` method and nothing about shopping in it;
- **the currency table is the leaf**: `money.py` knows nothing of this package
  and imports nothing installed, which is what "a currency is added there and
  nowhere else" comes to when six modules read a derivation off it (ADR-0054);
- **a bound read out of the request is applied by nobody**: `bounds.py` reaches
  `money.py`, for the marks that make a figure a budget, and no other module of
  the package -- not the constraints that do apply a bound, and not the
  `config.LIMITS` that would refuse one, a figure the setting refuses being
  dropped at the door rather than pre-filled into a box the form then marks
  (ADR-0059);
- **the steps take values and answer values**: nothing in the pipeline or the
  domain reads an environment variable, a file, a clock or a random number --
  the half of "the pipeline never reads the config" no layer can state, and what
  says a remembered answer (ADR-0044) is the same answer;
- **the steps do not chain themselves**: the order of the pipeline is
  `BuyAgent.run`'s to know, since a joint argued in one place is a joint that
  can be moved, and the one edge inside that layer is `verification.py` sharing
  `extraction.py`'s vocabulary -- *one*, which is a rule of its own beside it,
  since a module left out of the subject of a rule is left out of it for every
  other step too;
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
allows, so the thirty-third test collects the placings and counts them. An edge
*inside* a layer is skipped in that same silent way and no table can fix that
one, which is what the rules naming a single module above are for. Size is
deliberately not asserted: a ceiling on lines, methods or cohesion would be a
policy nobody has decided.

### The live suite

`integration/` is the second Python suite and the only place a real model is
involved (ADR-0026). It is Ollama's alone: vLLM needs a GPU and a CPU runner
cannot host one honestly, so that provider's half is asserted in
`tests/test_providers.py` and named as a gap in ADR-0028 -- and a LiteLLM proxy
in front of the same Ollama would test the proxy's translation rather than this
code, which ADR-0068 names the same way. A directory rather
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
passes -- either of the two, one `Tool` row each saying what that tester calls a
mutant nothing noticed and which floor is under it (ADR-0061); `update_ollama.py` decides what "updated" means -- a digest that moved
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
`$OLLAMA_MODEL`/`$OLLAMA_HOST`, `$VLLM_MODEL`/`$VLLM_HOST` and
`$LITELLM_MODEL`/`$LITELLM_HOST` still reach it and
no default is written down twice. Off one config rather than three constants,
because the pair belongs to the provider. Ollama is the only server it starts --
the install-and-pull half is behind a provider check, and anything else is
waited for at `/models` and named rather than launched, a vLLM needing a GPU, a
served model and flags this script has no business choosing, and a LiteLLM proxy
the `config.yaml` saying what it routes to. Paying is the same
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

`scripts/setup.ps1` and `scripts/preflight.ps1` are the contributor's half of
that directory (ADR-0067): `start.ps1` sets a machine up to *run* the agent, and a
checkout it set up passes nothing -- no pytest, no linter, no AP2 SDK. `setup.ps1`
is `.claude/hooks/session-start.sh` for a person: a `.venv` holding
`requirements-dev.txt` and the SDK in its two commands, `npm ci` in `ui/`, Python
and Node checked against the pins it reads out of `ci.yml` and never writes down,
and the count of CRLF files a clone made before `.gitattributes` still carries,
named with the command that rewrites them rather than rewriting them over whatever
is uncommitted. `preflight.ps1` is `ci.yml`'s checking steps, step for step and in
order, each job stopping at its first failure and the other running anyway. Both
are held by `tests/test_conventions.py` -- `setup.ps1` installs what `ci.yml`'s
jobs install, `preflight.ps1` runs exactly the checks `ci.yml` runs -- and read
through the same probe by `tests/test_setup_scripts.py`: every program
`setup.ps1` runs goes through `Run` and is looked for with `Have` first, and every
one `preflight.ps1` runs goes through `Job`, which records a failure rather than
throwing it. `.gitattributes` is `* text=auto eol=lf` with the tree's binary kinds
named, and a convention test finds every binary file and holds it to a line there.

## Environment

Development happens on Windows with PowerShell as the default shell; prefer
PowerShell syntax for terminal commands, or use the Bash tool explicitly for
POSIX scripts.
