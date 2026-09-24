# ADR-0067: Script a contributor's setup and the gate, and check every text file out with LF

- **Status:** Accepted
- **Date:** 2026-09-24

## Context

Three things stood between a fresh clone and a green gate, and none of them was
written down anywhere a person joining would read first.

- **Line endings.** There was no `.gitattributes`, and Git for Windows installs
  with `core.autocrlf=true`, so every text file came out of a clone with CRLF.
  Prettier's `endOfLine` is `lf`: `npm run format:check` failed on all 28 files of
  an untouched checkout, and its remedy, `npm run format`, rewrote files nobody had
  changed. CI never saw it, the runners checking out LF -- so the machines the
  project is developed on (the CLAUDE.md "Environment" section) were the only
  ones it failed on.
- **Setting up to run is not setting up to contribute.** `scripts/start.ps1`
  installs `requirements.txt` and serves the page (ADR-0023). A contributor also
  needs `requirements-dev.txt`, `ui/` installed from its lockfile, and the AP2
  SDK. README called that SDK optional, and to a *run* it is -- but without it 74
  tests skip and the coverage floor cannot be reached, so a checkout set up by
  README's own steps reported a red gate that CI would pass. Only
  `docs/testing.md` and the `preflight` skill said otherwise. The only script
  that did the whole of it was `.claude/hooks/session-start.sh`, which runs in a
  remote Linux session and nowhere else.
- **The gate was a list in three places, none of them for people.** `ci.yml`
  runs eight checks. README's Tests section named four. The whole list was in
  CLAUDE.md and in the `preflight` skill, which a person does not invoke.

## Decision

- **`.gitattributes` checks every text file out with LF** (`* text=auto eol=lf`)
  and names the binary kinds the tree holds. A checkout is then the same on every
  machine rather than on every machine configured like the runners. It changes
  nothing already committed -- the index was LF throughout -- and a clone made
  before it keeps its CRLF copies until git next writes them, so `setup.ps1`
  counts them and names `git rm -r --cached -q . ; git reset --hard` rather than
  running it, that command overwriting whatever in the tree is uncommitted.
  `git checkout-index --force --all` looks like the gentler answer and is not
  one: a file whose size and time match the index is taken as up to date and
  left as it was.
- **`scripts/setup.ps1` is the hook for a person**: a `.venv` holding
  `requirements-dev.txt` and the SDK in its two commands (asked for afterwards
  with `mandates.available()`, as `start.ps1` asks), `npm ci` in `ui/` as `ci.yml`
  runs it, each step skipped where it is already done. The Python and Node it
  checks against are read out of `ci.yml`, as the hook reads them: an older Node
  is refused, which the Angular CLI would do a step later and less clearly, and an
  older Python is named and used, which is the hook's own answer. No arguments,
  for ADR-0023's reason -- there is nothing a flag could choose that is not
  already decided somewhere else.
- **`scripts/preflight.ps1` runs `ci.yml`'s checks, step for step and in its
  order.** A job stops at its first failing step, as on a runner, and the other
  job runs anyway, so one run reports everything that is red. It takes one
  parameter, `-Only python|ui`, which is not a setting of the project but which
  of two jobs to spend a minute on. The commands are written out rather than read
  off the YAML, which PowerShell has no parser for; a convention test holds the
  two to one list instead, as it already does the skill. The `preflight` skill
  says to run the script.
- These are a separate script from `start.ps1` and do not give it a switch.
  `start.ps1` has no parameters because everything it could be asked is a setting
  already (ADR-0023); "set up to develop" would be the first thing it was asked
  that is not, and the two share nothing but the venv.

## Consequences

A new joiner's setup is two commands -- `.\scripts\setup.ps1`, then
`.\scripts\preflight.ps1` -- and the second is the same gate a pull request meets.

What it obliges:

- A check added to `ci.yml` is added to `preflight.ps1` and the skill; a
  convention test fails otherwise. A requirements file added to what CI's Python
  job installs is added to `setup.ps1` and the hook.
- `setup.ps1` never writes down a version `ci.yml` pins, and installs the SDK the
  way `mandates.INSTALL` spells it -- the rules `start.ps1` and the hook are
  already held to, read by the same tests.
- Every program `setup.ps1` runs goes through `Run`, and every one `preflight.ps1`
  runs through `Job`, which records a failure rather than throwing it. Both are
  read off the AST by `tests/test_setup_scripts.py` through the probe
  `start.ps1` is tested with.
- A binary kind of file added to the tree gets a `binary` line in
  `.gitattributes`, or `text=auto`'s guess is what decides whether it survives a
  checkout.
