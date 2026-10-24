# Security policy

## Reporting a vulnerability

Report it privately, through **Report a vulnerability** on this repository's
**Security** tab, rather than in an issue or a pull request. Say what an attacker
can do, against which version (`/api/config` and `--version` both say), and how to
reproduce it.

## Supported versions

Fixes go into the next release. Only the latest release, and the image tagged
`latest` on `ghcr.io/adamw7/buy_agent`, is supported.

## What is in scope

Run it the way the docs say. That is loopback on a laptop, or reachable from other
machines with `$BUY_AGENT_API_TOKEN` set ([docs/docker.md](docs/docker.md)). These
are in scope:

- a request that gets past the token, the `Origin`/`Host` checks or the request size
  limits;
- making the server fetch, list or pay an address it was not started with;
- a payment for a product the server never sealed, or above a mandate's limits;
- the container running as root, writing outside its cache volume, or leaking a
  secret into a log or a response.

A server bound to every interface without a token answers everybody. The server
warns about this when it starts. Reporting that it does so is not a vulnerability.
