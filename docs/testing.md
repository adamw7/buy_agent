# Tests

Two suites, one per language, and the checks that watch them: coverage floors on
both, a linter and a type checker over the package, cross-module conventions, a PowerShell script
neither suite can run, a nightly run against a real model, a benchmark scored
against a fixed answer key, a weekly mutation run and a nightly audit of both
dependency lists. Everything the [README](../README.md) leaves out.

```powershell
python -m pytest              # whole suite
python -m pytest tests/test_ranking.py::test_cheaper_wins_when_rating_is_equal

python -m coverage run -m pytest ; python -m coverage report   # with coverage
python -m pylint buy_agent    # the linter, from the repository root
python -m mypy buy_agent      # the type checker, from the same place

cd ui; npm test               # the UI's own tests, in jsdom
cd ui; npm run test:coverage  # the same, with a coverage floor

python -m pytest integration  # against a real Ollama; see below

python -m benchmark --scripted perfect   # the benchmark, with no model at all
python -m benchmark                      # ...and against whatever is serving
```

2552 Python tests and 255 UI tests. Nothing in either suite touches the network
or a model server: the model is faked through the `llm=` argument of `BuyAgent`
-- a class with one `answer` method, which is the whole of `chat.ChatModel`,
both the search backend and the page fetcher are monkeypatched -- the backends'
own tests reaching one row further down, to `buy_agent.search.DDGS` and
`buy_agent.search.httpx.get`, which are the two ways a row reaches out
(ADR-0057) -- the two clients
`buy_agent.providers` builds are patched where that module imported them, and
the server tests inject a stub agent through `create_server(agent_factory=...)`.
The only real sockets are the loopback ones the HTTP tests need in order to be
about HTTP at all. Nor does either suite touch the machine's own cache:
`conftest.py` points `$BUY_AGENT_CACHE_DIR` at a scratch directory per test,
autouse, so a test building a real `BuyAgent` remembers its answers somewhere
disposable (ADR-0044) and no test can be answered by another test's question.
The same fixture unsets `$BUY_AGENT_AP2_KEY`, `$BUY_AGENT_AP2_MANDATE` and
`$BUY_AGENT_MERCHANT_URL`, so a developer who has configured paying does not end
up with a suite that signs with their key and buys on their budget -- every test
that needs one of those points at a file it made itself.

The payment tests do sign real mandates. `tests/test_mandates.py` builds genuine
SD-JWTs with keys generated in the test and reads them back through the AP2
SDK's own verifier -- the one a merchant or a credential provider runs --
because a mandate that verifies only against a fake verifier is a mandate nobody
else would take. That needs the optional SDK: `pip install -r
requirements-ap2-deps.txt` and then `pip install --no-deps -r
requirements-ap2.txt` -- the flag is on the second command only, and putting it
on the first installs `cryptography` without the `cffi` it is built on, which is
the failure `requirements-ap2.txt` was split in two to avoid. `ci.yml` and
`mutation.yml` each run both in a step of their own. Nothing there reaches a
network; the HTTP rail's transport is patched where `buy_agent.rails` imported
it. The 31 tests in `integration/` are the exception that proves it, and they
live outside `testpaths` so a bare `pytest` cannot reach them.

Without that SDK the 74 tests that need it **skip**, the way
`tests/test_start_script.py` skips where there is no PowerShell: `needs_ap2` in
`tests/conftest.py` is the marker, and it asks `mandates.available()` once at
import. `needs_powershell` is the other, and with neither `pwsh` nor
`powershell` on PATH 13 of the 19 tests in that file sit out. So a machine with
the SDK and no PowerShell reads `2532 passed, 13 skipped`, and a checkout set up
with `requirements-dev.txt` alone reads `2458 passed, 87 skipped` rather than 74
failures claiming the project is broken when one optional feature is simply not
installed. It is not a way of
not noticing: both workflows install the SDK, so on the runs that decide
anything nothing here is skipped and the coverage floor still has to be met --
which it cannot be with 74 tests sitting out. Nor is it a way of skipping more
than that: every one of the 74 really does fail without the SDK, and the marker
goes on the parametrised case rather than the function where only one case
needs it.

