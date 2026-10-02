# Tests

Two suites, one per language, and the checks around them. Everything the
[README](../README.md) leaves out.

```powershell
.\scripts\setup.ps1           # what every line below needs, the AP2 SDK included
.\scripts\preflight.ps1       # everything ci.yml checks, both halves, in its order

python -m pytest              # whole suite
python -m pytest tests/test_ranking.py::test_cheaper_wins_when_rating_is_equal

python -m coverage run -m pytest ; python -m coverage report   # with coverage
python -m pylint buy_agent    # the linter, from the repository root
python -m mypy buy_agent      # the type checker, from the same place

cd ui; npm test               # the UI's own tests, in jsdom
cd ui; npm run test:coverage  # the same, with a coverage floor
cd ui; npm run lint           # ESLint and angular-eslint, templates included

python -m pytest integration  # against a real Ollama; see below

python -m benchmark --scripted perfect   # the benchmark, with no model at all
python -m benchmark                      # ...and against whatever is serving
```

## The unit suites

2910 Python tests and 318 UI tests. Neither touches the network or a model
server:

- the model is faked through `BuyAgent(llm=...)`, a class with one `answer`
  method, which is the whole of `chat.ChatModel`;
- search and fetch are patched on `buy_agent.agent`, and the backends' own tests
  patch `buy_agent.search.DDGS` and `buy_agent.search.httpx.get` (ADR-0057);
- the clients `buy_agent.providers` builds are patched where it imports them;
- the server tests hand `create_server` a stub agent and a stand-in camera, and
  `screenshots.Camera` takes `launch=`, so no test starts a browser (ADR-0065).

The only real sockets are loopback ones. `conftest.py` points
`$BUY_AGENT_CACHE_DIR` at a scratch directory per test (ADR-0044) and unsets
`$BUY_AGENT_AP2_KEY`, `$BUY_AGENT_AP2_MANDATE` and `$BUY_AGENT_MERCHANT_URL`, so a
developer's paying setup never reaches the suite.

