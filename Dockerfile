# The web tier as one image; the model server stays outside (ADR-0015).
#
#   docker build -t buy-agent .
#   docker run --rm -p 8000:8000 --add-host=host.docker.internal:host-gateway buy-agent

# -- stage 1: build the UI, on the Node CI builds with ------------------------
FROM node:22.23.3-bookworm-slim AS ui

WORKDIR /ui

# The lockfile first, so `npm ci` is only re-run when the dependencies change.
COPY ui/package.json ui/package-lock.json ./
RUN npm ci

COPY ui/ ./
RUN npm run build

# -- stage 2: the server, on the Python CI tests with -------------------------
FROM python:3.14-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY buy_agent/ ./buy_agent/

# Where `server.DEFAULT_UI_DIR` looks, so the container needs no --ui-dir.
COPY --from=ui /ui/dist/ui/browser/ ./ui/dist/ui/browser/

RUN useradd --create-home --uid 1000 shopper
USER shopper

# Every provider's address on the host (on Linux, via --add-host).
ENV OLLAMA_HOST=http://host.docker.internal:11434 \
    VLLM_HOST=http://host.docker.internal:8000/v1 \
    LITELLM_HOST=http://host.docker.internal:4000/v1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/config', timeout=4)"]

# `python` as the entrypoint keeps the CLI reachable:
#   docker run --rm buy-agent -m buy_agent "espresso machine"
ENTRYPOINT ["python"]
CMD ["-m", "buy_agent.server", "--host", "0.0.0.0"]
