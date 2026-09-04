# ADR-0037: Run the Windows half of CI weekly rather than on every push

- **Status:** Accepted
- **Date:** 2026-09-04

## Context

[ADR-0020](0020-test-on-windows-as-well-as-linux.md) matrixed both jobs in
`.github/workflows/ci.yml` over `ubuntu-latest` and `windows-latest`, so every
push to `main` and every pull request ran four jobs and had to pass all four.
That record was right about the blind spot and is still right about it: the
differences it lists -- a `mimetypes` lookup that reads the registry, a socket
that resets where the other closes, a default encoding, `scripts/start.ps1` --
each surface on exactly one of the two platforms, and the first Windows run found
one immediately.

What has changed is what that costs per merge, and where the risk actually sits.

- **The Windows runs are the slow halves.** A Windows runner is slower to start,
  `npm ci` is slower on NTFS, and the `ui` job does both. The Python suite takes
  about four seconds and the UI's about two; almost all of the wall-clock time
  between pushing and knowing is queueing for and warming up two more runners.
  ADR-0020 said as much in its own consequences, and named the Windows UI job as
  the honest cut if it ever needed trimming.
- **Windows is not the platform nobody runs.** Development happens on Windows,
  in PowerShell -- that is what `CLAUDE.md` says and what every command block in
  `README.md` is written for -- so a Windows-only break meets a developer's own
  machine on the way to the pull request. Linux is the platform that would
  otherwise be run by nobody, and it is the one thing on this list that has to
  stay on every push.
- **The differences are in a handful of stable places.** The registry lookup, the
  raw-socket tests, the `encoding="utf-8"` rule and the PowerShell script are not
  code that changes weekly. A platform difference is far likelier to arrive with
  a change to one of them than with an ordinary commit, and a manual run is
  available for exactly that case.

## Decision

`ci.yml` keeps both jobs, both platforms and `fail-fast: false`, and chooses its
runners from the event:

```yaml
os: ${{ fromJSON((github.event_name == 'schedule' || github.event_name == 'workflow_dispatch') && '["ubuntu-latest", "windows-latest"]' || '["ubuntu-latest"]') }}
```

- **A push and a pull request run Linux alone.** That is the gate on a merge, and
  it is half of what it was.
- **Windows runs on a schedule -- `9 4 * * 6`, Saturday morning** -- beside
  ADR-0016's mutation run at 05:17 and ADR-0026's nightly at 03:41, off the hour
  for the reason both of those are, and off each other's minute so the two weekly
  jobs are not queueing for runners at once. Linux runs again on that schedule,
  which is a couple of runner minutes for a weekly report that says what it found
  without anyone cross-referencing the last push.
- **`workflow_dispatch` runs both**, which is how a branch that touched a path,
  an encoding, a socket or `start.ps1` asks for Windows *before* it is merged.
  That is the replacement for the per-push gate, and it is deliberately a
  decision someone makes rather than a default.
- **It stays one workflow.** A second file would be a second copy of both jobs'
  steps and of the Python and Node this project pins in one place -- the fourth
  copy `CLAUDE.md` already refuses elsewhere -- kept in step by nothing but
  attention.
- **The concurrency group names the event.** The schedule fires on
  `refs/heads/main`, which is also where pushes land, so with
  `cancel-in-progress` a Saturday morning merge would otherwise cancel the only
  Windows run there is.

## Consequences

A pull request costs two runners instead of four and reports in about half the
time. The rules ADR-0020 wrote down are unchanged -- one Python, one Node, every
step under `bash`, no test skipped on one platform and run on the other -- and
Windows still runs the same two jobs, on the same suites, with the same floors.

The cost is when a Windows-only failure is found. It is now up to a week later,
on `main` rather than on the branch, which means the weekly run can go red over a
range of commits rather than over one, and the first job is to work out which.
That is the same hazard ADR-0016 accepted for the mutation run, with the same
mitigation: the run is dispatchable, so bisecting it is dispatching it on a
branch rather than waiting a week. A red Saturday run is also a report nobody is
blocked on, which is how a scheduled job comes to be ignored -- if that starts
happening, the honest response is to put Windows back on the pull request rather
than to keep a schedule nobody reads.

Three obligations, in `tests/test_conventions.py` beside ADR-0020's:

- **Every job still names both platforms somewhere.** Dropping Windows from the
  weekly branch is the one-word edit that would restore the blind spot ADR-0020
  closed, and it now has a plausible-looking place to happen.
- **No job names Windows in the branch a push takes.** The other direction: the
  wait this record removed is as easy to put back.
- **The events the matrix asks about are the events the workflow runs on.** A
  branch of the expression keyed to an event `on:` does not list is a Windows
  check that never runs, and nothing else would notice.

`docs/testing.md`, `CLAUDE.md` and `.claude/skills/preflight` each describe what
CI runs and are edited in step; the local gate itself is unchanged, since
`preflight` was always both suites on one machine.
