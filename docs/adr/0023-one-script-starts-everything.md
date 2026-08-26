# ADR-0023: Start the whole stack from one script, and give it nothing to decide

- **Status:** Accepted
- **Date:** 2026-08-26

## Context

Getting the page up on a fresh machine is six steps, each with its own skip
condition: create `.venv`, install `requirements.txt`, have an Ollama serving,
have the model pulled, build `ui/`, start `buy_agent.server`. README walks
through them, and every one of them is quietly conditional -- the second run
needs none of the downloads, and a machine with no Node needs the server anyway,
because the API answers without the page (ADR-0013).

Left to the reader, that is six commands to remember and one wrong order away
from a 503 nobody can explain. Written as a script, it invites the opposite
failure: a startup script is exactly where a model tag, a port and an Ollama URL
get typed in for convenience, and from then on `config.py` is one of two places
the default lives. `$OLLAMA_MODEL` would move one of them and not the other.

## Decision

`scripts/start.ps1` is the README's "Starting it on localhost" as one command
with no arguments, and it decides nothing the rest of the project decides.

- Every value it needs that the project already holds is read back out of the
  project at run time -- `DEFAULT_MODEL` and `DEFAULT_BASE_URL` through a
  `python -c` -- so `$OLLAMA_MODEL` and `$OLLAMA_HOST` still reach it and no
  default is written down twice. Anything beyond that is a flag on
  `python -m buy_agent.server`, which is why the script takes no parameters of
  its own.
- Each step is skipped when it is already done, and only what the script started
  is stopped when it ends.
- Every external program goes through `Run`, which checks `$LASTEXITCODE`: a
  native command that fails raises nothing in PowerShell whatever
  `$ErrorActionPreference` says, so a step without that check fails silently and
  the next step reports something unrelated.
- Both servers are polled with `Answers` rather than assumed up. A port that is
  not listening *yet* looks exactly like one that never will.

It is tested like everything else that decides an answer, and it cannot be run to
test it -- it installs, downloads, starts two servers and opens a browser. So
`tests/start_script_probe.ps1` does everything short of running it: it parses the
script into an AST, lifts the function definitions out of that AST and
dot-sources them on their own, leaving the body unrun, and reports what it found
as JSON. `Run` is then exercised against a real interpreter and `Answers` against
a stubbed `Invoke-WebRequest` and a clock that only moves when it sleeps. The AST
is also what enforces the rule the error handling rests on: every program the
script runs goes through `Run`.

The four agreements it keeps with the rest of the project are cross-file rules,
so they live in `tests/test_conventions.py` with the others (ADR-0014): the URL
it opens is the one `server.build_parser` binds, the build it probes for is the
one `server.DEFAULT_UI_DIR` serves, the Python and Node it sends you to install
are the ones `ci.yml` pins, and neither default's *value* appears in the script.

## Consequences

A first run is one command, and a second costs the seconds pip needs to say it
has nothing to do. Changing the model or the Ollama server stays a change to
`config.py` or an environment variable, and the script picks it up without an
edit -- which is the whole reason it reads them rather than knowing them.

The costs are real. It is PowerShell, matching the Windows this is developed on;
elsewhere it needs `pwsh`, and where there is neither, all of
`tests/test_start_script.py` skips except the two tests that read the script as
text. And the probe never runs the script's body: the flow through the steps is
parsed and read but not executed, so a mistake there surfaces on the machine of
whoever runs it next. That is why the body stays a sequence of small steps over
tested helpers, and why anything with a decision in it belongs in a function the
probe can call.

Two obligations follow for anything added to the script. A step that runs a
program goes through `Run`, or the AST test fails. A setting it needs is read out
of `buy_agent.config` -- or given to the server as a flag -- rather than typed
into the script, or the conventions test that looks for those values fails.
