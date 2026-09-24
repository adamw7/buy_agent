"""``scripts/setup.ps1`` and ``scripts/preflight.ps1``: that they parse, and how each
runs a program (ADR-0067).

Read through the probe ``tests/test_start_script.py`` reads ``scripts/start.ps1``
with, which parses a script into an AST and runs none of its body. What either
script *installs* and *checks* is a rule between it and ``ci.yml``, and lives in
``tests/test_conventions.py`` with the others.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import POWERSHELL, needs_powershell

_ROOT = Path(__file__).resolve().parent.parent
_SETUP = _ROOT / "scripts" / "setup.ps1"
_PREFLIGHT = _ROOT / "scripts" / "preflight.ps1"
_PROBE = Path(__file__).resolve().parent / "start_script_probe.ps1"


def probe(script: Path, report: Path) -> dict[str, Any]:
    """What the probe found in ``script``: its parse errors, functions and commands."""
    finished = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(_PROBE),
            "-Script",
            str(script),
            "-Python",
            sys.executable,
            "-OutFile",
            str(report),
        ],
        capture_output=True,
        text=True,
        cwd=_ROOT,
    )

    assert finished.returncode == 0, finished.stdout + finished.stderr
    # utf8 out of Windows PowerShell carries a byte-order mark; json.loads will not.
    return dict(json.loads(report.read_text(encoding="utf-8-sig")))


@pytest.fixture(scope="module")
def setup(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return probe(_SETUP, tmp_path_factory.mktemp("setup") / "probe.json")


@pytest.fixture(scope="module")
def preflight(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return probe(_PREFLIGHT, tmp_path_factory.mktemp("preflight") / "probe.json")


def invoked(probed: dict[str, Any], operator: str) -> list[dict[str, Any]]:
    """Every command of one kind: ``Ampersand`` is ``& $exe``, ``Unknown`` a name."""
    return [call for call in probed["invocations"] if call["operator"] == operator]


@needs_powershell
def test_both_scripts_parse(setup: dict[str, Any], preflight: dict[str, Any]) -> None:
    """A PowerShell script is compiled whole before its first line runs, so a typo
    anywhere is a script that does nothing at all -- on a new joiner's first day."""
    assert setup["parseErrors"] == []
    assert preflight["parseErrors"] == []


@needs_powershell
def test_every_program_setup_runs_goes_through_run(setup: dict[str, Any]) -> None:
    """``Run`` is the only thing there that reads ``$LASTEXITCODE``, and a native
    command that fails raises nothing however ``$ErrorActionPreference`` is set --
    so a pip that failed would be a setup that carried on and reported success."""
    calls = invoked(setup, "Ampersand")

    assert calls, "nothing invokes a program; Run has presumably been rewritten"
    for call in calls:
        assert call["function"] == "Run", f"line {call['line']}: {call['text']}"


@needs_powershell
def test_setup_looks_for_a_program_before_running_it(setup: dict[str, Any]) -> None:
    """Python, Node, npm and git are what it does not install, so each is looked for
    with ``Have`` first and a machine missing one is told where to get it rather
    than shown PowerShell's own 'not recognized'."""

    def arguments(function: str) -> set[str]:
        return {
            call["text"].split()[1].strip("'()")
            for call in invoked(setup, "Unknown")
            if call["name"] == function
        }

    # $python is the venv's own, created a step earlier rather than looked for.
    named = arguments("Run") - {"$python"}
    checked = arguments("Have")

    assert named, "nothing is run by name any more"
    for program in named:
        assert program in checked, f"{program} is run without checking it is installed"


@needs_powershell
def test_every_program_preflight_runs_goes_through_job(preflight: dict[str, Any]) -> None:
    """``Job`` records a failing step and stops that job, and nothing else there reads
    an exit code -- a step run beside it would be a red check reported as green."""
    calls = invoked(preflight, "Ampersand")

    assert calls, "nothing invokes a program; Job has presumably been rewritten"
    for call in calls:
        assert call["function"] == "Job", f"line {call['line']}: {call['text']}"
