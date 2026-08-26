# ADR-0026: Test against a real model nightly, on one small enough to run on a CPU

- **Status:** Accepted
- **Date:** 2026-08-26

## Context

Every test in `tests/` fakes the model. `BuyAgent(config, llm=...)` takes a
`FakeLLM` whose `with_structured_output` returns a canned object, the search
backend and the page fetcher are monkeypatched, and `create_server` is handed a
stub agent. That is deliberate and worth keeping: the suite runs in three and a
half seconds, on any machine, with nothing installed, and it is the reason
anything gets tested at all.

What it cannot check is the half of this project that is a claim about Ollama.

- **ADR-0004 is a claim about decoding.** Extraction fields are non-nullable with
  sentinels because Ollama compiles the JSON schema into a grammar, which makes
  `"N/A"` in a number structurally impossible. Against `FakeLLM`, which returns
  a `ProductList` because it was told to, that claim is untested. It is a claim
  about a *third party's* behaviour, and third parties ship releases.
- **ADR-0019 is a claim about a model's attention.** `reasoning=False` and
  `num_ctx=8192` are there because a thinking model on Ollama's default 4096
  window spends the whole context reasoning about a copying task and emits no
  JSON at all. Nothing in the suite has ever watched that happen or not happen.
- **`BuyAgent._invoke` catches four things**, with the longest docstring in the
  module explaining that a stopped Ollama arrives as a raw `httpx` error rather
  than an `OSError`, because `ChatOllama` always chats over the streaming path.
  The unit tests raise each of those four themselves. That proves the `except`
  tuple contains them and nothing whatever about what Ollama actually raises.
- **Grounding is written against a model that improvises**, and the fake never
  does. `clean_products`, `ground`, `verify_opinions` and `deduplicate` exist
  because a small model reports listicle headlines, carries figures out of the
  prompt's own example, and paraphrases a reviewer. Every test of them feeds
  them an invention chosen by the test author.

Two things stopped this being obvious. Line coverage is ~100% on both suites, so
it had nothing left to say; and ADR-0014's conventions tests and ADR-0016's
mutation run both improve on coverage while staying entirely inside the same
faked world.

The obvious objection is cost. `DEFAULT_MODEL` is `gemma4:12b`, which wants a
GPU and minutes per call; a suite that needs one is a suite that runs on one
developer's machine and no CI at all.

## Decision

A second, separate suite in `integration/`, run against a real Ollama on a model
small enough for a runner's four cores, nightly.

- **The model is real; the web is not.** `search_web` and `enrich` are still
  faked, over three fabricated pages `integration/conftest.py` owns. DuckDuckGo
  rate-limits and shops redesign their listings; a nightly failure caused by
  either would report nothing about this code, and a suite whose failures are
  usually somebody else's is one people stop reading.
- **The model is a tiny one, named in `integration/__init__.py`.** `TINY_MODEL`
  is `qwen3:0.6b` -- half a gigabyte, instruction-tuned enough to fill in a
  schema Ollama is already constraining it to, and answering in seconds without
  a GPU. `$BUY_AGENT_TEST_MODEL` moves it; `$OLLAMA_MODEL` deliberately does
  not, because that variable moves the default the agent *ships* with and must
  not be able to start a 12B pull on a runner.
- **A directory, not a marker.** `pytest.ini` keeps `testpaths = tests`, so
  `python -m pytest` cannot collect these by accident. "Nothing in the suite
  touches the network or Ollama" stays a property of where a file is, rather
  than of somebody remembering an annotation.
- **One run, many assertions.** A CPU model answers in seconds rather than
  milliseconds, so a session-scoped fixture runs the pipeline once and each test
  reads something different off the same answer.
- **Almost nothing asserts that the model was right.** What is asserted is what
  must hold whatever came back: every name, figure and quote is in the sources,
  every link is a page that was searched, nothing is listed twice, the ranking
  is ordered. Those are the guarantees the unit suite checks against an answer
  it dictated; here they meet one nobody wrote. The single exception is a smoke
  test that *something* was extracted, because every other assertion here passes
  vacuously on an empty answer.
- **Missing Ollama skips locally and fails on the schedule.**
  `$BUY_AGENT_REQUIRE_OLLAMA`, set by the workflow and by nothing else, turns the
  skip into a failure. A nightly job that quietly skipped every test it has is
  a green job that checked nothing, and that is the failure a schedule is worst
  at reporting.
- **Nightly, capped at five minutes, never on a pull request.**
  `.github/workflows/integration.yml` runs at `41 3 * * *` and on
  `workflow_dispatch`. `ci.yml` stays what a push has to pass. The cap covers
  everything -- installing Ollama, pulling the model, and the inference -- so the
  day the model stops fitting inside it, a red run says so.
- **Ollama and the model tag are deliberately unpinned**, where this project
  pins everything else. Half of what this job is for is noticing that a new
  Ollama release or a re-tagged model has changed how `method="json_schema"`
  decodes. A pinned pair would hide exactly that until somebody upgraded, which
  is the moment it is least welcome.

## Consequences

The claims about Ollama now have somewhere to fail. A release that changes
structured-output decoding, a model tag re-pushed with different behaviour, or a
prompt edited past what a small model can follow all surface overnight instead
of on the first shopper's machine.

The obligations are three, and all three are in `tests/test_conventions.py`,
because the two sides never import each other:

- **The workflow pulls the tag `integration/__init__.py` names.** Pull a
  different one and every test skips -- or, with `$BUY_AGENT_REQUIRE_OLLAMA` set,
  fails on a machine whose Ollama is running perfectly well.
- **The workflow names the directory, and `testpaths` does not.** Outside
  `testpaths` these are collected only by being named, so a bare `pytest` in the
  workflow would run the unit suite twice and the live tests never.
- **`setup.cfg` copies `integration/` into a mutation run.** The conventions
  tests import it for the model tag, so without it the Saturday run dies at
  collection -- the failure ADR-0016 already records as the one nothing in a
  normal run can see coming.

What this costs is a suite that can fail for reasons that are not a bug here: a
model that has a bad night, an install script that changes, a runner slower than
usual. That is priced in by keeping it off pull requests entirely and by
asserting invariants rather than answers -- the assertions that survive a model
being merely mediocre are exactly the ones about this code.

It is also a suite nobody watches unless they open Actions, which is ADR-0016's
unresolved cost repeated. The five-minute cap is the partial answer: a job that
cannot finish is red rather than expensive.

Only the pipeline is covered. The server, the CLI and the UI are still tested
entirely against fakes, and a live run through `buy_agent.server` -- SSE relay,
worker thread and all -- is left open rather than half-done.
