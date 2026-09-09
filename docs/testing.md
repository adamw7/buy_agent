# Tests

Two suites, one per language, and the checks that watch them: coverage floors on
both, cross-module conventions, a PowerShell script neither suite can run, a
nightly run against a real model, a benchmark scored against a fixed answer key,
and a weekly mutation run. Everything the [README](../README.md) leaves out.

```powershell
python -m pytest              # whole suite
python -m pytest tests/test_ranking.py::test_cheaper_wins_when_rating_is_equal

python -m coverage run -m pytest ; python -m coverage report   # with coverage

cd ui; npm test               # the UI's own tests, in jsdom
cd ui; npm run test:coverage  # the same, with a coverage floor

python -m pytest integration  # against a real Ollama; see below

python -m benchmark --scripted perfect   # the benchmark, with no model at all
python -m benchmark                      # ...and against whatever is serving
```

1787 Python tests and 187 UI tests. Nothing in either suite touches the network or
a model server: the model is faked through the `llm=` argument of `BuyAgent` -- a class
with one `answer` method, which is the whole of `chat.ChatModel`, both
the search backend and the page fetcher are monkeypatched, the two clients
`buy_agent.providers` builds are patched where that module imported them, and the
server tests inject a stub agent through `create_server(agent_factory=...)`. The
only real sockets are the loopback ones the HTTP tests need in order to be about
HTTP at all. Nor does either suite touch the machine's own cache: `conftest.py`
points `$BUY_AGENT_CACHE_DIR` at a scratch directory per test, autouse, so a test
building a real `BuyAgent` remembers its answers somewhere disposable (ADR-0044)
and no test can be answered by another test's question. The same fixture unsets
`$BUY_AGENT_AP2_KEY`, `$BUY_AGENT_AP2_MANDATE` and `$BUY_AGENT_MERCHANT_URL`, so
a developer who has configured paying does not end up with a suite that signs
with their key and buys on their budget -- every test that needs one of those
points at a file it made itself.

The payment tests do sign real mandates. `tests/test_mandates.py` builds genuine
SD-JWTs with keys generated in the test and reads them back through the AP2
SDK's own verifier -- the one a merchant or a credential provider runs -- because
a mandate that verifies only against a fake verifier is a mandate nobody else
would take. That needs the optional SDK: `pip install -r requirements-ap2-deps.txt` and then
`pip install --no-deps -r requirements-ap2.txt` -- the flag is on the second
command only, and putting it on the first installs `cryptography` without the
`cffi` it is built on, which is the failure `requirements-ap2.txt` was split in
two to avoid. `ci.yml` and `mutation.yml` each run both in a step of their own.
Nothing there reaches a network; the HTTP rail's transport is patched where
`buy_agent.rails` imported it. The 31 tests in `integration/` are the exception
that proves it, and they live outside `testpaths` so a bare `pytest` cannot
reach them.

Without that SDK the 73 tests that need it **skip**, the way
`tests/test_start_script.py` skips where there is no PowerShell: `needs_ap2` in
`tests/conftest.py` is the marker, and it asks `mandates.available()` once at
import. So a checkout set up with `requirements-dev.txt` alone reads
`1701 passed, 86 skipped` rather than 73 failures claiming the project is
broken when one optional feature is simply not installed. It is not a way of
not noticing: both workflows install the SDK, so on the runs that decide
anything nothing here is skipped and the coverage floor still has to be met --
which it cannot be with 73 tests sitting out. Nor is it a way of skipping more
than that: every one of the 73 really does fail without the SDK, and the marker
goes on the parametrised case rather than the function where only one case
needs it.

A deprecation warning fails the Python suite. `pytest.ini` sets `filterwarnings` to
turn `DeprecationWarning` and `PendingDeprecationWarning` into errors, in both the
main suite and `integration/`, so a call that a pinned dependency has started
warning about is a red run rather than a block of text under a green one. Every
direct dependency is pinned to an exact version, so that run is the one where
somebody moved a pin -- the moment the call is worth changing. A warning a
dependency raises about something it does to itself is not ours to fix: that one
gets an `ignore` line in `pytest.ini` naming the message and the module, and a
comment saying which upgrade removes it again.

Both suites are measured and CI fails on a drop: the Python side covers every line
and branch (`.coveragerc` sets the floor at 99%), and the UI's statements and
lines sit just under 100% (`ui/scripts/check-coverage.mjs`, floor 98%). Coverage
that high stops being a useful signal on its own, so `tests/test_conventions.py`
asserts the rules that hold *between* modules, which no amount of per-module
coverage can protect: the three places a failure mode has to be listed; the four
places a sort criterion has to be offered; the ranges both front ends hold a
number to, and the form taking those off the server rather than out of its own
markup while sending every key a refusal can name (ADR-0033); the two halves of a
provider agreeing about which providers exist; the payloads
`ui/src/app/agent.types.ts` mirrors; the `Dockerfile` agreeing with CI and with the
server's own defaults; the four workflows agreeing on the version of every action
they share and on the Python and Node they run; the release archive carrying the
UI build where the server looks for it; the nightly run pulling the model the live
tests ask for; the decision log agreeing with its own index; and every module in
the package logging under the package's own name, in the deferred form a handler
can still read, leaving stdout to the report.

