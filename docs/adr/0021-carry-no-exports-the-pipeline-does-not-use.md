# ADR-0021: Carry no exports the pipeline does not use

- **Status:** Accepted
- **Date:** 2026-08-26

## Context

ADR-0002 fixed the control flow in Python and kept the model out of it. It also
left `search.py` exposing `search_products_tool`, a LangChain `@tool` wrapper
around `search_web`, on the reasoning that search is useful as a tool elsewhere.

Nothing ever called it. `BuyAgent` calls `search_web()` directly, which is the
whole point of ADR-0002; `api.py` and `server.py` reach the same function
through the agent; `buy_agent/__init__.py` does not re-export it. The only
callers it ever had were the four tests written for it, which is what kept it at
100% coverage and made it look like live code in every report.

It was not free. It held the package's one import of `langchain_core.tools`, it
carried a second docstring of the same search arguments to keep in step with
`search_web`'s, and a reader met a tool interface in a project whose first
architectural decision is that there is no tool loop. "Useful as a tool
elsewhere" is a caller nobody has, and a hypothetical caller cannot say whether
the signature it is offered is the right one.

## Decision

The package exports what the pipeline, the two front ends and the documented
Python surface in `buy_agent/__init__.py` actually use. Something kept only for
a caller that might one day exist is deleted, and written again when that caller
turns up and can say what it needs.

`search_products_tool` is removed under this rule. This does not change the
decision in ADR-0002 -- a fixed pipeline, the model never choosing what happens
next -- it drops the one concession that record made to a tool-calling caller
that never arrived.

## Consequences

`search.py` is the DuckDuckGo wrapper and nothing else, and no longer imports
from `langchain_core.tools`; the chains in `extraction.py` are the package's
only LangChain surface. Four tests go with it, which is the point: a test whose
only job is to keep an unused export at 100% reports coverage that means nothing.

The obligation is on anything added here from now on. A new public name needs a
caller in this repository -- the pipeline, `api.py`, `server.py`, `scripts/`, or
the re-exports in `buy_agent/__init__.py`. A name whose only caller is its own
test is the shape this record exists to catch, and the tests will not catch it:
coverage counts such a name as covered, and `tests/test_conventions.py` reads
declarations that exist rather than asking whether they should.

Anyone who does want the search as a LangChain tool writes it against
`search_web` where they need it, which is four lines, rather than finding a
wrapper here whose arguments were guessed years earlier.
