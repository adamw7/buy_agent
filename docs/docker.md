# Running in Docker

The image carries the built UI and the Python side, so it needs neither toolchain
-- only a model server, which stays on the host
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

`OLLAMA_HOST`, `VLLM_HOST` (`host.docker.internal:8000/v1`), `LITELLM_HOST`
(`host.docker.internal:4000/v1`) and `TRTLLM_HOST`
(`host.docker.internal:8000/v1`) are preset, so another server needs only its
provider named ([ADR-0028](adr/0028-serve-the-model-from-ollama-or-vllm.md),
[ADR-0068](adr/0068-reach-a-litellm-proxy-as-a-third-model-server.md),
[ADR-0081](adr/0081-reach-tensorrt-llm-as-a-fourth-model-server.md)). The
entrypoint is `python`, so the CLI is there too:

```powershell
docker run --rm -e OLLAMA_HOST=http://10.0.0.5:11434 -p 8000:8000 buy-agent
docker run --rm -e BUY_AGENT_PROVIDER=vllm -p 8000:8000 buy-agent
docker run --rm buy-agent -m buy_agent "espresso machine" --top 5
docker run --rm -v "${PWD}:/out" buy-agent -m buy_agent "running shoes" --json /out/results.json
```

It runs as a non-root user and writes only its caches and run journal, to a volume
at `/var/cache/buy-agent` (`$BUY_AGENT_CACHE_DIR`), so the rest can be read-only
and `--json` needs a mounted directory. On `docker stop` it finishes the requests
in flight for up to 25 seconds; Docker's default is ten, so give it
`--stop-timeout 30`. Only a release builds the image; the [tests](testing.md) run
on the host.

## Deploying it for more than one machine

Bound to every interface, the server is reachable by anyone who can reach the
port, so it asks for a token and calls only the addresses it was started with
([ADR-0092](adr/0092-deploy-the-server-for-more-than-one-machine.md)).
`compose.yaml` runs it beside Ollama, a one-off job that pulls the model, and a
SearXNG that needs no key:

```bash
export BUY_AGENT_API_TOKEN="$(openssl rand -hex 24)"
docker compose up -d --build                                       # :8000
docker compose -f compose.yaml -f compose.gpu.yaml up -d --build   # with a GPU
```

A browser asks for a name and a password on the first page: any name, and the
token as the password. A script sends `Authorization: Bearer <token>`. The
settings, each also a flag of `python -m buy_agent.server`:

| Variable | What it does |
| --- | --- |
| `BUY_AGENT_API_TOKEN` | Asked of every request but `/healthz` and `/readyz`; 16 characters at least. No flag. |
| `BUY_AGENT_HOST`, `BUY_AGENT_PORT` | Where it listens; the image sets `0.0.0.0`. |
| `BUY_AGENT_ALLOWED_HOSTS` | The names browsers reach it by, comma-separated; the `Host` check against DNS rebinding. |
| `BUY_AGENT_BEHIND_PROXY` | `1` behind a proxy that sets `X-Forwarded-Host` and `X-Forwarded-For`, which it then reads. |
| `BUY_AGENT_MAX_RUNS` | Searches at once (default 4); the next is a 503 with `Retry-After`. |
| `BUY_AGENT_ACCESS_LOG` | `1` for a line per request: client, path, status, time, request id. |
| `BUY_AGENT_LOG_FORMAT` | `json` for one object per line, for a log collector. |

Once other machines can reach it (bound beyond loopback, or behind a proxy), it
asks only the model server and the merchant it was started with, refusing a typed
model address or payment endpoint that is not its own on its box. It never fetches
a page on a private network, a redirect to one included; that check reads the
address as written, so run it on a network that cannot reach anything it should
not. A re-sort or a payment must hand back the `seal` its run came with.

TLS is the proxy's job. nginx in front of it wants
`proxy_set_header X-Forwarded-Host $host;`, `proxy_set_header X-Forwarded-For
$proxy_add_x_forwarded_for;` and `proxy_read_timeout 15m;` for the event stream,
which says `X-Accel-Buffering: no` itself. For an orchestrator:

| Path | Answers |
| --- | --- |
| `/healthz` | 200 while the process serves: liveness, and the image's `HEALTHCHECK`. |
| `/readyz` | 200 while its model server answers; 503 while stopping or while it does not. |
| `/metrics` | Requests, runs by outcome, run time and open connections, in Prometheus's text format, behind the token. |

Each answer carries an `X-Request-Id`, the caller's when it sent one. A 500 names
that id and nothing else; the log has the traceback under it.

## On Kubernetes

`deploy/kustomization.yaml` runs what `compose.yaml` runs, on a cluster: the web
tier, Ollama and SearXNG
([ADR-0093](adr/0093-run-on-kubernetes-what-compose-runs.md)). The manifests hold no
secret, so create the namespace and the token first:

```bash
kubectl create namespace buy-agent
kubectl -n buy-agent create secret generic buy-agent \
  --from-literal=api-token="$(openssl rand -hex 24)"
kubectl apply -k deploy
kubectl -n buy-agent rollout status deploy/buy-agent --timeout=30m
kubectl -n buy-agent port-forward svc/buy-agent 8000:8000     # http://127.0.0.1:8000
```

