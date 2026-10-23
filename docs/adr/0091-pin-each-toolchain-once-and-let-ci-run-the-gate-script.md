# ADR-0091: Pin each toolchain once, and let CI run the gate script

- **Status:** Accepted
- **Date:** 2026-10-23

## Context

ADR-0067 had `scripts/setup.ps1` and the session hook read the Python and Node
versions out of `ci.yml`, and had `scripts/preflight.ps1` write `ci.yml`'s checks
out a second time, held equal to them by a convention test. Both made `ci.yml` the
source everything else copied from, and the copies were the cost.

- **The pins.** Node's version was written in nine files: four workflows, the
  `Dockerfile`, `scripts/start.ps1`, README, CLAUDE.md and the `preflight` skill.
  Moving it a patch release touched all nine, twice in one week (#351, #356).
  Renovate needed two hand-written regex managers to find the copies in prose, and
  CLAUDE.md was left out of them because the pattern would also catch Angular's
  own floor, so that copy was moved by hand. Eight convention tests held the copies
  to `ci.yml`, one per place, and the setup script and the hook each read the YAML
  with a regex to find the number.
- **The gate.** `ci.yml` ran eight checks as steps, and `preflight.ps1` ran the
  same eight as a list of its own. `test_the_preflight_script_runs_what_ci_runs`
  held the two equal, so a check added to one was a red test until it was added to
  the other. What a contributor ran and what a pull request met were two lists
  kept equal rather than one list.

## Decision

- **`.python-version` and `.nvmrc` hold the versions**, one line each, and nothing
  else writes them. Every workflow's `actions/setup-python` and `actions/setup-node`
  read them (`python-version-file`, `node-version-file`); `setup.ps1`, `start.ps1`
  and the session hook read them; README, CLAUDE.md and the `preflight` skill name
  the files rather than the numbers. Job names carry no version.
- **The `Dockerfile` keeps the one other copy**, because `FROM` takes no file. A
  convention test holds its base images to the two files, and Renovate's `nvm` and
  `pyenv` managers move the files in the same Node and Python groups its
  `dockerfile` manager moves the images in. The regex managers are gone.
- **`ci.yml` runs its checks through `scripts/preflight.ps1`.** Each job installs,
  then runs `preflight.ps1 -Only <job>` under `pwsh`, which both runners carry. The
  script uses `.venv` where there is one and otherwise the `python` on PATH, which
  is where a runner installs. A job's checks are one step, whose log heads each
  command it runs and ends by naming the one that failed.
- What stands of ADR-0067: `.gitattributes`, `setup.ps1` as the hook for a person,
  and the preflight script with its `-Only`.

## Consequences

A Node or Python bump is one line in one file, plus the `Dockerfile`, in one
Renovate pull request. A check is added once, to `preflight.ps1`, and a
contributor's gate and a pull request's both have it.

What it costs: CI shows one checking step per job rather than one per command, so
a red job is read in that step's log. The UI build is now kept after lint and
formatting rather than before them, which changes nothing a pull request sees, the
browser job needing the `ui` job green either way. The checks now depend on
PowerShell being on the runners.

What it obliges:

- A workflow sets a toolchain up from its pin file and never writes a version
  (`test_every_workflow_sets_up_the_toolchains_the_pin_files_name`). A script that
  checks one reads its file
  (`test_every_script_that_checks_a_toolchain_reads_its_pin_file`), and nothing that
  names one to a person writes its version down
  (`test_nothing_that_names_a_toolchain_writes_its_version_down`). The
  `Dockerfile`'s images stay on the pins
  (`test_the_image_is_built_on_the_pinned_toolchains`).
- Each `ci.yml` job runs `preflight.ps1 -Only` its own job, and the three together
  are the script's `ValidateSet`
  (`test_ci_runs_its_checks_through_the_preflight_script`). Nothing else in `ci.yml`
  runs but the installs
  (`test_ci_runs_nothing_beside_the_script_but_what_installs_its_tools`). A test
  that reads a check -- pylint's and mypy's targets, the browser suite -- reads it
  off the script.
- `setup.ps1` and the session hook still install what `ci.yml` installs. That list
  is not the script's, and is held to `ci.yml` as before.
- A pin file the suite reads is listed in `setup.cfg`'s `also_copy`, as every file
  outside the package is.
