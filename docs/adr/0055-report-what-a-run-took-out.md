# ADR-0055: Report what a run took out

- **Status:** Accepted
- **Date:** 2026-09-18

## Context

Eight heuristics thin a run's own results, and every one of them already says so:
the count at INFO, the names at DEBUG, held to it by
`tests/test_logging_contract.py`. That is the whole answer to "why is the one I
had in mind not in there?", and on the two front doors it is not much of one.

On a terminal the count has scrolled past by the time the report is read, and the
names need a `-v` that was not given -- which means re-running a pipeline that
takes a minute to find out what the last run did. In the browser it is worse: the
progress panel holds the lines while the run is going and the results replace
them, so the one question a short report provokes is answered by a panel nobody
is looking at any more. The "Nothing came back" banner ends by pointing at it --
"Progress, above, says which" -- which is the page admitting that it knows and is
not saying.

The run *does* know. `ground` drops a product the pages never mention,
`clean_products` drops a headline, `merge_variants` folds one listing into
another and the shorter name is the one that survives, and the shopper's own
bounds drop whatever they were set to drop. Each of those is a judgement worth
arguing with, and each was reaching a log handler and nothing else.

Three ways of keeping them were available. The steps could answer a pair --
survivors and removals -- which is the purest reading of "the steps take values
and answer values", and would have rewritten some sixty call sites in the suite
for a field the pipeline itself never reads. `BuyAgent` could diff the list
before each step against the list after, which needs no signature to change and
is wrong: `clean_products` *renames* what it keeps, so a diff by name reports
every cleaned product as dropped and every cleaned name as new. Or the caller
could hand in a recorder, the way it already hands in a `checkpoint` (ADR-0034)
and a `wait` (ADR-0053).

## Decision

A step that removes a whole product hands it to a recorder: `record`, a keyword
argument defaulting to `models.nothing_recorded`, called with a `models.Removal`
carrying the name it went under, the step that took it, and the reason as a
finished sentence. `BuyAgent.run` takes the same keyword and passes it down;
`api.run_search` collects into a list and the run payload carries it as
`dropped`.

Five steps record: `clean_products`, `drop_ungrounded`, `deduplicate`'s nameless
drop, `merge_variants` and `Constraints.apply`. The other three of the eight do
not, because they blank a figure, a quote or a link on a product that stays in
the report and says so on its own card.

The sentence is Python's. The browser groups the removals and counts them and
composes no wording of its own, which is ADR-0012 applied to this payload.

## Consequences

The answer to a short report is now in the answer itself: the CLI keeps the
count-and-names it always had, and the page lists what went under the results and
under the "Nothing came back" banner alike.

What it obliges:

- **A new step that removes a whole product takes the recorder and uses it**, or
  the panel quietly under-reports and nothing fails. `tests/test_logging_contract.py`
  holds the eight to the count and the names and now partitions them by whether
  they record, so a ninth is added to that table and answers both questions.
- **A step that blanks a field must not record.** A panel listing products that
  are still in the report is a panel that has to be read twice to be believed.
- **The default must stay "nobody is recording", and must change nothing.**
  Every step is called with no recorder throughout both suites and by any Python
  caller holding an agent; `test_nobody_recording_is_the_default_and_changes_nothing`
  drives each step both ways and compares the survivors.
- **Every stand-in for `BuyAgent` takes `record`**, since `run_search` always
  passes it -- the obligation `checkpoint` already carries (ADR-0034).
- **`Removal` is mirrored in `agent.types.ts`**, like every other payload, and
  `test_a_removal_is_mirrored_field_for_field_in_typescript` fails when it is not.
- **A re-sort reports no removals of its own** (ADR-0035): it runs no pipeline, so
  it took nothing out, and the page carries the run's own list across rather than
  taking the empty one. Answering the run's list from `/api/rank` would be that
  endpoint speaking for a run it never saw.
- **The reason is written where the removal is made**, beside the log line that
  already says the same thing, and pinned by that module's own test file. Two
  wordings for one judgement is how the panel and the progress log come to
  disagree -- which is why `Constraints.apply` builds its sentence from the same
  `describe` the logged line uses, currency and all (ADR-0043).
- `--json` is deliberately unchanged: `api.results_payload` shapes a run's
  *products* and is the one shaping of them (the API's answer, the `--json` file
  and Download results alike), and a removal is not one. A CLI reader has the
  count and `-v`.
