# Running in Docker

The third way to run the web UI, after `scripts/start.ps1` and the manual route
in the [README](../README.md). The image carries the built UI and the Python
side already installed, so serving the page needs neither toolchain -- only a
model server, which stays on the host:

```powershell
docker build -t buy-agent .
docker run --rm -p 8000:8000 buy-agent          # http://127.0.0.1:8000
```

Ollama is deliberately *not* in the image: it is a several-gigabyte server
holding models you have already pulled, usually with GPU access a container here
cannot arrange (see [ADR-0015](adr/0015-package-the-web-tier-as-a-container.md)).
The container reaches the host's copy through `host.docker.internal`, which Docker
Desktop supplies on its own; on Linux, hand it over explicitly:

```bash
docker run --rm -p 8000:8000 --add-host=host.docker.internal:host-gateway buy-agent
```

`OLLAMA_HOST` overrides that for an Ollama somewhere else, and the CLI is in the
same image -- the entrypoint is `python`, so anything after the image name is
what `python` is given:

```powershell
docker run --rm -e OLLAMA_HOST=http://10.0.0.5:11434 -p 8000:8000 buy-agent
docker run --rm buy-agent -m buy_agent "espresso machine" --top 5
docker run --rm -v "${PWD}:/out" buy-agent -m buy_agent "running shoes" --json /out/results.json
```

The other model server is the same story with the other pair of variables. The
image sets `VLLM_HOST` to `host.docker.internal:8000/v1` beside `OLLAMA_HOST`, so
a vLLM on the host needs only the provider named -- as a variable for every run
in that container, or from the form's **Model server** picker per search
([ADR-0028](adr/0028-serve-the-model-from-ollama-or-vllm.md)):

```powershell
docker run --rm -e BUY_AGENT_PROVIDER=vllm -p 8000:8000 buy-agent
docker run --rm -e VLLM_HOST=http://10.0.0.5:8000/v1 buy-agent `
  -m buy_agent "espresso machine" --provider vllm
```

The container runs as a non-root user and writes nothing, so `--json` needs a
mounted directory to write into, as above. No pull request builds the image; the
[tests](testing.md) run on the host, and the build happens once per release
(below).

## The published image

`.github/workflows/release.yml` builds this `Dockerfile` when a release is
published and pushes it to the registry that comes with the repository, so a
version can be run without a checkout at all
([ADR-0030](adr/0030-publish-a-release-as-an-archive-and-an-image.md)):

```powershell
docker run --rm -p 8000:8000 ghcr.io/adamw7/buy_agent:latest
docker run --rm -p 8000:8000 ghcr.io/adamw7/buy_agent:1.2.0    # or a version
```

`latest` follows full releases only; a pre-release is published under its own
version and moves nothing. Everything above -- `host.docker.internal`, the
environment variables, the CLI behind the same entrypoint -- is the same image
and behaves the same way.

The other half of a release is an archive with the same two halves in it -- the
package and the built UI -- for running the server straight from a Python
environment:

```powershell
tar -xzf buy-agent-1.2.0.tar.gz ; cd buy-agent-1.2.0
pip install -r requirements.txt
python -m buy_agent.server                       # http://127.0.0.1:8000
```

Both are checked before they are published: the workflow starts each one and
asks it for `/api/config` and for the page, so an archive whose UI landed in the
wrong place, or an image that no longer boots, fails the release rather than the
download.