The rule those last ones are the declared half of is exercised in
`tests/test_logging_contract.py`: the eight steps that take something away each
say how many at INFO and which at DEBUG. Each step's own file pins its wording;
what neither they nor coverage can see is the set, so a step that quietly stopped
saying anything leaves every other file green.

Those are the rules that span a *declaration*. `tests/test_architecture.py` is
the other half -- the rules that span an *import* -- and it asserts them against
the import graph with
[ArchUnitPython](https://github.com/LukasNiessen/ArchUnitPython), which parses
the package with `ast` and answers rules about the result (ADR-0047). Nineteen
rules, every one the executable form of a sentence already written down: the
package has no import cycles and imports none of the five trees that import it;
every module sits in a layer that reaches only downward, so the pipeline never
reads the config and never pays, paying never asks the model, and the model seam
knows nothing about products; `buy_agent/__init__.py` imports the four modules it
re-exports from and no others, since importing any submodule runs it first;
`mandates.py` is the only module that imports the optional AP2 SDK, and knows
about no module of the package in return; `providers.py` the only one that
imports a model client and `search.py` the only one that imports the search
backend, which is what the suite's fakes rest on; `fetch.py` the only one that
parses HTML; the three that speak HTTP are the three that are patched; `argparse`
belongs to the two modules handed an `argv`; the server imports nothing outside
the standard library, read off the graph rather than off `requirements.txt`,
while `api.py` reaches no socket, thread or queue; the two tables know nothing
about the config resolved from them; the steps read no environment, file, clock
or random number and never call each other, the order of the pipeline being
`BuyAgent.run`'s to know; and nothing that decides the answer -- ranking, the
bounds, grounding, the types -- may reach the model, the fetcher or the search.

Every rule is checked with `ignore_type_checking_imports=True`. An import under
that guard never runs, so it is a name and not a dependency -- and it is how this
package already spells "I use this type and not this module", which is what lets
`providers.py` take an `AgentConfig` while `config.py` reads its rows. A negated
rule whose subject matches nothing passes, so the two helpers that name modules
check first that each one exists: a renamed module fails the rule about it
instead of quietly turning it into a no-op. The layers are guarded the same way
and for the same reason -- an edge to or from a file in no layer is skipped, and a
module named in two layers may reach whatever either row allows -- so a twentieth
test holds the layer table against the directory and counts the placings, and a
module added to neither layer or to both is a test failure rather than an
exemption.

Both suites run on Windows and on Linux, on different triggers.
`.github/workflows/ci.yml` spreads its two jobs -- `coverage run -m pytest` on
Python 3.13, `npm run test:coverage && npm run build` on Node 22.22.3 -- over
`ubuntu-latest` and `windows-latest`, with `fail-fast` off so a failure on one
platform still reports the other. This project is written on Windows and its
runners were Linux, each checking the half of the differences the other hides: a
path separator, a default encoding, a socket that resets where the other closes, a
`mimetypes` lookup that reads the registry (ADR-0020). What gates a merge is the
Linux half: a push to `main` and a pull request run those two jobs and no more.
Windows joins the matrix on the schedule -- 04:09 UTC on Saturdays -- and on
`workflow_dispatch`, which is how a branch that touched one of the things above
asks for all four runs before it is merged (ADR-0037).

`scripts/start.ps1` is the one file neither suite can import or run, so
`tests/test_start_script.py` does everything short of running it: a PowerShell
helper parses the script, lifts out the functions it declares, exercises them on a
stubbed clock and a stubbed web request, and reports what it found as JSON. Those
tests skip where there is no `pwsh` or `powershell` on PATH -- neither Windows nor
either runner CI uses -- and the Windows job runs them on the platform the script
is actually for.

## Integration tests

Everything above fakes the model, which leaves the half of this project that is a
*claim about Ollama* checked nowhere: that a JSON schema compiled into a decoding
grammar makes `"N/A"` in a number impossible (ADR-0004), that `reasoning=False` and
`num_ctx=8192` are what make a thinking model answer at all (ADR-0019), and that a
stopped server arrives as a raw `httpx` error rather than an `OSError`. The unit
tests raise those errors themselves, which proves the provider's
`transport_errors` contains them and nothing about what Ollama raises.

`integration/` closes that, on a model small enough to run anywhere (ADR-0026):

```powershell
ollama pull qwen3:0.6b
python -m pytest integration
```

The model is real and the web is not. `search_web` and `enrich` are still patched,
over the ten fabricated pages in `benchmark/corpus.py` -- a nightly failure caused
by DuckDuckGo rate-limiting or a shop redesigning its listing would report nothing
about this code. The corpus sits there rather than here because the benchmark
below scores this same run against the answer key beside it (ADR-0036). One session-scoped fixture runs the pipeline once, a CPU
model answering in seconds rather than milliseconds, and each test reads something
different off the same answer.

The fake stops at the *transport*. `enrich` reads the fabricated page text instead
of fetching a URL, then condenses it with the real `fetch.condense` on the real
`page_chars` and `opinion_chars` budgets -- so the prompt is shaped the way a
production prompt is, and a verdict worded outside `fetch._OPINION`'s vocabulary
never reaches the model here either. Ten pages rather than three because the
*width* of the prompt is under test too: ADR-0019's `num_ctx` is about a prompt
that fills the window, and three pages of tidy prose came to ~675 tokens.

Almost nothing there asserts the model was *right*. What is asserted is what holds
whatever came back: every name, price, rating and quote is in the sources, every
link is a page that was searched, every quote names a page that was searched and
printed it, a currency never outlives its price, nothing is
listed twice, the ranking is ordered and numbered -- the guarantees the unit suite
checks against an answer it dictated, here meeting one nobody wrote. The one
exception is a smoke test that *something* was extracted, and a second that
something was *quoted*, since the opinion assertions go vacuous the same way.
Neither holds the model to an answer: both read what the extraction chain
returned, before grounding judged it.

## The benchmark

Those tests answer "did the promises hold?", which is the right question for a
nightly job and not the one a maintainer has after changing a prompt, a threshold
or `GENERIC_WORDS`. That one is "did it get *better*?", and nothing can answer it
without knowing what the right answer was.

`benchmark/` writes the right answer down. The corpus lives there and
`integration/conftest.py` reads it back, so the nightly run is scored *and*
checked for its invariants off one model call:

```powershell
python -m benchmark --scripted perfect   # no model, no network: scores 1.000
python -m benchmark --scripted sloppy    # the same, wrong in eight ways
python -m benchmark -v --json score.json # against a real model, keeping the numbers
```

`benchmark/answers.py` records what the ten pages print for each of the seven
products -- as **sets**, not as one right answer. `$328`, the refurbished `$269`
and EuroTech's `329 EUR` are all things the sources say the Sony costs, and a
model reporting any of them has copied rather than invented. A currency travels
with its price and a review count with its rating, so "329 USD" -- two figures
the corpus prints and a pairing it never does -- is one wrong price (ADR-0022).

`benchmark/scoring.py` turns a run into eight shares in `[0, 1]`, each a promise
the pipeline makes, weighed into one score:

| Metric | What it counts |
| --- | --- |
| `identified` | Slots filled with a product that is really there |
| `genuine` | Reported entries that are a real product, not a shop and not a repeat |
| `figures` | Price, rating and review count reported *and* printed for that product |
| `attribution` | The other half: figures printed for somebody else |
| `links` | Products pointed at a page that is about them (ADR-0017) |
| `quotes` | Products carrying a verdict a page about them printed (ADR-0024) |
| `faithful` | The other half: quotes that are not verbatim on such a page |
| `order` | Whether the ranking came out in the order the key's own figures give |

Each pair is split on purpose: a model that reports nothing scores 0 on `figures`
and 1.0 on `attribution`, one that reports confident nonsense scores the other way
round, and a single blended number would call them equally good. Three of the
eight catch failures the tests above structurally cannot (ADR-0036): a figure
copied off another product's line, which `verify_numbers` grounds against the
*pooled* pages and therefore accepts; a product reported twice under names
`deduplicate` does not merge, which the invariant test checks by re-running that
same merge; and a ranking in the wrong order, which is ordered and numbered either
way.

`integration/test_benchmark.py` scores the live run and fails under
`benchmark.scoring.FLOORS`, one test per metric so a red job names which half
slipped. Those floors are a **tripwire, not a target**: set where a 0.6B model
happens to sit today, the job would fail for a reworded prompt, which is how a
scheduled run gets ignored. The whole scorecard is logged pass or fail, and
raising a floor is a commit of its own quoting the runs that justify it.

What keeps the key honest is `tests/test_benchmark.py`, which runs entirely
without a model. It reads every name, figure and page in the key back off the
*condensed* corpus -- a line the fetch layer throws away is a figure no run can
ever be credited for -- and puts two hand-written answers through the real
pipeline: `PERFECT`, which must score exactly 1.000, and `SLOPPY`, wrong in eight
ways and pinned to the exact counts each mistake should produce. **Editing the
corpus means re-running both.**

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
run *differently* is the question [mutmut](https://github.com/boxed/mutmut) asks:
it breaks the code on purpose -- an `and` for an `or`, a `+= 1` for a `= 1` -- and
reports the mutants the suite still passes on, which are the lines that are
covered and unchecked.

`.github/workflows/mutation.yml` runs it against `buy_agent/` every Saturday
morning and on demand, never on a pull request: it takes a couple of minutes where
the suite takes six seconds, and it is a report rather than a gate (ADR-0016).
The job summary carries the score, a row per module worst first, and the functions
the survivors cluster in; the full list is uploaded as an artifact. The run fails
only if the score drops under 75% -- a guard against a module arriving with thin
tests, not a target. It sits at 77% today, a good share of the survivors being
equivalent mutants: a reworded log line, a debug counter nothing reads.

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
