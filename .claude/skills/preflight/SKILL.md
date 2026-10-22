---
name: preflight
description: Run the full gate CI applies -- both unit suites, both coverage floors, the linter, the type checker and the built page in a browser -- before committing or pushing. Use when asked to check, verify, or validate a change, when finishing work on a branch, or before opening a pull request. Not for a single failing test, which is faster run directly.
---

# Preflight

`.github/workflows/ci.yml` runs three jobs on Linux for every push to `main` and
every pull request, and on Windows weekly (ADR-0037). `scripts/preflight.ps1` runs
all three, step for step in `ci.yml`'s order, each stopping at its first failure:

```powershell
.\scripts\preflight.ps1               # all three
.\scripts\preflight.ps1 -Only python  # ...or some, for a change that touched one
```

Run it from the repository root; it finds `.venv` itself. `scripts/setup.ps1`
installs everything it needs, the AP2 SDK included (ADR-0067).

## Python (Python 3.15)

```powershell
python -m pytest -n 3 --cov
python -m pylint buy_agent
python -m mypy buy_agent
```

- The tests run in three workers, and pytest-cov prints the table and holds the
  floor, `fail_under = 99` over branches and lines; a drop is a new branch with
  no test (ADR-0076).
- Expect a few seconds. Much longer means something reaches the network. A
  failure only under `-n 3` is a test leaning on another's state or on a thread
  it never waited for.
- Without `pwsh`, `tests/test_start_script.py` skips on `needs_powershell`.
  Without the AP2 SDK the paying tests skip on `needs_ap2`, and the coverage
  floor fails. Add it with `pip install -r requirements-ap2-deps.txt`, then `pip
  install --no-deps -r requirements-ap2.txt`. Expected counts are in
  `docs/testing.md` only.
- `testpaths = tests`, so `integration/` is never reached.
- pylint (ADR-0048, ADR-0049) and mypy (ADR-0063) must report nothing at all. Fix
  the line or the declaration, or suppress on the line with a reason.

## UI (Node 24.22.1)

```powershell
cd ui
npm run test:coverage
npm run build
npm run lint
npm run format:check
```

- The floor is 98% of statements and lines in `ui/angular.json`, with no branch
  floor. Do not add one.
- `npm run build` is the type check (`strict`, `strictTemplates`,
  `noUncheckedIndexedAccess`).
- `npm run lint` must report nothing, warnings included (ADR-0066); it is the one
  check that sees an inline handler the CSP would refuse.
- `npm run format` fixes what `format:check` refuses.

## Browser

```powershell
python -m pytest browser
```

- The build `npm run build` just made, opened in headless Chromium behind the real
  server and its CSP, with the agent stood in for (ADR-0087). It fails on anything
  the page reports as an error: an inline handler or script the CSP refuses, a
  stylesheet or script served as the wrong type, a broken event stream.
- It skips without a build or without Playwright; `scripts/setup.ps1` installs
  both Playwright and the headless Chromium. CI sets `BUY_AGENT_REQUIRE_BROWSER`, so
  there it fails instead.
- `testpaths = tests`, so a bare `pytest` never reaches it.

## What this does *not* cover

- `pytest integration` needs Ollama and `ollama pull qwen3:0.6b`. Run it when a
  change touches the prompts, the schema or the decoding.
- `python -m mutmut run` and `npx stryker run` (ADR-0061) are weekly; narrow
  Stryker's `mutate` to the file in hand.
- The browser tests load the page and run one search; a new off-origin request or
  an inline handler on a path they never reach still needs the page loaded by hand.
- The pictures in `docs/` and the recordings in `demo/` are not regenerated.

## If it fails

Fix it before pushing. Dispatch `CI` on the branch if the change touched a path,
an encoding, a socket or `scripts/start.ps1`. A convention test failing means a
change landed on one side of a boundary: read its docstring before changing it.