The payment tests sign real SD-JWT mandates with keys made in the test and read
them back through the AP2 SDK's own verifier. That needs `pip install -r
requirements-ap2-deps.txt` and then `pip install --no-deps -r
requirements-ap2.txt` (`--no-deps` on the second only, or `cryptography` arrives
without `cffi`). The HTTP rail's transport is patched in `buy_agent.rails`.

### What skips

Optional prerequisites skip, never fail:

- **`needs_ap2`** (84 tests) asks `mandates.available()` once, and goes on the
  parametrised case that reaches signing rather than the whole function. Both
  workflows install the SDK, so nothing skips where it matters, and the coverage
  floor cannot be met without it.
- **`needs_powershell`** skips 16 of the 22 tests in `tests/test_start_script.py`
  and all 4 in `tests/test_setup_scripts.py` without `pwsh` or `powershell`.
- `tests/test_session_hook.py` (6) skips on Windows, as do the 2 `needs_tzset`
  tests in `tests/test_journal.py`, which move `$TZ` and need `time.tzset`.

So the SDK without PowerShell reads `2923 passed, 20 skipped`, and
`requirements-dev.txt` alone reads `2839 passed, 104 skipped`.

### `pytest.ini`

- **Deprecation warnings are errors**, in both suites, so a pin moving is the run
  that says a call needs changing. A warning a dependency raises about itself gets
  an `ignore` line naming the message, the module and the upgrade that removes it.
- **`timeout = 60`** per test, via
  [pytest-timeout](https://pypi.org/project/pytest-timeout/). Nothing sleeps, so a
  minute catches a stopped test (a fetch that reached the real web, a socket
  nobody answers), not a slow one. Linux fails the test; Windows dumps every stack
  and ends the process.
- The live tests use `integration.LIVE_TIMEOUT_SECONDS` (120), which a convention
  test holds above the unit cap and below `integration.yml`'s five minutes.

### Pylint and mypy

Both run after the tests in the Python job, over the package `.coveragerc` names
in `source` and `setup.cfg` mutates, and both must report nothing at all.

**Pylint** (ADR-0048). The tests are not linted: fixtures shadow their own names
by design. `.pylintrc` turns off three checks (docstrings on every function, too
few public methods, too many dataclass fields), each with its reason. Anything
else is suppressed on its line with a sentence above it; a convention test fails
a suppression with no reason and `useless-suppression` fails a stale one. Fifteen
of pylint's twenty-five optional checkers are loaded, each stating a rule the
package already follows; the ten left out carry their reasons in the same file
(ADR-0049).

**Mypy** (ADR-0063), configured in `setup.cfg [mypy]`: default checks plus
`warn_unused_ignores`, not `strict`. The libraries neither tool can read (the AP2
SDK and the two it signs with, Playwright and `lxml`) are listed once per tool,
as `ignore_missing_imports` and as `ignored-modules`/`extension-pkg-allow-list`,
and a convention test holds the two lists together.

### The UI's checks

- **`npm run build` is the type check.** `ui/tsconfig.json` sets `strict` and
  `strictTemplates`; `ui/tsconfig.app.json` adds `noUncheckedIndexedAccess` for
  shipped code only. Since the specs compile without it, the maps a template looks
  up by payload key (`App.receipts`, the form's `limits` and `placeholders`)
  declare `| undefined` themselves.
- **`npm run lint`**: ESLint with `typescript-eslint` and `angular-eslint` over
  `src/`, templates and specs included, configured like `.pylintrc` (ADR-0066).
  `no-restricted-syntax` refuses `on*` attributes and `javascript:` URLs, which
  the CSP would block and jsdom would not.
- **`npm run format:check`**: Prettier; `npm run format` fixes it.
- **Accessibility**: `ui/src/app/a11y.ts` runs `axe-core` over each component in
  `TestBed`, with rules enabled one at a time, each with its reason. An
  inconclusive rule fails. Contrast and target size need a real browser and stay
  out. `ui/README.md` has the detail.

### Coverage

The Python floor is 99% of lines and branches (`.coveragerc`) over `buy_agent/`
in `source` plus `benchmark/` and `scripts/` in `source_dirs`, which are measured
but not mutated, linted or type-checked (ADR-0064). The UI's floor is 98% of
statements and lines in `ui/angular.json`; there is no branch floor, because v8
attributes template branches to unreachable positions.

### Convention tests

Coverage cannot see rules that hold *between* files, so
`tests/test_conventions.py` asserts them (ADR-0014):

- the three failure modes are handled in all three places, and a sort criterion,
  provider, rail and backend is offered everywhere;
- both front ends hold numbers to the same ranges, the form takes them off the
  server, and `ui/src/app/agent.types.ts` mirrors every payload (ADR-0033);
- the `Dockerfile`, `.dockerignore`, workflows, release, nightly, mutation and
  audit settings agree with each other and with CI's pins;
- everything a release publishes is scanned before it is pushed and attested,
  and only a job that attests can mint a token naming the run (ADR-0069);
- ADRs are indexed, skills name real paths, and every Markdown link resolves;
- the tsconfig strictness stays on, and every CSS token read is declared, with a
  dark value and no fallback;
- requirements match imports; pylint and mypy read the package the other tools
  measure, and no suppression lacks a reason;
- every `*Error` is raisable, and only the `__main__` guard calls `sys.exit`;
- no test is switched off, nothing sleeps but `tests/test_server.py`, and the
  environment changes only through `monkeypatch`;
- every module logs under the package's name in deferred form, leaving stdout to
  the report.

`tests/test_logging_contract.py` checks that the eight heuristics that take
something away say how many at INFO and which at DEBUG, and that the five that
remove a whole product call `record` while the three that blank do not (ADR-0055).

### Architecture tests

`tests/test_architecture.py` asserts the import graph with
[ArchUnitPython](https://github.com/LukasNiessen/ArchUnitPython) (ADR-0047):
thirty-two rules, each listed in `CLAUDE.md`. Every rule uses
`ignore_type_checking_imports=True`, since an import under `if TYPE_CHECKING:`
never runs. A negated rule over nothing passes, so the helpers naming modules
check each exists, and a thirty-third test checks every module sits in exactly
one layer. Edges inside a layer are skipped, which is why the two doors, the
seams under the journal and the domain under `money.py` have rules of their own.

Two of those rules and five convention tests are
[ArchUnit](https://www.archunit.org) rules from
[adamw7/tools](https://github.com/adamw7/tools): no process, no socket, `*Error`
raisable, only the guard exits, no disabled test, no sleeping, `monkeypatch` only.

### Windows

`ci.yml` runs both jobs on Linux for pushes and pull requests, and adds Windows
on the Saturday 04:09 UTC schedule and on `workflow_dispatch`, with `fail-fast`
off (ADR-0020, ADR-0037). Dispatch a branch that touches paths, encodings,
sockets, MIME types or `start.ps1`.

### The PowerShell scripts

`tests/test_start_script.py` parses `scripts/start.ps1`, dot-sources its
functions, runs them against a stubbed clock and web request, and reads back JSON.
`tests/test_setup_scripts.py` does the same for `scripts/setup.ps1` and
`scripts/preflight.ps1`; what they install and check is held against `ci.yml` in
`tests/test_conventions.py` (ADR-0067).

## Integration tests

The unit tests raise Ollama's errors themselves, so the claims about Ollama --
that a schema-compiled grammar makes `"N/A"` impossible (ADR-0004), that
`reasoning=False` and `num_ctx=16384` let a thinking model answer (ADR-0019),
that a stopped server raises raw `httpx` errors -- are checked in `integration/`
on a model small enough for a CPU (ADR-0026):

```powershell
ollama pull qwen3:0.6b
python -m pytest integration
```

The model is real and the web is `benchmark/corpus.py`'s ten pages, condensed by
the real `fetch.condense` on the real budgets so the prompt is production-shaped
and production-sized. One session-scoped run is shared by all 31 tests, which
assert what holds whatever the model said: every name, figure, quote and link is
in the sources, currencies travel with prices, nothing is listed twice, the
ranking is ordered. Two smoke tests check that something was extracted and
quoted, so the rest are not vacuous.

| Variable | Effect |
| --- | --- |
| `BUY_AGENT_TEST_MODEL` | Test against another tag instead of `qwen3:0.6b` |
| `BUY_AGENT_REQUIRE_OLLAMA` | Fail where Ollama is absent, instead of skipping |

`.github/workflows/integration.yml` sets the second, runs at `41 3 * * *` and on
demand, never on a pull request, and caps itself at **five minutes**. Ollama and
the model are deliberately unpinned: noticing a release that changes decoding is
half of what the job is for.

## The benchmark

The live tests ask whether the promises held; `benchmark/` asks whether a change
made things *better*, by scoring a run against a key (ADR-0036):

```powershell
python -m benchmark --scripted perfect   # no model, no network: scores 1.000
python -m benchmark --scripted sloppy    # the same, wrong in eight ways
python -m benchmark -v --json score.json # against a real model, keeping the numbers
```

`benchmark/answers.py` records, per product, the **sets** of `(price, currency)`
and `(rating, review_count)` the pages print, so any printed figure counts and a
mispairing does not (ADR-0022). `benchmark/scoring.py` scores eight shares in
`[0, 1]`:

| Metric | What it counts |
| --- | --- |
| `identified` | Slots filled with a product that is really there |
| `genuine` | Reported entries that are a real product, not a shop and not a repeat |
| `figures` | Price, rating and review count reported *and* printed for that product |
| `attribution` | The other half: figures printed for somebody else |
| `links` | Products pointed at a page that is about them (ADR-0017) |
| `quotes` | Products carrying a verdict a page about them printed (ADR-0024) |
| `faithful` | The other half: quotes that are not verbatim on such a page |
| `order` | Whether the ranking came out in the order the key's own figures give |

Pairs are split so that reporting nothing and reporting nonsense do not score
alike. `integration/test_benchmark.py` fails a metric under
`benchmark.scoring.FLOORS`. The floors are a **tripwire, not a target**; raise one
only in its own commit, quoting runs.

`tests/test_benchmark.py` keeps the key honest without a model: every figure in it
must survive condensing, `PERFECT` must score exactly 1.000, and `SLOPPY` must hit
its pinned counts. **Editing the corpus means re-running both.**

## Mutation testing

[mutmut](https://github.com/boxed/mutmut) runs over `buy_agent/` in
`.github/workflows/mutation.yml` every Saturday at 05:17 and on demand, never on a
pull request (ADR-0016). It fails only under 75%, and sits at about 84%; many
survivors are equivalent (help text, log wording). Settings, including
`also_copy`, are in `setup.cfg`:

```powershell
pip install -r requirements-mutation.txt
python -m mutmut run                        # a couple of minutes; results cached
python -m mutmut results --all true > mutation-results.txt
python scripts/mutation_report.py mutation-results.txt
python -m mutmut browse                     # or read them one mutant at a time
```

A run tests a copy in `mutants/` (ignored by git) whose modules are wrapped in
mutmut's trampoline. So tests that read source use `conftest.SOURCE_ROOT`, files
read from outside the package are listed in `also_copy`, and no test reads a
declaration such as `__kwdefaults__` off a function object -- the trampoline
strips it.

### The front end's own run

[Stryker](https://stryker-mutator.io/) runs over `ui/src/app` in
`.github/workflows/mutation-ui.yml` at `23 6 * * 6` and on demand (ADR-0061). It
runs the real `ng test` once per mutant -- about 1031 mutants and a hundred
minutes on four cores:

```powershell
cd ui
npx stryker run                     # ~100 minutes; reports/mutation/ is the output
cd ..
python scripts/mutation_report.py ui/reports/mutation/mutation.json
```

`ui/stryker.config.mjs` says what is mutated and what is excluded by name
(`agent.types.ts`, `testing.ts`, `app.config.ts`). `ui/tsconfig.mutation.json`
turns off only the template checks, declaring no `compilerOptions`.
`scripts/mutation_report.py` holds one `Tool` row per tester, each with its
statuses and floor.

## The dependency audit

Renovate keeps pins fresh; `.github/workflows/audit.yml` asks whether what is
pinned is known to be broken today, nightly at `47 2 * * *` and on demand
(ADR-0062):

```powershell
pip install -r requirements-audit.txt
python -m pip_audit -r requirements.txt -r requirements-dev.txt `
  -r requirements-mutation.txt -r requirements-ap2-deps.txt `
  -r requirements-audit.txt
cd ui; npm audit --audit-level=high
```

`requirements-ap2.txt` is exempt: it is installed `--no-deps`, so its metadata
pins describe nothing installed. A convention test fails a list nothing reads and
an exemption naming a missing file. pip-audit has no severity threshold; npm uses
`high`, since its lockfile is mostly build-time transitives. On pull requests,
`actions/dependency-review-action` checks only what the change adds, at the same
`high`. An unfixable advisory is suppressed with `--ignore-vuln` and a sentence
saying why, never `|| true`.
