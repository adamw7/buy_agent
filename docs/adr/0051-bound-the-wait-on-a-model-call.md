# ADR-0051: Bound the wait on a model call, and ask once

- **Status:** Accepted
- **Date:** 2026-09-12

## Context

Both providers are reached through a client this package builds, and neither was
given a timeout. What that meant was not "a long wait" but different wrong
answers on each row:

- `ollama.Client(base_url)` passes `timeout` straight to `httpx.Client`, and
  `None` there *disables* httpx's own. An Ollama that accepted the prompt and
  then went quiet -- a model being loaded into too little memory, a laptop
  asleep, a container with no GPU left -- hung `BuyAgent.run` with nothing to
  catch and nothing to report. On the CLI that is a process to go and find; in
  the browser it is a stream that pings every 15s for as long as anyone watches
  (ADR-0011).
- `openai.OpenAI(...)` defaults to 600 seconds *and two retries*, so the same
  failure against a vLLM took half an hour and sent the 4.3k-token extraction
  prompt three times to a server already too slow for it.

The provider rows had the sentence for this failure all along: `_too_slow_hint`
says "did not answer in time ... try a smaller model, or a smaller --num-ctx",
which is the right remedy and was unreachable on the Ollama row, no timeout
existing to raise `httpx.TimeoutException`.

A listing was already bounded, at `_LIST_TIMEOUT` of five seconds, because a form
waits on it (ADR-0032). That number is no answer for a chat call: the two are
"how long a page waits to draw a dropdown" and "how long a shopper waits for an
answer", and a 0.6B model on CPU takes about 75 seconds of it.

## Decision

`AgentConfig.model_timeout` is how long one question may take, in seconds,
defaulting to 600 and bounded by `config.LIMITS` to 1..3600. It is an ordinary
setting: `--model-timeout` on the CLI, `model_timeout` in both JSON payloads, a
number box in the form.

Both provider rows set it on the client they build, and neither asks twice: the
OpenAI client is given `max_retries=0`. The wait a shopper set is the wait they
get -- a client retrying behind the number would make it mean three times itself
-- and a question that timed out is a hint about the model rather than a question
to repeat.

It is deliberately absent from `_asks_the_same_question`, the fingerprint a
remembered answer is filed under (ADR-0044): how long a run was willing to wait
decides nothing about what the model said.

## Consequences

- A hung model server now ends a run with the sentence `_too_slow_hint` was
  written for, as a `ModelUnavailableError` -- which is one of the three failures
  `BuyAgent.run` already documents (ADR-0009), so nothing downstream changes.
- Ten minutes is long enough for the slow end of what this is run with and short
  enough to be an upper bound. A big model on CPU with a wide window can exceed
  it, and the remedy is the one the hint names before it is a bigger number here.
- The two timeouts in `providers.py` now have to stay different on purpose. A
  change that makes the listing read `model_timeout` puts a ten-minute wait
  behind the model picker; `test_the_listing_keeps_its_own_short_wait` is what
  says so.
- `max_retries=0` means a transient 5xx from a vLLM fails the run rather than
  being retried inside the client. That is the same promise the rest of the
  package makes about the model: the *web* is retried, once, where a server said
  to come back (ADR-0053), and the model is not, a repeated extraction prompt
  being the most expensive thing this does.
- The setting is one more row in every table an option lives in --
  `config.LIMITS`, `api._BOUNDED`, `defaults_payload`, `AgentDefaults`,
  `SearchOptions`, the form's `numberFields` -- and
  `test_both_front_doors_hold_a_number_to_the_same_range` holds the doors
  together.
- `fetch.enrich` is no longer the widest function in the package on its own:
  `.pylintrc`'s `max-args` and `max-locals` track what is actually there, and
  both moved by one with the clock of ADR-0053.
