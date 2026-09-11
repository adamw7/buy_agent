# ADR-0048: Lint the package with pylint, and write down every answer

- **Status:** Accepted
- **Date:** 2026-09-11

## Context

There has never been a Python linter here. `CLAUDE.md` said so in as many words,
beside the sentence naming the Prettier the UI half has had since it became a
workspace of its own ([ADR-0013](0013-ui-as-a-separate-angular-workspace.md)),
and `.claude/skills/preflight` repeated it. What the Python side had instead was a
coverage floor at 99% over branches, a suite of cross-module conventions
([ADR-0014](0014-conventions-tests-over-coverage.md)), the import graph
([ADR-0047](0047-check-the-import-graph-with-archunit.md)) and a weekly mutation
run ([ADR-0016](0016-mutation-testing-weekly-not-per-push.md)) -- four checks
that between them say almost everything about whether the code *works* and
nothing at all about the shape of a line.

Two things made the gap visible.

The first is that the package was already annotated for a linter. Twenty-seven
lines of `buy_agent/` carried `# noqa` codes -- `BLE001` on every deliberate
`except Exception`, `PLC0415` on each of the eleven deferred SDK imports, `N802`
on the three `do_GET`-shaped methods, `A002` on the `format` parameter
`BaseHTTPRequestHandler` names -- each with the reason beside it, each addressed
to a tool no command in this repository ran. They were written in ruff's
vocabulary and none of the codes they name is in ruff's default rule set, so even
a bare `ruff check` would have read straight past them. A suppression nothing
suppresses is worse than none: it reads as a check somebody is applying.

The second is what a run over the package actually found. Fifty-eight messages,
of which one was a real finding the other four checks cannot make:
`_dry_run_settle` takes an `authorisation` it never uses and never `del`s, in a
module whose other three rail functions all mark their unused arguments that way.
The rest split cleanly in two: rules this project has deliberately answered the
other way (a 25-field `AgentConfig` is what "a new option is a field in
`config.py`" produces; `ChatModel` has one method because a stand-in has to be a
class with one method), and single lines where the tool cannot see what the code
is doing (a tuple of exception classes assembled from `api._STATUS` at run time,
`parser.error` exiting rather than returning).

That ratio is the argument for a linter here and also the argument for
configuring it carefully. Run with its defaults it is fifty-odd warnings a week
about decisions already recorded, which is a check nobody reads; run with half of
it switched off to make the noise stop, it is a check that says nothing.

## Decision

`.github/workflows/ci.yml` runs `python -m pylint buy_agent` in the job that
already runs the tests, last, so a red lint never hides a red test. `pylint` is
pinned in `requirements-dev.txt` like every other dependency, and `.pylintrc` at
the repository root holds the settings -- another file that is not a packaging
file, beside `setup.cfg` and `pytest.ini`, for the reason there is no
`pyproject.toml`.

Three things are settled about how it is used.

**The linter's target is the package the other tools measure.** `.coveragerc`
measures `buy_agent`, `setup.cfg` mutates `buy_agent`, and the lint reads
`buy_agent`. `tests/test_conventions.py` holds the three together, so a package
that grows a second top-level module is measured, mutated and linted or none of
the three. The test trees are deliberately outside it: pytest's fixtures shadow
their own names by design and its tests say what they assert in the name rather
than a docstring, so linting them means turning off the checks that give the
package's own gate most of its value, and a configuration that says two different
things about the same repository is one nobody can reason about.

**A rule this project has answered goes in `.pylintrc` with the answer; a line
the tool misreads is suppressed on the line, with its reason.** The file turns off
three checks and only three, each with a paragraph saying which decision already
covers it. Everything else that fires is a `# pylint: disable` beside the code,
carrying prose above it, and `tests/test_conventions.py` fails a suppression that
carries none. Pylint holds the other end itself: `useless-suppression` is enabled,
so a pragma that has outlived the line it was written for fails the run rather
than becoming the next `# noqa`, and `use-symbolic-message-instead` keeps every
one of them spelled as a name rather than as a code.

**The `# noqa` codes in the package are gone, replaced by what they meant.** Every
one of them became either a pylint pragma with the same reason attached or, where
pylint has nothing to say about the line, the reason on its own. One linter, one
vocabulary: a second set of codes for a tool nothing runs is the situation this
record exists to end. Six of them survive in `tests/` and `integration/`, which
nothing lints today and nothing linted yesterday either -- they are exactly as
inert as they were, and converting them is for whoever decides those trees should
be linted, not for a record that decided they should not.

The two design limits that remain -- `max-args` and `max-locals` -- are set at
the widest the package already is rather than at a round number, which is how
`benchmark/scoring.py` sets its floors and for the same reason: a tripwire above
where the thing stands catches nothing until long after it mattered.

## Consequences

Something now reads the code as code. Most of what a linter says on any given day
is not news, which is why the configuration above is as deliberate as it is -- but
the one real finding in the first run was in `rails.py`, a module with 100%
coverage that survives the mutation run and violates no import rule, because an
argument nobody reads is invisible to every check that asks whether the code
*works*.

It obliges the reasons to be kept. A new `except Exception`, a new deferred
import, a new stdlib override is now three lines rather than one: the code, the
pragma and the sentence above it saying why the check is wrong here. That is the
intended cost -- it is the same bargain [ADR-0006](0006-ground-every-figure.md)
makes with every figure, and the alternative is a growing block of names in
`.pylintrc` that nobody can date or attribute.

It also obliges `.pylintrc` to be read when it fires. Three checks are off, and
the reason each is off is a decision recorded elsewhere: a change that made
`AgentConfig` narrow, or gave `ChatModel` a second method, would leave a
suppression in place that has stopped standing for anything. Pylint cannot see
that, since the check is off rather than suppressed at a line -- which is the
argument for keeping that list at three and putting everything else beside the
code.

What it does not do is ship. Nothing in `requirements.txt` moves, the image is
unchanged, `.dockerignore` keeps the config out of the build context beside
`.coveragerc` and `pytest.ini`, and `setup.cfg` names it in `also_copy` so the
Saturday mutation run can still collect the tests that read it. If pylint were
abandoned tomorrow, what would be lost is one file, one CI step and fourteen
comments that would still be true.
