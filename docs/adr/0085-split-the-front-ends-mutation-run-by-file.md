# ADR-0085: Split the front end's mutation run by file, a runner to each shard

- **Status:** Accepted
- **Date:** 2026-10-13

## Context

ADR-0061 runs Stryker over `ui/src/app` as one job: the command runner, a whole
`ng test` per mutant, four at a time on one four-core runner. It measured 969 mutants
and an hour and a half. The front end has grown faster than the job was sized for, and
the weekly report has not said where the front end stands since 2026-09-26:

| Run | Mutants | What happened |
| --- | ---: | --- |
| 2026-09-26 | 1127 | Passed at 78.3%, Stryker done in 172 minutes |
| 2026-10-03 | 1317 | Died compiling a template before its first mutant, fixed that day |
| 2026-10-10 | 1410 | Cut off by the job's 180 minutes with 1354 tested |

That is eight to nine seconds a mutant, whatever else changes, and about a hundred more
mutants a week. Raising the job's cap to five hours the next day bought a few weeks of
that; GitHub ends a hosted job at six, and nothing in a workflow raises it.

Making each mutant cheaper is the road ADR-0061 already declined: Stryker's vitest runner
cannot compile an Angular component, and reusing the builder's output from one mutant to
the next means a second vitest configuration to keep in step with the builder's. So the
run has to be spread over runners instead. Stryker has no shards of its own, and the one
way it narrows a run below a file, a line range in `mutate`, takes a mutant only when its
whole node lies inside the range (`locationIncluded`, in its instrumenter): a block that
crosses the cut belongs to neither side, and the score loses it without a word. A whole
file is the unit that splits cleanly.

## Decision

**The run is split by file into shards, each a job on a runner of its own, and reported
as the one run it is.**

- **`ui/stryker.config.mjs` holds the shards.** `SHARDS` names each shard's files, and
  `rest` takes every file `mutate` takes that no named shard does, so a new file is
  mutated by default and none is mutated twice. `STRYKER_SHARD` picks a shard; unset, the
  run is whole, exactly as before. A shard's reports are named after it,
  `reports/mutation/<shard>.json` and `.html`, so every shard's can sit in one
  directory. A name that is no shard fails the run as the config loads.
- **The split today is `search-form` (704 mutants), `app` (388) and `rest` (318).** A
  dry run of each, and of the whole, instruments 1410 mutants either way. The largest
  shard sets how long the run takes: about an hour and a half now, under a cap that stays
  at five hours as a guard on a hung mutant.
- **`.github/workflows/mutation-ui.yml` runs a job per shard, then `Report`.** The shards
  do not fail fast, and each uploads `mutation-report-ui-<shard>` however it ended.
  `Report` runs whether or not they finished: it downloads every shard's reports into one
  directory and hands `scripts/mutation_report.py` each shard's JSON by name.
- **The report adds the shards up before it counts anything.** One run's results may
  come as several files of one tester. A file with nothing at it is named at the top of
  the report, which is still published for its survivors, and fails the run whatever the
  rest scores. A source file two shards both report is refused rather than counted twice.
  The floor (60%, ADR-0061) is held over the front end, never over a shard.

## Consequences

The weekly run takes as long as its largest shard rather than as long as all of them in
turn, and the six-hour wall is reached when one shard alone takes six hours, not the
front end. What it costs is three runners for an hour and a half where there was one for
three, a fourth job that only reports, and three lists that must agree.

What it obliges:

- **The config, the matrix and the report agree.** A shard added to or renamed in
  `SHARDS` is a matrix entry and a results file in the `Report` step, in the same order;
  `tests/test_conventions.py::test_each_shard_of_the_front_end_s_run_is_a_job_and_a_results_file`
  holds the three together.
- **A shard narrows the run and never widens it.** A file a shard names is there, is one
  the whole run mutates, and is named by one shard only:
  `tests/test_conventions.py::test_every_file_a_shard_names_is_one_the_whole_run_mutates_once`.
  `rest` is built from `mutate` in the config itself, so the two cannot disagree about
  what is left over.
- **A shard that outgrows the cap is split in the source, never by line range.** When
  `search-form.ts` alone nears five hours, it becomes two files and two shards: a range
  would drop the mutants that straddle it.
- **A run on a developer's machine is unchanged.** `npx stryker run` with no
  `STRYKER_SHARD` is the whole run and writes `reports/mutation/mutation.json`; with one,
  it is that shard and writes that shard's files.
