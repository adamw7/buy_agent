# ADR-0030: Publish a release as a runnable archive and a container image

- **Status:** Accepted
- **Date:** 2026-08-30

## Context

There was no way to get this project other than cloning it. That is the right
default for something run from a checkout -- there is no `pyproject.toml`, no
`setup.py` and no install step (ADR-0016 says so about `setup.cfg` in passing) --
but it makes a tag a marker in the history and nothing else: a release published
on GitHub carried source archives GitHub generated on its own, and those hold the
`ui/` sources rather than a built app. Anyone who wanted the page still needed
Node, an `npm ci` and an `npm run build` before the server answered with anything
but the 503 that says to build the UI.

Two things about the shape of this project decide what a release can be:

- The Python side is never installed. `pytest.ini` carries `pythonpath = .` and
  the `Dockerfile` sets `PYTHONPATH=/app`; a wheel would be the first artefact
  anywhere that claimed the package is importable from `site-packages`, and the
  claim is untested by both suites and by the container.
- The UI is a build, not a source (ADR-0013), and the server looks for it at one
  path (`server.DEFAULT_UI_DIR`). Whatever is published has to carry the build,
  at that path, or it is a download that cannot serve the page it is for.

The `Dockerfile` already packages exactly that pair (ADR-0015) and nothing built
it: no job in CI ran `docker build`, so the one artefact designed to be handed to
someone else was checked by convention tests reading it as text and by nobody
running it.

## Decision

`.github/workflows/release.yml` runs when a release is **published**, and on
`workflow_dispatch` with a tag, so a failed upload can be retried without
re-cutting the release. It builds two packages from the tag being released --
never from the branch the workflow sits on -- and puts both on GitHub:

- **`buy-agent-<version>.tar.gz` and `.zip`, attached to the release** by `gh`,
  with a `SHA256SUMS.txt` beside them. Each holds `buy_agent/`, the built UI at
  `ui/dist/ui/browser`, `requirements.txt`, `README.md` and `docs/` -- a tree that
  needs `pip install -r requirements.txt` and nothing else to serve the page.
  There is no wheel and no sdist: the project is still run from a directory, and
  a release is not the place to start claiming otherwise.
- **`ghcr.io/<owner>/<repo>:<version>`**, the image the `Dockerfile` already
  describes, pushed to the registry GitHub gives the repository. `latest` follows
  full releases only -- not a pre-release, and not a manual re-run of an old tag.

Neither is published unattended. The archive is unpacked, its dependencies
installed and its own server started, and the image is run as a container; both
are asked for `/api/config` and for `/`, which is the Python half answering and
the built UI being served from where the server looks. A package that cannot do
both never reaches the release page.

## Consequences

A tag now yields something usable by someone with neither toolchain, and the
`Dockerfile` is built and run once per release rather than never -- so a build
that has quietly stopped working is a red release job instead of a discovery made
by whoever pulled it.

The obligations are the ones every other cross-file agreement here has, and
`tests/test_conventions.py` holds them:

- **The archive puts the UI where the server looks.** The workflow's `UI_DIST`
  is checked against `server.DEFAULT_UI_DIR`, the same agreement the
  `Dockerfile`'s `COPY` carries. Anywhere else and the archive unpacks,
  installs, and serves the 503 telling its downloader to build a UI.
- **Both jobs check out the tag.** A default checkout would package what `main`
  has moved on to and attach it to an older release -- assets that install and
  serve perfectly and are not the code the tag names, which nothing downstream
  can detect.
- **The UI is built on the Node `ci.yml` pins**, now checked per workflow the way
  the Python version already was. A second toolchain in a second workflow is the
  version drift ADR-0020 describes, one file further out.

What it costs: a fourth workflow, a `packages: write` permission this repository
did not need before, and a release that takes a few minutes -- the archive
installs the runtime dependencies and the image is built from scratch, because
both are only honest if what is published is what was started. And the release
is a *packaging* step, not a version bump: nothing in the tree records a version
number, so the tag is the only place one exists and the archive is named from it.
