# ADR-0069: Scan and attest what a release publishes

- **Status:** Accepted
- **Date:** 2026-10-01

## Context

[ADR-0030](0030-publish-a-release-as-an-archive-and-an-image.md) made a release
two packages, an archive and a `ghcr.io` image, and made sure each one starts
before it is published. That answers "does it start". Nothing answered "what is
in it" or "who made it":

- The image is `python:3.14-slim` plus `requirements.txt`. The base tag moves
  between releases and the pins do not, so two releases from identical trees can
  ship different Debian packages. Nothing scanned what was pushed.
- `SHA256SUMS.txt` shows the archive was not corrupted in transit. It cannot show
  who built it, because anyone able to replace the archive can replace the sums
  file next to it.
- There was no SBOM. "Which `lxml` was in the image we shipped in March" meant
  reading the tag's `requirements.txt` and hoping the transitive resolution had
  not changed.

These packages are the only thing this repository hands to other people.

## Decision

`release.yml` does three more things:

- **It scans the image before pushing it.** `anchore/scan-action` (Grype) fails
  the job on a finding of HIGH or above that has a fix. `.github/grype.yaml`
  limits the judgement to the OS layer by ignoring Python packages, which
  pip-audit already reads nightly
  ([ADR-0062](0062-audit-both-dependency-lists-on-a-schedule.md)).
  A second gate on the same pins would only fail a release on news the audit has
  already reported. Unfixed findings are ignored because the only remedy a
  release has is a rebuild on the moving base tag. An unfixed finding would fail
  every release until Debian ships a patch, and a gate that is always red stops
  being read.
- **It lists the image's contents before pushing it.** `anchore/sbom-action`
  (Syft) writes `buy-agent-<version>.spdx.json`, and the job attaches it to the
  release next to the archive.
- **It attests build provenance for both packages** with
  `actions/attest-build-provenance`. The archive job attests the tarball and the
  zip before uploading them. The image job attests the pushed digest, which can
  only happen after the push, since the registry assigns the digest. The image's
  attestation is pushed to the registry too.

Attesting needs `id-token: write` and `attestations: write`. Both are granted on
the two release jobs that attest and nowhere else. The workflow-wide floor stays
`contents: read`. The image job also widens from `contents: read` to `write` to
upload the SBOM, as the archive job already does for its assets.

## Consequences

A consumer can now check where a package came from with
`gh attestation verify` (`docs/docker.md` gives the commands), and can re-scan an
old release's SBOM against today's advisories without rebuilding it. An image
with a fixable HIGH or CRITICAL finding in its OS layer is never pushed.

The obligations, held by `tests/test_conventions.py`:

- **Only a job that attests can mint a token naming the run.** A job holds
  `id-token: write` and `attestations: write` exactly when it uses
  `actions/attest-build-provenance`. Anywhere else, those grants are a credential
  that some other step could borrow
  (`test_only_a_job_that_attests_can_mint_a_token_naming_the_run`).
- **Everything a release publishes is attested.** A release job that runs
  `gh release upload` or `docker push` also attests
  (`test_everything_a_release_publishes_is_attested`). A new package cannot
  skip provenance.
- **The scan and the SBOM come before the push**
  (`test_the_image_is_scanned_and_listed_before_it_is_pushed`). A scan after the
  push reports on an image that is already public.
- **The threshold stays as decided:** HIGH, fixed findings only, OS layer only
  (`test_the_scan_fails_a_release_only_on_what_it_could_fix`). Lowering it, or
  counting unfixed findings, needs a new record arguing why the gate would not
  then cry wolf.

What it costs: two third-party actions in the one workflow that holds write
tokens, a wider token on the image job, and a minute or so per release to
download Grype's database and run both tools. None of it can be tested on a pull
request. The first published release, ideally a pre-release, is where it is
first exercised.
