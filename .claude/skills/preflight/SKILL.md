---
name: preflight
description: Run the full gate CI applies -- both test suites, both coverage floors, the linter and the type checker -- before committing or pushing. Use when asked to check, verify, or validate a change, when finishing work on a branch, or before opening a pull request. Not for a single failing test, which is faster run directly.
---

# Preflight

`.github/workflows/ci.yml` runs two jobs on Linux for every push to `main` and
every pull request, and on Windows weekly (ADR-0037). `scripts/preflight.ps1` runs
both, step for step in `ci.yml`'s order, each stopping at its first failure:

```powershell
.\scripts\preflight.ps1               # both halves
.\scripts\preflight.ps1 -Only python  # ...or one, for a change that touched one
```

Run it from the repository root; it finds `.venv` itself. `scripts/setup.ps1`
installs everything it needs, the AP2 SDK included (ADR-0067).

## Python (Python 3.14)

```powershell
python -m coverage run -m pytest
python -m coverage report
python -m pylint buy_agent
python -m mypy buy_agent
```

- The floor is `fail_under = 99` over branches and lines; a drop is a new branch
  with no test.
- Expect a few seconds. Much longer means something reaches the network.
- Without `pwsh`, `tests/test_start_script.py` skips on `needs_powershell`.
  Without the AP2 SDK the paying tests skip on `needs_ap2`, and the coverage
  floor fails. Add it with `pip install -r requirements-ap2-deps.txt`, then `pip
  install --no-deps -r requirements-ap2.txt`. Expected counts are in
  `docs/testing.md` only.
- `testpaths = tests`, so `integration/` is never reached.
- pylint (ADR-0048, ADR-0049) and mypy (ADR-0063) must report nothing at all. Fix
  the line or the declaration, or suppress on the line with a reason.

## UI (Node 22.23.3)

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

## What this does *not* cover

- `pytest integration` needs Ollama and `ollama pull qwen3:0.6b`. Run it when a
  change touches the prompts, the schema or the decoding.
- `python -m mutmut run` and `npx stryker run` (ADR-0061) are weekly; narrow
  Stryker's `mutate` to the file in hand.
- CSP and critical-CSS breakage needs a browser. Load the page after touching
  `_SECURITY_HEADERS`, `ui/angular.json` or adding an off-origin request.
- `docs/ui.png` and the recordings in `demo/` are not regenerated.

## If it fails

Fix it before pushing. Dispatch `CI` on the branch if the change touched a path,
an encoding, a socket or `scripts/start.ps1`. A convention test failing means a
change landed on one side of a boundary: read its docstring before changing it.
