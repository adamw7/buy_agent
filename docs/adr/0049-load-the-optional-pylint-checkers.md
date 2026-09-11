# ADR-0049: Load the optional pylint checkers that state a rule already held

- **Status:** Accepted
- **Date:** 2026-09-11

## Context

[ADR-0048](0048-lint-the-package-with-pylint.md) put a linter in front of the
package and settled how it is configured: a rule this project has answered goes
in `.pylintrc` with the answer, a line the tool misreads is suppressed where it
fires, and the run has to come out with no message at all. What it settled
nothing about is *which* checks are running, and the answer it left behind was
whatever pylint switches on by itself.

That is not a decision, it is a default, and it leaves a good deal out. Pylint
ships twenty-five checkers it does not load, each an extension somebody has to
name before it runs, and several of them ask exactly the questions this codebase
already answers everywhere and has never had anything check. Nothing was reading
docstring sections against the code under them, though `tests/test_conventions.py`
reads one of them -- `BuyAgent.run`'s `Raises:` -- against `api._STATUS`, and
ADR-0009 rests on that agreement holding. Nothing was reading `except` tuples for
a class caught beside its own ancestor, though ADR-0046 puts `RailUnreachableError`
under `PaymentError` on purpose and `api.PAY_STATUS` is ordered subclass-first
because of it. Nothing was reading imports for a private name out of somebody
else's package, though the whole of `tests/test_architecture.py` is about which
module may import which (ADR-0047).

Running all twenty-five over `buy_agent/` says why they are not simply all loaded.
It produces ninety-two messages, and they split the same way the fifty-eight in
ADR-0048 did. Twelve are findings: an `except (ArithmeticError, InvalidOperation,
ValueError)` in `payment.minor_units`, where the second of the three is a subclass
of the first and the other `DecimalException`s -- `Overflow` among them -- were
being caught only by the accident of the first being there at all; a loop variable
reassigned in its own body in `verification.verify_numbers`; and ten
`typing.Callable`s in three modules, in a package where eight other modules
already spell it `collections.abc.Callable`. The rest are the other kind: two
dozen comparisons against numbers this project argues for in prose beside them,
twenty-two `try` blocks wider than one statement in a package whose comments say
why each is, twenty-eight `if` statements a checker would rather see as walrus
assignments, and a branching ceiling `tests/test_architecture.py` has already
declined in as many words.

## Decision

`.pylintrc` loads fifteen of pylint's optional checkers, and the rule for that
list is the mirror of the rule ADR-0048 set for the `disable` block: **a checker
is loaded when the answer it wants is the answer already written into every
module of this package**, and left out where this project answers differently. A
checker is run over `buy_agent/` before it goes in, and what it finds is fixed in
the code rather than configured around -- fixing the finding is what makes a
newly loaded checker a check, and suppressing it would make it a preference.

The `enable` block gains the other four of the family the two already there
belong to: `bad-inline-option`, `deprecated-pragma`, `file-ignored` and
`raw-checker-failed`. Each is a way for a check to stop running with nothing
going red -- a pragma pylint could not parse, the `disable-msg=` spelling it no
longer reads, a file switched off from its first line, a checker that could not
read the source -- and all four are informational and off by default, which
leaves the default a linter that skips something and mentions it in a message
nobody has turned on. `locally-disabled` and `suppressed-message` stay off: they
fire on every pragma that is doing its job, which here is every pragma there is.

Every one of those names is written as a name. `use-symbolic-message-instead`
already holds that for the pragmas in the package, but pylint does not apply it
to its own settings file -- `disable = W0718` is read there without a word -- so
`tests/test_conventions.py` holds that end, for the same reason the file says a
code is the one thing a comment beside the line is there to save.

## Consequences

The gate now fails on three shapes of mistake it could not see: a docstring
section that has stopped describing the code beneath it, an exception tuple that
reads as two cases where there is one, and a `typing` spelling this package
retired everywhere else. None of the three is visible to the other four checks --
each of them passes the tests, the coverage floor, the mutation run and the
import graph, because each of them *runs*.

The obligation is the list itself. A checker left out is left out for a reason,
and `.pylintrc` writes that reason down beside the ones that are in, which is
what stops the list being reopened every time somebody notices `magic_value`
exists. The reasons are about this package and not about the checker, so a
change to the package can change the answer: if the `try` blocks here ever became
one statement each, `broad_try_clause` would be worth another run.

The cost is that the list has to be kept. Pylint's extensions are added to
between releases, and `requirements-dev.txt` pins the version renovate bumps, so
a new checker arrives switched off and stays there until somebody runs it --
which is the safe direction for the failure but not a free one. The other cost is
the ordinary one of a wider gate: a fix that would once have been noticed by a
reviewer is now a red job, and the answer to a red job is never a suppression
without the sentence ADR-0048 requires above it.
