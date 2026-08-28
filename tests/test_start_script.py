"""``scripts/start.ps1``: that it parses, what it declares, and how its helpers behave.

This is the one file in the project no other test reaches. It is PowerShell, so
pytest cannot import it; it installs, downloads, starts two servers and opens a
browser, so pytest cannot run it either. What is left is everything short of
running it, which is most of what a script gets wrong: a typo that only shows up
on the machine of whoever ran it next, a step whose failure goes unnoticed
because nothing checked an exit code, a poll that gives up on the first refusal.

``tests/start_script_probe.ps1`` is the other half. It parses the script into an
AST, lifts the function definitions out of that AST and dot-sources them on their
own -- so the body, the part that would install and download, never runs -- and
writes what it found and what those functions did as one JSON document. One
PowerShell process for the whole module, because starting one costs about as long
as the rest of this suite takes.

Everything here is skipped where there is no PowerShell to run. That is every
Linux machine without ``pwsh`` installed; it is not the Windows this script is
for, nor the runner CI uses, both of which have one.

The four agreements this script keeps with the rest of the project -- the URL it
opens, the build it looks for, the toolchain versions it names and the defaults it
refuses to write down twice -- are in ``test_conventions.py`` with the other
cross-file rules, not here.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_START = _ROOT / "scripts" / "start.ps1"
_PROBE = Path(__file__).resolve().parent / "start_script_probe.ps1"

#: ``pwsh`` is PowerShell 7 and ``powershell`` is the 5.1 that ships with Windows.
#: The script asks for 5.1 or newer and the probe uses nothing either one lacks,
#: so whichever is on PATH answers.
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")

needs_powershell = pytest.mark.skipif(
    POWERSHELL is None, reason="no pwsh or powershell on PATH to read the script with"
)


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
    """The behaviour tests below call these by name. Renamed, they would not fail:
    the probe would report that the call went wrong and every one of them would
    have to be read to see that none of them tested anything."""
    assert set(probed["functions"]) == {"Step", "Note", "Have", "Run", "Answers"}


@needs_powershell
def test_every_program_it_runs_goes_through_run(probed: dict[str, Any]) -> None:
    """``Run`` is the only thing here that looks at ``$LASTEXITCODE``, and a native
    command that fails does not raise on its own however ``$ErrorActionPreference``
    is set. A program invoked around it fails silently: pip installs nothing and
    the server starts anyway, ``ng build`` writes no bundle and the page is the
    503 that says to build it -- each one a script that reported success."""
    invoked = invocations(probed, "Ampersand")

    assert invoked, "nothing invokes a program; Run has presumably been rewritten"
    for call in invoked:
        assert call["function"] == "Run", f"line {call['line']}: {call['text']}"


@needs_powershell
def test_it_looks_for_a_program_before_running_it(probed: dict[str, Any]) -> None:
    """python, ollama and npm are the three things this script does not install,
    and each is checked for with ``Have`` first so that a machine missing one is
    told where to get it. Run without that check, the whole message is PowerShell's
    "is not recognized" -- which is true, unhelpful, and looks like a bug here."""

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
    """Ctrl+C is how this script is meant to end, and both servers are children of
    it that outlive it unless something kills them. A leftover ``buy_agent.server``
    holds port 8000, so the next run of this script starts a server that cannot
    bind and a browser pointed at the *old* one -- serving the UI built before the
    change that was being checked."""
    assert probed["cleanups"], "nothing is cleaned up at all"
    outermost = probed["cleanups"][0]

    for process in ("$serverProcess", "$ollamaProcess"):
        assert f"{process}.Kill()" in outermost, f"{process} is left running"
        assert f"-not {process}.HasExited" in outermost, f"{process} is killed unconditionally"
    assert "Pop-Location" in outermost, "the shell is left in the repository root"


@needs_powershell
def test_it_points_the_pull_at_the_ollama_it_probed(probed: dict[str, Any]) -> None:
    """``ollama pull`` reads $OLLAMA_HOST itself, and the one this script found is
    the one it waited for and is about to search against. Pulled before that
    assignment, several gigabytes land on whichever server the environment named --
    and the run that follows finds the model still missing."""
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
    """The point of the wrapper: an exit code nobody reads is a step that failed
    and a script that carried on. The message is the script's rather than the
    program's, because a pip traceback does not say which step it belonged to."""
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
    """A server that is starting refuses connections until it is listening, which
    is indistinguishable from one that never will except by waiting. Ollama takes a
    few seconds; giving up on the first refusal would fail every cold start."""
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
    """Both lines are load-bearing and neither is a default. Without ``Stop``, a
    failed ``Invoke-RestMethod`` is a red message and a script that keeps going;
    without strict mode, a variable misspelled in a condition is quietly empty --
    which reads as "not built yet" or "not pulled yet" and does the work again."""
    source = start_script()

    assert re.search(r"^Set-StrictMode -Version Latest$", source, re.M)
    assert re.search(r"^\$ErrorActionPreference = 'Stop'$", source, re.M)
    assert re.search(r"^#Requires -Version 5\.1$", source, re.M), "no floor under the syntax"


def test_it_only_starts_the_server_it_can_install() -> None:
    """Ollama is installed with one command and started with another; a vLLM needs
    a GPU, a served model and flags this script has no business choosing. So the
    whole install-and-pull half is behind the provider check, and anything else is
    waited for and named rather than launched -- a script that tried to
    ``vllm serve`` on a laptop would fail in a way that read as this script's bug."""
    source = start_script()
    guard = re.search(r"^\s+if \(\$provider -eq 'ollama'\) \{$", source, re.M)
    assert guard, "the Ollama steps are not behind a check of which provider is configured"

    for line in ("Start-Process 'ollama'", "Run 'ollama' @('pull', $model)"):
        assert line in source
        assert source.index(line) > guard.start(), f"{line} runs whatever the provider is"


def test_it_probes_the_endpoint_the_other_provider_actually_answers() -> None:
    """vLLM's API root is a 404 on a server that is working perfectly; ``/models``
    is the listing the form's model picker calls anyway. Probing the root would
    make every run stop on a server that was up the whole time."""
    assert '(Answers "$llm/models" 1)' in start_script()


def test_it_takes_no_arguments() -> None:
    """README, CLAUDE.md and the script's own synopsis all say so, and the reason is
    that everything it could ask is already a setting the rest of the project reads
    from somewhere: a parameter here would be a second way to say one of them."""
    assert not re.search(r"^\s*param\s*\(", start_script(), re.M | re.I)
