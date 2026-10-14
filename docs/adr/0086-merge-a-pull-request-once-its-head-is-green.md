# ADR-0086: Merge a pull request once its head is green

- **Status:** Accepted
- **Date:** 2026-10-14

## Context

Every pull request here has one author, and most are written by an agent. CI
and dependency review are the whole gate, so a person pressing Merge once both
were green added a wait but no judgement. GitHub's own auto-merge needs a
person to switch it on for each pull request, and a branch protection rule
naming the required checks, which this repository does not keep in code.

A merge made with `GITHUB_TOKEN` starts no workflow. `ci.yml` would not run on
main's new commit, and `release.yml` refuses to package a commit with no passing
CI run (ADR-0082). The same token also cannot merge a change to
`.github/workflows/`, which Renovate's action bumps make.

## Decision

`.github/workflows/automerge.yml` squash-merges a pull request when the latest
`pull_request` run of each workflow in its `REQUIRED` list (`ci.yml`,
`audit.yml`) has succeeded on the head commit. It wakes on either workflow
finishing and checks all of them, so whichever finishes second merges. The
merge names the head it checked (`--match-head-commit`), so a later push is
never merged unchecked.

Forks, drafts and pull requests labelled `no-automerge` are never merged. When
the `AUTOMERGE_TOKEN` secret is set, the merge is made with it; otherwise with
`GITHUB_TOKEN`, followed by a dispatched `ci.yml` run on main.

## Consequences

- A workflow that comes to gate pull requests joins `REQUIRED`, or a pull request
  merges without waiting for it.
- Without `AUTOMERGE_TOKEN`, a pull request touching `.github/workflows/` fails
  to merge and waits for a person. The dispatched CI run on main also brings in
  Windows (ADR-0037).
- `workflow_run` runs main's copy of the workflow, so a change to it applies from
  the pull request after the one that makes it.
- Renovate's pull requests merge on green like any other. A held major update is
  still held at the dashboard, before a pull request exists.
