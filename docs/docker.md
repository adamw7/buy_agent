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
mounted directory to write into, as above. Nothing in CI builds the image; the
[tests](testing.md) still run on the host.
