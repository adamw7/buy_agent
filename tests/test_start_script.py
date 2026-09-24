"""``scripts/start.ps1``: that it parses, what it declares, and how its helpers behave."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import POWERSHELL, needs_powershell

_ROOT = Path(__file__).resolve().parent.parent
_START = _ROOT / "scripts" / "start.ps1"
_PROBE = Path(__file__).resolve().parent / "start_script_probe.ps1"


def start_script() -> str:
    return _START.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def probed(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """What one run of the probe found, shared by every test in this module."""
    report = tmp_path_factory.mktemp("start_script") / "probe.json"
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
            str(_START),
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
    return json.loads(report.read_text(encoding="utf-8-sig"))


def case(probed: dict[str, Any], name: str) -> dict[str, Any]:
    """One probe result, with the exception it raised reported as the failure."""
    result = probed["cases"].get(name)

    assert result is not None, f"the probe never reached {name}; see the failures above"
    assert "error" not in result, f"{name}: {result['error']}"
    return result


def invocations(probed: dict[str, Any], operator: str = "Unknown") -> list[dict[str, Any]]:
    """Every command the script invokes, ``Ampersand`` being ``& $exe`` and
    ``Unknown`` a name written out -- a cmdlet, a function of its own, or a
    program PowerShell will go looking for on PATH."""
    return [call for call in probed["invocations"] if call["operator"] == operator]


# -- what the script is --------------------------------------------------------


@needs_powershell
def test_the_startup_script_parses(probed: dict[str, Any]) -> None:
    """A PowerShell script is compiled whole before its first line runs, so a typo
    anywhere in it is not a broken step -- it is a script that does nothing at all,
    on the machine of whoever ran it next. Nothing else here would see one."""
    assert probed["parseErrors"] == []


@needs_powershell
def test_it_declares_the_helpers_the_rest_of_these_tests_exercise(probed: dict[str, Any]) -> None:
    """The behaviour tests below call these by name."""
    assert set(probed["functions"]) == {"Step", "Note", "Have", "Run", "Stale", "Answers"}


@needs_powershell
def test_every_program_it_runs_goes_through_run(probed: dict[str, Any]) -> None:
    """``Run`` is the only thing here that looks at ``$LASTEXITCODE``, and a native
    command that fails does not raise on its own however ``$ErrorActionPreference`` is
    set."""
    invoked = invocations(probed, "Ampersand")

    assert invoked, "nothing invokes a program; Run has presumably been rewritten"
    for call in invoked:
        assert call["function"] == "Run", f"line {call['line']}: {call['text']}"


@needs_powershell
def test_it_looks_for_a_program_before_running_it(probed: dict[str, Any]) -> None:
    """python, ollama and npm are the three things this script does not install, and each
    is checked for with ``Have`` first so that a machine missing one is told where to
    get it."""

    def arguments(function: str) -> set[str]:
        return {
            call["text"].split()[1].strip("'")
            for call in invocations(probed)
            if call["name"] == function
        }

    # $python is the venv's own, created a step earlier rather than looked for.
    named = arguments("Run") - {"$python"}
    checked = arguments("Have")

    assert named, "nothing is run by name any more"
    for program in named:
        assert program in checked, f"{program} is run without checking it is installed"


@needs_powershell
def test_it_stops_what_it_started(probed: dict[str, Any]) -> None:
    """Ctrl+C is how this script is meant to end, and both servers are children of it that
    outlive it unless something kills them."""
    assert probed["cleanups"], "nothing is cleaned up at all"
    outermost = probed["cleanups"][0]

    for process in ("$serverProcess", "$ollamaProcess"):
        assert f"{process}.Kill()" in outermost, f"{process} is left running"
        assert f"-not {process}.HasExited" in outermost, f"{process} is killed unconditionally"
    assert "Pop-Location" in outermost, "the shell is left in the repository root"


@needs_powershell
def test_it_points_the_pull_at_the_ollama_it_probed(probed: dict[str, Any]) -> None:
    """``ollama pull`` reads $OLLAMA_HOST itself, and the one this script found is the one
    it waited for and is about to search against."""
    assigned = re.search(r"^\s+\$env:OLLAMA_HOST = \$ollama$", start_script(), re.M)
    assert assigned, "the pull is left to $env:OLLAMA_HOST as the environment had it"

    line = start_script()[: assigned.start()].count("\n") + 1
    pulls = [call for call in invocations(probed) if call["text"].startswith("Run 'ollama'")]

    assert pulls, "nothing pulls the model"
    for call in pulls:
        assert call["line"] > line, "the model is pulled before the server is named"


# -- what its helpers do -------------------------------------------------------


@needs_powershell
def test_run_hands_back_what_the_command_printed(probed: dict[str, Any]) -> None:
    """The model tag and the Ollama URL are read by running python and keeping what
    it wrote, so ``Run`` swallowing output would leave both of them empty -- and the
    script would pull a model with no name."""
    assert case(probed, "run_hands_back_what_the_command_printed")["output"] == "gemma4:12b"


@needs_powershell
def test_run_throws_its_own_message_on_a_non_zero_exit(probed: dict[str, Any]) -> None:
    """The point of the wrapper: an exit code nobody reads is a step that failed and a
    script that carried on."""
    failed = case(probed, "run_throws_its_own_message_on_a_non_zero_exit")

    assert failed["threw"], "a command that exited 3 was taken for a success"
    assert failed["message"] == "could not pull the model"


@needs_powershell
def test_have_finds_a_command_that_is_there(probed: dict[str, Any]) -> None:
    """``Get-Command`` answers for anything PowerShell can call, and the cast to
    ``[bool]`` is what turns that answer into the yes or no the callers branch on."""
    assert case(probed, "have_finds_a_command_that_is_there")["found"] is True


@needs_powershell
def test_have_is_false_for_a_command_that_is_not(probed: dict[str, Any]) -> None:
    """Under ``$ErrorActionPreference = 'Stop'`` a missing command is a terminating
    error, so the ``-ErrorAction SilentlyContinue`` inside ``Have`` is what makes it
    a question rather than the failure it is asked in order to avoid."""
    assert case(probed, "have_is_false_for_one_that_is_not")["found"] is False


@needs_powershell
def test_stale_is_true_for_something_never_made(probed: dict[str, Any]) -> None:
    """No build and no ``node_modules`` is the first run, which has to make both."""
    assert case(probed, "stale_is_true_for_something_never_made")["stale"] is True


@needs_powershell
def test_stale_is_false_for_something_newer_than_its_sources(probed: dict[str, Any]) -> None:
    """The second run, whose point is that it costs seconds: a build nothing has changed
    under is not built again."""
    assert case(probed, "stale_is_false_for_something_newer_than_its_sources")["stale"] is False


@needs_powershell
def test_stale_is_true_once_one_source_is_newer(probed: dict[str, Any]) -> None:
    """The run after a pull. Asking only whether the build existed served one from a month
    earlier against an API that had changed shape: the page stored a model object as
    ``[object Object]`` and every run after that asked Ollama for a model by that name."""
    assert case(probed, "stale_is_true_once_one_source_is_newer")["stale"] is True


@needs_powershell
def test_answers_stops_at_the_first_reply(probed: dict[str, Any]) -> None:
    """The common case is a server already running, and the whole script waits on
    this: a poll that always sleeps once would add half a second per step to a
    second run whose point is that it costs seconds."""
    assert case(probed, "answers_stops_at_the_first_reply") == {
        "answered": True,
        "attempts": 1,
        "waits": 0,
    }


@needs_powershell
def test_answers_keeps_polling_until_it_gets_one(probed: dict[str, Any]) -> None:
    """A server that is starting refuses connections until it is listening, which is
    indistinguishable from one that never will except by waiting."""
    assert case(probed, "answers_keeps_polling_until_it_gets_one") == {
        "answered": True,
        "attempts": 3,
        "waits": 2,
    }


@needs_powershell
def test_answers_gives_up_at_the_deadline(probed: dict[str, Any]) -> None:
    """And it does give up: the callers read the answer as "did it come up", and a
    poll that waited forever would hang the script on a server that has already
    printed why it is not starting."""
    assert case(probed, "answers_gives_up_at_the_deadline") == {
        "answered": False,
        "attempts": 2,
        "waits": 2,
    }


# -- what is read off the text, PowerShell or not -----------------------------


def test_it_stops_at_the_first_failure() -> None:
    """Both lines are load-bearing and neither is a default."""
    source = start_script()

    assert re.search(r"^Set-StrictMode -Version Latest$", source, re.M)
    assert re.search(r"^\$ErrorActionPreference = 'Stop'$", source, re.M)
    assert re.search(r"^#Requires -Version 5\.1$", source, re.M), "no floor under the syntax"


def test_it_only_starts_the_server_it_can_install() -> None:
    """Ollama is installed with one command and started with another; a vLLM needs a GPU,
    a served model and flags this script has no business choosing."""
    source = start_script()
    guard = re.search(r"^\s+if \(\$provider -eq 'ollama'\) \{$", source, re.M)
    assert guard, "the Ollama steps are not behind a check of which provider is configured"

    for line in ("Start-Process 'ollama'", "Run 'ollama' @('pull', $model)"):
        assert line in source
        assert source.index(line) > guard.start(), f"{line} runs whatever the provider is"


def test_it_installs_the_payment_sdk_only_where_paying_is_configured() -> None:
    """The AP2 SDK is optional, and a git checkout of somebody else's repository: fetched
    on every run it is a download nobody who is not paying asked for, and fetched on
    none the form's payment block is permanently absent with nothing on the console to
    say why."""
    source = start_script()
    guard = re.search(r"^\s+\} elseif \(\$paying\) \{$", source, re.M)
    assert guard, "the AP2 install is not behind what the environment says about paying"

    installs = list(re.finditer(r"'(requirements-ap2[\w.-]*)'", source))
    assert len(installs) == 2, "the AP2 requirements are not both installed here"
    for install in installs:
        assert install.start() > guard.start(), f"{install.group(1)} is installed on every run"
    skipped = source[source.index("} else {", guard.end()) :]
    assert "BUY_AGENT_RAIL" in skipped, "the run that will not offer to pay says nothing"


def test_it_checks_the_payment_sdk_imports_after_installing_it() -> None:
    """pip exits 0 for an install that cannot be imported -- which is the documented
    failure of this particular one, ``--no-deps`` over the wrong file leaving
    ``cryptography`` without ``cffi``."""
    source = start_script()
    asked = [match.start() for match in re.finditer(r"Run \$python \$asked", source)]

    assert len(asked) == 2, "the SDK is asked about once; an install nothing verified"
    assert "throw 'the AP2 SDK still will not import" in source


def test_it_probes_the_endpoint_the_other_provider_actually_answers() -> None:
    """vLLM's API root is a 404 on a server that is working perfectly; ``/models`` is the
    listing the form's model picker calls anyway."""
    assert '(Answers "$llm/models" 1)' in start_script()


def test_it_takes_no_arguments() -> None:
    """README, CLAUDE.md and the script's own synopsis all say so, and the reason is
    that everything it could ask is already a setting the rest of the project reads
    from somewhere: a parameter here would be a second way to say one of them."""
    assert not re.search(r"^\s*param\s*\(", start_script(), re.M | re.I)
