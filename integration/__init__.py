"""Integration tests: the same pipeline, against a real Ollama model."""

from __future__ import annotations

#: The model the nightly run pulls and these tests ask for.
TINY_MODEL = "qwen3:0.6b"

#: Overrides :data:`TINY_MODEL`, for trying another small model without editing this file.
MODEL_ENV_VAR = "BUY_AGENT_TEST_MODEL"

#: Set by the nightly workflow, and by nobody else.
REQUIRE_ENV_VAR = "BUY_AGENT_REQUIRE_OLLAMA"

#: What one of these tests may take before it is a stopped one, in seconds.
LIVE_TIMEOUT_SECONDS = 120

__all__ = ["LIVE_TIMEOUT_SECONDS", "MODEL_ENV_VAR", "REQUIRE_ENV_VAR", "TINY_MODEL"]
