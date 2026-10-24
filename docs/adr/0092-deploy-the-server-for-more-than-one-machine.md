# ADR-0092: Deploy the server for more than one machine

- **Status:** Accepted
- **Date:** 2026-10-24

## Context

ADR-0010 built the server for "one user, on their own laptop", and said that if it
ever had to serve more than one person over a network, that decision would be the
first one to revisit. ADR-0018 said authentication was "not the answer here",
because there was one user and nowhere to keep a credential. The container already
binds every interface (ADR-0015), so anyone who could reach the port could use it.
Running it that way turned up these problems, each of which was checked against
the running server:

- **No authentication.** The admission checks read headers a browser writes.
  `curl` sends no `Origin`, so it got in. With `$BUY_AGENT_AP2_MANDATE` set, that
  includes paying.
- **The server would call any address a client typed.** `GET /api/models?base_url=`
  connected to an arbitrary internal address. A run's `base_url`, a payment's
  `merchant_url`, and a search result's link or one of its redirects reached any
  address the same way, including `169.254.169.254`.
- **Products were taken from the client.** `/api/pay` and `/api/rank` take the
  products from the request body (ADR-0035). A client could pay, within whatever a
  mandate allows, for a product it made up.
- **`--allowed-host` broke the health check.** The image's `HEALTHCHECK` sent
  `Host: 127.0.0.1`. Naming a host, as the server's own warning advises, got that
  check a 403 and the container was marked unhealthy.
- **Stopping the container was slow and abrupt.** Python as PID 1 ignores SIGTERM
  unless it installs a handler. `docker stop` waited out its ten seconds and then
  killed the server along with any runs in flight.
- **No limits.** There was a thread per connection and no cap on runs, so ten
  clients meant ten model calls at once on one GPU.
- **Little to operate it with.** Settings came only as flags, which in a container
  means rewriting `CMD`. There was no probe for readiness, no metrics, no request
  id, and no access log above DEBUG. A 500 sent the raw exception text to the
  client.

## Decision

`buy_agent/deployment.py` holds what changes when other machines can reach the
server: who may ask, what it may reach, how much it takes on at once, how it stops,
and what it reports. It does no HTTP; `server.py` reads the headers and passes them
in. The defaults stay what a laptop wants. Each of the following is off or
unchanged unless it is configured, or unless the server can tell other machines
reach it.

- **A token, from `$BUY_AGENT_API_TOKEN`, with no flag and no form field.** It is
  accepted as a bearer token, or as the password of HTTP Basic. A browser asks its
  user for the Basic password on the first 401 and then sends it on every request,
  the event stream included, so the page needs no login code. The server refuses to
  start with a token shorter than 16 characters, and warns when other machines can
  reach it without one.
- **Probes and metrics come before the browser checks.** `/healthz` and `/readyz`
  need no token and no particular `Host`, because a probe arrives by a pod IP with
  neither. `/readyz` answers 503 while the server is stopping or while the model
  server it was started with does not answer. `/metrics`, in Prometheus's text
  format, accepts any `Host` but needs the token.
- **When other machines can reach it, it calls only the addresses it was started
  with.** That is the case when it is bound to a non-loopback address or started
  with `--behind-proxy`. A `base_url` or `merchant_url` that differs from the
  provider's or rail's own default is refused on that field, and the model picker
  says why rather than connecting.
- **Fetching never leaves the public web.** `fetch` refuses IP literals that are not
  global, single-label names, the private suffixes (`.local`, `.internal` and the
  rest) and numeric spellings of addresses. It checks the first URL and, through an
  httpx request hook, every redirect. A public name that resolves to a private
  address is not caught: no module but `server.py` may import `socket`, and a
  proxied deployment would see only the proxy's address anyway.
- **A run's products are sealed.** `api.Sealer` signs the set of products a run
  answered with, in any order, with HMAC-SHA256. A re-sort or payment over HTTP must
  send that `seal` back, or it gets a 409 on `products`. The key comes from the
  token, so replicas sharing it agree; without a token it is random for each
  process.
- **Runs and connections are capped.** `--max-runs` (default 4) sets how many
  searches run at once; the next gets a 503 with `Retry-After`, or a `failure` event
  on the stream. At most 256 connections are open; past that a connection is closed
  unanswered.
- **SIGTERM drains.** It raises in the main thread. The server stops accepting
  connections, takes no new runs, and waits up to 25 seconds for the requests in
  flight before exiting 0. Ctrl-C drains the same way and still exits 130. A second
  Ctrl-C stops the wait.
