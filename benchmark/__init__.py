"""A deterministic benchmark for the agent: one fixed corpus, one answer key.

``integration/`` asks whether the pipeline's promises held; this asks how well it
did. The two are different questions and need different fixtures, so they share
one run: the corpus and the run settings live in :mod:`benchmark.corpus`, and
``integration/conftest.py`` reads them back, which keeps the nightly job to a
single model call and keeps a benchmark score attached to the very run the
invariant tests were reading.

Everything except the model is fixed. The web is ten fabricated pages, the
request is one sentence, the widths are declared, the answer key is a
transcription of what those pages print, and :func:`benchmark.scoring.score_run`
is a pure function of the two. Run it twice with the same model output and the
eight numbers are the same numbers -- which is what "deterministic" is doing in
the name, and what makes a score comparable between two runs a month apart.

Three ways in:

* ``python -m benchmark --scripted perfect`` -- no model, no network, scores
  1.000. The reference, and the reason the scorer itself is testable.
* ``python -m benchmark`` -- the same run against whatever model server
  :class:`~buy_agent.config.AgentConfig` points at, printing the scorecard.
* ``python -m pytest integration`` -- ``integration/test_benchmark.py`` scores
  the nightly live run and fails under :data:`benchmark.scoring.FLOORS`.

See ADR-0036 for why the answer key is per-product sets rather than one right
answer, and what the benchmark catches that the invariant tests structurally
cannot.
"""

from __future__ import annotations

from benchmark.answers import ANSWER_KEY, Expected
from benchmark.corpus import NUM_PRODUCTS, PAGE_TEXT, PAGES, REQUEST, TOP_N, settings
from benchmark.runner import Report, run_benchmark, serving_the_corpus
from benchmark.scoring import FLOORS, WEIGHTS, Scorecard, score_run

__all__ = [
    "ANSWER_KEY",
    "FLOORS",
    "NUM_PRODUCTS",
    "PAGES",
    "PAGE_TEXT",
    "REQUEST",
    "TOP_N",
    "WEIGHTS",
    "Expected",
    "Report",
    "Scorecard",
    "run_benchmark",
    "score_run",
    "serving_the_corpus",
    "settings",
]
