# ADR-0064: Measure the two trees beside the package, without widening what three tools read

- **Status:** Accepted
- **Date:** 2026-09-22

## Context

`.coveragerc` set `source = buy_agent`, and three other rules read that one setting:
`setup.cfg` mutates what it names, and `ci.yml` lints and type-checks it (ADR-0048,
ADR-0063). Three tools, one target, each saying so in its own file -- which is the
right arrangement for the package and left two trees the suite tests under nobody's
floor:

- `benchmark/` is the answer key the live suite is scored against (ADR-0036).
  `tests/test_benchmark.py` exercises it, and `PERFECT` scoring exactly 1.000 is what
  says the key is reachable rather than a silent ceiling under every number the
  nightly reports.
- `scripts/mutation_report.py` decides whether the Saturday mutation run passes, and
  `scripts/update_ollama.py` decides what "updated" means -- a digest that moved
  rather than `ollama pull` reporting `success`. Both are tested for the reason
  anything that decides an answer is tested here rather than written into a
  workflow's shell, and then measured by nothing.

So a test deleted from either tree was a green run: 399 statements and 64 branches
that no number in this project was about. Coverage was in fact 99% over the two of
them -- three statements and two partial branches short -- which is the other half of
why this is worth doing rather than worth arguing about.

## Decision

Coverage measures all three trees under the one floor. The two beside the package are
named in `source_dirs` rather than added to `source`:

```ini
source = buy_agent
source_dirs =
    benchmark
    scripts
```

`source` stays the package and stays the thing mutmut, pylint and mypy read, because
those three answers do not change here. Mutating `benchmark/` would mutate the
*answer key* rather than the code it scores, which is a different question and not one
the Saturday run is asking.

The four gaps are covered rather than excluded, so the floor keeps the headroom it has
always had: 99 against 100% actual, over 3208 statements instead of 2809.

## Consequences

One floor now covers everything the Python suite tests, and a test deleted from either
tree fails `coverage report` the way one deleted from the package does.

It obliges three things.

- A tree added to `source_dirs` is measured and nothing more. A tree added to `source`
  is one pylint, mypy and mutmut are all then held to, and the mutation run would be
  mutating an answer key. `test_the_floor_measures_the_package_through_the_setting_three_tools_read`
  and `test_the_mutation_run_mutates_the_package_coverage_names` hold both halves of
  that, the second by requiring the two settings to name nothing in common.
- The package's own floor is now read across a wider denominator, so ~29 statements of
  headroom sit under it rather than ~25. That is the cost of one floor over three
  trees, and the alternative -- a second config file and a second run of the whole
  suite for a second `fail_under` -- buys four statements of strictness for a sixth
  configuration file and twice the suite.
- `scripts/start.ps1` stays outside all of it. It is not Python, nothing measures it,
  and `tests/test_start_script.py` is its gate.
