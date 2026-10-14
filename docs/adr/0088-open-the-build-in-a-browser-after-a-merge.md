# ADR-0088: Open the build in a browser after a merge, not before it

- **Status:** Accepted
- **Date:** 2026-10-14

## Context

[ADR-0087](0087-open-the-build-in-a-browser-before-a-merge.md) added `browser/` and
ran it in `ci.yml`'s `browser` job "on every push and pull request". Everything else
it decided still stands: the tests, where they live, what they open and that a
missing browser fails rather than skips in CI. Only the pull request is changed.

On a pull request that job is the slowest part of `ci.yml`. It cannot start until the
`ui` job has built the page, and then installs Python, Playwright and Chromium (with
system libraries on Linux) to spend three seconds on tests. `automerge.yml` waits for
the whole of `ci.yml` (ADR-0086), so every pull request waited on it. What it guards is
narrow: the CSP, `inlineCritical`, `_CONTENT_TYPES` and the `failure` event. Pull
requests rarely touch these, and `scripts/preflight.ps1` already runs the same tests
locally before a push.

## Decision

**The `browser` job does not run on `pull_request`.** It keeps running on every push
to `main`, on the Saturday schedule and on `workflow_dispatch`, with Windows joining
the last two as before (ADR-0037). The job carries
`if: github.event_name != 'pull_request'`, and nothing else in it changes.

## Consequences

A pull request's CI ends with the `ui` and `test` jobs, and `automerge.yml` can merge
it without the page having been opened in a browser. A merge that breaks something only
a browser refuses now turns `main` red on its own push instead of holding the pull
request. A release still needs it green, because `release.yml` only builds from a commit
whose `ci.yml` push run passed (ADR-0030), and that run includes the job. The same goes
for a Playwright bump, which ADR-0087 had checked in a browser before it merged.

What it obliges:

- **Open the build before pushing a change to what only a browser checks.**
  `scripts/preflight.ps1` runs `browser/` after the UI's steps. For a branch that
  touches the CSP, `angular.json`, `_CONTENT_TYPES` or the event stream, dispatch
  `ci.yml` on it, as ADR-0037 already asks for paths, encodings and sockets.
- **The job stays off pull requests and on everything else**, held by
  `test_the_browser_job_does_not_run_on_a_pull_request`. A condition that also caught
  `push` would leave the build unopened anywhere in CI.
- The `ui` job still uploads its build on a pull request, where nothing downloads it.
  That costs one short-lived artifact, and keeps one shape for the job everywhere.
