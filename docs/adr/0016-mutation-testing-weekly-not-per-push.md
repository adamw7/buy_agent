# ADR-0016: Check the tests with mutation testing, weekly rather than on every push

- **Status:** Accepted
- **Date:** 2026-08-25

## Context

Both suites cover essentially every line, and the Python floor sits at 99% against
100% actual. That number stopped being a signal a while ago: it says every line
ran, not that anything would have complained had a line run differently.
ADR-0014 was the first answer to that -- `tests/test_conventions.py` checks the
rules that hold between modules, which coverage cannot see at all -- but it says
nothing about the tests of a single module. A test that calls a function and
asserts nothing about the interesting half of its result is invisible to
coverage, and a suite full of them reports 100%.

Mutation testing is the direct question: break the code on purpose -- an `and`
for an `or`, a `+= 1` for a `= 1`, a boundary moved by one -- and see whether the
suite fails. A mutant that survives is a line that is covered and unchecked.

The cost is that it is not free and not exact. A full run is thousands of test
runs; on this project it is a couple of minutes on a four-core runner, which is
fine weekly and wrong on every push next to a suite that takes three seconds.
And a fair number of survivors are equivalent mutants: a log message with a word
changed, a debug counter that nothing reads, a default argument that every call
site passes anyway. A tool whose output is partly noise cannot be a gate on
merging without training everyone to ignore it.

## Decision

`.github/workflows/mutation.yml` runs [mutmut](https://github.com/boxed/mutmut)
against `buy_agent/` on a schedule -- `17 5 * * 6`, Saturday morning -- and on
`workflow_dispatch`. It is a report, not a gate:

- **It runs weekly, and never on a pull request.** `ci.yml` stays what a push has
  to pass. Nothing about a merge waits on a mutation run.
- **The report is the deliverable.** `scripts/mutation_report.py` turns the run
  into the job summary: the score, a row per module worst first, and the
  functions the survivors cluster in. That last list is the point -- it is the
  answer to "where does the next test go", which coverage can no longer give.
- **A floor guards against a slide, and only that.** The floor is 75%, under the
  77% the suite holds today, because a mutation score moves a little with the
  mutants generated and a floor that fails on noise is a floor nobody keeps. A
  module arriving with thin tests fails the Saturday run; a survivor or two does
  not.
- **A survivor is not automatically a bug in the tests.** Judgement about which
  ones matter stays with whoever reads the report.
- **mutmut is not in `requirements-dev.txt`.** `requirements-mutation.txt` layers
  it on top, so the jobs that run on every push do not install libcst, textual
  and rich to run pytest.

## Consequences

The suite is now checked by something that cannot be satisfied by exercising a
line. The first run put two thirds of the survivors in `server.py`, `__main__.py`
and `agent.py` -- argument parsing and log messages, mostly -- which is a more
useful list than "100% covered" was.

The obligations are two, and both are in `tests/test_conventions.py`:

- **mutmut mutates what coverage measures.** `source_paths` in `setup.cfg` and
  `source` in `.coveragerc` name the same package, and the workflow sets up the
  Python `ci.yml` tests against. A package measured by one tool and not the other
  has the reassuring number and none of the checking behind it.
- **Every file the tests read has to be copied into the run.** mutmut tests a
  copy of the tree under `mutants/`, taking `tests/` and `setup.cfg` along
  itself; `also_copy` names the rest. The conventions tests read the
  `Dockerfile`, the workflows, the decision log, `.coveragerc` and the TypeScript
  types off disk rather than through an import, so a file left out of that list
  kills the whole Saturday run at collection -- which is exactly the failure a
  weekly job is worst at reporting, and nothing in a normal run can see coming.

`setup.cfg` now exists, and it is not a packaging file: the project is still run
from a checkout and never installed. It is there because it is where mutmut looks
for its settings.

The report is only as useful as it is read, which is the real cost of a weekly
job. A run failing its floor is visible to whoever watches Actions; a run that
passes with a new cluster of survivors is not, unless someone opens it.

Only the Python suite is mutation-tested. The UI has its own toolchain
(ADR-0013) and would need its own mutation tester; that is left open rather than
half-done.
