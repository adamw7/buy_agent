# ADR-0087: Open the build in a browser every Sunday

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

The tests were first a third job in `ci.yml`, opening the build the `ui` job kept. Run
that way on a branch, they passed on both platforms, and cost every pull request a job
after the `ui` job's -- about forty seconds on Linux, fifteen of them installing
Chromium -- for four rules that rarely move. Windows spent three minutes installing it.

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

**`.github/workflows/browser.yml` runs them on Sundays, and on demand, never on a pull
request.** It builds the UI from the lockfile as `ci.yml` does, sets up Python, installs
`requirements-dev.txt` and `requirements-screenshots.txt`, and the headless shell
`screenshots.INSTALL` names, with `--with-deps` for the libraries a Linux runner lacks.
It runs on Linux and on Windows, whose registry is the MIME-type trap.
`BUY_AGENT_REQUIRE_BROWSER` turns a missing build or browser from a skip into a failure
there, as `BUY_AGENT_REQUIRE_OLLAMA` does nightly. They are not part of the gate, so
`scripts/preflight.ps1` and `scripts/setup.ps1` neither run nor install them.

## Consequences

A pull request waits on nothing new. The cost is when a break is seen: a change that
trips one of these rules merges green and is reported on the Sunday after, up to a week
late. A branch that touches `_SECURITY_HEADERS`, `_CONTENT_TYPES`, `ui/angular.json` or
the event stream should run `python -m pytest browser` on a fresh build, or dispatch
`browser.yml` on itself, before it merges -- the advice `CLAUDE.md` gives for Windows
and paths (ADR-0037). A Playwright release Renovate proposes is first run against a
browser on the Sunday after it lands, which narrows rather than closes the gap ADR-0065
named; the camera itself is still photographed by nobody, since these tests leave
`create_server`'s `camera` unset.

What it obliges:

- **`browser/` is a top-level tree like `integration/`**: left out of the image by
  `.dockerignore`, copied by mutmut's `also_copy`, named by the architecture rule on the
  trees that import the package, exempt from the coverage floor in `_UNMEASURED`, and
  held to the suite's rules -- nothing sleeps, so a test waits with Playwright's
  `expect`, and the environment changes only through `monkeypatch`.
- **The run is held to its purpose** by
  `test_a_normal_run_cannot_collect_the_tests_that_open_a_browser`,
  `test_the_sunday_run_runs_the_browser_tests_a_normal_run_leaves_out`,
  `test_the_sunday_run_refuses_to_pass_by_skipping`,
  `test_the_sunday_run_builds_the_page_before_opening_it`,
  `test_the_browser_run_is_on_sundays_and_on_both_platforms` and
  `test_the_nightly_run_is_never_a_gate_on_a_pull_request`, which now names it.
- **It is a workflow like the others**: the Python and Node `ci.yml` pins, a read-only
  token, and a pip cache keyed on every file it installs from, which the rules over
  every workflow already check.

What it does not cover: a path the two searches never reach -- paying, a re-sort, a
picture -- can still add an inline handler only a browser refuses, and axe's contrast
and target-size rules, which jsdom cannot run, are still not run here. The light theme
fails the first today (the score breakdown's weights, 4.27:1), so turning it on is a
change of its own.
