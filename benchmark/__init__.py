"""A deterministic benchmark for the agent: one fixed corpus, one answer key.

``integration/`` asks whether the pipeline's promises held; this asks how well it
did. Different questions, different fixtures, one run: the corpus and the run
settings live in :mod:`benchmark.corpus` and ``integration/conftest.py`` reads
them back, which keeps the nightly job to a single model call and keeps a score
attached to the very run the invariant tests were reading.

Everything except the model is fixed -- ten fabricated pages, one request,
declared widths, an answer key transcribing what those pages print, and a scorer
that is a pure function of the two. Run it twice on the same model output and the
numbers are the same numbers, which is what "deterministic" is doing in the name
and what makes two scores a month apart comparable.

Three ways in:

* ``python -m benchmark --scripted perfect`` -- no model, no network, scores
  1.000. The reference, and what makes the scorer itself testable.
* ``python -m benchmark`` -- the same run against whatever model server
  :class:`~buy_agent.config.AgentConfig` points at.
* ``python -m pytest integration`` -- ``integration/test_benchmark.py`` scores
  the nightly live run and fails under :data:`benchmark.scoring.FLOORS`.

ADR-0036 has why the key is per-product sets rather than one right answer, and
what the benchmark catches that the invariant tests structurally cannot. Nothing
is re-exported here: every module is reached by its own name (ADR-0021).
"""
