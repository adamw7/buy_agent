# ADR-0013: Keep the UI a separate Angular workspace with its own toolchain

- **Status:** Accepted
- **Date:** 2026-08-25

## Context

The web UI arrived after the CLI, into a Python project with no Node in it. Two
shapes were possible: fold the front end into the Python build -- templates, or
a bundle checked in and served -- or keep it as an ordinary Angular workspace
that happens to live in the same repository.

Folding it in makes the Python side depend on Node: a contributor fixing a
ranking bug installs npm, and CI builds the UI to run a unit test about
arithmetic.

## Decision

`ui/` is a plain Angular 22 workspace -- its own `package.json`, its own tests
(vitest in jsdom), its own build. Nothing on the Python side needs Node at all.
The Python server serves the *built* output from `ui/dist/ui/browser`, which is
not checked in; without it the API still answers and the page is a 503 that says
how to build it (`--ui-dir` points at a build elsewhere).

CI runs the two as separate jobs: `coverage run -m pytest` on Python 3.13, and
`npm run test:coverage && npm run build` on Node 22.22.3.

Both suites have a floor, and the UI's is checked by a script
(`ui/scripts/check-coverage.mjs`, 98% of statements and lines) rather than by the
test runner's own config: the Angular unit-test builder reads a vitest config's
coverage *reporters* but does not fail a run on its `thresholds`, so a floor
declared there is not a floor.

Angular components are tested in jsdom with `TestBed`, and `AgentService` against
a fake `EventSource` rather than a live one -- the same "no network in tests" rule
the Python suite keeps.

## Consequences

Someone working on the pipeline never installs Node, and someone working on the
UI gets the ordinary Angular experience, including `npm start` on :4200 proxying
`/api` to :8000. A broken UI build cannot fail the Python job or hide a Python
failure.

The costs are two toolchains, two coverage floors, and one boundary that no
compiler checks -- the payload shapes crossing between `api.py` and
`agent.types.ts`, which is why the conventions test reads both (ADR-0012,
ADR-0014). Serving the UI also has a build step in front of it, so a fresh clone
running `python -m buy_agent.server` gets the 503 until it runs `npm run build`.
