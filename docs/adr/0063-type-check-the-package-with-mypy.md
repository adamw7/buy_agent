# ADR-0063: Type-check the package, which was annotated and read by nothing

- **Status:** Accepted
- **Date:** 2026-09-21

## Context

`buy_agent/` is annotated throughout. 298 of its 337 `def`s carry a return type,
every dataclass field is declared, `chat.py` is generic over the Pydantic schema
one call is read back as ([ADR-0038](0038-own-the-model-call-instead-of-a-framework.md)), and
a dozen tests in `tests/test_conventions.py` hold those declarations against
`ui/src/app/agent.types.ts` so that a field added on one side of the language
boundary is added on the other. Nothing read them. The annotations were prose
that happens to parse.

The tell was one line. `api.py` carried a `# type: ignore[arg-type]` on the call
that builds the agent -- a suppression addressed to a checker no command here
runs, which is exactly what
[ADR-0048](0048-lint-the-package-with-pylint.md) says the twenty-seven `# noqa`
codes had become, and which `.pylintrc` answers for its own pragmas by enabling
`useless-suppression`. Put a checker behind it and the ignore turns out to be
unnecessary: it was suppressing nothing, on a line that was already right.

The other nine findings are declarations that are not true. None was a bug the
day it was written and none is a bug today, which is the point: each is a claim a
later edit can rely on and be wrong about, and not one of them is visible to
either suite, either coverage floor, the mutation run or the import graph. That
is the same sentence [ADR-0047](0047-check-the-import-graph-with-archunit.md)
makes about an import in the wrong direction and
[ADR-0049](0049-load-the-optional-pylint-checkers.md) about the optional
checkers: it passes every test, keeps the floor, survives every mutant, and is
still wrong.

Three of them are worth naming, because each is a rule written down elsewhere
that the types were contradicting.

- The three tables declared their `transport_errors` as
  `tuple[type[BaseException], ...]` and handed what an `except` on one binds to a
  `hint` taking an `Exception`. Every class any row names is an `Exception`, so
  the wide declaration bought nothing and cost the narrowing four call sites
  needed -- one declaration, four errors.
- `journal.Change.movement` is a six-way `Literal` and was being handed a `str`
  that is provably two of them, which is the kind of claim the `Literal` exists
  to make.
- `api._as_number` tied the kind of a value to the kind of its bound, so
  `temperature` -- read with `float`, against a range `config.LIMITS` holds as a
  pair of ints like every other -- could only ever have been a whole number.

## Decision

`.github/workflows/ci.yml` runs `python -m mypy buy_agent` in the job that
already runs the tests and pylint, after pylint, for the reason pylint is after
the tests: a job stops at its first failing step, and of the three the tests are
what a change is about. `mypy` is pinned in `requirements-dev.txt` beside pylint,
and a `[mypy]` section in `setup.cfg` holds the settings -- beside mutmut's, in
the file whose opening comment already explains why a project that is run from a
checkout and never installed has no `pyproject.toml` for either of them.

Three things are settled about how it is used, mirroring the three ADR-0048
settles.

**The target is the package the other tools measure.** `.coveragerc` measures
`buy_agent`, `setup.cfg` mutates `buy_agent`, pylint reads `buy_agent` and so
does this; `tests/test_conventions.py` already holds the first three together and
now holds the fourth. `tests/` is outside it for the reason it is outside the
lint: a test builds stubs that stand in for a Protocol by satisfying one method,
hands `None` where a run would hand a clock, and says what it asserts in its
name -- checking it means either turning checks off or annotating the fakes, and
a configuration that says two different things about one repository is one nobody
can reason about.

**The floor is the default checks and not `strict`.** `strict` pulls in
`disallow_untyped_defs`, which the thirty-nine `def`s here carrying no return
type would each have to answer, and most of them are `__post_init__`, `close` and
the route methods, where a reader gains nothing. What is added to the default is
the one check this was noticed through: `warn_unused_ignores`, which is
`useless-suppression` one tool over, and which is what makes a `# type: ignore`
that has stopped being true fail rather than become the next `# noqa`. Raising
the floor to `strict` later is a commit of its own; starting there would have
been thirty-nine annotations nobody asked for standing between the change and the
ten findings it was made for.

**The libraries neither tool can read are named once per tool and held
together.** The AP2 SDK and the two libraries it signs with ship no stubs and no
`py.typed`, and `mandates.py` defers every one of those imports because a
checkout may not have them at all
([ADR-0046](0046-pay-on-the-shoppers-behalf-with-ap2.md)); `lxml`, which `fetch.py` parses
pages with, is a C extension with the same gap. `.pylintrc` says the first three
as `ignored-modules` and the fourth as `extension-pkg-allow-list`; `setup.cfg`
says all four as `ignore_missing_imports`, one section each, that being the only
spelling mypy has. It is one decision written in two vocabularies, so
`tests/test_conventions.py` reads the two files back against each other, from
both sides. Left to fail instead, this would have been the one check here that is
red on a checkout the tests call fine -- and red only where the SDK is missing,
which is the machine least able to do anything about it.

## Consequences

Ten declarations are now true that were not, and the eleventh finding was the
suppression that started this. The fixes are small and none is behavioural: three
tables narrow an annotation the values always satisfied, a `Literal` is written
where one was meant, a padding list of `[None]` becomes the `if` it stood for, a
`frozenset` annotation gets the `frozenset` call it claimed, a bound stops
pretending to be the value it bounds, each door casts the sort criterion its own
`_among` check already guaranteed, and the vLLM row casts this project's
`Message` into the OpenAI client's own parameter type at the one place the two
vocabularies meet -- which is that row's job and nobody else's, the seam knowing
nothing about either server.

It obliges the annotations to be kept, which is the cost and the point. A field
added to a payload, a `Literal` widened, a table row declaring a failure class:
each is now checked rather than described. The convention tests have held the
Python and TypeScript halves of those payloads against each other for a long
time; this is the same rule applied within the Python half, where it had been
running on trust.

It obliges one more thing to be installed. `requirements-mutation.txt` reads
`requirements-dev.txt`, so the pin lands in the Saturday job whether that run
wants it or not -- the same way pylint's does -- and the AP2 step `ci.yml`
already runs is what makes the SDK's absence a missing stub rather than a missing
module.

What it does not do is ship. Nothing in `requirements.txt` moves, the image is
unchanged, `.dockerignore` keeps `setup.cfg` and the new `.mypy_cache/` out of
the build context beside `.coveragerc` and `pytest.ini`, and `.gitignore` keeps
the cache out of the tree. If mypy were abandoned tomorrow, what would be lost is
one CI step and a section of one file; the ten corrected declarations would still
be correct.
