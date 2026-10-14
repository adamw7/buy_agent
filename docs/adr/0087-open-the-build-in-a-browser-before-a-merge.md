# ADR-0087: Open the build in a browser before a merge

- **Status:** Accepted
- **Date:** 2026-10-14

## Context

[ADR-0065](0065-photograph-each-products-page-from-a-server-bound-to-this-machine.md)
gave the server a camera, and said in its consequences that "no test starts a browser,
and nothing on a schedule does either". Everything else it decided still stands. That
sentence is what has changed.

Several rules in this project hold only in a browser, and `CLAUDE.md` says so where it
states them: the CSP `server._SECURITY_HEADERS` sends, which refuses an inline handler
or script ("only a browser shows it"); `inlineCritical`, off in `ui/angular.json` because
the policy blocks what it writes; `server._CONTENT_TYPES`, which spells out MIME types
because Windows' registry can serve `.js` as `text/plain`; and the event stream, whose
failure event is `failure` because an `EventSource` reconnects on `error` (ADR-0011).
Nothing checked any of them. The UI's specs run the components in jsdom, never the build
and never under the policy. The release's smoke test asks `curl` whether `/` answers,
which it does whatever the page then does. The preflight skill's answer was to load the
page by hand after touching the policy or `angular.json`.

Each of the four was broken on purpose and run through everything the gate has:

- `inlineCritical: true` -- which Angular 22 now writes as an inline `<script>` rather
  than an `onload` -- is refused by the policy, and the page is left in the browser's
  own serif.
- `.js` served as `text/plain` is refused as a module script, and the app never starts.
- `.css` served as `text/plain` is refused, and the page is unstyled.
- The failure sent as `error` shows "Lost the connection to the agent" instead of what
  failed.

Every unit test passed every time, and the release's `curl` would have published each.

## Decision

**`browser/` holds tests that open the build in a real Chromium**, served by the real
server (`create_server`, on loopback) with the agent stood in for, through Playwright --
the library `requirements-screenshots.txt` already pins for the camera. They fail on any
error the page reports (a refused handler, script or stylesheet is reported there and
nowhere else), check that `styles.css`'s tokens apply, run one search through the event
stream and one that fails, and hold the cards to the order the run ranked them in.

**The directory sits outside `testpaths`, as `integration/` does (ADR-0026).** `tests/`
still starts no browser, a bare `pytest` stays the seconds it is, and the camera is
still reached there only through `launch=` and a `Photographer`.

**`ci.yml` runs them in a third job, `browser`, on every push and pull request.** It
waits for the `ui` job and opens the build that job kept, so the page tested in a
browser is the one whose specs, lint and type check just passed. It sets up Python,
installs `requirements-dev.txt` and `requirements-screenshots.txt`, and the headless
shell `screenshots.INSTALL` names, with `--with-deps` for the libraries a Linux runner
lacks. Windows joins weekly, as every job does (ADR-0037). `BUY_AGENT_REQUIRE_BROWSER`
turns a missing build or browser from a skip into a failure there, as
`BUY_AGENT_REQUIRE_OLLAMA` does nightly. `scripts/preflight.ps1` runs them after the
UI's steps, and `scripts/setup.ps1` installs Playwright and Chromium.

## Consequences

A pull request waits on a third job after the second: installing Python, Playwright
and Chromium (with its system libraries on Linux), then three seconds of tests. A Playwright release Renovate
proposes now runs against a browser before it merges, which ADR-0065 named as the gap
the camera had; the camera itself is still photographed by nobody, since these tests
leave `create_server`'s `camera` unset.

What it obliges:

- **`ci.yml` sets up Python and Node in two jobs each.**
  `test_ci_sets_up_one_python_and_one_node` now holds every declaration to the same
  version rather than the file to one line, because the setup script, the session hook
  and the Dockerfile's test read the first.
- **The mypy cache is keyed on what the job running mypy installs**, not on the whole
  workflow: Playwright is installed only where the browser tests run
  (`test_mypys_cache_is_keyed_on_everything_that_moves_its_answers`).
- **`browser/` is a top-level tree like `integration/`**: left out of the image by
  `.dockerignore`, copied by mutmut's `also_copy`, named by the architecture rule on the
  trees that import the package, exempt from the coverage floor in `_UNMEASURED`, and
  held to the suite's rules -- nothing sleeps, so a test waits with Playwright's
  `expect`, and the environment changes only through `monkeypatch`.
- **The job is held to its purpose** by
  `test_a_normal_run_cannot_collect_the_tests_that_open_a_browser`,
  `test_ci_runs_the_browser_tests_a_normal_run_leaves_out`,
  `test_the_browser_job_refuses_to_pass_by_skipping` and
  `test_the_browser_job_opens_the_build_the_ui_job_tested`.

What it does not cover: a path the two searches never reach -- paying, a re-sort, a
picture -- can still add an inline handler only a browser refuses, and axe's contrast
and target-size rules, which jsdom cannot run, are still not run here. The light theme
fails the first today (the score breakdown's weights, 4.27:1), so turning it on is a
change of its own.
