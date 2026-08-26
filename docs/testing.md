# Tests

Two suites, one per language, and the checks that watch them: coverage floors on
both, cross-module conventions, a PowerShell script neither suite can run, and a
weekly mutation run. Everything the [README](../README.md) leaves out.

```powershell
python -m pytest              # whole suite
python -m pytest tests/test_ranking.py::test_cheaper_wins_when_rating_is_equal

python -m coverage run -m pytest ; python -m coverage report   # with coverage

cd ui; npm test               # the UI's own tests, in jsdom
cd ui; npm run test:coverage  # the same, with a coverage floor
```

608 Python tests and 62 UI tests. Nothing in either suite touches the network or
Ollama: the model is faked through the `llm=` argument of `BuyAgent`, both the
search backend and the page fetcher are monkeypatched, and the server tests
inject a stub agent through `create_server(agent_factory=...)`. The only real
sockets are the loopback ones the HTTP tests need in order to be about HTTP at
all.

Both suites are measured, and CI fails on a drop: the Python side covers every
line and branch (`.coveragerc` sets the floor at 99%), and the UI's statements
and lines sit just under 100% (`ui/scripts/check-coverage.mjs`, floor 98%). Line
coverage that high stops being a useful signal on its own, so
`tests/test_conventions.py` asserts the rules that hold *between* modules --
the three places a failure mode has to be listed, the four places a sort
criterion has to be offered, the payloads `ui/src/app/agent.types.ts`
mirrors, the `Dockerfile` agreeing with CI and with the server's own defaults,
the two workflows agreeing on the version of every action they share, and
the decision log agreeing with its own index -- which no amount of per-module
coverage can protect.

Both suites run on Windows and on Linux. `.github/workflows/ci.yml` spreads its
two jobs -- `coverage run -m pytest` on Python 3.13, `npm run test:coverage &&
npm run build` on Node 22.22.3 -- over `ubuntu-latest` and `windows-latest`, four
runs in all, with `fail-fast` off so a failure on one platform still reports the
other. This project is written on Windows and its runners were Linux, which left
each of them checking the half of the differences the other hides: a path
separator, a default encoding, a socket that resets where the other closes, a
`mimetypes` lookup that reads the registry (ADR-0020).

`scripts/start.ps1` is the one file neither suite can import or run, so
`tests/test_start_script.py` does everything short of running it: a PowerShell
helper parses the script, lifts out the functions it declares and exercises them
on a stubbed clock and a stubbed web request, and reports what it found as JSON.
Those tests skip where there is no `pwsh` or `powershell` on PATH, which is
neither Windows nor either runner CI uses. The Windows job is the one that runs
them on the platform the script is actually for.

## Mutation testing

Coverage says every line ran. Whether anything would have complained had a line
run *differently* is a separate question, and the one
[mutmut](https://github.com/boxed/mutmut) asks: it breaks the code on purpose --
an `and` for an `or`, a `+= 1` for a `= 1` -- and reports the mutants the suite
still passes on. Those are the lines that are covered and unchecked.

`.github/workflows/mutation.yml` runs it against `buy_agent/` every Saturday
morning and on demand, never on a pull request: it takes a couple of minutes
where the suite takes three seconds, and it is a report rather than a gate
(ADR-0016). The job summary carries the score, a row per module worst first, and
the functions the survivors cluster in; the full list is uploaded as an artifact.
The run fails only if the score drops under 75%, which is a guard against a
module arriving with thin tests, not a target. It sits at 77% today, and a good
share of the survivors are equivalent mutants -- a reworded log line, a debug
counter nothing reads.

To run it locally (settings, including what to mutate and what to copy alongside
it, are in `setup.cfg`):

```powershell
pip install -r requirements-mutation.txt
python -m mutmut run                        # a couple of minutes; results cached
python -m mutmut results --all true > mutation-results.txt
python scripts/mutation_report.py mutation-results.txt
python -m mutmut browse                     # or read them one mutant at a time
```

A run copies the tree to `mutants/` and tests the copy, so both that directory
and `mutation-results.txt` are ignored by git.
