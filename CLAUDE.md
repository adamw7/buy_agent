# CLAUDE.md

Guidance for Claude Code (claude.ai/code) in this repository.

## What this is

A shopping agent: a plain-language request ("wireless headphones under $200")
goes in, the web is searched, up to 10 products and what pages say about them
are extracted, ranked, and the top 3 logged. The model is local, served by
Ollama, vLLM, or whatever a LiteLLM proxy routes to. `AgentConfig.provider`
chooses, and `buy_agent/providers.py` is the only module that knows the
difference (ADR-0028, ADR-0068). There is no framework: `buy_agent/chat.py` is the
whole model seam (ADR-0038). `ui/` is an Angular front end served by
`buy_agent.server`. Two things are optional, off by default and need extra
installs: *buying* what was found, authorised by signed
[AP2](https://ap2-protocol.org) mandates (ADR-0046), and a screenshot of each
product page from a loopback-bound server with Playwright (ADR-0065).

Longer docs: `README.md` (tour), `docs/models.md`, `docs/docker.md`,
`docs/testing.md` (suites, floors, nightly, benchmark, mutation, audit, and what
the convention tests check), `docs/architecture.md` (C4),
`ui/README.md` (components), `demo/README.md` (recordings, local merchant).
`docs/adr/` explains why each rule exists. The rules themselves live here or in a
convention test. Prefer adding a convention test over restating an ADR.

## Commands

Dependencies go in a stdlib `.venv`. There is no `pyproject.toml` and no
packaging. Run everything from the repo root.

```powershell
.\scripts\setup.ps1                           # full contributor setup (ADR-0067)
.\scripts\preflight.ps1                       # the gate ci.yml applies; -Only python|ui
pip install -r requirements-dev.txt          # runtime deps: requirements.txt

python -m pytest                              # whole suite (~8s)
python -m pytest tests/test_ranking.py::test_cheaper_wins_when_rating_is_equal
python -m coverage run -m pytest ; python -m coverage report
python -m pylint buy_agent                    # from the root (ADR-0048)
python -m mypy buy_agent                      # from the root (ADR-0063)
ollama pull qwen3:0.6b ; python -m pytest integration   # against a real model
python -m benchmark --scripted perfect        # score the pipeline, no model needed
python -m benchmark --model qwen3:0.6b --model gemma4:12b   # compare models, every case
python -m benchmark.server                    # the same comparison as a page on :8100

python -m buy_agent "gaming laptop under $1500"
python -m buy_agent "gaming laptop" --provider vllm      # or litellm
python -m buy_agent "headphones" --max-price 200 --min-rating 4.5
python -m buy_agent "espresso machine" --compare         # what moved since last run
python -m buy_agent "wireless earbuds" --source rtings.com --source @mkbhd

pip install -r requirements-ap2-deps.txt        # what the AP2 SDK imports
pip install --no-deps -r requirements-ap2.txt   # the SDK itself, for paying only
python -m buy_agent "headphones" --pay          # dry-run rail: signs, charges nobody
pip install -r requirements-screenshots.txt ; python -m playwright install --only-shell chromium

python -m buy_agent.server                    # UI + API on :8000
.\scripts\start.ps1                           # everything from cold (ADR-0023)
python -m scripts.update_ollama               # re-pull Ollama's models, report what moved
python -m mutmut run                          # needs requirements-mutation.txt
python scripts/mutation_report.py mutation-results.txt
```

```powershell
cd ui
npm install ; npm test ; npm run test:coverage ; npm run build
npm run lint ; npm run format:check          # `npm run format` fixes formatting
npm start                                     # :4200, proxying /api to :8000
npx stryker run                               # UI mutation testing, ~90 min
```

`ui/` is its own Angular 22 workspace (Node 22.22.3+, 24.15+ or 26+). The Python
side has no formatter. Every checker is a gate and must finish with **no
messages at all**, including warnings and unused suppressions: pylint and mypy
(configured in `.pylintrc` and `setup.cfg [mypy]`), and ESLint and Prettier in
`ui/`. `npm run build` is also the UI's type check. `ui/tsconfig.json` sets
`strict` and `strictTemplates`, and `ui/tsconfig.app.json` adds
`noUncheckedIndexedAccess` for app code only (not the specs). Maps looked up by a
payload key (`App.receipts`, the form's `limits` and `placeholders`) declare
`| undefined` on themselves.

If `ui/dist/ui/browser` is missing, the API still works and the page returns a
503 with how to build it (HTML for browsers, JSON otherwise). `_unbuilt_remedy`
writes that message. When `_workspace_for` finds no workspace (for example a
release archive), the message names `--ui-dir` instead of `npm install`.

### The container

`docker build -t buy-agent .` then `docker run --rm -p 8000:8000 buy-agent`. A
Node stage builds `ui/`, and a `python:3.14-slim` stage installs
`requirements.txt` and copies the build to `ui/dist/ui/browser`. No model server
runs in the image (ADR-0015). It reaches the host through
`host.docker.internal`, which `$OLLAMA_HOST`, `$VLLM_HOST` and `$LITELLM_HOST`
are set to (on Linux, add `--add-host=host.docker.internal:host-gateway`).
`ENTRYPOINT` is `python`, so the CLI is reachable too. Only `release.yml` builds
the image (ADR-0030). `.dockerignore` must cover everything `.gitignore` names
(and `.env`), must not catch anything the `Dockerfile` copies, and must account
for every top-level directory. Convention tests check all three. See
`docs/docker.md`.

### Settings and their environment

- `$BUY_AGENT_CACHE_DIR` sets where `pages/` (ADR-0040), `answers/` (ADR-0044),
  `runs/` and the benchmark's `benchmark/board.json` (ADR-0070) live. It has no
  flag and no form field. `cache_ttl` covers both
  cache kinds, and 0 means always fetch fresh and always ask the model. Sampled
  runs (`temperature > 0`) are never cached. `cache.MAX_BYTES` caps each kind,
  pruning oldest first (ADR-0052).
- `runs/` is `journal.py`'s. It is **not** a cache: it never expires and is
  bounded by `MAX_RUNS`/`MAX_SEARCHES`, least recently *run* first (ADR-0060). It
  stores only name, price and currency per product. It shares
  `cache.write_atomically` and `cache.file_for` rather than copying them.
  `agent.journal_for` is the only place a config becomes a key, and the key is
  what was *asked* (request, region, scale, backend, sources, bounds, count),
  never the model.
- `$BUY_AGENT_AP2_KEY` (the signing key, a secret) and `$BUY_AGENT_AP2_MANDATE`
  (a pre-signed open mandate) have no flag. If the mandate is present, the run
  is human-not-present (ADR-0046).
- `$BUY_AGENT_BACKEND` plus `$SEARXNG_HOST`, `$BRAVE_HOST` and `$BRAVE_API_KEY`
  are read on each row of `search.BACKENDS` (ADR-0057). A missing key fails at
  the row with a sentence naming the variable, not at the config.
- `$BUY_AGENT_PROVIDER` plus `$OLLAMA_MODEL`/`$OLLAMA_HOST`,
  `$VLLM_MODEL`/`$VLLM_HOST`/`$VLLM_API_KEY` and
  `$LITELLM_MODEL`/`$LITELLM_HOST`/`$LITELLM_API_KEY` are read on each row of
  `providers.PROVIDERS` (ADR-0029). `model`, `base_url` and `api_key` default to
  `""` and are resolved per provider in `__post_init__` (ADR-0012). API keys have
  no flag or form field and are never in `defaults_payload` or
  `provider_options()`. `$LITELLM_MODEL` defaults to the placeholder
  `local_model` (ADR-0068).
- Every other CLI flag defaults to its `AgentConfig` field. One is renamed:
  `reasoning` is `--think` and `think` in JSON and `agent.types.ts`. Its `None`
  means "send nothing".
- `reasoning=False` and `num_ctx=16384` are the defaults because the default
  model (`gemma4:12b`) thinks, and the ~4.3k-token extraction prompt plus the
  JSON output would not fit in 4096 (ADR-0019, ADR-0050). `num_ctx` and
  `cpu_only` are Ollama-only (`Provider.takes_num_ctx`/`takes_cpu_only`), and
  both front ends say so. `reasoning` and `model_timeout` are shared.
  `model_timeout` is set on each client, and OpenAI clients get `max_retries=0`
  (ADR-0051). `cpu_only` and `model_timeout` are not part of the answer-cache
  fingerprint.

### CI and the workflows beside it

`ci.yml` has two jobs, Python 3.14 and Node 22.23.3, and every step runs under
`bash`:

- **Python:** coverage + pytest, then pylint, then mypy.
- **UI:** `test:coverage`, `build`, `lint`, `format:check`.

Pushes and PRs run on Linux. Windows joins on the Saturday 04:09 UTC schedule and
on `workflow_dispatch` (ADR-0037). Use dispatch for a branch that touches paths,
encodings, sockets or `start.ps1`. `fail-fast` is off, and the concurrency group
names the event.

Scheduled workflows (none gate a PR except dependency review):

- `audit.yml`: pip-audit and `npm audit --audit-level=high`, nightly at 02:47.
  On PRs, dependency-review runs at `high` (ADR-0062).
  `requirements-ap2.txt` is exempt.
- `integration.yml`: real Ollama, nightly at 03:41, Linux, 5 minutes, model left
  unpinned (ADR-0026).
- `mutation.yml`: mutmut, Saturday 05:17, with a 75% floor (ADR-0016).
  Configured in `setup.cfg`. Files the suite reads from outside `buy_agent` must
  be listed in `also_copy`. Tests reading source rules use `conftest.SOURCE_ROOT`.
  Never read declarations (such as `__kwdefaults__`) off function objects, because
  mutmut's trampoline strips them.
- `mutation-ui.yml`: Stryker, Saturday 06:23 (ADR-0061). Configured in
  `ui/stryker.config.mjs`. `ui/tsconfig.mutation.json` turns off only the
  template checks.
- `release.yml`: runs when a release is published. It builds the tarball/zip with
  `SHA256SUMS.txt` and the `ghcr.io` image, checks out `$TAG`, and smoke-tests
  both (ADR-0030). Before the push, Grype scans the image's OS layer
  (`.github/grype.yaml`; HIGH and above, fixed findings only) and Syft writes an
  SPDX SBOM for the release. Both packages get build provenance, and
  `id-token: write`/`attestations: write` go only on a job that attests
  (ADR-0069).

Config files (reasons in `docs/testing.md`):

- `pytest.ini`: `pythonpath = .`, `testpaths = tests`, `--strict-markers`,
  deprecation warnings as errors, and `timeout = 60`.
- `.coveragerc`: a 99% branch floor over the package, plus `benchmark/` and
  `scripts/` via `source_dirs` (ADR-0064).
- `.pylintrc`: ADR-0048 and ADR-0049. Every `# pylint: disable` carries a
  sentence of why above it.
- `ui/angular.json`: a 98% floor on statements and lines only. Don't add a
  branch floor.
- `ui/tsconfig*.json`: as above.
- `ui/eslint.config.mjs`: ADR-0066. It includes a rule refusing `on*`
  attributes and `javascript:` URLs in templates (the CSP).

## Architecture

`docs/architecture.md` has the C4 diagrams. Keep it in step when a
responsibility or boundary moves. `docs/adr/` is the decision log, indexed by
`docs/adr/README.md`, and a new ADR is two edits: the file and its index row. To
contradict an accepted record, write a new record that supersedes it. Never
reuse a number or rewrite an accepted record. `docs/adr/0000-template.md` is the
starting point. Every record is Accepted but ADR-0020, which ADR-0037
supersedes.

`.claude/skills/` holds checklists over these rules:

- `add-option`: a new setting.
- `add-row`: a model server, rail or search backend.
- `add-adr`: takes the next number off the directory.
- `preflight`: the CI gate.

Convention tests hold every path they name. `.claude/hooks/session-start.sh`
sets up remote web sessions. It installs the Node pinned in `ci.yml` into
`/opt/node-<version>`, runs `npm ci`, and builds `.venv` from the newest Python
on `$PATH` with dev deps and the AP2 SDK. It reads versions from `ci.yml`, is a
no-op when already done or not remote, and only warns on failure.
`tests/test_session_hook.py` tests it.

The pipeline is deliberately **not** a tool-calling loop (ADR-0002). The LLM does
the two steps small models are reliable at, and Python does the rest.

```
request -> refine query (LLM) -> search (once, or once per named source)
        -> fetch + condense pages -> extract products and opinions (LLM)
        -> clean_products -> ground -> deduplicate -> shopper's bounds -> rank -> log top 3
```

The order matters at three joints:

- `clean_products` runs before `ground`, so publisher suffixes don't fail the
  name check.
- `ground` runs before `deduplicate`, so a merge only combines backed figures.
  `models.QUALIFIERS` plus `_fill_gaps` move grouped fields together.
- The bounds come after `deduplicate` (which may supply the price) and before
  ranking (ADR-0039). `Constraints.apply` re-reads the budget currency against
  the set it leaves (ADR-0043).

| Module | Responsibility |
| --- | --- |
| `agent.py` | `BuyAgent.run()` -- orchestrates the pipeline, translates model-server errors |
| `chat.py` | The whole model-facing seam: a prompt, a chain, an answer read back as its schema (ADR-0038) |
| `extraction.py` | Both prompts, both chains, name cleaning, deduplication |
| `fetch.py` | Streams result pages up to a ceiling, keeps the lines quoting a figure or passing judgement, and tallies how the rest failed |
| `cache.py` | What a run can reuse: page text (ADR-0040) and answers (ADR-0044) -- and nothing else |
| `journal.py` | What past runs of this search reported and what moved since (ADR-0060) |
| `verification.py` | Drops products, figures and quotes absent from the sources; links what is left |
| `constraints.py` | The bounds the shopper set, applied before ranking |
| `bounds.py` | What the request itself asks for, read in Python and offered at both doors (ADR-0059) -- never applied |
| `ranking.py` | Scoring and sorting, and what each score is made of; no LLM involved |
| `models.py` | `ExtractedProduct` (LLM-facing) vs `Product` (domain), and which currency a set is counted in |
| `money.py` | Every currency table (ADR-0054) |
| `search.py` | Which backend a search is asked through, one row each -- and nothing else (ADR-0021, ADR-0057) |
| `sources.py` | What a trusted source is: domain, term, `site:` query, `covers` |
| `providers.py` | Everything that differs between Ollama, vLLM and a LiteLLM proxy, and nothing else |
| `screenshots.py` | The browser seam; the only module that imports `playwright` (ADR-0065) |
| `payment.py` | What may be bought and for how much: cart, spend limit, receipt -- and one failure |
| `mandates.py` | The AP2 seam; the only module that imports `ap2` (ADR-0046) |
| `rails.py` | Everything that differs between one counterparty and another, one row each |
| `config.py`, `logging_setup.py`, `__main__.py` | Config, the report, the CLI |
| `api.py` | Request options in, ranked products out -- the web-facing half worth testing |
| `server.py` | A stdlib HTTP server: the JSON API, the event stream, the built UI |

### Nineteen conventions

- **A model server is one row in one table, reached one way.**
  `providers.PROVIDERS` holds each server whole: defaults from its own env vars,
  its client, schema declaration, listing, transport errors, hint sentences,
  `takes_num_ctx`, `takes_cpu_only` and `more_room` (ADR-0029, ADR-0068).
  `AgentConfig.model_server` is the *only* place a provider name becomes
  behaviour. There is no `if provider == ...` above the table and there are no
  module-level wrappers. Listings answer `InstalledModel`s, so embedding-only
  models are marked, not hidden (ADR-0032). Shared hints go in
  `_too_slow_hint`/`_unreachable_hint`, and shared failures are decided once in
  `_hint`. A hint naming a *model* requires the row's own client to have raised
  it (`_answered_by`); otherwise the address is what's wrong. Each chat model may
  have a `close`. `chat.release` calls it, `BuyAgent.close` decides when, and
  both doors build one agent per request and release it.
- **A payment rail is one row in one table too, and the default one spends
  nothing.** `rails.RAILS` holds, per row: address, whether it needs one and an
  enrolled key, `moves_money`, how checkouts and authorisations work, transport
  errors and hint. No real merchant is named anywhere (ADR-0046).
  `AgentConfig.rail_used` is the only place a rail name becomes behaviour. The
  default `dry-run` signs a verifiable chain and charges nobody.
- **A search backend is one row in one table, and the default one needs nothing.**
  `search.BACKENDS` is organised the same way (ADR-0057), and
  `AgentConfig.search_backend` is the only place its name becomes behaviour. A row
  is handed *itself*, not a config, so `search.py` imports nothing from `config`.
  The retry (ADR-0053) is shared, while each row decides what "nothing matched"
  means. A missing key is a `SearchError` from the row, and `Backend.configured`
  marks the picker.
- **Never pay on an unverified number, and never on one this run cannot place.**
  `payment._check` refuses a product in any of these cases:
  - its price was blanked by grounding;
  - its currency is unprinted;
  - the run's scale is not `money.placeable`;
  - the price is outside the run's currency (ADR-0043);
  - it has no source page.

  That is the deliberate opposite of the shopper's bounds, which *keep* what
  they can't judge. The CLI prompt, the card's button (`cannot_pay`) and the
  payment all ask this one function.
- **The sources are whatever was searched, and the shopper may narrow them.**
  With `sources` set, `BuyAgent._search` runs one search per source, filters
  through `Source.covers()`, pools the results deduplicated by URL, and cuts back
  to `search_results` (ADR-0027). The **domain** is what's enforced. Every spec
  must name something (`_HOSTNAME`, `_HANDLE`). There is never a fallback to the
  wider web. `sources.py` does no I/O.
- **`ExtractedProduct` uses sentinels, `Product` uses `None`.** Use `-1`/`""`
  rather than nullable fields, because Ollama compiles the schema into a grammar
  and a required `number` makes `"N/A"` impossible (ADR-0004). New extraction
  fields stay non-nullable and are converted in `to_product()`. `opinions`'s
  empty list survives as itself.
- **Never rank on an unverified number, never link to an unverified page, and
  never quote what nobody said.** `verification.ground()` drops products whose
  name is absent from the sources and blanks unbacked figures (ADR-0006). Only
  price is checked as a bare number. A rating needs its scale ("4.3/5"), and a
  review count needs somebody counted ("3,200 ratings"). A new figure a page
  could print by accident needs its own `mentions_*`. Extraction and verification
  must see the same text, which is why `fetch.enrich()` puts content on
  `SearchResult`. `attribute_sources()` (inside `ground`) links each product to
  the first searched page mentioning it and keeps the model's URL only if it was
  searched (ADR-0017).
- **A quote is checked as running text, on the page it came from** (ADR-0024,
  ADR-0025). `fetch.py` sweeps twice, for figures (`page_chars`) and judgement
  (`opinion_chars`). `verify_opinions()` checks overlapping five-word runs,
  most of which must be found, against one page at a time that `mentions_name`
  says is about the product. `_OPINION` is a vocabulary of judgement, never of
  subject matter. `mentions_name` decides realness, links and quotes, so
  loosening `GENERIC_WORDS` loosens all three.
- **A quote carries the page that printed it** (ADR-0042). `Product.opinions`
  are `Opinion(words, url)`. The model is never asked for the page, and a `url`
  of `None` means the search result had no URL, not "unprinted". The pair travels
  as one object.
- **A currency belongs to its price, and a review count to its rating**
  (ADR-0022). `models.QUALIFIERS` pairs them. `_fill_gaps`,
  `verification.verify_numbers` and `ExtractedProduct.to_product` all move or
  drop a qualifier with its figure. A new field that only qualifies another joins
  that field's group. `opinions` is merged separately in `_merge_opinions`.
- **Every listing a product was priced at is kept, and the cart is for one of
  them** (ADR-0058). `deduplicate` seeds one `models.Offer` per listing (after
  `ground`), and `_combine` keeps all of them via `_merge_offers`. They are not in
  `_MERGEABLE_FIELDS`. Ranking, bounds and currency read only `Product.price`.
  `payment.offer_for` picks the offer matching the headline price and currency,
  which is who gets paid. `Product.offers_label` writes the spread, and the front
  ends display it.
- **`GENERIC_WORDS` is shared, and edits to it pull in two directions.**
  `verification.py` imports it, `NAME_TOKENS` and `SUPERLATIVES` from
  `extraction.py` (ADR-0008). Adding a word merges more names *and* makes
  `mentions_name` stricter against its 0.6 coverage bar. Compare token to token,
  never by substring. Only add words that identify nothing ("wireless",
  "black"), never brands or model numbers.
- **Missing data scores neutral, not zero -- and says that it did.**
  `ranking.NEUTRAL` is 0.5 for an unknown rating, count or price (ADR-0007).
  Price and rating sorts sink unknowns rather than dropping them, and bounds keep
  them (ADR-0039). Prices are compared within one currency and never converted.
  `models.dominant_currency` picks the set's currency: the shopper's `currency`
  if named, else the vote (ADR-0056). `models.comparable_price` answers `None`
  outside it (ADR-0043), and both `rank_products` and `Constraints` must go
  through it.

  Spellings are settled in `money.code_for`. `money.SIGNS`, `money.WORDS` and
  `money.SCANNED_CODES` are *derived* from the one table and read on either side
  of the figure. Words are case-folded, and codes are read as written (so "Try"
  isn't TRY). The exemptions `UNPLACEABLE` (`¥`) and `UNSCANNED` ("pounds") are
  tested from both sides. A new currency is added in `money.py` only (ADR-0054).

  `score_product` answers `ScoreParts` with `neutral` naming the assumed
  criteria (ADR-0041), and runs report `RankingWeights.fractions`, paired by
  `ranking.CRITERIA` (ADR-0045).
- **A bound written in the request is offered and never applied** (ADR-0059).
  `bounds.notice` reads "under $200", "at least 4 stars" and "over 500 reviews"
  in plain Python, never via the model. Each bound is anchored on its unit. The
  CLI logs a line naming the flag, and `GET /api/bounds` pre-fills an empty box
  once, as a hint. `bounds.py` doesn't know `config.LIMITS`. Nothing may apply
  one.
- **The report says what it is ordered by.** `ranking.ORDERINGS` holds a phrase
  per `SortBy` ("cheapest first"). `BuyAgent.run` passes its `sort_by` to
  `log_top_products`. `defaults_payload` sends the same phrases as
  `sort_labels`, and `--sort-by`'s help spells them out. A new criterion needs a
  phrase.
- **The report is output; the progress is narration.** `log_top_products` marks
  its records for stdout (plain `_REPORT_FORMAT`). Everything else goes to stderr
  with `_FORMAT`'s clock. Only the console handler filters. `configure_logging`
  sets the level itself. `_NOISY_LIBRARIES` are quiet until verbose, while
  `_TRACE_LIBRARIES` (httpcore, httpx2...) stay quiet even at `--verbose`.
  `tests/test_logging_setup.py` holds every logger the clients create to one list
  or the other.
- **A heuristic that takes something away says how many at INFO and which at
  DEBUG.** There are eight: `clean_products`, `drop_ungrounded`,
  `merge_variants`, `deduplicate`'s nameless drop, `Constraints.apply`,
  `verify_numbers`, `verify_opinions` and `attribute_sources`. The first five
  drop whole products and also call `record` (default
  `models.nothing_recorded`) with a `models.Removal`. The run payload carries
  those as `dropped` (ADR-0055), with a sentence written beside the log line.
  `tests/test_logging_contract.py` covers all eight.
- **The web is asked twice and the model once, and the clock is handed in.**
  `fetch.py` and `search.py` take a `wait` (`BuyAgent` passes `time.sleep`, and
  tests pass `None` to ask once) (ADR-0034, ADR-0053). A page answering 429/503 is
  retried after a capped `Retry-After` (never parsed as a date), and so is any
  search failure other than "matched nothing". The model is never retried
  (ADR-0051). Every stand-in for `search_web`/`enrich` takes `wait`, as every
  `BuyAgent` stand-in takes `checkpoint`.
- **Model output is never trusted as judgement.** Filtering, scoring and
  ordering belong in Python, where they're testable. `clean_products` filters
  headlines the model reports as products.

### Failures

`BuyAgent.run()` raises exactly `ValueError`, `ModelUnavailableError` and
`SearchError` (ADR-0009). `__main__.main()` catches exactly those around the run
and returns 1, and `api._STATUS` maps them to 400/503/502. A new failure mode must
be handled in all three places. Other exit codes: 130 for Ctrl-C (including at
the payment prompt), `NOTHING_FOUND` (3), `PAYMENT_FAILED` (4), and 2 for
argparse. `main` has a separate `except OSError` for writing `--json`.

Payment failures are *not* a fourth row. `payment.PaymentError` is the one thing
paying raises. Its subclass `RailUnreachableError` (502) covers a counterparty
that was unreachable or answered with nothing readable. A plain `PaymentError`
(400) means it declined. These map through `api.PAY_STATUS` and are caught in
`__main__._bought`.

Only query refinement is recoverable: it falls back to the raw request but lets
`ModelUnavailableError` through. `_invoke` catches
`config.model_server.transport_errors`. Ollama's tuple includes
`httpx.HTTPError` beside ollama's own `RequestError`, and OpenAI-style rows use
`openai.OpenAIError`. Every row that dials an address, and `fetch`, also counts
`UnicodeError`, which the socket raises unwrapped for a host it cannot encode
(`shop..example`). `chat.UnreadableAnswerError` is a `ValueError`, so
`_extract_products` turns it into a `ModelUnavailableError` with the provider's
hint about room (ADR-0019).

### Options, and the ten that are special

**A setting is one row in `api.OPTIONS`**, used by both doors: `parse_options`,
`defaults_payload`, `_BOUNDED`/`limits_payload`, and `__main__.main`. A new
option is a row there plus a flag with help in `__main__.build_parser`. The
exceptions: `sources` is a list, the CLI's `--num-ctx` sentinel is not a value,
and `search_results = max(results, top)`.

**A sentence below either door names the setting, never the flag.** Hints from
`providers`, `rails` and `AgentConfig.__post_init__` are shown in the browser
too. Other programs' flags (`vllm serve --max-model-len`) are fine, and so are
docstrings. This is enforced by a convention test.

- **Numbers:** the range lives in `config.LIMITS`. The CLI checks it with a
  `type` function, and both doors refuse the same values in the same words
  ("must be a number"). `--json`'s `type` refuses a missing directory.
- **`region`:** `config.parse_region` is the only shape check, and each door
  wraps it in its own `_checked` (ADR-0031). `_empty_search_note` composes
  `_region_note` and `_sources_note` for an empty search.
- **`currency`:** `config.parse_currency` checks `money.placeable`, folding
  spellings. `--currency`'s help lists the codes. Blank means vote (ADR-0056).
  `rank_products`, `Constraints`, `payment.cart_for` and `api.results_payload`
  all take the override. A finished run carries the currency it was counted in
  as `scale` (`ranking.scale_of`), and a re-sort and a payment are handed it back
  rather than voting again: a vote over products in rank order can break a tie
  the other way.
- **`backend`, `provider` and `rail`** are checked against their table at both
  doors and offered in four places, the fourth being the picker rows
  (`backend_options()`, `ProviderOption`, `rail_options()`). When `provider`
  changes, the front ends pass `model`/`base_url` as `""` and the form refills
  them.
- **`sources`** bypasses `api._read`. `_read_sources` takes an array or a
  separated string. The CLI's repeatable `--source` checks the spec in `type`.
  An empty value over the wire means the whole web, while the CLI refuses a flag
  naming nothing.
- **The three bounds** (`max_price`, `min_rating`, `min_reviews`) default to
  `None`, meaning no bound. Unknown figures pass, and help text says so. The
  placeholder is "No limit". `max_price` is in the run's currency (ADR-0039,
  ADR-0043).
- **`journal`:** its help names the directory. `--compare` is CLI-only, not a
  config field, and the browser gets `changes` on every run (ADR-0060).
- **Paying:** `pay` is off by default. `rail` defaults to `DEFAULT_RAIL`
  (`$BUY_AGENT_RAIL` overrides it). `merchant_url` is resolved per rail. A
  paying rail with no address raises `ValueError` in `__post_init__`, and each
  door translates it: an `ApiError` on `merchant_url`, argparse exit 2 on the
  CLI. `spend_limit` has `max_price`'s range but refuses what it can't judge.
  The form hides the other three until `pay` is ticked, and the CLI names idle
  ones (`_idle_paying_flags`).
- **`weights`** is reachable only from Python.

`buy_agent/__init__.py` re-exports `BuyAgent`, `AgentConfig`, `Product`,
`RankedProduct`, `RankingWeights` and `rank_products`.

## The UI and its server

`buy_agent.server` is stdlib-only (ADR-0010). `/api` goes to `api.py`, and
everything else goes to the built app, with `index.html` as the fallback.

| Endpoint | Answers with |
| --- | --- |
| `GET /api/config` | The form's defaults |
| `GET /api/models` | What a named server is serving, or why not and what to do |
| `GET /api/sources` | Whether a Trusted sources field names sites (runs nothing) |
| `GET /api/bounds` | What the request asks for, offered to the form, never applied |
| `GET /api/screenshot` | A JPEG of a card's page, from a server with a camera |
| `POST /api/search` | One run, as JSON |
| `POST /api/rank` | A finished run's products in another order -- runs no pipeline |
| `POST /api/pay` | One product bought, given the witnessed approval -- runs no pipeline |
| `GET /api/search/stream` | One run, as SSE: `log` lines, then `result` or `failure` |

- **Runs are streamed** (ADR-0011). `_LogRelay` routes records by context, and
  `fetch.enrich` starts its pool workers with `_as_the_caller`. A `ping` goes out
  every 15s, and each line carries its `%H:%M:%S` `time`.
- **Closing the stream stops the run at the next step** (ADR-0034).
  `BuyAgent.run` calls `checkpoint` before `search`, `fetch`, `extract` and
  `rank`, and `server._stop_when` raises `_Stopped`. A stop is not a failure, so
  it is kept out of `_STATUS`, `main` and `Raises`. New steps must announce
  themselves.
- **The failure event is `failure`, never `error`,** because `EventSource`
  reconnects on `error`. `HEAD /api/search/stream` is 405.
- **The browser decides nothing** (ADR-0012). Python supplies `cannot_pay`,
  `pay_currency`/`pay_label`/`pay_merchant` (all null together), `price_label`,
  `rating_label`, each model's `completion`, the provider `label` and `hint`.
  `sort_by` is a request even for a finished run. `ui/src/app/agent.types.ts`
  mirrors every payload.
- **Paying is witnessed** (ADR-0046). `POST /api/pay` gets the run, the product
  and `approved` (an echo of title, `pay_label` and `pay_currency`). The server
  builds the cart and the echo must match. An open mandate replaces the echo.
  Receipts never carry the mandate chain, only a `reference` hash.
- **Re-sorting is a request, not a re-run** (ADR-0035). `rank_again` is only
  `rank_products`, and the products travel in the body. `api.results_payload` is
  the one shaping of a run's products (API, `--json`, Download). A re-sort
  reports no `dropped` or `changes` of its own, so the page keeps the run's.
- **A blank value means "use the default"** in `parse_options` and in the UI's
  `toQuery`.
- **The form refuses first, on Python's rules** (ADR-0033). `limits` bind
  `[min]`/`[max]`, and `GET /api/sources` answers `{"sources", "error"}` with a
  200 either way. `ApiError.field` marks the box. Region is not pre-checked.
- **Every request is admitted first** (ADR-0018). `_refused()` runs at the top
  of `do_GET`, `do_POST` and `do_HEAD`; a new method needs it too. It refuses
  cross-site `Sec-Fetch-Site`, a foreign `Origin`, and a `Host` outside
  `allowed_hosts`. Typed addresses go through `_bound_host` (IPv6 brackets), and
  `_family_for` picks `AF_INET6`.
- **The server never asks itself for a model.** `server._reaches` detects an
  address that lands on this server (`:8000` is vLLM's default). `/api/models`
  explains, and a run is refused on `base_url`.
- **Only a loopback-bound server with Playwright takes pictures** (ADR-0065),
  via `server.camera_for`. `defaults_payload.screenshots` says whether it can.
  Nothing in the pipeline knows about pictures. Only `http`/`https` are
  photographed, and a failure is a 502. `screenshots.Camera` is one browser on
  one thread, launched lazily and closed after a minute idle.
- **Every request is answered.** `do_GET`/`do_POST` end in a catch-all 500.
  `server.main` refuses bad `$BUY_AGENT_PROVIDER`/`RAIL`/`BACKEND` and an
  out-of-range `--port` before binding. `_clashing_provider` hints only on
  `EADDRINUSE`.

**Platform traps.** `server._CONTENT_TYPES` spells out MIME types, because
Windows' registry can serve `.js` as `text/plain`. `_resolve` catches
`OSError`/`ValueError`, and tests make `resolve()` raise rather than rely on
platform-specific inputs (ADR-0020).

**The CSP coupling.** `_SECURITY_HEADERS` sets CSP to `'self'`, with
`'unsafe-inline'` for styles only. `optimization.styles.inlineCritical` is off in
`ui/angular.json` because its inline `onload` would be blocked and the page left
unstyled. Anything adding inline handlers, inline scripts or cross-origin
requests breaks in the same way, and only a browser shows it.

### The components

`ui/README.md` describes each component and the reasons for it. These rules must
hold:

- No sentence about the model server is written in TS. `App.unreachable` is
  Python's `hint`. `App.asking` holds the in-flight `ModelSource`. Re-ask on
  load, on Check again, and after a contradicting run.
- `progress-log` is presentation. Download log is offered only for failed or
  stopped runs.
- `App.reveal`/`showResults` scroll results into view (`nearest`). A failure
  naming a field is not scrolled to.
- `dropped` (ADR-0055) and `changes`/`compared_with` (ADR-0060) are grouped and
  counted but never re-worded, and they survive a re-sort. `movement` is for
  colour and counting only.
- `offers_label` and each offer's `price_label` are Python's (ADR-0058).
- A screenshot frame is drawn only when `screenshots` is set and the product
  links somewhere. It is `loading="lazy"`, reserves 640x400, and a failed image
  drops the frame by address.
- Buying takes two clicks, and the second restates the *cart* (`pay_label`,
  `pay_merchant`, the rail, whether anyone is charged). A press moves the
  keyboard to what replaced its block (`LANDING`): the cart and never the button
  that buys, Pay again, the wait, the receipt. Focus the reader moved elsewhere
  stays there.
- Receipts are keyed by product name, never by index.
- `pay` is the one setting not remembered in `localStorage`. Every storage call
  is wrapped.
- A request-noticed bound fills its box once (`noticedNow`, `offered`) and marks
  nothing. Its note sits above the box's hint, never in its place. A submit waits
  for a reading still on its way (`reading`, `held`), and one that puts a figure
  in a box sends nothing and focuses that box. `App` answers a failed reading as
  one that noticed nothing.
- `problems()` gates `canSubmit` using server ranges and the last sources
  answer. Disabled fields are neither checked nor sent. `notes()` adds the
  server's `rejected` field while `submitted` still matches, and `moved` tells
  `App` when to drop the banner. `options()` builds the payload. A mark is
  `aria-invalid` plus `aria-describedby` from `problemId`. `numberTyped` reads
  `validity.badInput`.
- Number boxes are declared once in `numberFields`, keyed like
  `limits_payload`. `settings` spreads `numberSettings(this.numberFields)`, and
  `remembersBlank` is set per row. `payingFields`/`settingFields` partition it.
- The model field marks rather than hides ("not served", "embedding only")
  (ADR-0032).
- A mark opens the Settings panel it's in, once per change of marks.
- Each component is checked with `axe-core` via `ui/src/app/a11y.ts`, with rules
  enabled one at a time, each with a reason. An inconclusive rule counts as a
  failure. Colour is never the only carrier.

`create_server(agent_factory=..., allowed_hosts=..., camera=...)` are the server
seams. Components are tested in jsdom with `TestBed`, and `AgentService` against
a fake `EventSource`.

### demo/

See `demo/README.md`. `demo/server.py` runs the real server with only
`search_web`, `enrich` and the model faked. The scripts make the fake model wrong
in six ways. Recordings are MPEG-2 program streams: `VIDEO` in `recording.mjs`
(shared by both recorders) is fixed, so don't go back to MPEG-1, and its `-f vob`
is the MPEG-2 system layer, which `-f mpeg` is not. `encode` refuses a stream
ffmpeg reports a buffer underflow or overflow in. Sound is
synthesised: `record.mjs` writes cues, and `sound.py` renders them.
`benchmark.mjs` films the benchmark's page on real Ollama models, narrated by
`narration.py`, and refuses a take in which `/api/ps` shows GPU memory.
`demo/merchant.py` verifies with the AP2 SDK,
never `buy_agent.mandates`. Nothing in `demo/` is imported, covered, mutated or
shipped.

## Tests

`docs/testing.md` is the long form and the one place counts are written. The
rules a change must obey:

- **Seams:** `BuyAgent(config, llm=...)` with `tests/conftest.py`'s `FakeLLM`
  (one `answer` method). A chain stand-in has `invoke`. `create_server`'s three
  seams. `Photographer` stands in for the camera. No test starts a browser:
  `screenshots.Camera` takes `launch=`.
- **Network patch points:** `buy_agent.agent.search_web`,
  `buy_agent.agent.enrich`, `buy_agent.search.DDGS`,
  `buy_agent.search.httpx.get`, `buy_agent.fetch.httpx.Client`, and
  `rails.httpx.post`. `search_web` is patched only on `agent`, which is why the
  source fan-out lives there.
- **Model clients:** patch `providers.Client` (Ollama chat and `show`),
  `providers.openai.OpenAI`, and `providers.httpx.get` (all listings). Assert on
  the *request*. Never patch `ollama.Client` or `DDGS.text`.
- **Table rows** are compared by identity only through their module
  (`providers_module.OLLAMA`), because tests reload those modules.
- **No test in `tests/`** touches the network, a model server or the real cache.
  Autouse fixtures point `$BUY_AGENT_CACHE_DIR` at a scratch directory and unset
  `$BUY_AGENT_AP2_KEY`, `$BUY_AGENT_AP2_MANDATE` and `$BUY_AGENT_MERCHANT_URL`.
- **Payment tests sign real mandates** and verify them with the SDK's verifier.
  CI installs the SDK.
- **Server tests bind loopback** and use `serve_forever(0.01)`. `raw()` reads
  until the declared body has arrived.
- **`integration/`** holds the real-model tests, outside `testpaths`.
- **Nothing sleeps** but `tests/test_server.py` (`StubAgent.delay`). Change the
  environment through `monkeypatch` only.
- **Optional prerequisites skip, never fail:** `needs_powershell` and
  `needs_ap2` (in `tests/conftest.py`, and `skipif` only). `needs_ap2` goes on
  the parametrised case that reaches signing. `tests/test_session_hook.py` skips
  on Windows, and so does `needs_tzset` in `tests/test_journal.py`, which moves
  `$TZ` through `monkeypatch` and has to `time.tzset()` either side.

**Convention tests** (`tests/test_conventions.py`) assert the rules that hold
*between* modules and across the language boundary, listed in `docs/testing.md`
-- this file's conventions heading matching its count among them.

**Architecture tests** (`tests/test_architecture.py`, ArchUnitPython, ADR-0047)
hold the import graph:

- no cycles, and no imports from `tests/`, `integration/`, `benchmark/`, `demo/`
  or `scripts/`;
- no subprocesses except Playwright's Chromium;
- no async, with threading only in `fetch`, `providers`, `server` and
  `screenshots`;
- downward-only layers (entry points, web, orchestration, pipeline, paying,
  seams, settings, domain);
- one module per seam (`ap2`, model clients, search libraries, the HTML parser,
  Playwright, `argparse`), and HTTP only in `fetch`, `providers`, `rails` and
  `search`;
- `tempfile`/`hashlib` only in `cache`;
- env reads only in `config`, the three tables, `cache` and `mandates`;
- stdlib network modules only in `server`, which imports nothing third-party;
- the two doors don't import each other;
- `chat.py` knows nothing of products, and `money.py` is a leaf;
- `bounds.py` reaches only `money`;
- pipeline steps don't chain each other and read no env, files, clocks or
  randomness;
- decision modules never reach the model, fetch or search.

`only()`/`every_module_but()` verify that the modules they name exist, and a
33rd test checks every module sits in exactly one layer.

**The live suite** (`integration/`, ADR-0026) is Ollama-only, on the
`benchmark/corpus.py` pages through real `fetch.condense`, asserting invariants of
one session-scoped `live_run`. It skips without Ollama unless
`$BUY_AGENT_REQUIRE_OLLAMA` is set; `$BUY_AGENT_TEST_MODEL` moves the tag.

**The benchmark** (`benchmark/`, ADR-0036, ADR-0070):

- A case is a module (`REQUEST`, `QUERY`, `REFINED_QUERY`, `PAGES`, `PAGE_TEXT`,
  `ANSWER_KEY`, `PERFECT`, `SLOPPY`) and a row in `cases.CASES`. The nightly
  scores `headphones` alone.
- The key is per-product *sets* of `(price, currency)` and
  `(rating, review_count)`.
- Metrics are declared once in `scoring.METRICS`, beside `MEANINGS`, and the
  scorer reuses the pipeline's own rules. The query is scored apart in
  `query.py` and never blended into the score.
- The floors are a tripwire. Raise one only in its own commit, quoting runs.
- After editing a case, its `PERFECT` must score exactly 1.000, its `SLOPPY`
  must hit its pinned counts, and every page mentioning a product must be one
  its entry lists.
- A contender is reached only through its provider row. An unreadable answer is
  the case's result; a model that cannot be asked is no result, kept nowhere, and
  its other cases are skipped. The board drops runs whose case fingerprint moved.
- `benchmark/server.py` subclasses `BuyAgentHandler`. The page in
  `benchmark/web/` decides nothing, loads nothing inline and writes text only;
  `tests/test_benchmark_server.py` holds its script to the payloads.

**Scripts.** `scripts/mutation_report.py` (one `Tool` row per tester) and
`scripts/update_ollama.py` (run as `python -m scripts.update_ollama`) are
tested. `scripts/start.ps1` reads its provider, model and address from one
`AgentConfig()` and starts only Ollama. It installs the AP2 SDK only when paying
env vars are set, using `mandates.INSTALL`. It is tested via
`tests/start_script_probe.ps1` (AST-parsed, functions dot-sourced, and every
program run through `Run`). `scripts/setup.ps1` and `scripts/preflight.ps1` must
match `ci.yml`'s installs and checks (`Run`/`Have` and `Job` respectively), and
`tests/test_setup_scripts.py` tests them. `.gitattributes` is
`* text=auto eol=lf` with the binary kinds named.

## Environment

Development is on Windows with PowerShell as the default shell. Prefer
PowerShell syntax, or use the Bash tool explicitly for POSIX scripts.
