# The web tier as one image: the Angular app built in a Node stage, then copied
# into a Python stage that runs `buy_agent.server` over it. Ollama stays outside
# -- see ADR-0015 and the "Docker" section of README.md.
#
#   docker build -t buy-agent .
#   docker run --rm -p 8000:8000 --add-host=host.docker.internal:host-gateway buy-agent

# -- stage 1: build the UI ----------------------------------------------------
# The Node version CI builds with; the Angular CLI refuses anything older.
FROM node:22.22.3-bookworm-slim AS ui

WORKDIR /ui

# The lockfile first, so `npm ci` is only re-run when the dependencies change.
COPY ui/package.json ui/package-lock.json ./
RUN npm ci

COPY ui/ ./
RUN npm run build

# -- stage 2: the server and the pipeline -------------------------------------
# The Python version CI tests against.
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# Runtime dependencies only: pytest and coverage have no business in the image.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY buy_agent/ ./buy_agent/

# Where `server.DEFAULT_UI_DIR` looks, so the container needs no --ui-dir.
COPY --from=ui /ui/dist/ui/browser/ ./ui/dist/ui/browser/

# The agent only reads its own files; nothing here needs to be written to.
RUN useradd --create-home --uid 1000 shopper
USER shopper

# Ollama runs on the host, not in here. Docker Desktop resolves this name on its
# own; on Linux, `--add-host=host.docker.internal:host-gateway` supplies it.
ENV OLLAMA_HOST=http://host.docker.internal:11434

EXPOSE 8000

# Cheap, dependency-free, and it exercises the same handler the UI's form calls.
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/config', timeout=4)"]

# `python` as the entrypoint keeps the CLI reachable from the same image:
#   docker run --rm buy-agent -m buy_agent "espresso machine"
ENTRYPOINT ["python"]
CMD ["-m", "buy_agent.server", "--host", "0.0.0.0"]
