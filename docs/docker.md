# Running in Docker

The image carries the built UI and the Python side, so it needs neither
toolchain -- only a model server, which stays on the host
([ADR-0015](adr/0015-package-the-web-tier-as-a-container.md)):

```powershell
docker build -t buy-agent .
docker run --rm -p 8000:8000 buy-agent          # http://127.0.0.1:8000
```

The container reaches the host through `host.docker.internal`, which Docker
Desktop supplies; on Linux, hand it over explicitly:

```bash
docker run --rm -p 8000:8000 --add-host=host.docker.internal:host-gateway buy-agent
```

`OLLAMA_HOST`, `VLLM_HOST` (`host.docker.internal:8000/v1`) and `LITELLM_HOST`
(`host.docker.internal:4000/v1`) are preset, so another server needs only its
provider named
([ADR-0028](adr/0028-serve-the-model-from-ollama-or-vllm.md),
[ADR-0068](adr/0068-reach-a-litellm-proxy-as-a-third-model-server.md)). The
entrypoint is `python`, so the CLI is there too:

```powershell
docker run --rm -e OLLAMA_HOST=http://10.0.0.5:11434 -p 8000:8000 buy-agent
docker run --rm -e BUY_AGENT_PROVIDER=vllm -p 8000:8000 buy-agent
docker run --rm buy-agent -m buy_agent "espresso machine" --top 5
docker run --rm -v "${PWD}:/out" buy-agent -m buy_agent "running shoes" --json /out/results.json
```

It runs as a non-root user and writes nothing, so `--json` needs a mounted
directory. Only a release builds the image; the [tests](testing.md) run on the
host.

## The published image

`.github/workflows/release.yml` builds and pushes the image when a release is
published ([ADR-0030](adr/0030-publish-a-release-as-an-archive-and-an-image.md)).
`latest` follows full releases only:

```powershell
docker run --rm -p 8000:8000 ghcr.io/adamw7/buy_agent:latest
docker run --rm -p 8000:8000 ghcr.io/adamw7/buy_agent:1.2.0    # or a version
```

A release also carries an archive with the package and the built UI:

```powershell
tar -xzf buy-agent-1.2.0.tar.gz ; cd buy-agent-1.2.0
pip install -r requirements.txt
python -m buy_agent.server                       # http://127.0.0.1:8000
```

The workflow starts both and asks each for `/api/config` and the page before
publishing.
