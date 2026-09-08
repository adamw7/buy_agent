# ADR-0047: Check the import graph against the rules already written down

- **Status:** Accepted
- **Date:** 2026-09-08

## Context

[ADR-0014](0014-conventions-tests-over-coverage.md) took the rules that hold
between modules and made them a test, because coverage at 99% could no longer
say where the next test should go. What it took were the rules that span a
*declaration*: a table, a payload, an `except` tuple, a heading, a workflow.
`tests/test_conventions.py` reads those declarations and asserts that the places
quoting them agree.

The other half was left as prose. `CLAUDE.md` and this log say, in a dozen
places, which module may know about which:

- "`providers.py` imports nothing from `config`; the dependency runs the other
  way" ([ADR-0029](0029-one-table-per-model-server.md)).
- "`search.py` is a DuckDuckGo wrapper -- and nothing else"
  ([ADR-0021](0021-carry-no-exports-the-pipeline-does-not-use.md)).
- "`sources.py` does no I/O -- it decides what a source *is* and `agent.py` does
  the searching"
  ([ADR-0027](0027-let-the-shopper-name-the-sources.md)).
- "`buy_agent.mandates` is the AP2 seam, and the only module that imports `ap2`"
  ([ADR-0046](0046-pay-on-the-shoppers-behalf-with-ap2.md)).
- "Anything that decides the answer -- filtering, scoring, ordering -- belongs in
  Python" ([ADR-0002](0002-fixed-pipeline-not-a-tool-loop.md)).
- "the dependency list is already the interesting part of this project"
  ([ADR-0010](0010-stdlib-http-server.md)).

Every one of them is a sentence a single `from` line makes false, and none of
them is checkable by exercising anything. An import in the wrong direction runs
correctly. It passes the module's own tests, keeps the coverage floor, survives
the mutation run, and reports itself years later as the reason two modules
cannot be moved apart -- or, for four of the six above, as something worse:
`ap2` imported outside `mandates.py` breaks a checkout that has not installed
the optional SDK, `ddgs` or a model client imported anywhere but its own wrapper
is a call site the suite's fakes do not patch and a test that quietly asks the
real thing, and a second module fetching HTML is a second reading of a page,
which is exactly what grounding cannot survive
([ADR-0006](0006-ground-every-figure.md)).

Two other things about the shape are true, undocumented, and would be noticed
only by breaking: the package has no import cycles, and it imports none of the
five trees that import *it* (`tests/`, `integration/`, `benchmark/`, `demo/`,
`scripts/`) -- none of which is in the image or the release archive
([ADR-0030](0030-publish-a-release-as-an-archive-and-an-image.md)), so the first
report of that mistake would be an `ImportError` out of a container.

## Decision

`tests/test_architecture.py` asserts the import graph with
[ArchUnitPython](https://github.com/LukasNiessen/ArchUnitPython), pinned in
`requirements-dev.txt` like every other dependency. It parses the package with
`ast` and answers rules about the graph, so a rule costs no run, no model and no
network, and a violation names the file that broke it.

Two things are settled about how those rules are written.

**Every module of the package is in a layer, and a layer reaches only
downward.** Entry points, web, orchestration, pipeline, paying, model access,
settings, domain -- the module table in `CLAUDE.md` read as a stack, with the
four edges that are decisions named in the test: the pipeline never reads the
config, the pipeline never pays, paying never asks the model, and the model seam
knows nothing about products. `buy_agent/__init__.py` is in no layer and outside
the cycle rule, because `from buy_agent import mandates` -- the deferred import
three modules use -- reads as an edge onto the package rather than onto the
module, and importing any submodule runs `__init__.py` first regardless.

**`TYPE_CHECKING` imports do not count.** Every rule is checked with
`ignore_type_checking_imports=True`. An import under that guard never runs, so it
cannot be a cycle and cannot be a dependency -- and it is precisely how this
package already spells "I name this type and do not use this module", which is
what lets `providers.py` and `rails.py` take an `AgentConfig` while `config.py`
reads their rows.

What is deliberately *not* asserted is size. The library also measures lines,
methods, cohesion and distance from the main sequence, and a ceiling on any of
them would be a policy nobody has decided; a rule belongs in `CLAUDE.md` first
and here second, which is the rule for this file as much as for the code.

## Consequences

The sentences above stop being prose. Moving an import that contradicts one is a
test failure at the point of editing, naming the module and the rule -- and the
rule's `because` clause says why it exists rather than leaving the next reader to
find the record.

It obliges the layers to be maintained, and says so out loud. An edge to or from
a file in no layer is skipped, so a module added to `buy_agent/` and to no layer
would be silently exempt -- which is why the layers are a table and a test holds
it against the directory: adding a module means placing it, and the test that
fails is the one that says that. The same care covers the rest of the file, since
a negated rule whose subject matches nothing *passes*: `only()` and
`every_module_but()` assert that each filename they name is really a module of
the package, so a rename fails the rule about that module instead of quietly
turning it into a green no-op.

It also draws a line under the two carve-outs. `__init__.py` is outside the
layers and the cycle check, so nothing here says what the re-export surface may
import -- that is [ADR-0021](0021-carry-no-exports-the-pipeline-does-not-use.md)
and a convention test, not this file. And a rule is about the graph, so a module
that imports nothing and still does the wrong thing is not its business:
`clean_products` filtering headlines, `ground` dropping figures and the browser
deciding nothing are all still ordinary tests.

The cost is another dependency in `requirements-dev.txt`, on a young library, for
a suite that had none but pytest and coverage. It buys thirteen rules that no
amount of behavioural testing can express, it does not ship -- nothing in
`requirements.txt` moves and the image is unchanged -- and if it were ever
abandoned, what is lost is a test file, not a line of `buy_agent/`.
