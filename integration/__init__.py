"""Integration tests: the same pipeline, against a real Ollama model.

Separate from ``tests/`` on purpose, and not under it. ``pytest.ini`` sets
``testpaths = tests``, so ``python -m pytest`` never collects this package --
which is what keeps "nothing in the suite touches the network or Ollama" a
property of the directory rather than a marker somebody has to remember to
apply. These are run by naming them: ``python -m pytest integration``.

What is real here is the *model*, and only the model. The web is still faked:
DuckDuckGo rate-limits, pages get rewritten, and a nightly job that fails
because a shop redesigned its listing reports nothing about this code. So the
search results are fabricated in ``conftest.py``, and every LLM call underneath
them is genuine -- Ollama's JSON-schema decoding, its thinking switch, its
transport errors, and whether a small model can still copy a price out of a page.

:data:`TINY_MODEL` is the whole reason this can run on a schedule: a
half-gigabyte model answers on a runner's four cores in seconds, where
``config.DEFAULT_MODEL`` would need a GPU and a much longer budget. It is
deliberately *not* ``$OLLAMA_MODEL``: that variable moves the default the agent
ships with, and a run of these tests must not silently start pulling a 12B model
onto a CI runner. ``$BUY_AGENT_TEST_MODEL`` moves this one instead.
"""

from __future__ import annotations

#: The model the nightly run pulls and these tests ask for. Small enough to be
#: pulled and to answer inside the five minutes
#: ``.github/workflows/integration.yml`` allows itself, and instruction-tuned
#: enough to fill in a JSON schema Ollama is already constraining it to.
#: ``tests/test_conventions.py`` checks that the workflow pulls this exact tag.
TINY_MODEL = "qwen3:0.6b"

#: Overrides :data:`TINY_MODEL`, for trying another small model without editing
#: this file. Not ``$OLLAMA_MODEL`` -- see the module docstring.
MODEL_ENV_VAR = "BUY_AGENT_TEST_MODEL"

#: Set by the nightly workflow, and by nobody else. An Ollama that is not there
#: is a skip on a developer's machine -- these tests are opt-in locally -- and a
#: failure where this is set, because a scheduled run that skipped every test it
#: has is a green job that checked nothing, and the one thing a nightly job is
#: worst at reporting is having done nothing at all.
REQUIRE_ENV_VAR = "BUY_AGENT_REQUIRE_OLLAMA"

__all__ = ["MODEL_ENV_VAR", "REQUIRE_ENV_VAR", "TINY_MODEL"]
