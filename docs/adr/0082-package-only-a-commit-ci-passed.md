# ADR-0082: Package only a commit CI passed, under a license both packages carry

- **Status:** Accepted
- **Date:** 2026-10-12

## Context

`release.yml` (ADR-0030) checks that what it builds starts and serves, scans the
image and attests both packages (ADR-0069). None of that runs the suites. A tag
can name any commit -- one whose CI went red, one cancelled by a later push to
`main`, one never pushed to `main` at all -- and the release would package it,
smoke-test it, attest it, and publish it as though it had passed.

Separately, the repository had no license. The archive and the image on
`ghcr.io` were public, and code published with no license grants nobody the
right to use it, whatever was meant.

## Decision

- **A release packages only a commit `ci.yml` passed.** Its first job,
  `tested`, resolves the tag to its commit and asks the Actions API for a
  successful `ci.yml` run on that SHA. With none, it fails and says to run CI on
  the tag; `archive` and `image` both `need` it, so neither builds. Any passing
  run counts: the push to `main`, a manual run on the tag, or the Saturday one.
- **The repository is MIT-licensed**, in `LICENSE` at the root. The archive
  carries the file, the image copies it to `/app/LICENSE`, and the image declares
  `org.opencontainers.image.licenses`.

## Consequences

A red tag is a red release that published nothing, rather than a release
nobody can tell from a good one. The cost is one more job that needs
`actions: read`, and a commit whose push run was cancelled by the next merge
needs a manual CI run on its tag before it can be released.

The obligations, held by `tests/test_conventions.py`:

- **Every release job that publishes needs `tested`**
  (`test_nothing_is_published_from_a_commit_ci_has_not_passed`). A new job that
  uploads or pushes and skips it is the gap this record closes, one job over.
- **Both packages carry `LICENSE`** (`test_both_packages_carry_the_license`):
  the archive's `cp` names it, the `Dockerfile` copies it, and `.dockerignore`
  must not drop it.
