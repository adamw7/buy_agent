# Tests

Two suites, one per language, and the checks that watch them: coverage floors on
both, cross-module conventions, a PowerShell script neither suite can run, a
nightly run against a real model, and a weekly mutation run. Everything the
[README](../README.md) leaves out.

```powershell
python -m pytest              # whole suite
python -m pytest tests/test_ranking.py::test_cheaper_wins_when_rating_is_equal

python -m coverage run -m pytest ; python -m coverage report   # with coverage

cd ui; npm test               # the UI's own tests, in jsdom
cd ui; npm run test:coverage  # the same, with a coverage floor

python -m pytest integration  # against a real Ollama; see below
```

1003 Python tests and 105 UI tests. Nothing in either suite touches the network or
a model server: the model is faked through the `llm=` argument of `BuyAgent`,
both the search backend and the page fetcher are monkeypatched, the two clients
`buy_agent.providers` builds are patched where that module imported them, and the
server tests inject a stub agent through `create_server(agent_factory=...)`. The
only real sockets are the loopback ones the HTTP tests need in order to be about
HTTP at all. The 20 tests in `integration/` are the exception that proves it, and
they live outside `testpaths` so that a bare `pytest` cannot reach them.

Both suites are measured, and CI fails on a drop: the Python side covers every
line and branch (`.coveragerc` sets the floor at 99%), and the UI's statements
and lines sit just under 100% (`ui/scripts/check-coverage.mjs`, floor 98%). Line
coverage that high stops being a useful signal on its own, so
`tests/test_conventions.py` asserts the rules that hold *between* modules --
the three places a failure mode has to be listed, the four places a sort
criterion has to be offered, the ranges both front ends hold a number to -- and
the third door, the form, taking those ranges off the server rather than out of
its own markup, and sending every key a refusal can name (ADR-0033) -- the two
halves of a provider agreeing about which
providers exist, the payloads `ui/src/app/agent.types.ts`
mirrors, the `Dockerfile` agreeing with CI and with the server's own defaults,
the four workflows agreeing on the version of every action they share and on
the Python and the Node they run, the release archive carrying the UI build
where the server looks for it, the
nightly run pulling the model the live tests ask for, and
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

## Integration tests

Everything above fakes the model, which leaves the half of this project that is
a *claim about Ollama* checked nowhere: that a JSON schema compiled into a
decoding grammar makes `"N/A"` in a number impossible (ADR-0004), that
`reasoning=False` and `num_ctx=8192` are what make a thinking model answer at
all (ADR-0019), and that a stopped server arrives as a raw `httpx` error rather
than an `OSError` -- the reason `BuyAgent._invoke` catches four things. The unit
tests raise those four themselves, which proves the `except` tuple contains them
and nothing about what Ollama raises.

`integration/` closes that, on a model small enough to run anywhere (ADR-0026):

```powershell
ollama pull qwen3:0.6b
python -m pytest integration
```

The model is real and the web is not. `search_web` and `enrich` are still
patched, over ten fabricated pages `integration/conftest.py` owns -- a nightly
failure caused by DuckDuckGo rate-limiting or a shop redesigning its listing
would report nothing about this code. One session-scoped fixture runs the
pipeline once, because a CPU model answers in seconds rather than milliseconds,
and each test reads something different off the same answer.

The fake stops at the *transport*, though. `enrich` reads the fabricated page
text instead of fetching a URL, and then condenses it with the real
`fetch.condense` on the real `page_chars` and `opinion_chars` budgets -- so the
prompt is shaped the way a production prompt is shaped, and a verdict worded
outside `fetch._OPINION`'s vocabulary never reaches the model here either. Ten
pages rather than three because the *width* of the prompt is under test too:
ADR-0019's `num_ctx` is about a prompt that fills the window, and three pages of
tidy prose came to ~675 tokens, where the question cannot arise.

Almost nothing there asserts the model was *right*. What is asserted is what
holds whatever came back: every name, price, rating and quote is in the sources,
every link is a page that was searched, a currency never outlives its price,
nothing is listed twice, the ranking is ordered and numbered. Those are the
guarantees the unit suite checks against an answer it dictated; here they meet
one nobody wrote. The one exception is a smoke test that *something* was
extracted -- and a second that something was *quoted*, since the opinion
assertions go vacuous the same way. Neither holds the model to an answer: both
read what the extraction chain returned, before grounding judged it.

Two knobs, and neither is `$OLLAMA_MODEL` -- that one moves the default the
agent ships with, and must not be able to start a 12B pull on a runner:

| Variable | Effect |
| --- | --- |
| `BUY_AGENT_TEST_MODEL` | Test against another tag instead of `qwen3:0.6b` |
| `BUY_AGENT_REQUIRE_OLLAMA` | Fail where Ollama is absent, instead of skipping |

Without Ollama every test skips, so these are opt-in locally.
`.github/workflows/integration.yml` sets `BUY_AGENT_REQUIRE_OLLAMA` for the
opposite reason: a scheduled run that quietly skipped every test it has is a
green job that checked nothing.

That workflow runs at `41 3 * * *` and on demand, never on a pull request, and
caps itself at **five minutes** -- installing Ollama, pulling half a gigabyte of
model and doing the inference, all inside it. Ollama and the model tag are
deliberately unpinned where the rest of this project pins everything: noticing
that a new release has changed how `method="json_schema"` decodes is half of
what the job is for.

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
