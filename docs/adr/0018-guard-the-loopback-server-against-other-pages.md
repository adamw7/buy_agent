# ADR-0018: Guard the loopback server against the other pages in the browser

- **Status:** Accepted
- **Date:** 2026-08-25

## Context

ADR-0010 binds the server to loopback and says it "is not meant to be exposed".
That is true of the network and false of the browser. A page on any site the user
has open can send requests to `http://127.0.0.1:8000`, and two consequences
follow that binding to loopback does nothing about.

The first is that a cross-site *write* does not need a readable answer to be
worth making. `POST /api/search` accepted a body with no check on where the
request came from, and `Content-Type` was never read, so an ordinary form post --
no preflight, no CORS negotiation -- started a run. `GET /api/search/stream` is
worse, being a bare GET: an `<img>` or an `EventSource` pointed at it runs the
pipeline too. The browser refuses to show the attacker the result in every one of
these cases, and the run still happens: ten pages fetched and a local model
driven, repeatable as fast as the page cares to ask.

The second is DNS rebinding, which manufactures the read the first one cannot
get. The attacker points their own name at 127.0.0.1 on a short TTL; the browser
then believes their page and this server are the same origin, and hands over
everything -- `/api/config`, `/api/models`, and the results of runs it starts.
Nothing in the request distinguished this from the real page except the `Host`
header, which was not being read.

Neither is a hypothetical for a tool of this shape. Local development servers are
where rebinding is actually exploited, which is why Vite, webpack-dev-server and
Angular's own CLI all grew a host allowlist.

Authentication is not the answer here. There is one user, on their own machine,
with no account and nowhere to keep a credential; a password on this would be
ceremony protecting nothing that a same-origin check does not already protect.

## Decision

Every request is admitted by `BuyAgentHandler._admits()` before any routing, and
a refusal is a terse 403 that closes the connection and never reaches `api.py`.

Three headers decide it, all of them written by the browser and none of them
forgeable by a page:

- **`Sec-Fetch-Site: cross-site` is refused.** This is the only check that covers
  the requests carrying no `Origin` at all -- an `<img>`, an `<iframe>`, a
  cross-site form post. `same-site` is deliberately allowed: a site is a
  registrable domain and not a port, so the Angular dev server proxying from
  `localhost:4200` reports `same-site`, and refusing it would refuse `npm start`
  to close a hole nobody on the internet can reach through.
- **`Origin` must be loopback, or must equal the `Host` the request was
  addressed to.** The second clause is what lets a deliberately public bind work:
  a page served by this server always agrees with the authority the request went
  to, and a page anywhere else cannot make the two headers agree, because the
  browser writes both. An `Origin` of `null` -- a sandboxed iframe, a `data:`
  document -- is refused; the app is served from a real origin.
- **`Host` must be a name this server answers to**, which closes rebinding. A
  loopback bind answers the loopback names; `--allowed-host` adds more.

A bind to a public interface (`--host 0.0.0.0`, which is what the container uses)
gets no `Host` check and a warning saying so at startup, because the name that
reaches it is the operator's to know and not ours to guess. The other two checks
still stand there.

Alongside that, three smaller rules the same threat model asks for:

- Every response carries `Content-Security-Policy`, `X-Content-Type-Options` and
  `Referrer-Policy`. The app is served whole from one origin and asks nothing of
  any other, so the policy is `'self'` throughout, with `'unsafe-inline'` for
  styles only -- what Angular's per-component `<style>` blocks need -- and
  `frame-ancestors 'none'` in place of `X-Frame-Options`.
- `optimization.styles.inlineCritical` is off in `ui/angular.json`. Angular's
  critical-CSS inliner defers the global stylesheet with
  `<link media="print" onload="this.media='all'">`, and `script-src 'self'`
  refuses to run that inline handler -- leaving the sheet at `media="print"` and
  the app unstyled. Turning the inliner off is the only one of the three fixes
  that does not weaken the policy.
- A `Content-Length` that is negative is refused like one that is not a number.
  It parses as an integer and so used to fall through to "no body at all",
  leaving the declared body in the socket to be read as the next request line --
  the same desync the other two malformed lengths already closed the connection
  to prevent.

## Consequences

The API answers its own page, and clients that are not browsers -- `curl`, the
scripts `POST /api/search` was shaped for, the tests -- send none of these
headers and are unaffected. That is the honest limit of this: it is the browser's
own account of where a request came from, so it stops a page and not a program.
A program running on this machine could always have talked to this server, and
still can.

What it obliges:

- **A new endpoint gets the guard for free, and a new HTTP method does not.**
  `_admits()` is called at the top of `do_GET`, `do_POST` and `do_HEAD`; a
  `do_PUT` added without that line is unguarded, and nothing would fail.
- **The CSP and the UI's build are now coupled.** Anything that puts an inline
  handler, an inline `<script>`, or a request to another origin into the built
  app will be refused by the browser, and the symptom is a page that renders
  wrong rather than an error in either test suite. `tests/test_server.py` asserts
  the headers are sent; only a real browser can tell you the app still works
  under them.
- **`--allowed-host` is the escape hatch, and the warning is the signpost.**
  Anyone reaching the container by a name is told at startup why the check is off
  and what turns it on.

`api.py` stays framework-free and knows nothing about any of this: the guard is
transport, and lives with the transport.
