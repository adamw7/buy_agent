# The web tier as one image; the model server stays outside (ADR-0015).
#
#   docker build -t buy-agent .
#   docker run --rm -p 8000:8000 --add-host=host.docker.internal:host-gateway buy-agent
#
# compose.yaml runs it beside a model server and a search engine (ADR-0092).

# -- stage 1: build the UI, on the Node .nvmrc pins ---------------------------
# On the builder's own platform: the bundle is the same bytes for every image, and an
# arm64 image needs no emulated Node to build it.
FROM --platform=$BUILDPLATFORM node:24.22.2-bookworm-slim@sha256:c86ecead9e55a5210ac1727de38a4ca840bf03dcaf34dc9749addbf383b1920c AS ui

WORKDIR /ui

# The lockfile first, so `npm ci` is only re-run when the dependencies change.
COPY ui/package.json ui/package-lock.json ./
RUN npm ci

COPY ui/ ./
RUN npm run build

# -- stage 2: the server, on the Python .python-version pins ------------------
FROM python:3.15-slim@sha256:94d3426f1def7db4a106a232ac121bfd975393e5dfd40a619b955fae45fe7913 AS runtime

# The terms the image is published under, said where a registry reads them.
LABEL org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# The lock, not requirements.txt: every package at the version CI installed it at,
# and nothing pip would otherwise resolve on the day of the build (ADR-0092).
COPY requirements.lock ./
RUN pip install --no-cache-dir --no-deps -r requirements.lock && pip check

COPY LICENSE ./
COPY buy_agent/ ./buy_agent/

# Which build this is, as `buy_agent.version` reads it (ADR-0083). release.yml
# passes the release's number and commit; a local build says `dev`.
ARG VERSION=dev
ARG REVISION=
RUN printf '%s\n%s\n' "$VERSION" "$REVISION" > buy_agent/_version.txt

# Where `server.DEFAULT_UI_DIR` looks, so the container needs no --ui-dir.
COPY --from=ui /ui/dist/ui/browser/ ./ui/dist/ui/browser/

# The page cache, the answer cache and the run journal, in a volume of their own so a
# restart keeps them and the rest of the filesystem can be read-only (ADR-0092).
ENV BUY_AGENT_CACHE_DIR=/var/cache/buy-agent
RUN useradd --create-home --uid 1000 shopper \
    && mkdir -p /var/cache/buy-agent \
    && chown shopper /var/cache/buy-agent
# By number: Kubernetes' runAsNonRoot refuses a name it cannot check (ADR-0093).
USER 1000:1000
VOLUME ["/var/cache/buy-agent"]

# Every provider's address on the host (on Linux, via --add-host).
ENV OLLAMA_HOST=http://host.docker.internal:11434 \
    VLLM_HOST=http://host.docker.internal:8000/v1 \
    LITELLM_HOST=http://host.docker.internal:4000/v1 \
    TRTLLM_HOST=http://host.docker.internal:8000/v1

# Every interface: loopback inside a container is the container's own. A variable
# rather than a flag, so `docker run -e` and compose can change it (ADR-0092).
ENV BUY_AGENT_HOST=0.0.0.0

EXPOSE 8000

# /healthz asks for no token and no Host, so naming either does not fail the check.
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + (os.environ.get('BUY_AGENT_PORT') or '8000') + '/healthz', timeout=4)"]

# `python` as the entrypoint keeps the CLI reachable:
#   docker run --rm buy-agent -m buy_agent "espresso machine"
# The server stops on SIGTERM, finishing what it is answering first (ADR-0092).
ENTRYPOINT ["python"]
CMD ["-m", "buy_agent.server"]