- **Behind a proxy, `--behind-proxy` reads `X-Forwarded-Host` and
  `X-Forwarded-For`.** They feed the `Origin`/`Host` checks and the client address.
  The proxy is trusted to set them. Without the flag they are ignored, since any
  client can write them. The event stream sends `X-Accel-Buffering: no` so nginx
  passes it through as it arrives. A server started with `--behind-proxy` has no
  camera, because behind a proxy a loopback bind is reachable by anybody.
- **Every server flag defaults to a variable:** `$BUY_AGENT_HOST`, `_PORT`,
  `_ALLOWED_HOSTS`, `_MAX_RUNS`, `_BEHIND_PROXY`, `_ACCESS_LOG` and `_LOG_FORMAT`.
  `config.server_environment` reads them when the server starts. argparse runs a
  string default through the flag's type, so a misspelt variable is a usage error.
- **Each request gets an id,** the caller's `X-Request-Id` when it looks like one.
  It is echoed on the response, written in the access log, and given in a 500 in
  place of the exception, which goes only to the server's log. `--access-log` logs
  one line per request, without its query string. `--log-format json` writes stderr
  one JSON object per line.
- **The image installs `requirements.lock`, and every other install is held to
  it.** The lock is generated with `uv pip compile --universal`. The Dockerfile
  installs it with `--no-deps`. `requirements-dev.txt` constrains every other
  install with `-c requirements.lock`, so CI tests the versions the image ships.
  The lock has no hashes: a hash in a constraints file turns on hash-checking for
  every install that reads it, and the dev tools are not hashed. Renovate
  regenerates it when a pin moves and refreshes it weekly.
- **The image** reads its address from `BUY_AGENT_HOST`, checks `/healthz`, keeps
  its caches in a volume at `/var/cache/buy-agent`, and runs with a read-only root
  filesystem. A release pushes amd64 and arm64 under one tag. The UI stage is built
  once, on the runner's own platform, and the amd64 image pushed is the one the
  smoke test and the scan checked, rebuilt from the same builder cache.
- **`compose.yaml`** runs the server, Ollama (with a one-off job that pulls the
  model) and SearXNG (no API key needed). It refuses to start without the token,
  and gives the server 30 seconds to drain. `compose.gpu.yaml` adds a GPU.
- **Supply chain and workflows.** Renovate pins actions and images by digest. The
  release workflow uses no cache, which a pull request's run could have poisoned.
  Every checkout leaves no token on disk. zizmor reads the workflows nightly and
  CodeQL runs weekly, so no pull request waits on either.

## Consequences

A deployment needs a token and a reverse proxy for TLS. Either a container is
reachable only by its own name, or the proxy preserves `Host` or sets
`X-Forwarded-Host`. Everything else ships configured.

What it obliges:

- **A new path a machine asks belongs in `_PROBES`, or in the `_METRICS` branch of
  `_refused`.** Anything else goes through the `Origin`/`Host` checks and the token.
- **A new address a client can name is refused unless it is the server's own.**
  This applies wherever `policy.fixed_addresses` is set, the way `base_url` and
  `merchant_url` are. A new place the pipeline reads a URL from the web goes
  through `fetch.on_the_public_web`.
- **Anything that takes products back from a client checks the seal,** as
  `rank_again` and `pay_now` do. The run payload's `seal` is mirrored in
  `agent.types.ts`, and the page echoes it.
- **A new step counts against nothing on its own,** but a new entry point that runs
  the pipeline goes through `Traffic.admit_run`, as `_search` does.
- **A pin moved in `requirements.txt` is moved in the lock too,**
  `test_the_lock_pins_what_requirements_txt_pins` holds this. The command to
  regenerate it is in the lock's header.
- **`DRAIN_SECONDS` stays under the grace period of whatever runs the server:**
  Kubernetes' 30 seconds, and compose's `stop_grace_period`, which a convention
  test holds above it. Docker's own default is ten seconds, so a plain `docker run`
  wants `--stop-timeout 30`.
- **`server.py` is over pylint's 1000 lines and says so in one suppression.** It is
  one stdlib server (ADR-0010); what a deployment adds belongs in `deployment.py`.
- **A public name that resolves to a private address is the network's to stop.**
  The fetch check does not catch it, so run the container on a network that cannot
  reach anything it should not.
- The server's `$BUY_AGENT_*` variables are unset for every test by
  `serve_as_nobody_deployed_it` in `tests/conftest.py`.
