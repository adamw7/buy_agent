# ADR-0083: Say which build is running, from a stamp a release writes

- **Status:** Accepted
- **Date:** 2026-10-13

## Context

Nothing knew its own version. There was no `__version__`, no `--version` flag,
nothing in `/api/config`, a `Server` header of plain `buy_agent`, and an image on
`ghcr.io` with no OCI labels but its license (ADR-0082). A bug report could not
say which release it ran, and a checkout could not say which commit.

There is no packaging (no `pyproject.toml`), so there is no installed metadata to
ask; the version has to reach the code another way. The package starts no
process (`test_the_package_starts_no_process`), so a checkout cannot ask `git`
either, and the image is built without `.git` in its context.

## Decision

- **`buy_agent/version.py` is the one place a version is read.** It answers a
  `Version(number, commit)` whose `label` is `1.2.0+0123456789ab`, `dev+…` or
  `dev`: semver build metadata, with no spaces, so the same string fits every
  place it is shown. `current()` reads it once per process.
- **A release writes a stamp, `buy_agent/_version.txt`**: the number on the first
  line and the commit on the second. `release.yml` writes it into the archive;
  the `Dockerfile` writes it from the `VERSION` and `REVISION` build arguments
  `release.yml` passes. It is git-ignored and kept out of the build context, so a
  stamp left by hand reaches neither a commit nor an image.
- **A checkout with no stamp says `dev` plus the commit its `.git` names**, read
  off the files git keeps (`HEAD`, a loose ref, `packed-refs`, a worktree's
  `gitdir:` and `commondir`). Anything unreadable leaves the commit out; it never
  fails a run.
- **It is said in four places:** `--version` on both entry points,
  `defaults_payload`'s `version` (shown in the UI's footer), and the handler's
  `server_version`. The image also carries `org.opencontainers.image.version`,
  `.revision`, `.source` and `.created`; `source` links the `ghcr.io` package to
  the repository.

## Consequences

A bug report can quote the footer, the `Server` header or `--version`, and the
label says both the release and the commit. The cost is a stamp written by shell
in two places, which the module cannot check for itself.

The obligations:

- **Both writers write the same two lines to the same file.**
  `tests/test_version.py::test_both_releases_write_the_stamp_this_module_reads`
  holds the `printf` in the `Dockerfile` and in `release.yml` to `STAMP` and its
  order, and `test_a_stamp_is_never_committed` holds `.gitignore` to naming it.
  Both smoke tests in `release.yml` check that the served `version` starts with
  the release's number, so a stamp that did not land fails the release.
- **`version` is not a setting.** It is in `defaults_payload` and mirrored in
  `agent.types.ts`, but it has no `OPTIONS` row, no flag beyond `--version`, and
  nothing reads it back.
- **`version.py` reads files and nothing else.** It sits in the settings layer
  and imports nothing from the package; reading `git` through a process would
  break `test_the_package_starts_no_process`.
