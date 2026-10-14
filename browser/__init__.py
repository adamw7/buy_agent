"""Browser tests: the built page in a real Chromium, behind the real server (ADR-0087)."""

from __future__ import annotations

#: Set by the Sunday workflow, browser.yml, and by nobody else.
REQUIRE_ENV_VAR = "BUY_AGENT_REQUIRE_BROWSER"

__all__ = ["REQUIRE_ENV_VAR"]
