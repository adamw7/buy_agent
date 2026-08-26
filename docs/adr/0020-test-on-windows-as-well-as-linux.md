# ADR-0020: Run both suites on Windows as well as Linux

- **Status:** Accepted
- **Date:** 2026-08-26

## Context

This project is written on Windows, in PowerShell -- that is what `CLAUDE.md`
says, what `scripts/start.ps1` is, and what every command block in `README.md` is
written for. Until now `.github/workflows/ci.yml` ran on `ubuntu-latest` and
nothing else. So the only Windows the code met was one machine, unrecorded, and
the only machine that gated a merge ran an operating system nobody develops on.

That is two blind spots rather than one, and both of them are stocked. The
project already carries scars from the differences:

- `server._CONTENT_TYPES` spells out the types `ng build` emits because
  `mimetypes` reads the registry on Windows and can answer `text/plain` for a
  `.js`, which a browser refuses to run as a module. That table is a fix for a
  platform only the developer's machine ran.
- The server tests speak HTTP over raw sockets, and two of them read to EOF after
  a body the server refuses unread. Whether the peer sees a FIN or a reset there
  is the platform's call, not the program's.
- Every file this project reads it opens with an explicit `encoding="utf-8"`,
  which is a rule that only bites where the default is not UTF-8 -- and on Linux
  it always is.
- `tests/test_start_script.py` exercises `scripts/start.ps1`, a Windows script,
  and had only ever run under `pwsh` on Linux.

None of those is exotic and none is caught by more tests on one platform. They
are caught by running the tests that exist on the other one.

## Decision

Both jobs in `ci.yml` are matrixed over `[ubuntu-latest, windows-latest]`: the
Python suite with coverage, and the UI's tests and build. Four runs, and a push
has to pass all four.

- **`fail-fast` is off.** A Windows-only failure that cancelled the Linux job
  would report half of what happened, which is the opposite of the reason for
  having two.
- **The matrix is over platforms and nothing else.** One Python and one Node,
  the ones the `Dockerfile` pins and `scripts/start.ps1` names. A second version
  in either axis would multiply the runs and leave those two files agreeing with
  whichever version was written down first.
- **Every step runs under `bash`.** It is on both runners, so the workflow reads
  the same on either. The alternative is PowerShell on Windows, where a step of
  several commands carries on past a failing one -- the same trap
  `scripts/start.ps1` has its own `Run` helper to avoid.
- **The suites are not conditioned on the platform.** No test is skipped on one
  and run on the other, beyond the PowerShell skip that was already there and
  that neither runner takes. A test that cannot pass on both is a bug in the
  code or in the test, and gets fixed rather than guarded.

## Consequences

The differences above are now checked rather than assumed, and `start.ps1` is
finally exercised on the platform it was written for.

CI costs roughly twice what it did, and the Windows runs are the slower halves --
Windows runners are slower to start and `npm ci` is slower on NTFS. This is the
price of the coverage and it is paid on every push. If it ever needs trimming,
the honest cut is the Windows UI job: the browser code is the half least likely
to notice a path separator. The Python job is not the cut, because that is where
every difference listed above lives.

Two obligations, both in `tests/test_conventions.py` beside the other cross-file
rules:

- **Every job names both platforms.** Dropping one is a one-word edit that looks
  like tidying and quietly restores the blind spot this record exists to close.
- **`ci.yml` sets up exactly one Python and one Node.** Three other files pin
  themselves to the version it names -- the `Dockerfile` (ADR-0015),
  `scripts/start.ps1` and `README.md` -- and each reads it as *the* version.
  A version matrix would leave all three agreeing with one entry and untested
  against the rest, so the matrix stays one-dimensional.

ADR-0016's Saturday mutation run stays on Linux alone. It is a report about the
tests rather than about the platform, and running it twice would double a job
that already takes a hundred times what the suite does to say the same thing.