A deprecation warning fails the Python suite. `pytest.ini` sets `filterwarnings`
to turn `DeprecationWarning` and `PendingDeprecationWarning` into errors, in
both the main suite and `integration/`, so a call that a pinned dependency has
started warning about is a red run rather than a block of text under a green
one. Every direct dependency is pinned to an exact version, so that run is the
one where somebody moved a pin -- the moment the call is worth changing. A
warning a dependency raises about something it does to itself is not ours to
fix: that one gets an `ignore` line in `pytest.ini` naming the message and the
module, and a comment saying which upgrade removes it again.

A hung test fails the Python suite too. `pytest.ini` sets `timeout = 60`, which
[pytest-timeout](https://pypi.org/project/pytest-timeout/) applies to each test
on its own -- generously, since nothing here sleeps and the slowest test spawns
an interpreter in about 1.5s. What a minute catches is not a slow test but a
stopped one: a fetch that reached the real web because a patch was spelt wrong,
a socket nothing is going to answer, two threads each holding half a lock.
Without it that is a job sitting at 4% until GitHub's six-hour cap kills it
having reported nothing, and a local run to go and find with a task manager;
with it, the run goes red naming the test. How the timeout arrives is the
platform's: Linux has `SIGALRM`, so the test fails and the rest of the run still
reports, while Windows has none, so the plugin dumps every thread's stack and
takes the process with it. Both are red and both name the test.

The live tests need a different number, and `integration/conftest.py` marks them
with `integration.LIVE_TIMEOUT_SECONDS` -- 120 -- rather than the minute a faked
model deserves. It sits between two limits and a convention test holds it there:
under the unit suite's cap, a 0.6B model answering on a runner's four cores
would start failing tests for being slow; at or over the five minutes
`integration.yml` gives the whole job, an Ollama that accepted the request and
never answered would be a cancelled job naming no test at all.

A lint failure fails the Python job too, after the tests rather than before
them: `python -m pylint buy_agent` runs last in the job, since a job stops at
its first failing step and of the two the tests are what a change is about. The
target is the package `.coveragerc` measures and `setup.cfg` mutates, and a
convention test holds the three together. The test trees are deliberately
outside it: pytest's fixtures shadow their own names by design and its tests say
what they assert in the name rather than in a docstring, so linting them would
mean turning off the checks that give the package's own gate most of its value
(ADR-0048).

It has no threshold, unlike the two floors below: a run has to come out with no
message at all. `.pylintrc` turns off three checks -- the docstring on every
function, the class with too few methods, the dataclass with too many fields --
each because this project has already answered that question differently, and
each with the answer written beside it. Everything else that fires is suppressed
on the line it fires on, with prose above it saying why the tool is wrong there:
a tuple of exception classes built from `api._STATUS` at run time,
`parser.error` exiting rather than returning, `BaseHTTPRequestHandler`'s own
spellings. `tests/test_conventions.py` fails a suppression that carries no
reason, and pylint's own `useless-suppression` fails one that has stopped
suppressing anything -- which is what the `# noqa` codes it replaced had quietly
become, written for a linter no command here ever ran.

The checks that run are chosen the same way round (ADR-0049). Pylint loads
twenty-five of its checkers only when asked, and `.pylintrc` asks for fifteen of
them: the docstring sections held against the code beneath them, the `except`
naming a class beside its own ancestor, the loop variable reassigned in its own
body, the private name imported out of another package, the `typing` spelling
3.14 answers with a builtin, and ten more, each stating a rule every module here
already follows. The ten left out are left out in the same file and for the same
kind of reason as the three that are off -- a ceiling on branching is a policy
`tests/test_architecture.py` has already declined, a comparison against a number
this project argues for in prose is not a magic value, a `try` wider than one
statement is what `money.minor_units` has a comment about. A checker is run
over the package before it is added and what it finds is fixed rather than
configured around: between them the fifteen found an overlapping `except`, a
rebound loop variable and ten `typing.Callable`s, and none of the three was
visible to the tests, the coverage floor, the mutation run or the import graph,
because every one of them ran perfectly.

`python -m mypy buy_agent` is the same shape one step further along the job, and
it is there for the same reason (ADR-0063). The package is annotated throughout
-- 298 of its 337 `def`s carry a return type, every dataclass field is declared,
and a dozen of the convention tests below hold those declarations against
`agent.types.ts` -- and until this step nothing read them, which made them prose
that happens to parse. The tell was a `# type: ignore[arg-type]` in `api.py`
addressed to a checker no command here ran, which is what the paragraph above
says the `# noqa` codes had become; put a checker behind it and the suppression
turned out to be unnecessary. The other ten findings were declarations that were
not true -- three tables declaring a failure class wider than any row names, a
six-way `Literal` handed a bare `str`, a bound tied to the kind of the value it
bounds -- none a bug, every one a claim a later edit can rely on and be wrong
about, and not one of them visible to the tests, either floor, the mutation run
or the import graph.

Its settings are a `[mypy]` section in `setup.cfg`, beside mutmut's, and there
are three of them. The target is the package the other three tools read, held by
the same convention test. The floor is the default checks and not `strict`,
which would pull in `disallow_untyped_defs` and thirty-nine annotations nobody
asked for; what is added to the default is `warn_unused_ignores`, which is
`useless-suppression` one tool over and is the check this was noticed through.
And the four libraries neither tool here can read -- the AP2 SDK, the two it
signs with, and the `lxml` `fetch.py` parses pages with -- are named once per
tool, as `ignore_missing_imports` here and as `ignored-modules` and
`extension-pkg-allow-list` in `.pylintrc`, with a convention test holding the
two lists together from both sides. Left to fail instead it would be the one
check here that is red on a checkout the tests call fine.

The UI's half of that gate is two checks and no threshold either. `npm run
build` is a type check before it is a build: `ui/tsconfig.json` sets `strict`
and `strictTemplates` for the whole workspace and `ui/tsconfig.app.json` adds
`noUncheckedIndexedAccess` for the shipped half -- the one `strict` leaves out
and the one this app needs, every lookup a component makes being by a key that
came off a payload (`limits()[number.key]`, `receipts()[product.name]`). It
stops at the specs, where indexing past the end of a list the test built itself
is a failing assertion on the next line and the `!` per subscript would say
nothing about the code that ships. Without it a
miss is typed as a hit, which is how a `?? null` written for a real `undefined`
reads to the compiler as one that can be deleted -- and with it, the nullable
halves of a payload (an `Opinion.url` off a result with no page, a
`pay_currency` on a bare price) are a case every component has to handle rather
than a comment in `agent.types.ts`.

`strictTemplates` is that same promise one file over: `strict` checks the
TypeScript a component is written in, and this checks its *bindings*, which is
where the payload actually lands -- an input handed a type it cannot hold, a
`$event` typed as `any`, a signal read with the wrong arity. Every label the
page shows is Python's (`price_label`, `cannot_pay`, `offers_label`), so a
binding is the one place one of them can be misused with no `.ts` file saying
so. It is shared rather than the app's alone, and that is what decides how the
three maps a template looks up by a payload key are written: `App.receipts`,
the form's `limits` and its `placeholders` each say `| undefined` in their own
type rather than leaning on `noUncheckedIndexedAccess`, since the specs are
compiled without that one and the `?? null` each is read through would be
reported *there* as a `??` to delete. That report is a warning, which neither
`ng build` nor `ng test` fails on -- so a lookup left typed as a hit is a check
that says something and stops nothing, which is the one way this setting could
be worse off on than off. `npm run format:check` is the other:
Prettier reading rather than writing, which is the whole of what lints this half
-- `npm run format` is the same glob with `--write`, and is what to run when the
step goes red. It runs last in its job for the reason pylint runs last in the
other.

The four components are also held to an accessibility check, which is the one
thing above that neither the type check nor the coverage floor can see: every
rule the form is designed around is a claim about what somebody can perceive and
reach, and a mark drawn as a colour on a border, a control with no accessible
name and an error that never reaches an assistive technology are all green under
a spec asserting a CSS class. `ui/src/app/a11y.ts` runs `axe-core` over each
component in the jsdom `TestBed`, on rules turned on one at a time -- every one
of them carrying the sentence saying which promise it holds, and what is left
out carrying its reason beside them, which is `.pylintrc`'s argument (ADR-0049)
one language over. A rule that ran and could not decide counts as one that did
not pass. Contrast and target size stay out: they want pixels and layout, which
is a browser's job, the way the CSP and the critical-CSS inliner already are.
`ui/README.md` argues the whole of it beside the components it is about.

Both suites are measured and CI fails on a drop: the Python side covers every
line and branch (`.coveragerc` sets the floor at 99%), and the UI's statements
and lines sit just under 100% (`coverageThresholds` in `ui/angular.json`, floor
98%). Coverage that high stops being a useful signal on its own, so
`tests/test_conventions.py` asserts the rules that hold *between* modules, which
no amount of per-module coverage can protect: the three places a failure mode
has to be listed; the four places a sort criterion has to be offered; the ranges
both front ends hold a number to, and the form taking those off the server
rather than out of its own markup while sending every key a refusal can name
(ADR-0033); the two halves of a provider agreeing about which providers exist;
the payloads `ui/src/app/agent.types.ts` mirrors; the `Dockerfile` agreeing with
CI and with the server's own defaults, and nothing at the top of the tree
reaching the build context without a line either copying it or keeping it out;
the five workflows agreeing on the version of every action they share, on the
Python and Node they run and on keying a pip cache to every requirements file
they hand pip; every dependency list being one the nightly audit reads or the
one named with its reason, and the two halves of that audit that can fail over a
severity agreeing on which one; the release archive carrying the UI build where
the server looks for it; the nightly
run pulling the model the live tests ask for and leaving its own cap room to
fail a stopped model first; the decision log agreeing with its own index; the
checklists in `.claude/skills/` naming files, tests, names and records that are
really there, since nothing else in either suite opens them; every link in every
Markdown file here pointing at something that is there, a renamed file otherwise
leaving valid Markdown that is a dead end and only the reader who follows it
finding out; the UI being compiled with its checks on, which is a setting and
not a property of the build; the
dependency list holding only what the package imports, and holding all of it; the
linter and the type checker reading the package the other two tools measure, no
line of it taking a check away without saying why, and the two of them naming
the same libraries as ones neither can read; every type named for a failure being one, and
nothing but the `__main__` guard ending the process; the suite's own
hygiene -- no test switched off outright, nothing sleeping but the server tests
that need a run to still be going, and the environment changed through
`monkeypatch` rather than written; and every module in the package logging under
the package's own name, in the deferred form a handler can still read, leaving
stdout to the report.

The rule those last ones are the declared half of is exercised in
`tests/test_logging_contract.py`: the eight steps that take something away each
say how many at INFO and which at DEBUG. Each step's own file pins its wording;
what neither they nor coverage can see is the set, so a step that quietly
stopped saying anything leaves every other file green. The same table
partitions those eight by what they take: the five that remove a whole product
hand it to the run's recorder and the three that only blank a figure, a quote or
a link hand over nothing, since the product they touched is still in the report
(ADR-0055). A step recording on the wrong side of that line is a panel that
reads as either a short answer or a wrong one, and nothing else notices.

Those are the rules that span a *declaration*. `tests/test_architecture.py` is
the other half -- the rules that span an *import* -- and it asserts them against
the import graph with
[ArchUnitPython](https://github.com/LukasNiessen/ArchUnitPython), which parses
the package with `ast` and answers rules about the result (ADR-0047). Thirty
rules, every one the executable form of a sentence already written down: the
package has no import cycles and imports none of the five trees that import it,
starts no process of its own -- installing Ollama, pulling a model and opening
a browser are `scripts/start.ps1`'s (ADR-0023), and a child process is the one
way out of this one that no fake in the suite could answer -- and awaits
nothing, there being no event loop under a `ThreadingHTTPServer`, so
`threading`, `concurrent.futures`, `queue` and `contextvars` belong to the
three modules that wait on somebody else: the page pool, the per-tag listing
and the run a request is served by; every module sits in a layer that reaches
only downward, so the pipeline never reads the config and never pays, paying
never asks the model, and the seams reach the domain types and nothing else
above them -- `journal.py` writes products down (ADR-0060), which is why the
fourth of those edges, the model seam knowing nothing about products
(ADR-0038), is a rule of its own, as are the edges the layers are blind to
*inside* a layer: the two doors do not know about each other, and `money.py`
sits under a domain that reads it while reaching nothing itself;
`buy_agent/__init__.py` imports the four modules it re-exports from and no
others, since importing any submodule runs it first; `mandates.py` is the only
module that imports the optional AP2 SDK, and knows about no module of the
package in return; `providers.py` the only one that imports a model client and
`search.py` the only one that imports the search backend, which is what the
suite's fakes rest on; `fetch.py` the only one that parses HTML; `cache.py` the
only one with a `tempfile` and a `hashlib`, since the journal puts a file on
disk by importing that one dance rather than repeating it (ADR-0060); the three
that speak HTTP are the three that are patched, and the standard library's own
network -- a socket, a `urllib.request` -- belongs to `server.py`, which is one
on purpose and listens rather than calls out; `argparse` belongs to the two
modules handed an `argv` and the environment to the six modules a setting is
declared in, neither door among them; the server imports nothing outside the
standard library, read off the graph rather than off `requirements.txt`, while
`api.py` reaches no socket, thread or queue; the three tables know nothing about
the config resolved from them; the steps read no environment, file, clock or
random number and never call each other, the order of the pipeline being
`BuyAgent.run`'s to know, which is also why nothing above the orchestrator
reaches into the middle of the line -- `ranking.py` is the one step the doors,
the payload and the settings may name, for the re-sort that runs no pipeline
(ADR-0035) -- and why `verification.py`, the one exemption from the chaining
rule, may share the extractor's vocabulary and nothing else; a bound read out
of the request reaches the money it is written in and no module that could
apply it (ADR-0059); and nothing that decides the answer -- ranking, the
bounds, grounding, the types -- may reach the model, the fetcher or the search.

Every rule is checked with `ignore_type_checking_imports=True`. An import under
that guard never runs, so it is a name and not a dependency -- and it is how
this package already spells "I use this type and not this module", which is what
lets `providers.py` take an `AgentConfig` while `config.py` reads its rows. A
negated rule whose subject matches nothing passes, so the two helpers that name
modules check first that each one exists: a renamed module fails the rule about
it instead of quietly turning it into a no-op. The layers are guarded the same
way and for the same reason -- an edge to or from a file in no layer is skipped,
and a module named in two layers may reach whatever either row allows -- so a
thirty-first test holds the layer table against the directory and counts the
placings, and a module added to neither layer or to both is a test failure
rather than an exemption. An edge *inside* a layer is skipped too, which no
table can fix: that one is why the modules sharing a layer -- the two doors, the
seams under the journal, the domain under `money.py` -- are named in rules of
their own.

Two of those rules, and five in `tests/test_conventions.py` beside them, are
[ArchUnit](https://www.archunit.org) rules from
[adamw7/tools](https://github.com/adamw7/tools) read in this project's terms:
the package starts no process and reaches no socket of its own, a type named
`*Error` really is raisable, nothing but the `__main__` guard ends the process,
no test is switched off outright, nothing in either suite sleeps but the server
tests that need a run to still be going, and the environment is changed through
`monkeypatch` rather than written. Every one of them is a sentence this project
had already decided and nothing was checking, which is the difference between
porting a rule and adopting a rulebook: the Java-shaped ones -- a logger is a
constant, an abstract class carries the prefix, `@BeforeAll` is static -- say
nothing here, and the rules about layers, cycles and one seam per module were
already the file above.

Both suites run on Windows and on Linux, on different triggers.
`.github/workflows/ci.yml` spreads its two jobs -- `coverage run -m pytest` and
then `pylint buy_agent` and `mypy buy_agent` on Python 3.14, `npm run
test:coverage`, `npm run build`
and `npm run format:check` on Node 22.23.2 -- over `ubuntu-latest` and `windows-latest`, with `fail-fast`
off so a failure on one platform still reports the other. This project is
written on Windows and its runners were Linux, each checking the half of the
differences the other hides: a path separator, a default encoding, a socket that
resets where the other closes, a `mimetypes` lookup that reads the registry
(ADR-0020). What gates a merge is the Linux half: a push to `main` and a pull
request run those two jobs and no more. Windows joins the matrix on the schedule
-- 04:09 UTC on Saturdays -- and on `workflow_dispatch`, which is how a branch
that touched one of the things above asks for all four runs before it is merged
(ADR-0037).

`scripts/start.ps1` is the one file neither suite can import or run, so
`tests/test_start_script.py` does everything short of running it: a PowerShell
helper parses the script, lifts out the functions it declares, exercises them on
a stubbed clock and a stubbed web request, and reports what it found as JSON.
Those tests skip where there is no `pwsh` or `powershell` on PATH -- neither
Windows nor either runner CI uses -- and the Windows job runs them on the
platform the script is actually for.

## Integration tests

Everything above fakes the model, which leaves the half of this project that is
a *claim about Ollama* checked nowhere: that a JSON schema compiled into a
decoding grammar makes `"N/A"` in a number impossible (ADR-0004), that
`reasoning=False` and `num_ctx=16384` are what make a thinking model answer at
all (ADR-0019), and that a stopped server arrives as a raw `httpx` error rather
than an `OSError`. The unit tests raise those errors themselves, which proves
the provider's `transport_errors` contains them and nothing about what Ollama
raises.

`integration/` closes that, on a model small enough to run anywhere (ADR-0026):

```powershell
ollama pull qwen3:0.6b
python -m pytest integration
```

The model is real and the web is not. `search_web` and `enrich` are still
patched, over the ten fabricated pages in `benchmark/corpus.py` -- a nightly
failure caused by DuckDuckGo rate-limiting or a shop redesigning its listing
would report nothing about this code. The corpus sits there rather than here
because the benchmark below scores this same run against the answer key beside
it (ADR-0036). One session-scoped fixture runs the pipeline once, a CPU model
answering in seconds rather than milliseconds, and each test reads something
different off the same answer.

The fake stops at the *transport*. `enrich` reads the fabricated page text
instead of fetching a URL, then condenses it with the real `fetch.condense` on
the real `page_chars` and `opinion_chars` budgets -- so the prompt is shaped the
way a production prompt is, and a verdict worded outside `fetch._OPINION`'s
vocabulary never reaches the model here either. Ten pages rather than three
because the *width* of the prompt is under test too: ADR-0019's `num_ctx` is
about a prompt that fills the window, and three pages of tidy prose came to ~675
tokens.

Almost nothing there asserts the model was *right*. What is asserted is what
holds whatever came back: every name, price, rating and quote is in the sources,
every link is a page that was searched, every quote names a page that was
searched and printed it, a currency never outlives its price, nothing is listed
twice, the ranking is ordered and numbered -- the guarantees the unit suite
checks against an answer it dictated, here meeting one nobody wrote. The one
exception is a smoke test that *something* was extracted, and a second that
something was *quoted*, since the opinion assertions go vacuous the same way.
Neither holds the model to an answer: both read what the extraction chain
returned, before grounding judged it.

## The benchmark

Those tests answer "did the promises hold?", which is the right question for a
nightly job and not the one a maintainer has after changing a prompt, a
threshold or `GENERIC_WORDS`. That one is "did it get *better*?", and nothing
can answer it without knowing what the right answer was.

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

Each pair is split on purpose: a model that reports nothing scores 0 on
`figures` and 1.0 on `attribution`, one that reports confident nonsense scores
the other way round, and a single blended number would call them equally good.
Three of the eight catch failures the tests above structurally cannot
(ADR-0036): a figure copied off another product's line, which `verify_numbers`
grounds against the *pooled* pages and therefore accepts; a product reported
twice under names `deduplicate` does not merge, which the invariant test checks
by re-running that same merge; and a ranking in the wrong order, which is
ordered and numbered either way.

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
pipeline: `PERFECT`, which must score exactly 1.000, and `SLOPPY`, wrong in
eight ways and pinned to the exact counts each mistake should produce. **Editing
the corpus means re-running both.**

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
run *differently* is the question [mutmut](https://github.com/boxed/mutmut)
asks: it breaks the code on purpose -- an `and` for an `or`, a `+= 1` for a `=
1` -- and reports the mutants the suite still passes on, which are the lines
that are covered and unchecked.

`.github/workflows/mutation.yml` runs it against `buy_agent/` every Saturday
morning and on demand, never on a pull request: it takes a couple of minutes
where the suite takes six seconds, and it is a report rather than a gate
(ADR-0016). The job summary carries the score, a row per module worst first, and
the functions the survivors cluster in; the full list is uploaded as an
artifact. The run fails only if the score drops under 75% -- a guard against a
module arriving with thin tests, not a target. It sits at 77% today, a good
share of the survivors being equivalent mutants: a reworded log line, a debug
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
and `mutation-results.txt` are ignored by git. The copy's package is the code as
run -- every module opening with mutmut's trampoline and holding every mutant of
every line at once -- so the two files that read source rather than run it,
`tests/test_conventions.py` and `tests/test_architecture.py`, read the package
from the tree the copy was made of (`conftest.SOURCE_ROOT`). Everything else
they open the copy carries unchanged, which is what `also_copy` is for and what
`test_a_mutation_run_copies_everything_the_tests_reach_for` reads back.

The trampoline is a wrapper, which is the other half of that: the defaults, and
everything else written under a `def`, are on the function it wraps and not on
the one the suite imports, so `search_web.__kwdefaults__["backend"]` reads
`None` there and the run dies at its baseline before a mutant has been tried --
on a Saturday, days after the pull request the test passed on.
`test_no_test_reads_a_declaration_off_a_function_object` is that rule for both
suites: what a function was declared with is asked of a run that was told
nothing.

### The front end's own run

ADR-0016 left the UI out and said so: it has its own toolchain (ADR-0013) and
would need its own mutation tester. It has one now.
[Stryker](https://stryker-mutator.io/) runs over `ui/src/app` at `23 6 * * 6`
and on demand -- `.github/workflows/mutation-ui.yml`, an hour after the
package's own run and in a workflow of its own, because ninety minutes of
`ng test` stacked into a two-minute job would make that report wait on this one
(ADR-0061). It is the half of the project where coverage says least: the 98%
floor is on statements and lines only, `ui/angular.json` deliberately setting no
branch floor because v8 attributes the branches inside a compiled Angular
template to positions no test can reach.

A run is the project's own test command, once per mutant. Stryker instruments
the sources with every mutant at once and switches one on through an
environment variable, so what runs is `ng test` with a variable set -- no second
Angular compiler beside the one `ci.yml` runs, and a surviving mutant is a
sentence about the specs as CI runs them. That is also what it costs: 1031
mutants, a whole build and a whole suite each, about a hundred minutes on a
four-core runner with four running at a time.

```powershell
cd ui
npx stryker run                     # ~100 minutes; reports/mutation/ is the output
cd ..
python scripts/mutation_report.py ui/reports/mutation/mutation.json
```

`ui/stryker.config.mjs` is the `setup.cfg` of that half: what is mutated, what
is left out by name (`agent.types.ts` is the payloads written down as types,
`testing.ts` the ones the specs are written against, `app.config.ts` what
`main.ts` boots the app with), and the timeout a loaded runner needs so a slow
run is not reported as a killed mutant. `ui/tsconfig.mutation.json` beside it is
the one concession: Stryker's instrumentation widens the types a template reads,
so the templates' own checks are off for the run while `strict` and
`noUncheckedIndexedAccess` stay exactly where `npm run build` has them -- it
declares no `compilerOptions` at all, and a convention test reads that back.
Both the sandbox under `ui/.stryker-tmp/` and the reports under `ui/reports/`
are ignored by git.

The report is `scripts/mutation_report.py` again. One script holds a `Tool` per
tester -- what its statuses mean, how a run of it reads, and the floor under it
-- and the file it is handed says which one wrote it: mutmut a listing, Stryker
a JSON report. The shape is the same either way, the score and a row per module
worst first and where the survivors cluster, except that Stryker records a
position rather than a function, so a cluster is a file and the mutator that
made it. The run's own HTML report is uploaded as an artifact, which is the
survivor read beside the line it was made from.

## The dependency audit

Renovate keeps the pins fresh; `.github/workflows/audit.yml` asks the other
question, which is whether what is pinned is known to be broken today
(ADR-0062). The two run on different clocks -- one starts when a maintainer
publishes a release, the other when somebody publishes an advisory -- and the
second can go off about a line that has not moved in a year.

```powershell
pip install -r requirements-audit.txt
python -m pip_audit -r requirements.txt -r requirements-dev.txt `
  -r requirements-mutation.txt -r requirements-ap2-deps.txt `
  -r requirements-audit.txt
cd ui; npm audit --audit-level=high
```

That is the whole of the nightly job, at `47 2 * * *` and on demand: about ten
seconds of resolving and half a second of reading a lockfile. Neither half
installs anything -- `pip-audit` resolves each file with its transitives in an
environment of its own, twenty-two packages behind the six runtime pins, and
`npm audit` reads `ui/package-lock.json` rather than a `node_modules`.

`requirements-ap2.txt` is the one list it does not read, and the reason is the
reason that file is installed `--no-deps`: its metadata pins `cryptography`,
`jwcrypto` and `pytest` to versions this project deliberately does not install,
so resolving it reports advisories against a tree nothing here has -- fifteen of
them the day this was written, none against anything installed. What paying
really signs with is `requirements-ap2-deps.txt`, and that one is audited.
`tests/test_conventions.py` holds both sides of that: a list nothing reads fails,
and so does an exemption naming a file that has been renamed.

What fails a run was decided before the first one ran, because a gate that cries
wolf gets an `|| true` within a month -- ADR-0016's failure mode one step
further along. The Python side has no severity threshold: those lists are short,
every line is a direct pin, and all of it is installed on a machine that runs the
agent or its suite. The npm side has one, `high`, because the lockfile is two
megabytes of transitive build-time packages and the advisories standing today are
two moderate ones in the HTTP client `@stryker-mutator/core` reports with, which
runs on a runner one morning a week and never reaches a browser.

None of that gates a merge. An advisory published on a Tuesday is not news that
a Tuesday push made true, so the audit is scheduled and a red run is a morning's
work. What a pull request *adds* to either list is this repository's own change,
so that half is `actions/dependency-review-action` on `pull_request`, at the same
`high` -- it reads the difference rather than the whole of what is pinned, so it
cannot go red for something published since the branch was cut. A convention test
holds the two thresholds together, neither tool reading the other's
configuration.

An advisory with no fix available is suppressed with `--ignore-vuln` and the
sentence saying why and what releases it, the way a `# pylint: disable` in the
package carries its reason. An `|| true` is not one of the options.
