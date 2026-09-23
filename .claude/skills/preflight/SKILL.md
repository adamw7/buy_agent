---
name: preflight
description: Run the full gate CI applies -- both test suites, both coverage floors, the linter and the type checker -- before committing or pushing. Use when asked to check, verify, or validate a change, when finishing work on a branch, or before opening a pull request. Not for a single failing test, which is faster run directly.
---

# Preflight

`.github/workflows/ci.yml` runs two jobs on every push to `main` and every pull
request, on Linux; Windows runs the same two on Saturdays and on a manual run
(ADR-0037). This is the same gate, locally. Run it from the repository root with
`.venv` active.

## Python (Python 3.14)

```powershell
python -m coverage run -m pytest
python -m coverage report
python -m pylint buy_agent
python -m mypy buy_agent
```

- The floor is `fail_under = 99` over branches as well as lines. The suite covers
  essentially every line, so a drop is a new branch with no test, not slack.
- Expect a few seconds. A run that takes much longer means something is reaching
  the network; find it rather than waiting it out.
- Two optional prerequisites decide how many tests run, and neither absent one
  is a failure: without `pwsh` or `powershell`, `tests/test_start_script.py`
  skips on `needs_powershell`, and without the optional AP2 SDK the paying tests
  skip on `needs_ap2` -- which does fail the gate, on coverage rather than on a
  red test, the floor being unreachable with them sitting out. Add the SDK with
  `pip install -r requirements-ap2-deps.txt` and then `pip install --no-deps -r
  requirements-ap2.txt`; the flag belongs to the second command only.
  What a run should say is in `docs/testing.md`, which is the one place those
  counts are written down: a second copy here only ever falls behind it.
- `pytest.ini` sets `testpaths = tests`, so a bare run cannot reach
  `integration/`. That is deliberate -- see below.
- pylint runs over `buy_agent/` and nothing else, and has to come out at 10.00
  with no message at all: `.pylintrc` turns off the checks this project has
  answered differently, loads fifteen of the optional checkers pylint does not run
  by itself, and every remaining message is suppressed on its own line with its
  reason (ADR-0048, ADR-0049). A new message is a line to fix or a pragma to
  write, not a number to let slip.
- mypy runs over that same package, from the same directory, and has to come out
  with no error at all. `setup.cfg` holds the settings: the default checks rather
  than `strict`, `warn_unused_ignores` beside them, and `ignore_missing_imports`
  for the five libraries neither tool here can read (ADR-0063). An error is an
  annotation that is not true or a `# type: ignore` that has stopped being one --
  fix the declaration rather than widening it, which is what the pragmas above
  are for.

## UI (Node 22.23.2)

```powershell
cd ui
npm run test:coverage
npm run build
npm run lint
npm run format:check
```

- The floor is `coverageThresholds` on the test target in `ui/angular.json`: 98%
  of statements and lines, and deliberately no branch floor. Do not add one.
- `npm run build` is part of the gate, not an extra: a template error is
  invisible to the unit tests -- and it is a *type* check as well as a build:
  `ui/tsconfig.json` sets `strict` and `strictTemplates` and
  `ui/tsconfig.app.json` adds
  `noUncheckedIndexedAccess` for the shipped half, so a payload's nullable half
  reaching a component that does not handle it stops the build, and so does a
  binding handing an input a type it cannot hold.
- `npm run lint` is ESLint with `angular-eslint` over `ui/src`, templates included,
  and has to come out with no message at all, warnings included:
  `ui/eslint.config.mjs` says which rules run and why the ones left off are off,
  and every suppression is an HTML or a line comment on its own lines with its
  reason -- one nothing needs any more fails too (ADR-0066). It is the one check
  here that sees an inline event handler, which the CSP refuses in a browser and
  nothing else notices.
- `npm run format:check` is Prettier reading rather than writing: `npm run
  format` is the same glob with `--write`, which is what to run when this step is
  what went red. The Python half has a linter and no formatter: pylint above, run
  from the repository root so it finds `.pylintrc`.

## What this does *not* cover

- `pytest integration` needs a real Ollama and the model pulled
  (`ollama pull qwen3:0.6b`). It runs nightly, never on a pull request. Run it
  by hand only when a change touches the prompts, the schema or the decoding.
- `python -m mutmut run` is the Saturday job, and `npx stryker run` -- from the
  directory `npm test` is run in -- is the front end's own an hour later
  (ADR-0061). Only worth running locally when
  the change is to logic the suite might be exercising without asserting on --
  and the second of them is ninety minutes, so narrow `mutate` to the file in
  hand rather than waiting on the whole front end.
- CSP and inline-critical-CSS breakage mostly takes a browser -- neither suite
  sees it, and the linter catches only an inline handler or a `javascript:` URL
  in a template. If the change touched `_SECURITY_HEADERS`, `ui/angular.json`, or
  added an off-origin request, load the page.
- `docs/ui.png` and the recordings in `demo/` are not regenerated by anything
  here. A visible change to the form leaves them stale.

## If it fails

Fix it before pushing rather than after: a red push costs two runners and a
cycle, and the half of the matrix that would have caught a platform difference
does not run until Saturday -- dispatch `CI` on the branch if the change touched a
path, an encoding, a socket or `scripts/start.ps1`.
Convention-test failures in particular are telling you a change landed on one
side of a boundary and not the other -- read what the test's docstring says the
rule is before changing the test.
