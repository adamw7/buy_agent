# ADR-0072: Print and keep the nightly's scorecard, pass or fail

- **Status:** Accepted
- **Date:** 2026-10-05

## Context

ADR-0036 gave the nightly run a number so that "did that change to a prompt make
things better?" could be argued from evidence, and said the number would be "logged
whole pass or fail". It was logged only on a failing night. `integration/test_benchmark.py`
wrote the scorecard at INFO from a session fixture; `pytest.ini` sets no log level, so
the root logger's WARNING dropped the record before pytest could capture it, and
`-rA` showed nothing either. The table reached the job's log only inside a floor's
assertion message -- that is, only when something had already gone wrong.

Nor was anything kept. The workflow's one artifact was Ollama's log, uploaded on
failure. So the project's only scheduled measurement of a real model was discarded
on every green night, which are the nights a trend is made of, and the floors --
raised "only in their own commit, quoting runs" -- had no runs anyone could quote.

Two smaller things went with it. The nightly scored what the model extracted and not
the query it was asked for first, though ADR-0070 had given the query a score and the
same run had already answered it; and it read its settings from `corpus.settings`, a
copy of `Case.settings` that a test held equal to the original.

## Decision

The nightly's shared run is scored the way `python -m benchmark` scores one, and its
scorecard is printed and kept whatever the floors say.

- The run the live tests share asks its model through the comparison's `Stopwatch`,
  and `compare.scored_run` builds the same `CaseRun` a comparison keeps -- the
  scorecard, the query's checks, the time per question, the products -- off that one
  run. Nothing is asked twice.
- `integration/conftest.py`'s `pytest_terminal_summary` prints `compare.describe`, the
  command line's own text, at the end of every session that scored a run; appends
  `compare.summary_markdown` to `$GITHUB_STEP_SUMMARY` where it is set; and writes
  `compare.write_standings`, the `--json` shape, to `$BUY_AGENT_SCORECARD` where that is
  set.
- `integration.yml` sets `BUY_AGENT_SCORECARD` and uploads the file with `if: always()`.
- The query and the time are reported, not floored. No run has yet said where such a
  floor belongs, and a floor guessed at is how a scheduled job gets ignored (ADR-0036).
- The nightly takes its settings off `HEADPHONES`, and `corpus.settings` is gone.

## Consequences

A green night says its number in the job's log and on the run's summary page, and
leaves a file in the shape every other run of the benchmark is written in.

What it obliges:

- `tests/test_conventions.py::test_the_nightly_run_keeps_its_scorecard_pass_or_fail`
  holds the file the workflow uploads to the one `integration.SCORECARD_ENV_VAR` names,
  and the upload to `if: always()`. A step that uploaded only on success would keep the
  nights least worth keeping.
- The summary exists only once the scorecard fixture has run. A session that never
  reaches it -- no Ollama, no model, a selection without `integration/test_benchmark.py`
  -- prints nothing, and the upload finds no file, which it ignores rather than failing
  a job that has already failed for the real reason.
- The log's text is `describe`'s, which `python -m benchmark` prints too: a change to one
  door's wording is a change to the other's.
- An artifact lasts as long as the repository keeps artifacts. A longer history would
  need somewhere to live and is not decided here.
- The floors are still a tripwire, raised in their own commit. These files are now the
  runs that commit quotes.
