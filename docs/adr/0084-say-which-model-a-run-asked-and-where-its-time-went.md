# ADR-0084: Say which model a run asked and where its time went

- **Status:** Accepted
- **Date:** 2026-10-13

## Context

A run on a local model spends most of its time in one call. The README's own
example waits 64 seconds between "Got usable page text from 10 of 10 result(s)"
and "Extracted 9 candidate(s)", and the log said nothing through it, nor after
it, about how long that was or which model took it. The benchmark measured model
time (its `Stopwatch`), so a model could be compared on a page, but a shopper's
own run could not say whether its minute went on the model, the search or the
fetching.

The model is seldom typed, either. It comes from `$OLLAMA_MODEL` and its
siblings, or from the provider row's default, so a log pasted into a bug report,
or downloaded from the page after a failed run, did not say which model on which
server produced it.

Where to measure was decided by the import graph. `fetch.py` is a pipeline step
and `search.py` a seam, and neither may import `logging_setup`
(`tests/test_architecture.py`); a step takes values and answers values. The agent
orchestrates the steps and already announces each to the checkpoint (ADR-0034),
so it is the one place that sees each start and end.

## Decision

- **`BuyAgent.run` names the model it opened**, in the words the hints already
  use: "Asking gemma4:12b on Ollama at http://localhost:11434", right after
  "Shopping for:". A model handed in (`llm=`) is its caller's to name: the
  config then names a model that is not answering, as the benchmark's scripted
  answers run under the default config.
- **`agent._Stopwatch` times the steps a run waits on**: `query`, `search`,
  `fetch` and `extract`, where `extract` is the model call and not the grounding
  after it. A step that failed and was recovered from (a refinement the model
  botched) is timed all the same.
- **Each model call's line says how long it took**: "Refined search query in
  2.5 s: ...", "Extracted 9 candidate(s) in 1 min 04 s".
- **Every run that returns ends with one line**, whether it found anything or
  not: "Finished in 1 min 09 s: query 2.5 s, search 1.0 s, fetch 1.5 s, extract
  1 min 04 s" -- the whole, then each step in the order it ran. A run that
  raises does not: its door says what went wrong, and a stopped run says where
  it stopped.
- **`logging_setup.seconds_label` is the one wording of a duration**, which the
  benchmark imports rather than defines.

## Consequences

A log says which model it is about and where the wait was, without `-v` and
without the benchmark. The cost is two lines a run, and a total that includes
the untimed work between steps (grounding, ranking), which is milliseconds.

The obligations:

- **A new step a run waits on is timed in `agent.py`**, with `watch.timing`
  beside its checkpoint, never in its own module.
- **The summary is written when it is logged.** `_Stopwatch.summary()` reads the
  clock once; a `__str__` handed to a lazy `%s` would read it again in every
  handler that formats the record, and the console and the relay would disagree.
- **The line names `model`, `label` and `base_url` and nothing else**, the
  fields `provider_options()` already offers. An API key never reaches a log.
- **The benchmark keeps its own `Stopwatch`.** It times the `ChatModel` it hands
  in and sums model time per contender, which no log line can give it.
- `tests/test_agent.py` patches `buy_agent.agent.perf_counter` with a `Clock`
  that each stand-in advances by its own figure, so a step reported as another
  reads wrong; `test_a_duration_is_written_the_same_way_everywhere` holds the
  wording.
