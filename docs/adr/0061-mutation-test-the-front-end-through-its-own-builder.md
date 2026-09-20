# ADR-0061: Mutation-test the front end, through the builder it is already tested with

- **Status:** Accepted
- **Date:** 2026-09-20

## Context

ADR-0016 makes the argument once: coverage says a line ran, not that anything would
notice if it stopped working. It ends by saying the UI is not mutation-tested, that it
would need a tester of its own, and that this is left open rather than half-done. This
record is that half.

The front end is where the argument bites hardest. Its 244 tests hold a 98% floor on
statements and lines, and `ui/angular.json` deliberately sets no branch floor: v8
attributes the branches inside a compiled Angular template to positions no test can
reach, so a branch threshold there would measure the instrumentation rather than the
tests. What is left is the weakest of the numbers this project quotes, over the half of
it that decides what a shopper actually sees -- `problems()` gating `canSubmit`
(ADR-0033), `remembersBlank` deciding which cleared box is remembered, `noticedNow`
dropping an answer about a request the box no longer holds (ADR-0059).

[Stryker](https://stryker-mutator.io/) is the tester. How it is *run* was the decision,
and the two ways it can be run are not close:

- **Its vitest runner** drives vitest itself, instruments once and re-runs only the
  tests that cover a mutant. It is the fast one by a wide margin, and it cannot see
  these specs: what compiles an Angular component -- templates, decorators, the
  `@angular/build` unit-test builder's own pipeline -- is the builder, and a vitest
  Stryker starts knows none of it. Making it work means a second Angular toolchain
  beside the one `ci.yml` runs (`@analogjs/vite-plugin-angular` or the like): a second
  compiler, a second config to keep in step, and a mutation score about a program that
  is compiled differently from the one that ships.
- **Its command runner** runs whatever command the project already tests with, once per
  mutant, with the active mutant in an environment variable. Nothing new compiles the
  app, and what a surviving mutant says is a sentence about `npm test`.

Measured on a four-core machine, the shape of a runner: 969 mutants, a whole `ng test`
each at about ten seconds warm, four at a time -- an hour and a half. mutmut's whole run
over the package is two minutes.

One thing had to be given way on. Stryker puts every mutant of a file into that file at
once, switched at run time, so a method that answered `boolean` is inferred as `boolean |
undefined`. The mutated TypeScript is let off by the `@ts-nocheck` Stryker writes into
it; the templates reading that method are checked from the outside and are not, and the
build fails before a test runs.

## Decision

**Stryker runs over `ui/src/app` every Saturday, through `ng test`, in a workflow of its
own.**

- **The command runner, and the project's own test command.**
  `ui/stryker.config.mjs` runs `npx ng test`, which is `npm test` with the one
  difference below. No second compiler, and a mutant that survives survived the specs as
  CI runs them.
- **`ui/tsconfig.mutation.json` relaxes the templates and nothing else.**
  `strictTemplates` and `fullTemplateTypeCheck` are off there, which is what makes
  instrumented code compile; `strict` and `noUncheckedIndexedAccess` are untouched, so a
  run checks exactly what `npm run build` checks. It has no `compilerOptions` block at
  all, and `tests/test_conventions.py` reads that back -- it would otherwise be the one
  place the front end's real checks could be turned down.
- **Its own workflow, at `23 6 * * 6`.** `.github/workflows/mutation-ui.yml`, an hour
  after `mutation.yml` and with a concurrency group of its own. Ninety minutes stacked
  into the package's two-minute job would make that report wait on this one, and both
  would queue for the same runners.
- **What is mutated is named, and what is not is named too.** The four components, the
  service and the download helper. `agent.types.ts` is the payloads written down as
  types, `testing.ts` is the ones the specs are written against, and `app.config.ts` is
  what `main.ts` boots the app with -- mutating any of the three says nothing about a
  test.
- **One report, two readers.** `scripts/mutation_report.py` now holds a `Tool` per
  tester: what its statuses mean, how a run of it reads, and the floor under it. The
  file it is handed says which one wrote it -- mutmut a listing, Stryker a JSON report
  -- and the report itself is the same shape either way: the score, a row per file worst
  first, and where the survivors cluster. Stryker records a position rather than a
  function, so a cluster is a file and the mutator that made it.
- **The floor is where the front end stands, and the report holds it.** 60%, under the
  74% the first run was holding a seventh of the way through it -- ADR-0016's reasoning
  and `benchmark.scoring.FLOORS`': a floor above where a thing sits is a weekly job that
  goes red for a week and is then ignored. The number is the first full run's to settle
  and moving it is a commit of its own quoting the run behind it. Stryker's own
  `thresholds.break` stays `null`, so nothing fails a run before its report is
  published.

## Consequences

The weaker of the two coverage numbers now has the question coverage cannot ask behind
it: a mutant that lives through 244 specs is a line those specs run and do not read,
which is the half `ui/angular.json`'s missing branch floor was always silent about.

The obligations are three:

- **A new source file under `ui/src/app` is mutated by default.** The include glob is
  the directory, so leaving something out is a line in `mutate` and a reason beside it,
  which `test_every_source_the_front_end_ships_is_mutated_or_named` and
  `test_every_file_the_mutation_run_leaves_out_is_one_that_is_there` read back together.
- **A fifth schedule has to pick its own minute.** Four weekly and nightly runs now
  share a pool of runners, and
  `test_no_two_scheduled_runs_are_waiting_on_the_same_runners` is where that is held.
- **The report is one script and two floors.** A change to the report's shape lands on
  both halves at once, which is the point; a change to *either* floor is a commit of its
  own quoting the runs that justify it, exactly as raising a benchmark floor is.

What it costs is an hour and a half of runner time a week and a dev dependency in `ui/`,
and what it does not buy is a gate: like the package's run, it is a report, and a
survivor is not automatically a missing test. The honest gap is that `ng test` is a
build and a whole suite per mutant -- nine tenths of that ninety minutes is Angular
compiling the same instrumented tree over and over. If Stryker's vitest runner ever
learns to drive the builder's own pipeline, this run gets an order of magnitude faster
without anything about what it tests changing.
