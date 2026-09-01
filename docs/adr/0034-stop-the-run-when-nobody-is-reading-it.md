# ADR-0034: Stop the run when nobody is reading it, and say where it stops

- **Status:** Accepted
- **Date:** 2026-09-01

## Context

ADR-0011 made unsubscribing from the `EventSource` the UI's Stop button, with no
separate cancellation endpoint. What that button did was close the stream: the
run itself carried on to the end on the server, and `_stream_search` said so in a
comment -- "there is nothing here to cancel it with" -- while the page said
nothing at all. It appended a line reading "Stopped."

Both halves of that were wrong in the same direction. The word promised the
stronger thing, so a shopper who hits Stop because a run is taking too long and
starts another one immediately puts two searches on one model server, and both
are slower than the first would have been alone. On a laptop-sized Ollama that is
the difference between a slow run and an unusable one -- and the page had told
them the first run was over.

There is still nothing that can cancel a chat call already in flight: the run is
blocked inside an HTTP request to the model server, and neither client offers a
way in. What there *is* is a pipeline made of steps, with two long ones -- fetching
the pages and extracting products from them. Between any two of them the run can
simply stop.

## Decision

`BuyAgent.run` takes a `checkpoint`, called with the name of each step as it is
about to start -- `"search"`, `"fetch"`, `"extract"`, `"rank"`. Nothing in the
pipeline catches what it raises, and that is how a run ends: the caller's own
exception travels back to the caller. The default, `every_step_passes`, is a
function and not `None`, so there is no test for one at every boundary.

What it raises is deliberately **not** a fourth failure mode. `BuyAgent.run` still
raises exactly the three of ADR-0009, `api._STATUS` still maps exactly those, and
`__main__.main` still catches exactly those. A stopped run is not a failure and
there is nobody left to answer with a status, so the exception is defined where
the only caller that wants one lives: `server._Stopped`, raised by
`server._stop_when` and caught by the worker that raised it.

`_stream_search` sets that flag on the first frame it cannot write -- a log line,
or at worst the keepalive ping fifteen seconds later. The run then ends at its
next step boundary.

The page says what that buys and what it does not. The line Stop appends reads
"Stopped watching. The run ends on the server at its next step -- a call already
under way to the model server finishes first, so give it a moment before starting
another search." The button keeps the word Stop, because with the flag the word is
now true; the line is what says it is not instant.

## Consequences

Stop stops the run, coarsely: everything after the current step is saved, which
in the common case is the extraction -- the second and slower of the two model
calls -- and always the report a run nobody is reading would otherwise log. It is
not instant, and the wording on the page is what carries that rather than a
promise nothing can keep.

The obligations:

- A step added to `BuyAgent.run` announces itself to `checkpoint` before it runs,
  or a stopped run pays for it anyway. `tests/test_agent.py` pins the names and
  their order; a boundary belongs to its step, so a `fetch` that is not going to
  happen is not announced.
- A checkpoint must not be caught by the pipeline. `_refine_query` swallows a
  broad `Exception`, which is why no checkpoint is called inside it.
- `_Stopped` stays out of `api._STATUS`, out of `__main__.main` and out of
  `BuyAgent.run`'s `Raises:`. Putting it in any of them would break the
  three-failure agreement `tests/test_conventions.py` reads off all three, and
  would be answering a client that has already gone.
- Every stand-in for `BuyAgent` -- the stubs in `tests/test_api.py`,
  `tests/test_server.py` and `tests/test_conventions.py`, and `demo/server.py`'s
  scripted model if it ever grows one -- takes the keyword, since `run_search`
  always passes it.
- Detection is only as prompt as the next frame, so `_KEEPALIVE_SECONDS` is what
  bounds how long a reader can be gone unnoticed. Raising it makes Stop slower.
