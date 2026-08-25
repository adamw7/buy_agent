# ADR-0011: Stream a run as Server-Sent Events, and name the terminal failure `failure`

- **Status:** Accepted
- **Date:** 2026-08-25

## Context

A run takes tens of seconds -- around 75 with a 1.2B model, most of it in
extraction. A request/response API means a browser tab that does nothing at all
for over a minute, with no way to tell a slow run from a hung one. The CLI user,
meanwhile, watches the agent's log lines scroll past and always knows where it is.

The same progress is already being produced: the pipeline logs each stage. It
just needs a way out to the browser.

## Decision

`GET /api/search/stream` runs the agent in a worker thread and relays its log
records to the browser as Server-Sent Events -- `log` lines as they happen, then
exactly one `result` or `failure`. `POST /api/search` is the same run delivered
in one response, which is the shape a script wants.

Three details are load-bearing:

- **The terminal failure event is called `failure`, not `error`.** A browser's
  `EventSource` delivers transport errors under `error` and then *reconnects*. A
  named `error` event would be indistinguishable from a dropped connection, and
  the automatic reconnect would silently start the whole search again -- another
  minute of model time the user did not ask for. For the same reason
  `HEAD /api/search/stream` answers 405 rather than starting a run nobody reads.
- **`_LogRelay` routes records by the thread that produced them.** Log records
  are global; runs are not. Fanning out by thread id is what keeps two concurrent
  runs from seeing each other's progress.
- **A `ping` event goes out every 15 seconds.** Extraction is the slowest stage
  and logs nothing while it runs, so without a keepalive a browser or an
  intermediary times the stream out during the one stretch that matters.

Unsubscribing from the `EventSource` is the UI's Stop button -- there is no
separate cancellation endpoint.

## Consequences

The browser watches the same progress the terminal shows, from the same log
lines, with no second reporting path to keep in step. A run that is slow looks
slow rather than broken.

The costs are the ones streaming always brings: the response is committed before
the outcome is known, so a failure has to be delivered *inside* a 200 stream
carrying its own status, and the tests need real sockets and a stub agent with a
deliberate delay to exercise the keepalive and two overlapping runs. Those
deliberate sleeps are most of what the server tests cost.
