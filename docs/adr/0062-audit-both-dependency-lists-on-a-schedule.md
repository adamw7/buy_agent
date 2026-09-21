# ADR-0062: Audit both dependency lists on a schedule, and review what a pull request adds

- **Status:** Accepted
- **Date:** 2026-09-21

## Context

`renovate.json5` watches five manager types and keeps every pin fresh, and every
sentence of reasoning in it is about *versions moving*: a deprecation that
`pytest.ini` turns into an error, a peer range that cannot be satisfied, a file the
default pattern never read. That is one question. "Is anything we pin known to be
broken today" is another, and nothing here asked it.

The two questions run on different clocks. The first starts when a maintainer
publishes a release; the second starts when somebody publishes an advisory, and it
can go off about a line that has not moved in a year. A project that only watches
the first one finds out about the second by reading the news.

There is a specific reason to care here rather than in general.
`requirements-ap2.txt` is installed `--no-deps`, so pip resolves nothing for it and
`requirements-ap2-deps.txt` carries `cryptography` and `jwcrypto` by hand -- the two
libraries the mandates authorising money are signed with (ADR-0046). Renovate could
not even see that file until its `managerFilePatterns` entry was added, and four
majors of the signing library went by unseen. Nothing about that was visible to
either suite: the payment tests sign real mandates and read them back through the
SDK's own verifier, and they pass on a `cryptography` with an advisory against it
exactly as they pass on one without.

The lists are small, which is what makes this worth automating rather than
remembering: six runtime pins, four dev pins, one mutation pin, the two paying
files, and an npm lockfile that is two megabytes of transitive packages. Most weeks
the answer is "nothing".

Which is also where the danger is, and ADR-0016 already names it: a floor set above
where a thing stands is a weekly job that goes red for a week and is then ignored.
An audit has the same failure mode one step further along -- a gate that cries wolf
gets an `|| true` within a month -- and the npm tree is where it would do it. The
advisories standing the day this was written are two moderate ones in
`typed-rest-client`, the HTTP client `@stryker-mutator/core` reports with: a package
that runs on a runner one morning a week and never reaches a browser.

Auditing `requirements-ap2.txt` is the same trap in the other list, and worse for
being convincing. Handed that file, `pip-audit` resolves the SDK's own metadata --
`cryptography==46.0.5`, `jwcrypto==1.5.6`, `pytest==9.0.2` -- and reports fifteen
advisories in three packages. Not one of those versions is installed by anything
here: skipping that resolution is what `--no-deps` is for, and what *is* installed
for paying is the file beside it.

## Decision

**Both lists are audited nightly, and a pull request is reviewed for what it adds.**
`.github/workflows/audit.yml` is the fifth workflow, and it has two jobs on two
events.

- **The whole-tree audit is scheduled, not a merge gate.** `47 2 * * *` and
  `workflow_dispatch`, on its own minute beside the four runs already there
  (ADR-0061), and nightly rather than weekly: the mutation runs ask about code that
  changes when somebody pushes, and this asks about a database that changes when
  somebody else publishes. An advisory published on a Tuesday is not news that a
  Tuesday push made true, and failing that push's merge over it is how the `|| true`
  gets written.
- **`pip-audit` reads every requirements file but one.** `requirements.txt`,
  `requirements-dev.txt`, `requirements-mutation.txt`, `requirements-ap2-deps.txt`
  and `requirements-audit.txt`, each resolved with its transitives -- twenty-two
  packages behind the six runtime pins -- and none of them installed. No severity
  threshold: these lists are short, every line is a direct pin, and all of it is
  installed on a machine that runs the agent or its suite.
- **`requirements-ap2.txt` is the file left out, and it is left out for the reason
  it is installed `--no-deps`.** Its resolution is a tree this project refuses; the
  SDK itself is a git commit at an exact revision that no advisory database has a
  row for, and the name `ap2` on PyPI belongs to somebody else's package. What
  paying signs with is audited one file over.
- **`npm audit --audit-level=high` in `ui/`, off the lockfile.** It installs
  nothing -- half a second against the forty seconds `npm ci` takes -- and `high` is
  the threshold, for the `typed-rest-client` reason above. A moderate advisory is
  still printed; it is not what fails a run.
- **`actions/dependency-review-action` is the per-pull-request half, and the only
  one that gates a merge.** It reads what the branch *adds* to either list rather
  than the whole of what is pinned, so it cannot go red for an advisory published
  since the branch was cut. `fail-on-severity: high`, the same threshold the UI's
  audit uses.

## Consequences

Somebody moving a pin now finds out at the door that the version they are moving to
has an advisory against it, and the lists nobody reads are read every night by
something that knows what was published yesterday. The `cryptography` in
`requirements-ap2-deps.txt` is the one that matters and it is the one that was
invisible twice over: to Renovate until its pattern was widened, and to a suite that
signs real mandates with whatever version is installed.

The obligations are four:

- **A new requirements file is audited or named.**
  `test_every_dependency_list_is_audited_or_named` reads the workflow's `-r` lines
  against the files at the root of the tree, from both sides: a file nothing audits
  fails it, and so does a `-r` naming a file that has been renamed. The one
  exemption is named in the test with its reason, which is the shape
  `money.UNPLACEABLE` and `.dockerignore` already have here.
- **The two thresholds are one decision written twice.**
  `npm audit --audit-level=high` and `fail-on-severity: high` are the same sentence
  in two tools that do not read each other's configuration, and
  `test_the_two_audits_agree_on_what_is_bad_enough_to_fail` holds them together.
  Moving it is a commit of its own, the way moving a floor is.
- **A red audit is a bump, not a suppression.** `pip-audit` will take
  `--ignore-vuln` and `npm audit` an `|| true`, and neither belongs here: an
  advisory with no fix available gets the flag *with the sentence saying why and
  what releases it*, the way every `# pylint: disable` in the package carries its
  reason (ADR-0048). A suppression nobody can date is the thing this record is
  against.
- **A fifth schedule takes a fifth minute.** As ADR-0061 said when there were four:
  `test_no_two_scheduled_runs_are_waiting_on_the_same_runners` is where that is
  held.

What it costs is one pinned dev dependency, about a minute of runner time a night,
and a step on the merge path that depends on a repository setting nothing in this
tree can see -- Dependency Review reads GitHub's dependency graph, which is on by
default for a public repository and is not something a convention test can assert.

Two gaps are worth naming rather than papering over. The Python side has no
lockfile, so what is audited is what those files resolve to *tonight*: a transitive
that arrives tomorrow is audited tomorrow and was never reviewed at all, which is
the half `pip-audit` cannot fix and a lockfile would. And `--audit-level=high`
means a moderate advisory in something the browser really does load is a line in a
log rather than a red run -- the price of the threshold, paid in the direction that
keeps the gate believed.
