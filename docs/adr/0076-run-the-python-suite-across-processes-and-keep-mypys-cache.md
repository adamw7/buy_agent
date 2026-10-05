# ADR-0076: Run the Python suite across processes, and keep mypy's cache

- **Status:** Accepted
- **Date:** 2026-10-05

## Context

Every push and pull request waits on `ci.yml`'s Python job, and it had become the
slower of the two by double: about 75 seconds against the UI job's 35. Its own log
says where they went. The suite took 28 seconds under `coverage run`, in one
process, and thirteen of those were one stretch of it: the 21 tests that start
`pwsh`, which a machine without PowerShell skips and so nobody timing the suite
locally ever saw. mypy took six to nine seconds to analyse, on every run, the same
third-party packages it had analysed on the run before -- `openai`'s types above
all -- because nothing kept its cache. Installing took fourteen, pylint ten.

A runner is not the machine the suite is written on. Its four CPUs are two cores of
two threads each, and the core under them varies from run to run -- an EPYC 7763, a
9V45, a 9V74, two kinds of Xeon -- so the same serial run took anywhere from 15 to 32
seconds. A first try at a worker per CPU saved two seconds of the 28: every worker
collects the whole suite before running any of it, and each PowerShell module's
`pwsh` run, a module-scoped fixture, ran once per worker that drew one of its tests,
four at a time on two cores.

So the choices were timed on the runners themselves, each runner timing every
candidate in turn so that the hardware cancelled out: twelve runners, two rounds
each, the order reversed between them. Three workers beat four on every runner by
about two seconds, and two by one to eight. Sending each PowerShell module to one worker
ahead of the rest (`--dist loadgroup`) helped four workers by a second and three by
nothing measurable.

Running across processes found one thing on its first runs: two tests in
`tests/test_screenshots.py` checked what the camera does to a crashed browser right
after the picture's caller had been answered, while the camera's own thread was still
letting the browser go. One process in one order always won that race; four did not.

## Decision

`ci.yml` runs the suite as `python -m pytest -n 3 --cov`, and keeps mypy's cache.

- **pytest-xdist** runs three workers, a number measured on the runners rather than
  read off them. **pytest-cov** measures each worker, combines them into
  `.coverage`, prints the table `.coveragerc` describes and fails the step below its
  `fail_under`, so the separate `coverage report` step is gone;
  `python -m coverage report` still reads the file afterwards. Both are pinned in
  `requirements-dev.txt`. Nothing in `pytest.ini` asks for workers: a single test,
  `integration/`, whose shared run of a real model must happen once, and mutmut stay
  in one process.
- **Coverage's own subprocess measurement** (`[run] patch = subprocess`) was the
  other way to measure workers, and is not used: it makes every `coverage run` write
  parallel files that need a `coverage combine`, and it does not erase the files an
  earlier run left, so a combine can count lines a test reached before the code
  changed.
- **The default distribution stays.** Grouping the PowerShell modules bought nothing
  at three workers, and a mark every such module would have to carry is a rule with
  no measured reason behind it.
- **`actions/cache` restores `.mypy_cache`** before mypy runs, keyed on the runner's
  OS, the requirements files the job installs and `setup.cfg`. The key holds no
  commit, so a cache is saved once per set of pins and the package's own few files
  are re-analysed each run, which is a second where the third-party half was nine.
- **A test leans on no other test**, and one whose code answers before its own thread
  is done waits for that thread rather than for the answer. The two screenshot tests
  now do: one waits on the browser's `closed` event, the other closes the camera,
  which joins its thread, before reading the log.
- **Not done**: pylint and mypy stay last in the test job (ADR-0048, ADR-0063), so the
  job is still the longer one; a job of their own would take ten more seconds off it,
  at the price of superseding where those records put them and a second
  `python-version` in `ci.yml`. pylint's `--jobs` saved nothing measurable. Caching
  the installed packages would take back most of the install, and would also hold
  every unpinned transitive dependency where the cache was made until a pin moved,
  which is a different gate from the one that installs fresh.

## Consequences

The suite's step falls from 15-32 seconds to 11-21 on the same runners, and mypy's to
about a second once a cache for the current pins exists. The Windows half (ADR-0037)
runs the same command.

What it obliges:

- `scripts/preflight.ps1` runs the same command, and
  `tests/test_conventions.py::test_the_preflight_script_runs_what_ci_runs` holds the
  two together; the preflight skill, `CLAUDE.md` and `docs/testing.md` write it out.
  A contributor's machine with more cores still runs three workers, because the gate
  is one command.
- The worker count is a measurement of GitHub's runners. When their shape changes --
  more cores, or a larger runner -- it is measured again the same way, side by side on
  the runners, rather than set to the count of CPUs.
- A test that passes alone and fails under `-n 3` is a test sharing state with
  another or racing a thread of its own code. It is fixed in the test, never by taking
  the gate back to one process.
- `tests/test_conventions.py::test_only_the_gate_asks_for_workers` holds `pytest.ini`
  free of workers and coverage, so every other way of running pytest stays in one
  process.
- `tests/test_conventions.py::test_mypys_cache_is_keyed_on_everything_that_moves_its_answers`
  holds the cache's key to every file `ci.yml` hands pip and to `setup.cfg`. A key
  naming too little cannot change an answer, since mypy checks every entry against its
  file; it restores a cache made before a pin moved, and the nine seconds come back.