The web tier's pod pulls its model before the server starts, unless Ollama already
has it, so the first start takes as long as the download. The settings in the table
above are `configMapGenerator` literals in the kustomization, with `OLLAMA_MODEL`
naming the model. Change one and apply again, and the server restarts with it.

The image is `ghcr.io/adamw7/buy_agent:latest`; `newTag` in the kustomization pins a
release. 3.0 predates `/healthz` and `/readyz` and fails both probes, so use a later
release or an image built from this checkout.

What the manifests set up:

- **One replica of each, replaced rather than rolled.** The caches and run journal
  (1 GiB) and the models (20 GiB) are `ReadWriteOnce` claims from the cluster's
  default StorageClass. A new image or setting stops the server before the next one
  starts.
- **The web tier** runs as the image's user, 1000, with a read-only root, no
  capabilities and no service-account token. It is live while `/healthz` answers,
  ready while `/readyz` does, and has 30 seconds to drain. While Ollama does not
  answer it is not ready, and the Service sends nothing to it.
- **No pod is told where the Services beside it are** (`enableServiceLinks: false`).
  Otherwise, beside a Service named `buy-agent`, Kubernetes sets
  `BUY_AGENT_PORT=tcp://<address>:8000`, which the server refuses as its port and
  says why. Set it the same way in manifests of your own.
- **A GPU:** uncomment the `components` line, which needs the NVIDIA device plugin
  on the cluster.

What is left to the cluster:

- **TLS and a public name.** Put the `buy-agent` Service behind your ingress or
  gateway. Set `BUY_AGENT_ALLOWED_HOSTS` to the name it answers by, and
  `BUY_AGENT_BEHIND_PROXY=1` if it sets `X-Forwarded-Host` and `X-Forwarded-For`.
  Give the event stream a 15-minute read timeout, which ingress-nginx sets with
  `nginx.ingress.kubernetes.io/proxy-read-timeout: "900"`.
- **What the web tier may reach.** A public name that resolves to a private address
  passes the fetch check, so a NetworkPolicy should keep the web tier to DNS, Ollama,
  SearXNG and the public internet, if the cluster's network plugin enforces one.
- **Another model server.** Set `BUY_AGENT_PROVIDER` and its address in the
  literals, and drop the Ollama manifest and the `pull-model` init container in an
  overlay of your own.
- **Scraping `/metrics`,** which asks for the token as a bearer.

## The published image

`.github/workflows/release.yml` builds and pushes the image when a release is
published ([ADR-0030](adr/0030-publish-a-release-as-an-archive-and-an-image.md)),
for amd64 and arm64 under one tag; the UI stage is built once, on the runner's
platform, and the amd64 image is the one the smoke test and the scan below
checked. It installs `requirements.lock`, the versions CI tested
([ADR-0092](adr/0092-deploy-the-server-for-more-than-one-machine.md)). `latest`
follows full releases only:

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

The workflow builds neither unless `ci.yml` passed on the tag's commit (so a
commit whose CI run a later push cancelled needs CI run on its tag first), then
starts both and asks each for `/api/config` and the page before publishing
([ADR-0082](adr/0082-package-only-a-commit-ci-passed.md)).

Both say which build they are
([ADR-0083](adr/0083-say-which-build-is-running.md)): `release.yml` stamps the
archive with the release's number and commit and passes both to the image as the
`VERSION` and `REVISION` build arguments, so `--version`, `/api/config`, the
page's footer and the `Server` header all read `1.2.0+<commit>`; a local
`docker build` says `dev`. The image also carries the `org.opencontainers.image`
labels `version`, `revision`, `source` and `created`:

```powershell
docker inspect --format '{{json .Config.Labels}}' ghcr.io/adamw7/buy_agent:1.2.0
```

### What you can verify

Before the image is pushed, the workflow scans its OS layer with
[Grype](https://github.com/anchore/grype) and fails on a HIGH or CRITICAL finding
that has a fix; findings with no fix are reported but do not fail a release, as a
rebuild could not remove them. Its Python packages are `requirements.txt`, which
the nightly `pip-audit` reads instead
([ADR-0069](adr/0069-scan-and-attest-what-a-release-publishes.md)).

Both packages carry build provenance, a signed statement of which workflow run, on
which commit, produced them. `SHA256SUMS.txt` only shows the download arrived
intact, since anyone who could replace the archive could replace the sums too.
Check provenance with the GitHub CLI:

```powershell
gh attestation verify buy-agent-1.2.0.tar.gz --repo adamw7/buy_agent
gh attestation verify oci://ghcr.io/adamw7/buy_agent:1.2.0 --repo adamw7/buy_agent
```

Each release also has `buy-agent-<version>.spdx.json`, an SBOM of every package
in the image, the Debian ones and the resolved Python ones. It answers "which
`lxml` did 1.2.0 ship" without rebuilding anything, and can be re-scanned later
against newer advisories:

```powershell
grype sbom:buy-agent-1.2.0.spdx.json
```
