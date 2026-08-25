# ADR-0015: Package the web tier as a container, with Ollama left outside it

- **Status:** Accepted
- **Date:** 2026-08-25

## Context

Running the UI from a clone is a four-step setup on two toolchains: a virtual
environment and `pip install`, then `npm install` and `npm run build`, in that
order, before `python -m buy_agent.server` serves anything but a 503. Node is
needed for a build whose output the Python side only ever reads (ADR-0013), so
someone who wants the page rather than the source installs a toolchain to use it
once.

An image can carry that build already done. The question was what belongs in it.
The obvious temptation is everything -- Ollama in the same image, or a compose
file that starts it alongside -- so that one command yields a working page. But
Ollama is a several-gigabyte server holding models the user has already pulled,
usually with GPU access this project cannot arrange, and a second copy inside a
container would sit on the CPU, re-download a model, and be slower at the one
step that dominates a run. It is an external system in the C4 context diagram,
and putting it in the image would make the image lie about that.

## Decision

A multi-stage `Dockerfile` at the repository root builds the UI in a
`node:22.22.3-bookworm-slim` stage and copies `dist/ui/browser` into a
`python:3.13-slim` stage that installs `requirements.txt` and runs the server.
Both base images pin the versions CI tests against.

The image contains the two front ends and the pipeline, and nothing else:

- **Ollama is not in it, and not started by it.** `OLLAMA_HOST` defaults to
  `http://host.docker.internal:11434` in the image, so a container talks to the
  Ollama already running on the host. No compose file, because there is no second
  service to compose.
- **The entrypoint is `python`, not the server.** `CMD` makes the default run
  `-m buy_agent.server --host 0.0.0.0`, and `docker run ... -m buy_agent "..."`
  reaches the CLI in the same image. Two front ends onto one pipeline stays true
  of the image as well as of the code.
- **`--host 0.0.0.0` lives in `CMD`, not in the server's default.** The server
  still binds loopback everywhere else (ADR-0010): inside a container, loopback is
  the container's, and a published port would reach nothing.
- **The built UI is copied to `ui/dist/ui/browser` beside the package**, which is
  where `server.DEFAULT_UI_DIR` looks, so the image needs no `--ui-dir`.
- **Runtime dependencies only, and a non-root user.** `requirements.txt`, not
  `requirements-dev.txt`; no tests, docs or `.git` in the context (`.dockerignore`).

The image is a convenience, not the supported way to develop: CI does not build
it, and the test suites still run on the host.

## Consequences

Someone who wants the page runs two commands and needs no Python and no Node.
The four-step setup stays for anyone changing the code, which is the audience it
was already right for.

The obligations are version pins in a third place and a path that no import
checks. `tests/test_conventions.py` reads the `Dockerfile` for both: the base
images must name the same Python and Node versions as `.github/workflows/ci.yml`,
the `COPY --from=ui` destination must match `server.DEFAULT_UI_DIR`, and the
`EXPOSE`d port must be the one `server.build_parser` binds by default. A change
to any of those on one side alone fails there rather than in a container that
serves a 503, or that nothing can reach.

The costs are real and left in the open. `host.docker.internal` is supplied by
Docker Desktop but needs `--add-host=...:host-gateway` on Linux, so the README
says so. Nothing in CI builds the image, so a dependency that stops resolving on
`python:3.13-slim` -- a wheel that goes missing for the platform -- is found by
whoever next builds it, not by a pipeline. And `--json` writes inside a
read-only-by-convention container, so writing results out means mounting a
directory to write them into.
