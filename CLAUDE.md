# CLAUDE.md

The rules a change here must obey. The reasons are in `docs/adr/`; the long forms
in `README.md` (tour), `docs/testing.md` (suites, floors, workflows, benchmark,
mutation, audit, and what the convention and architecture tests check),
`docs/docker.md`, `docs/architecture.md` (C4), `docs/models.md`, `ui/README.md`
and `demo/README.md`. Prefer a convention test over restating an ADR, and a
pointer over restating a doc.

## What this is

A shopping agent: a plain-language request ("wireless headphones under $200") is
searched for; up to 10 products and what pages say about them are extracted and
ranked, and the top 3 logged. The model is local (Ollama, vLLM, TensorRT-LLM or a
LiteLLM proxy), picked by `AgentConfig.provider`; only `buy_agent/providers.py`
knows the difference (ADR-0028, ADR-0068, ADR-0081). No framework:
`buy_agent/chat.py` is the whole model seam (ADR-0088). `ui/` is an Angular front
end served by `buy_agent.server`. Optional, off by default, with extra installs:
*buying* under signed [AP2](https://ap2-protocol.org) mandates (ADR-0046), and a
Playwright screenshot of each product page from a loopback-bound server
(ADR-0065).

## Commands

Dependencies go in a stdlib `.venv`; no `pyproject.toml`, no packaging. Run
everything from the repo root.

```powershell
.\scripts\setup.ps1                           # full contributor setup (ADR-0067)
.\scripts\preflight.ps1                       # the gate ci.yml applies; -Only python|ui|browser
pip install -r requirements-dev.txt          # runtime deps: requirements.txt
python -m pytest                              # whole suite (~8s)
python -m pytest -n 3 --cov                   # three workers, with the floor (ADR-0076)
python -m pylint buy_agent ; python -m mypy buy_agent   # from the root (ADR-0048, ADR-0063)
ollama pull qwen3:0.6b ; python -m pytest integration   # against a real model
python -m pytest browser                      # the build in Chromium (ADR-0087)
python -m benchmark --scripted perfect        # score the pipeline, no model needed

python -m buy_agent "gaming laptop under $1500" --provider vllm   # or ollama, litellm, trtllm
pip install -r requirements-ap2-deps.txt ; pip install --no-deps -r requirements-ap2.txt   # paying
python -m buy_agent "headphones" --pay          # dry-run rail: signs, charges nobody
pip install -r requirements-screenshots.txt ; python -m playwright install --only-shell chromium
python -m buy_agent.server                    # UI + API on :8000
$env:BUY_AGENT_API_TOKEN = '<random>' ; docker compose up -d --build   # deployed (ADR-0092)
.\scripts\start.ps1                           # everything from cold (ADR-0023)
python -m mutmut run                          # needs requirements-mutation.txt

cd ui
npm install ; npm test ; npm run test:coverage ; npm run build
npm run lint ; npm run format:check          # `npm run format` fixes formatting
npm start                                     # :4200, proxying /api to :8000
$env:STRYKER_SHARD = 'app' ; npx stryker run  # one UI mutation shard (ADR-0085); unset, ~3 hours
```

`ui/` is its own Angular 22 workspace (Node 22.22.3+, 24.15+ or 26+); Python has
no formatter. Every checker (pylint and mypy under `.pylintrc` and
`setup.cfg [mypy]`, ESLint, Prettier) must print **nothing**, warnings and unused
suppressions included, and every `# pylint: disable` has a sentence of why above
it. `npm run build` is the UI's type check (`strict`, `strictTemplates`, and
`noUncheckedIndexedAccess` for app code only), so maps looked up by a payload key
(`App.receipts`, the form's `limits` and `placeholders`) declare `| undefined`.
Without `ui/dist/ui/browser` the API still works and the page is a 503 saying how
to build it (`_unbuilt_remedy`), naming `--ui-dir` when `_workspace_for` finds no
workspace.

**CI.** Each `ci.yml` job (`python`, `ui`, then `browser`, ADR-0087) installs and
runs `scripts/preflight.ps1 -Only <job>`, so checks are written once, in the
script (ADR-0091). Linux runs on pushes and PRs; Windows joins on the Saturday
schedule and `workflow_dispatch` (ADR-0037), which a branch touching paths,
encodings, sockets or `start.ps1` should get. Workflows read `.python-version` and
`.nvmrc`; only the `Dockerfile`'s base images repeat them. Of the scheduled
workflows and `release.yml` (`docs/testing.md`, `docs/docker.md`), only dependency
review gates a PR. Every checkout sets `persist-credentials: false`; `release.yml`
caches nothing (ADR-0092). `automerge.yml` squash-merges a same-repo, non-draft PR
once `ci.yml` and `audit.yml` pass on its head, unless labelled `no-automerge`
(ADR-0086). Floors: 99% branch coverage over the package, `benchmark/` and
`scripts/` (ADR-0064); 98% statements and lines in `ui/angular.json`, no branch
floor; 75% mutation (ADR-0016).

**The container** (`docs/docker.md`) runs no model server (ADR-0015):
`$OLLAMA_HOST`, `$VLLM_HOST`, `$LITELLM_HOST` and `$TRTLLM_HOST` point at
`host.docker.internal`, and `ENTRYPOINT` is `python`. Only `release.yml` builds
it, from a tag whose commit `ci.yml` passed (ADR-0030, ADR-0082). Both packages
carry `LICENSE` and the git-ignored `buy_agent/_version.txt`, written by shell
(`release.yml`; the `Dockerfile` from its `VERSION`/`REVISION` build arguments)
and read by `version.py` (ADR-0083). `.dockerignore` covers what `.gitignore`
names plus `.env`, catches nothing the `Dockerfile` copies, and accounts for every
top-level directory. The image installs `requirements.lock` `--no-deps` (no
hashes, so it also constrains `requirements-dev.txt`; regenerate it by its
header's command when `requirements.txt` moves), binds through
`ENV BUY_AGENT_HOST=0.0.0.0`, caches in a volume at `/var/cache/buy-agent`, and
checks `/healthz`. `compose.yaml` won't start without the token (ADR-0092).

**Mutation runs** test a copy of the tree: files the suite reads from outside
`buy_agent` go in `setup.cfg`'s `also_copy`, tests reading source use
`conftest.SOURCE_ROOT`, and no test reads declarations (`__kwdefaults__`) off
function objects, which mutmut's trampoline strips.

## Settings and their environment

- **The server's settings come from its environment** (ADR-0092):
  `config.server_environment()` reads `$BUY_AGENT_HOST`, `_PORT`,
  `_ALLOWED_HOSTS`, `_MAX_RUNS`, `_BEHIND_PROXY`, `_ACCESS_LOG` and `_LOG_FORMAT`
  at start-up as the defaults of `server.build_parser`'s flags, so a bad value is
  a usage error. `$BUY_AGENT_API_TOKEN` is a secret: no flag, in no `repr`,
  refused under `deployment.MIN_TOKEN` characters. None is a run setting or an
  `api.OPTIONS` row.
- `$BUY_AGENT_CACHE_DIR` (no flag or form field) holds `pages/` (ADR-0040),
  `answers/` (ADR-0044), `runs/` and `benchmark/board.json` (ADR-0070).
  `cache_ttl` covers both caches (0: always fetch and ask afresh); sampled runs
  (`temperature > 0`) are never cached; `cache.MAX_BYTES` caps each kind, oldest
  pruned first (ADR-0052).
- `runs/` is `journal.py`'s and **not** a cache (ADR-0060): it never expires, is
  bounded by `MAX_RUNS`/`MAX_SEARCHES` (least recently *run* first), keeps only
  name, price and currency per product, and shares `cache.write_atomically` and
  `cache.file_for`. Only `agent.journal_for` makes a config a key: what was
  *asked* (request, region, scale, backend, sources, bounds, count), never the
  model.
- `$BUY_AGENT_AP2_KEY` (the signing key, a secret) and `$BUY_AGENT_AP2_MANDATE`
  (a pre-signed open mandate, making the run human-not-present) have no flag
  (ADR-0046).
- `config` reads `$BUY_AGENT_PROVIDER`, `$BUY_AGENT_BACKEND` and
  `$BUY_AGENT_RAIL` as the defaults; each row reads its own address and key.
  `search.BACKENDS` rows read `$SEARXNG_HOST`, `$BRAVE_HOST` and `$BRAVE_API_KEY`
  (ADR-0057), and a missing key fails at the row, naming the variable.
  `providers.PROVIDERS` rows read `$OLLAMA_MODEL`/`$OLLAMA_HOST`,
  `$VLLM_MODEL`/`$VLLM_HOST`/`$VLLM_API_KEY`,
  `$LITELLM_MODEL`/`$LITELLM_HOST`/`$LITELLM_API_KEY` and
  `$TRTLLM_MODEL`/`$TRTLLM_HOST`/`$TRTLLM_API_KEY` (ADR-0029).
- `model`, `base_url` and `api_key` default to `""`, resolved per provider in
  `__post_init__` (ADR-0012). API keys have no flag or form field and are never in
  `defaults_payload` or `provider_options()`. `$LITELLM_MODEL` defaults to the
  placeholder `local_model` (ADR-0068). TensorRT-LLM shares vLLM's client,
  listing and `_one_model_hint`, its hint adding how to turn guided decoding on
  (ADR-0081).
- `reasoning=False` and `num_ctx=16384` are defaults because `gemma4:12b` thinks,
  and the ~4.3k-token extraction prompt plus its JSON outgrow 4096 (ADR-0019,
  ADR-0050). `num_ctx` and `cpu_only` are Ollama-only
  (`Provider.takes_num_ctx`/`takes_cpu_only`), as both front ends say.
  `model_timeout` is set on each client, and OpenAI clients get `max_retries=0`
  (ADR-0051). `cpu_only` and `model_timeout` stay out of the answer-cache
  fingerprint.

## Architecture

Keep `docs/architecture.md` (C4) in step when a responsibility or boundary moves.
`docs/adr/` is the decision log: a new ADR is a file from
`docs/adr/0000-template.md` plus its row in `docs/adr/README.md`. Contradict an
accepted record by superseding it with a new one; never reuse a number or rewrite
an accepted record. Every record is Accepted but ADR-0020, ADR-0038, ADR-0065 and
ADR-0067, which ADR-0037, ADR-0088, ADR-0087 and ADR-0091 supersede.

`.claude/skills/` holds checklists over these rules: `add-option` (a setting),
`add-row` (a model server, rail or search backend), `add-adr` (the next record)
and `preflight` (the CI gate). `.claude/hooks/session-start.sh` sets up remote web
sessions from `.nvmrc` and `.python-version`, is a no-op when done or not remote,
only warns on failure, and is tested by `tests/test_session_hook.py`.

The pipeline is deliberately **not** a tool-calling loop (ADR-0002): the LLM does
the two steps small models are reliable at, and Python the rest.

```
request -> refine query (LLM) -> search (once, or once per named source)
        -> fetch + condense pages -> extract products and opinions (LLM)
        -> clean_products -> ground -> deduplicate -> shopper's bounds -> rank -> log top 3
```

Three joints matter: `clean_products` before `ground`, so publisher suffixes
don't fail the name check; `ground` before `deduplicate`, so a merge combines only
backed figures (`models.QUALIFIERS` and `_fill_gaps` move grouped fields
together); the bounds after `deduplicate`, which may supply the price, and before
ranking (ADR-0039), with `Constraints.apply` re-reading the budget currency
against the set it leaves (ADR-0043).

| Module | Responsibility |
| --- | --- |
| `agent.py` | `BuyAgent.run()`: orchestrates the pipeline, translates model-server errors |
| `chat.py` | The model seam: a prompt, a chain, an answer read back as its schema |
| `extraction.py` | Both prompts and chains, name cleaning, deduplication |
| `fetch.py` | Streams pages to a ceiling, keeps lines quoting a figure, a listing's standing or a judgement, tallies failures |
| `structured.py` | A page's schema.org JSON-LD, written as lines a shop prints; no HTML, no I/O |
| `cache.py` | What a run can reuse: page text and answers, nothing else |
| `journal.py` | What past runs of this search reported, and what moved since |
| `verification.py` | Drops products, figures and quotes the sources lack; links what is left |
| `constraints.py` | The shopper's bounds, applied before ranking |
| `alerts.py` | Whether a finished run met the price alert; told, never applied |
| `bounds.py` | What the request itself asks for, offered at both doors, never applied |
| `ranking.py` | Scoring and sorting, and what each score is made of; no LLM |
| `models.py` | `ExtractedProduct` (LLM-facing) vs `Product` (domain); which currency a set is counted in |
| `money.py` | Every currency table |
| `search.py` | Which backend a search is asked through, one row each, nothing else |
| `sources.py` | What a trusted source is: domain, term, `site:` query, `covers` |
| `providers.py` | What differs between Ollama, vLLM, a LiteLLM proxy and TensorRT-LLM, nothing else |
| `screenshots.py` | The browser seam; the only module importing `playwright` |
| `payment.py` | What may be bought and for how much: cart, spend limit, receipt, one failure |
| `mandates.py` | The AP2 seam; the only module importing `ap2` |
| `rails.py` | What differs between one counterparty and another, one row each |
| `config.py`, `logging_setup.py`, `__main__.py` | Config, the report, the CLI |
| `version.py` | Which build is running: a release's stamp, else `dev` and the commit off `.git`'s files |
| `api.py` | Request options in, ranked products out: the web-facing half worth testing |
| `deployment.py` | What serving other machines adds: token, fixed addresses, caps, drain, metrics; no HTTP |
| `server.py` | A stdlib HTTP server: the JSON API, the event stream, the built UI |

`tests/test_architecture.py` holds the import graph (layers, one module per seam,
where HTTP, threads, env reads and files may go; ADR-0047), listed in
`docs/testing.md`.

### Conventions

- **A model server is one row in one table, reached one way.**
  `providers.PROVIDERS` holds each whole: env defaults, client, schema
  declaration, listing, transport errors, `own_hint`, `takes_num_ctx`,
  `takes_cpu_only`, `more_room` (ADR-0029, ADR-0068, ADR-0081). Only
  `AgentConfig.model_server` turns a provider name into behaviour: no
  `if provider == ...`, no module-level wrappers. Listings answer
  `InstalledModel`s, marking (not hiding) embedding-only models (ADR-0032), with
  the server's digest where listed (ADR-0075). Shared hints are
  `_too_slow_hint`/`_unreachable_hint`; shared failures are decided once in
  `Provider.hint`, before `own_hint`. A hint naming a *model* needs the row's own
  client to have raised it (`_answered_by`), else blame the address.
  `chat.release` calls a chat model's `close`, `BuyAgent.close` decides when, and
  `api.run_agent` builds and releases one agent per request for both doors.
- **A payment rail is one row, and the default spends nothing.** `rails.RAILS`
  rows: address, whether one and an enrolled key are needed, `moves_money`,
  checkout and authorisation, transport errors, hint. Only
  `AgentConfig.rail_used` turns a name into behaviour. No real merchant is named
  anywhere; `dry-run` signs a verifiable chain and charges nobody (ADR-0046).
- **A search backend is one row, and the default needs nothing**
  (`search.BACKENDS`, ADR-0021, ADR-0057); only `AgentConfig.search_backend`
  turns a name into behaviour. A row is handed *itself*, so `search.py` imports
  nothing from `config`. The retry is shared (ADR-0053); each row decides what
  "nothing matched" means. A missing key is the row's `SearchError`;
  `Backend.configured` marks the picker.
- **Never pay on an unverified or unplaceable number.** `payment._check` refuses
  a price grounding blanked, an unprinted currency, a scale not
  `money.placeable`, a price outside the run's currency (ADR-0043), no source
  page, or out of stock (ADR-0079) -- unlike the bounds, which *keep* what they
  can't judge. The CLI prompt, the card's `cannot_pay` and the payment all ask
  it.
- **The sources are what was searched; the shopper may narrow them** (ADR-0027).
  With `sources`, `BuyAgent._search` searches per source, filters by
  `Source.covers()`, pools results deduplicated by URL, and cuts to
  `search_results`. The **domain** is enforced; every spec names something
  (`_HOSTNAME`, `_HANDLE`); nothing falls back to the wider web. `sources.py` does
  no I/O.
- **`ExtractedProduct` uses sentinels (`-1`/`""`), `Product` uses `None`**
  (ADR-0004): extraction fields stay non-nullable, converted in `to_product()`.
  `ProductList` asks for one product at least via `json_schema_extra`, never a
  validator, so an empty answer still means nothing found; `EXTRACTION_PROMPT`
  asks for figures, no qualifier (ADR-0089).
- **Never rank on an unverified number, link an unverified page, or quote what
  nobody said.** `verification.ground()` drops products no source names and
  blanks unbacked figures (ADR-0006). Only price is a bare number: a rating needs
  its scale ("4.3/5"), a review count a counter ("3,200 ratings"), and a figure a
  page could print by accident its own `mentions_*`. Extraction and verification
  read the same text, which `fetch.enrich()` puts on `SearchResult`.
  `attribute_sources()` links each product to the first searched page naming it,
  keeping the model's URL only if searched (ADR-0017).
- **A quote is a judgement, found as running text on its own page, where that page
  is about the product, and it carries that page** (ADR-0024, ADR-0025, ADR-0042,
  ADR-0090). `fetch.py` sweeps for figures (`page_chars`) and judgement
  (`opinion_chars`). `verify_opinions()` wants most overlapping five-word runs on
  one page `mentions_name` ties to the product; `said_of` wants the nearest line
  at or above the quote naming any of the run's products to name this one, so line
  breaks must reach it. `judges` refuses a quote restating a rating or review
  count unless it is also in `models.JUDGEMENT`, the one vocabulary of judgement
  (never of subject matter), which `fetch` keeps lines in too. `Product.opinions`
  are `Opinion(words, url)`; the model is never asked for the page; `url=None`
  means the result had none.
- **A qualifier moves with its figure**: currency with price, review count with
  rating (ADR-0022), `availability` and `condition` with price (ADR-0079).
  `models.QUALIFIERS` groups them for `_fill_gaps`,
  `verification.verify_numbers` and `ExtractedProduct.to_product`; a new
  qualifier joins its figure's group. `opinions` merge in `_merge_opinions`.
- **Stock and condition have one vocabulary**, `models.STANDING_PHRASES`, a row
  per `Availability`/`Condition` value (ADR-0079): `verify_standing` keeps one
  only where a page about the product prints it, `fetch` keeps lines in it,
  `structured` writes in it; "new" or "used" alone never counts. Ranking reads
  neither; `Product.listing_label` is the one wording.
- **A page's declaration is page text** (ADR-0078): `fetch.html_to_text` puts
  `structured.declared`'s lines first, each one grounding accepts; nothing
  downstream knows it was declared.
- **Every listing a product was priced at is kept; the cart is for one**
  (ADR-0058). `deduplicate` seeds a `models.Offer` per listing (after `ground`);
  `_combine` keeps all via `_merge_offers`, not `_MERGEABLE_FIELDS`. Ranking,
  bounds and currency read only `Product.price`; `payment.offer_for` picks the
  offer matching the headline price and currency; `Product.offers_label` writes
  the spread.
- **`GENERIC_WORDS` is shared and pulls two ways** (ADR-0008):
  `verification.py` imports it, `NAME_TOKENS` and `SUPERLATIVES` from
  `extraction.py`. A new word merges more names *and* makes `mentions_name`
  (realness, links, quotes) stricter at its 0.6 coverage bar. Compare token to
  token, never substring; add only words that identify nothing ("wireless",
  "black").
- **Missing data scores neutral, and says so.** `ranking.NEUTRAL` (0.5) stands in
  for an unknown rating, count or price (ADR-0007); sorts sink unknowns, bounds
  keep them (ADR-0039). Prices compare within one currency, never converted:
  `models.dominant_currency` picks it (the shopper's `currency`, else a vote,
  ADR-0056), and `models.comparable_price`, used by `rank_products` and
  `Constraints`, is `None` outside it (ADR-0043). `score_product` answers
  `ScoreParts`, `neutral` naming assumed criteria (ADR-0041); runs report
  `RankingWeights.fractions` (ADR-0045).
- **A currency is added in `money.py` only** (ADR-0054); `money.code_for` settles
  spellings. `SIGNS`, `WORDS`, `SCANNED_CODES` derive from the one table, read
  either side of the figure; words case-fold, codes read as written ("Try" isn't
  TRY). The exemptions `UNPLACEABLE` (`¥`) and `UNSCANNED` ("pounds") are tested
  both ways.
- **A bound written in the request is offered, never applied** (ADR-0059).
  `bounds.notice` reads "under $200", "at least 4 stars", "over 500 reviews" in
  plain Python, anchored on the unit, blind to the settings' ranges. The CLI logs
  a line naming the flag; `GET /api/bounds` pre-fills an empty box once, as a
  hint.
- **The report says its order:** `ranking.ORDERINGS` has a phrase per `SortBy`
  ("cheapest first") for the heading, `sort_labels` and `--sort-by`'s help; a new
  criterion needs one.
- **The report is output, progress narration.** `log_top_products` marks its
  records for stdout (`_REPORT_FORMAT`); the rest goes to stderr with `_FORMAT`'s
  clock. Only the console handler filters; `configure_logging` sets the level.
  `_NOISY_LIBRARIES` are quiet until verbose, `_TRACE_LIBRARIES` even then;
  `tests/test_logging_setup.py` holds every client logger to one of the two.
- **A heuristic that takes something away logs how many at INFO, which at DEBUG**
  (`tests/test_logging_contract.py`): `clean_products`, `drop_ungrounded`,
  `merge_variants`, `deduplicate`'s nameless drop and `Constraints.apply` (whole
  products, each also `record`ing a `models.Removal`, the payload's `dropped`,
  ADR-0055), and `verify_numbers`, `verify_standing`, `verify_opinions`,
  `attribute_sources`.
- **A run names its model and its time** (ADR-0084): `BuyAgent.run` names the
  model it opened (never one handed in) as `label` at `base_url`, logs each model
  call's time, and ends every returning run with one `Finished in` line, the
  whole then each step. `agent.py`'s `_Stopwatch` times steps, never their
  modules; `logging_setup.seconds_label`, shared with the benchmark, writes
  durations.
- **Pages come only off the public web** (ADR-0092): `fetch.on_the_public_web`
  refuses non-global IPs, single-label names, private suffixes and numeric
  spellings, for `_markup`'s first URL and each redirect (`refuse_private`, the
  client's request hook). Any new reader of a web URL goes through it.
- **The web is asked twice, the model once, with the clock handed in** (ADR-0034,
  ADR-0053, ADR-0051). `fetch.py` and `search.py` take `wait` (`time.sleep` from
  `BuyAgent`, `None` in tests to ask once). A 429/503 page is retried after a
  capped `Retry-After` (never read as a date), as is any search failure but
  "matched nothing"; the model is never retried. Every `search_web`/`enrich`
  stand-in takes `wait`, as every `BuyAgent` stand-in takes `checkpoint`.
- **Model output is never trusted as judgement:** filtering, scoring and ordering
  are Python's, and testable; `clean_products` drops headlines posing as products.

### Failures

`BuyAgent.run()` raises exactly `ValueError`, `ModelUnavailableError`,
`SearchError` (ADR-0009): `__main__.main()` catches exactly those (exit 1) and
`api._STATUS` maps them to 400/503/502. A new failure mode goes in all three.
Other exits: 130 Ctrl-C (the payment prompt too), `NOTHING_FOUND` 3,
`PAYMENT_FAILED` 4, `ABOVE_ALERT` 5 (ADR-0080), 2 argparse; `main` has its own
`except OSError` for `--json`. Paying is *not* a fourth row: it raises only
`payment.PaymentError` (400, declined) or its `RailUnreachableError` (502,
counterparty unreachable or unreadable), via `api.PAY_STATUS`, caught in
`__main__._bought`.

Only query refinement recovers, falling back to the raw request but letting
`ModelUnavailableError` through. `_invoke` catches
`config.model_server.transport_errors` (Ollama's include `httpx.HTTPError`;
OpenAI-style rows `openai.OpenAIError`); rows that dial an address, and `fetch`,
add `UnicodeError`, raised unwrapped for a host the socket can't encode.
`chat.UnreadableAnswerError` is a `ValueError`, which `_extract_products` makes a
`ModelUnavailableError` with the provider's hint about room (ADR-0019).

### Options

**A setting is one `api.OPTIONS` row**, read by `parse_options`,
`defaults_payload`, `limits_payload` and the CLI's `build_parser` (`--<key>`, or
`--x`/`--no-x` for a switch). A row's parser raises `ValueError`: `api._read`
makes it an `ApiError` marking the key's box, `__main__._flag` a usage error, so
both doors refuse the same values in the same words (ADR-0071). A new option is a
row plus help in `__main__._written()`, which also holds a flag's departures from
its row (`metavar`, the raw `$BUY_AGENT_*` default, `--num-ctx`'s `None`).
`sources` is a list, `sort_by` no config field, `search_results` no row (unset,
`__post_init__` makes it `max(num_products, top_n)`). Keys unlike their field:
`results` (`num_products`), `top` (`top_n`), `fetch` (`fetch_pages`), `think`
(`reasoning`; `None` sends nothing).

**Sentences below either door name the setting, never the flag**, since hints
from `providers`, `rails` and `AgentConfig.__post_init__` reach the browser.
Other programs' flags (`vllm serve --max-model-len`) and docstrings are fine; a
convention test enforces it.

- **Numbers:** ranges live on the rows, read by field into `api.LIMITS`.
  `--json`'s `type` refuses a missing directory.
- **`region`:** only `config.parse_region` checks shape (ADR-0031);
  `_empty_search_note` composes `_region_note` and `_sources_note`.
- **`currency`:** `config.parse_currency` checks `money.placeable`, folding
  spellings; blank means a vote (ADR-0056); `--currency`'s help lists codes. A
  finished run's `scale` (`ranking.scale_of`) is handed back to a re-sort or
  payment, never re-voted: a vote in rank order can break a tie the other way.
- **`backend`, `provider`, `rail`** are checked against their tables and offered
  as picker rows (`backend_options()`, `ProviderOption`, `rail_options()`). On a
  `provider` change the front ends send `model`/`base_url` as `""` and the form
  refills them.
- **`sources`** bypasses `api._read`: `_read_sources` takes an array or a
  separated string. Empty over the wire is the whole web; the CLI refuses an
  empty `--source`.
- **`alert_below`** is told, never applied (ADR-0080): `api.alert_for` asks
  `alerts.price_alert` of a finished ranking for both doors. Only a placeable
  price printed with its currency and not out of stock meets it (a bare one is
  named, never met, as `payment._check` refuses it). It is not in the journal's
  key; the CLI exits `ABOVE_ALERT` when it is given and unmet.
- **The bounds** (`max_price`, `min_rating`, `min_reviews`) default to `None`,
  no bound; unknown figures pass, as help says; the placeholder is "No limit";
  `max_price` is in the run's currency.
- **`journal`:** `--compare` is CLI-only; the browser gets `changes` every run
  (ADR-0060).
- **Paying:** `pay` is off by default. `rail` defaults to `DEFAULT_RAIL`;
  `merchant_url` resolves per rail, and a paying rail without one raises
  `ValueError` in `__post_init__` (marked on `merchant_url`; exit 2).
  `spend_limit` has `max_price`'s range but refuses what it can't judge. The form
  hides the other three until `pay` is ticked; the CLI names idle ones
  (`_idle_paying_flags`).
- **`weights`** is Python-only.

`buy_agent/__init__.py` re-exports `BuyAgent`, `AgentConfig`, `Product`,
`RankedProduct`, `RankingWeights` and `rank_products`.

## The UI and its server

`buy_agent.server` is stdlib-only (ADR-0010): `/api` goes to `api.py`, the rest
to the built app, falling back to `index.html`. `README.md` lists the endpoints.

- **Runs are streamed** (ADR-0011): `_LogRelay` routes records by context,
  `fetch.enrich` starts pool workers with `_as_the_caller`, a `ping` goes every
  15s, each line carries a `%H:%M:%S` `time`.
- **Closing the stream stops the run at the next step** (ADR-0034):
  `BuyAgent.run` calls `checkpoint` before `search`, `fetch`, `extract`, `rank`;
  `server._stop_when` raises `_Stopped`. A stop is no failure: it stays out of
  `_STATUS`, `main` and `Raises`. New steps must announce themselves.
- **The failure event is `failure`, never `error`,** which `EventSource`
  reconnects on. `HEAD /api/search/stream` is 405.
- **The browser decides nothing** (ADR-0012): Python supplies `cannot_pay`,
  `pay_currency`/`pay_label`/`pay_merchant` (null together), `price_label`,
  `rating_label`, `offers_label`, `listing_label`, each model's `completion`, the
  provider `label` and `hint`. `ui/src/app/agent.types.ts` mirrors every payload.
- **Paying is witnessed** (ADR-0046): `POST /api/pay` takes the run, the product
  and `approved` (an echo of title, `pay_label`, `pay_currency`), which must match
  the cart the server builds; an open mandate replaces the echo. Receipts carry a
  `reference` hash, never the mandate chain.
- **Re-sorting is a request, not a re-run** (ADR-0035): `rank_again` is only
  `rank_products` over products in the body. `api.results_payload` is the one
  shaping of a run's products (API, `--json`, Download). A re-sort reports no
  `dropped`, `changes` or `alert`, so the page keeps the run's, and sends the
  run's `seal` with a re-sort or payment (ADR-0092).
- **Blank means default**, in `parse_options` and the UI's `toQuery`.
- **The form refuses first, on Python's rules** (ADR-0033): `limits` bind
  `[min]`/`[max]`; `GET /api/sources` answers `{"sources", "error"}`, 200 either
  way; `ApiError.field` marks the box. Region isn't pre-checked.
- **Every request is admitted first** (ADR-0018): `_refused()` opens `do_GET`,
  `do_POST`, `do_HEAD` and any new method, refusing cross-site `Sec-Fetch-Site`,
  a foreign `Origin`, a `Host` outside `allowed_hosts`. Typed addresses go through
  `_bound_host` (IPv6 brackets); `_family_for` picks `AF_INET6`.
- **Other machines change three things**, each in `deployment.Policy` (ADR-0092).
  With a token, `_refused` asks for it (bearer or Basic password) after the
  `Origin`/`Host` checks, which `_PROBES` and `/metrics` precede. Bound beyond
  loopback or `--behind-proxy`, `policy.fixed_addresses` refuses a `base_url` or
  `merchant_url` not the row's own (`refuse_foreign_address`, `_pay`). Only
  `--behind-proxy` reads `X-Forwarded-Host`/`-For`, in `_forwarded`.
- **A run's products come back sealed** (ADR-0092): `api.Sealer` signs the set,
  keyed off the token (else per process); `rank_again`, `pay_now` and anything
  taking products back refuse a mismatched `seal`, 409 on `products`.
- **A server counts and drains** (ADR-0092): `deployment.Traffic` caps runs
  (`admit_run`; a `_Busy` 503 with `Retry-After`) and connections and feeds
  `/metrics`; `serve_until_interrupted` takes SIGTERM like Ctrl-C, then
  `Server.drain` waits up to `DRAIN_SECONDS`. A 500 names the `request_id`, not
  the exception.
- **The server never asks itself for a model:** `server._reaches` spots an
  address landing here (`:8000` is vLLM's and TensorRT-LLM's default);
  `/api/models` explains, and a run is refused on `base_url`.
- **Only a loopback-bound server with Playwright takes pictures** (ADR-0065), via
  `server.camera_for`, as `defaults_payload.screenshots` tells; the pipeline
  knows nothing of them. Only `http`/`https`; a failure is a 502.
  `screenshots.Camera` is one browser on one thread, launched lazily, closed after
  a minute idle.
- **Every request is answered:** `do_GET`/`do_POST` end in `_send_failure` (an
  `ApiError`'s status, else 500). `server.main` refuses a bad
  `$BUY_AGENT_PROVIDER`/`RAIL`/`BACKEND` or out-of-range `--port` before binding;
  `_clashing_provider` hints only on `EADDRINUSE`.
- **Platform traps:** `server._CONTENT_TYPES` spells out MIME types (Windows can
  serve `.js` as `text/plain`); `_resolve` catches `OSError`/`ValueError`, and
  tests make `resolve()` raise rather than rely on platform inputs (ADR-0020).
- **The CSP coupling:** `_SECURITY_HEADERS` sets CSP `'self'`, `'unsafe-inline'`
  for styles only, so `ui/angular.json` keeps
  `optimization.styles.inlineCritical` off (its inline script would be blocked).
  Inline handlers or scripts and cross-origin requests break alike, visible only
  in a browser: `browser/` fails on any error the page reports (ADR-0087). ESLint
  refuses `on*` attributes and `javascript:` URLs in templates (ADR-0066).

### The components

Reasons are in `ui/README.md`. These must hold:

- No sentence about the model server is written in TS: `App.unreachable` is
  Python's `hint`; `App.asking` holds the in-flight `ModelSource`. Re-ask on load,
  on Check again, and after a contradicting run.
- `progress-log` is presentation; Download log only for failed or stopped runs.
  `App.reveal`/`showResults` scroll results into view (`nearest`), never to a
  failure naming a field.
- `dropped` (ADR-0055) and `changes`/`compared_with` (ADR-0060) are grouped and
  counted, never re-worded, and survive a re-sort; `movement` only colours and
  counts.
- A screenshot frame is drawn only when `screenshots` is set and the product
  links somewhere: `loading="lazy"`, 640x400 reserved, dropped by address if it
  fails.
- Buying takes two clicks; the second restates the *cart* (`pay_label`,
  `pay_merchant`, rail, whether anyone is charged). Pay buttons, rail and payment
  follow the form's paying settings as they stand (`payWith`), not the run's, and
  are held as a run is: while the form marks a paying box the card says so
  (`held`) in its button's place, and a payment refused on its endpoint is marked
  on that box, held against what it sent, its rail and the switch. A refused
  spend limit is one cart over it, a sentence beside the cards. The spend limit's
  hint names the results' currency (`countedIn`); an open confirmation closes
  when the switch, rail or `held` changes under it. A press moves the keyboard to
  its block's replacement (`LANDING`): the cart (never the buy button), Pay again,
  the wait, the receipt; focus moved elsewhere stays.
- Receipts are keyed by product name, never index.
- `pay`, the bounds and `alert_below` are not remembered in `localStorage`, and
  a bound an older build stored is not restored (ADR-0077). Every storage call is
  wrapped.
- A request-noticed bound fills its box once (`noticedNow`, `offered`) and marks
  nothing; its note sits above the box's hint. A submit waits for a reading on its
  way (`reading`, `held`); one that fills a box sends nothing, focuses the box,
  scrolls its field into view and says beside it nothing was searched yet
  (`stoppedAt`). `App` takes a failed reading as noticing nothing.
- `problems()` gates `canSubmit` on server ranges and the last sources answer.
  Disabled fields are neither checked nor sent; a switched-off number box shows
  empty so its placeholder reads. `notes()` adds the server's `rejected` field
  while `submitted` matches; `moved` tells `App` to drop the banner; `options()`
  builds the payload. A mark is `aria-invalid` plus `aria-describedby` from
  `problemId`; `numberTyped` reads `validity.badInput`.
- `numberFields` declares number boxes once, keyed like `limits_payload`;
  `settings` spreads `numberSettings(this.numberFields)`; `remembered` and
  `remembersBlank` are per row; `payingFields`/`settingFields` partition it.
- The model field marks, not hides ("not served", "embedding only", ADR-0032). A
  mark opens its Settings panel, once per change of marks.
- `ui/src/app/a11y.ts` runs `axe-core` over each component, rules enabled one at a
  time, each with a reason; an inconclusive rule fails. Colour is never the only
  carrier.

## Tests

`docs/testing.md` is the long form and the only place counts are written.

- **Seams:** `BuyAgent(config, llm=...)` with `tests/conftest.py`'s `FakeLLM`
  (one `answer` method); a chain stand-in has `invoke`;
  `create_server(agent_factory=..., allowed_hosts=..., camera=...)`, with
  `Photographer` as camera. `screenshots.Camera` takes `launch=`, so no test in
  `tests/` starts a browser. Components run in jsdom with `TestBed`,
  `AgentService` against a fake `EventSource`.
- **Network patch points:** `buy_agent.agent.search_web` and
  `buy_agent.agent.enrich` (only on `agent`, hence the source fan-out there),
  `buy_agent.search.DDGS`, `buy_agent.search.httpx.get`,
  `buy_agent.fetch.httpx.Client`, `rails.httpx.post`.
- **Model clients:** patch `providers.OllamaClient` (chat and `show`),
  `providers.openai.OpenAI`, `providers.httpx.get` (all listings), and assert on
  the *request*; `OllamaClient` is tested on `httpx.MockTransport` (ADR-0088).
  Never patch `DDGS.text`.
- **Table rows** compare by identity only through their module
  (`providers_module.OLLAMA`), as tests reload them.
- **No test in `tests/`** reaches the network, a model server or the real cache:
  autouse fixtures point `$BUY_AGENT_CACHE_DIR` at scratch and unset
  `$BUY_AGENT_AP2_KEY`, `$BUY_AGENT_AP2_MANDATE`, `$BUY_AGENT_MERCHANT_URL` and
  every variable `config.server_environment` reads.
- **Payment tests sign real mandates** and verify them with the SDK's verifier;
  CI installs the SDK.
- **`browser/`** (outside `testpaths`, ADR-0087) serves the build via
  `create_server`, agent stood in, to headless Chromium. Its tests fail on any
  error the page reports and wait with Playwright's `expect`, never sleeping;
  they skip without a build or browser, failing instead under CI's
  `$BUY_AGENT_REQUIRE_BROWSER`.
- **Server tests bind loopback** with `serve_forever(0.01)`; `raw()` reads until
  the declared body has arrived.
- **Nothing sleeps** but `tests/test_server.py` (`StubAgent.delay`); the
  environment changes only through `monkeypatch`.
- **Three workers** (`-n 3`, ADR-0076): no test leans on another's state or
  order, and one whose code answers before its thread ends waits for the thread.
- **Optional prerequisites skip, never fail:** `needs_powershell`, `needs_ap2`
  (`tests/conftest.py`, `skipif` only), the latter on the parametrised case that
  reaches signing.
- **Convention tests** (`tests/test_conventions.py`) hold rules between modules
  and across languages, and every path they name; **architecture tests**
  (`tests/test_architecture.py`) the import graph. `docs/testing.md` lists both.
- **`integration/`** (a real model, outside `testpaths`, ADR-0026) scores its
  shared run as the benchmark does, printed and kept pass or fail
  (`$BUY_AGENT_SCORECARD`, ADR-0072).
- **The benchmark** (ADR-0036, ADR-0070). After editing a case, its `PERFECT`
  scores exactly 1.000, its `SLOPPY` hits its pinned counts, every page mentioning
  a product is one its entry lists, and every judgement its pages pass is an
  entry's verdict or listed as about nobody (ADR-0073). A share with nothing to
  count is shown and floored, never scored: a new metric joins a pair in
  `scoring.PAIRS` or stands alone, any level luck reaches going in
  `scoring.CHANCE` (ADR-0074). A module coming to decide what a run reports or
  how it scores joins `pipeline.MODULES`; a setting coming to change what a model
  answers joins `pipeline.settings` (ADR-0075). Score a prompt or
  extraction-schema change on every case before and after, quoting both: the
  nightly gates only `headphones` (ADR-0089). The floors are a tripwire, raised
  only in their own commit, quoting runs. A contender is reached only through its
  provider row.
- **Scripts:** `scripts/start.ps1` and `setup.ps1` match `ci.yml`'s installs,
  and `ci.yml` checks through `preflight.ps1` (`tests/test_start_script.py`,
  `tests/test_setup_scripts.py`). `.gitattributes` is `* text=auto eol=lf`, binary
  kinds named.

`demo/` (`demo/README.md`) is not imported, covered, mutated or shipped.

## Environment

Development is on Windows with PowerShell as the default shell. Prefer
PowerShell syntax, or use the Bash tool explicitly for POSIX scripts.
