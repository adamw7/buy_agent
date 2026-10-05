# ADR-0075: Say what a kept run was scored under, and compare it with a baseline

- **Status:** Accepted
- **Date:** 2026-10-05

## Context

ADR-0070 keeps each contender's latest run of each case on a board, so a model scored
last week stands beside one scored today. It fingerprints the case a run was scored
against -- request, pages, key, metrics -- and leaves out a run whose case has changed.
It recorded nothing else a run depends on, so the board answered both of the
benchmark's questions worse than it looked.

- **Which model should the agent use?** After a change to a prompt, a threshold or
  `GENERIC_WORDS` -- ADR-0036's own list of what gets tuned -- re-running one model
  replaced its runs while every other model's stood beside them, made under the old
  code and shown as if comparable. A change to the shipped settings (`num_ctx`,
  `think`) did the same.
- **Is it the same model?** A contender is a tag on a server. A tag pulled again is
  other weights under the same name -- `scripts/update_ollama.py` exists to say which
  tags moved -- and the board could not tell a run of one build from a run of the next.
- **Did that change make it better?** The board keeps only the latest run, so the run
  from before a change is gone once the run after it is kept. Answering meant two
  `--json` files and reading them side by side.

## Decision

Every kept run records what it was scored under, a kept run made under something
else is marked rather than dropped, and the command line compares a run with an
earlier one.

- **The pipeline's code** (`benchmark/pipeline.py`): a fingerprint of the modules a run
  goes through between the pages and its scorecard -- the steps, the prompts and
  schemas, how the model is asked, how the corpus is served, how the answer is scored --
  read as syntax trees without docstrings, so a comment, a reworded docstring or a
  reflowed line moves nothing and a prompt or a step that changes moves it. The cases
  are not among them; each has its own fingerprint, which still drops a run.
- **Its settings**: what reaches the model and how much of each page it is shown --
  `temperature`, `think`, `num_ctx` where the server takes it, `page_chars`,
  `opinion_chars` -- named as the doors name them.
- **Its model's build**: the digest the server lists for the tag, asked through the
  provider row (`InstalledModel.digest`, "" where a server lists none).
- **A run made under other code or settings is kept and marked.** Its counts are true of
  what made them. The row is ranked as before, tagged on the page, and says how many of
  its runs to make again; the run says what differed. A row whose runs span two builds
  says so.
- **`python -m benchmark --baseline FILE`** sets each run beside the same contender's
  run of the same case in standings `--json` wrote earlier, metric by metric. The
  `--json` shape now carries each metric's value, the query's score, the seconds, the
  case's fingerprint and the pipeline's, so a later run can be read against it. The
  nightly's kept scorecard is such a file (ADR-0072).

## Consequences

A board kept across a change says which of its rows were made before it, and a model
pulled again is not mistaken for the one it replaced. "Did that make it better?" is one
command with the file from before.

What it obliges:

- **`pipeline.MODULES` is the list of what can move a run.** A module that starts
  deciding what a run reports or how it is scored joins it, or a change to it marks
  nothing. A case's module never does: its own fingerprint drops a kept run where this
  one marks it.
- **Code moves the fingerprint where words do not**, by design. A hint reworded in
  `providers.py` is a string in a tree and marks every row, which a docstring would not.
  Erring that way costs a re-run; the other way costs a comparison that is no
  comparison.
- **The fingerprint is of the syntax tree as the running Python reads it.** A Python
  whose `ast` dumps differently marks every row once; a board is kept on one machine.
- **The settings recorded follow `pipeline.settings`.** A setting that comes to change
  what a model answers or what it is shown joins it there.
- **A kept run whose case or server has gone is judged on its code alone**, since
  nothing can say what it would run with now.
- **`InstalledModel.digest` is read by the benchmark only.** The shop's `/api/models`
  does not carry it, so `agent.types.ts` mirrors no new field.
