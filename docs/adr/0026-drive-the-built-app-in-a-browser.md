# ADR-0026: Drive the built app in a real browser, in a suite of its own

- **Status:** Accepted
- **Date:** 2026-08-26

## Context

Two suites cover this project to the line: `tests/` covers the Python side
against a stubbed model and a stubbed network, and `ui/`'s vitest suite covers
the Angular components in jsdom. Both are green on things the shopper cannot use.

Three kinds of failure live in the gap between them, and all three have happened
here:

- **The seam between the languages.** `api.py` sends a `price_label`; a card
  renders whatever label it is handed. `tests/test_conventions.py` closes part of
  this by reading `agent.types.ts` field for field, but a field that is spelled
  right and never reaches the page -- because the request that would have fetched
  it was refused, or the component that renders it was never mounted -- is a
  runtime `undefined` that neither suite executes.
- **The policy the page is served under.** `optimization.styles.inlineCritical`
  is off in `ui/angular.json` because Angular's critical-CSS inliner defers the
  global stylesheet with an inline `onload`, which `script-src 'self'` refuses to
  run: the sheet stays at `media="print"` and the app renders unstyled. Nothing
  in either suite loads `index.html` under the server's own headers, so the
  symptom is a blank-looking page and a green CI. The same shape returns for any
  inline handler, inline `<script>`, or request to another origin.
- **Layout.** jsdom has no layout engine. It will say a pill is in the document;
  it cannot say the pill is 40px wider than the phone the document is on. Two
  pills were: `.pill` is `white-space: nowrap` so that "4.7/5 (5,874 reviews)"
  never breaks in two, and two of the things wearing that class hold text that is
  not a figure -- an example chip is a whole shopping request, and a seller and a
  host are whatever the page called itself. At 320px the longest example carried
  the page 43px sideways and a long seller name carried a card 255px past the
  edge. Both had been in `main` for as long as the pills had.

The pipeline itself is not the gap. Its steps are covered, and a browser test
that ran the real thing would need Ollama and the network -- which no test in
this repository touches, and which would make the suite slow and non-deterministic
in exchange for nothing the existing tests do not already say.

## Decision

A third suite, `e2e/`, drives the built app in headless Chromium against a real
`create_server`, with the agent as the only stub.

- **The agent is the only stub.** `create_server(agent_factory=...)` -- the seam
  the server tests already use -- takes a scripted agent that logs its way
  through a run and answers with a fixed catalogue. Everything under it is the
  real thing: `api.py`'s payloads, the routing, the event stream, the built
  bundle, the security headers. What a run must never need is a model or the web.
- **It lives outside `pytest.ini`'s `testpaths`.** `python -m pytest` collects
  `tests/` and nothing else, so the default run, the coverage floor and the
  Saturday mutation run are untouched by a suite that needs a built UI and a
  browser. This one is asked for by name: `python -m pytest e2e`.
- **Missing prerequisites skip, they do not fail.** No Playwright, no Chromium or
  no `ui/dist/ui/browser` skips all of it with the command that fixes it. The
  suite is only meaningful when all three are there, and a red suite on a machine
  that never claimed to have them teaches nothing.
- **CI runs it on both platforms** (ADR-0020), in a third job that sets up both
  toolchains, builds the UI, installs Chromium and runs the suite. A page that
  fails is photographed and the screenshots are uploaded, because a browser
  failure on a runner otherwise reports a selector and a timeout.

## Consequences

The two bugs above are now regression tests: `e2e/test_layout.py` asserts that
neither the idle page nor a page of results scrolls sideways at 320px, 390px or
1280px, with the most awkward content a page can hand a card. That rule is
cheap to state and impossible to state anywhere else in this repository.

What it costs is a third job, about three minutes: `npm ci`, a build, a browser
download and 28 tests that take under a minute between them. The suite is slower
than both other suites put together, which is the reason it is not part of the
default run.

Four obligations:

- **Keep it out of `testpaths`.** Moved under `tests/`, it would be collected by
  every bare run, by `coverage`, and by the mutation run -- none of which have a
  browser, and the last of which would die at collection.
  `tests/test_conventions.py` asserts both halves of this, and that the CI job
  builds the UI before it points a browser at it.
- **The agent stays the only stub.** A test that reaches past it -- patching a
  component, injecting a service, stubbing `fetch` -- is a unit test in the
  slowest possible harness, and belongs in one of the other two suites.
- **Nothing sleeps except the scripted run's pacing.** `e2e/stub.py` spaces its
  log lines 0.2s apart because two tests are about what the page shows *while* a
  run is going. Every other wait is a Playwright condition on the page, so the
  suite is as fast as the app is rather than as slow as its worst guess.
- **A console error fails the test that provoked it.** The page fixture asserts
  a clean console on teardown: an exception in the browser is precisely the class
  of failure the other two suites cannot see, and it should not need a test to
  think to check for it.

ADR-0020's obligation that `ci.yml` set up exactly one Python and one Node is
about versions, not about how many jobs name them: this job needs both
toolchains, and the conventions test now reads the versions rather than counting
the lines. One Python and one Node is still what the `Dockerfile`,
`scripts/start.ps1` and `docs/testing.md` pin themselves to.
