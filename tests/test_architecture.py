"""The shape of the package, read off its imports rather than off its prose.

``tests/test_conventions.py`` holds the rules that span a *declaration* -- a table,
a payload, a workflow, a heading -- and the places that have to agree with it.
These are the other half: the rules that span an *import*. Which module may know
about which is the one thing this project says most often and tested least --
``providers.py`` imports nothing from ``config`` (ADR-0029), ``search.py`` is a
DuckDuckGo wrapper and nothing else (ADR-0021), ``buy_agent.mandates`` is the only
module that imports ``ap2`` (ADR-0046), what decides the answer never asks the
model (ADR-0002) -- and every one of them is a sentence in ``CLAUDE.md`` that a
single ``from`` line quietly makes false. Nothing else in either suite can see
that: an import in the wrong direction runs perfectly, passes its own module's
tests, keeps the coverage floor and the mutation score, and shows up only years
later as the reason two things cannot be moved apart.

`ArchUnitPython <https://github.com/LukasNiessen/ArchUnitPython>`_ is the check.
It parses the package with ``ast`` and answers rules about the graph, so a rule
here is written the way the sentence in ``CLAUDE.md`` reads and costs no run,
no model and no network -- and a violation names the file and the line that
broke it (ADR-0047).

Two things are deliberately how these rules are written:

**``TYPE_CHECKING`` imports do not count.** ``CheckOptions`` is given
``ignore_type_checking_imports=True`` throughout, because an import under that
guard is not a dependency -- it never runs, it cannot make a cycle, and it is
precisely how this package already spells "I name this type and do not use this
module". ``providers.py`` names ``AgentConfig`` in its signatures while importing
nothing from ``config`` at runtime, which is the rule ADR-0029 states and the
opposite of what an unguarded reading would report.

**The rules are the ones already written down.** Every test below cites the
record or the convention it is the executable form of. A rule nobody has decided
does not belong here; it belongs in ``CLAUDE.md`` first, and then here.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from archunitpython import (
    CheckOptions,
    assert_passes,
    extract_graph,
    project_files,
    project_layers,
)

#: The package under analysis, as an absolute path: these tests run from wherever
#: pytest was started, and the Saturday mutation run starts them from a copy of
#: the tree under ``mutants/`` (``setup.cfg``), where the only honest answer to
#: "which package" is the one beside this file.
_PACKAGE = str(Path(__file__).resolve().parent.parent / "buy_agent")

#: An import under ``if TYPE_CHECKING:`` is a name, not a dependency -- see the
#: module docstring. Every rule here is checked with these.
_OPTIONS = CheckOptions(ignore_type_checking_imports=True)


#: The module table in ``CLAUDE.md``, read as the stack it implies: the entry
#: points build a config and call a run, the web tier turns a request into one,
#: the agent orchestrates the steps, the steps take values and answer values, the
#: seams are where a model server, a counterparty and the disk are spoken to, the
#: settings are what a run is assembled from, and the domain types are what
#: everything passes around. A module belongs to exactly one of these, and
#: ``test_every_module_of_the_package_is_in_a_layer`` is what keeps that true.
_LAYERS: dict[str, tuple[str, ...]] = {
    "entry points": ("__main__.py", "server.py"),
    "web": ("api.py",),
    "orchestration": ("agent.py",),
    "pipeline": (
        "extraction.py",
        "verification.py",
        "constraints.py",
        "ranking.py",
        "fetch.py",
        "search.py",
    ),
    "paying": ("payment.py", "rails.py", "mandates.py"),
    "model access": ("chat.py", "providers.py", "cache.py"),
    "settings": ("config.py", "logging_setup.py"),
    "domain": ("models.py", "sources.py"),
}

#: Which layer may reach which, and nothing else. Four of these rows are a
#: decision written down somewhere else rather than a tier being below another:
#:
#: * **the pipeline never reads the config.** ``agent.py`` takes it apart and
#:   hands each step values, which is what lets ``rank_products``, ``ground`` and
#:   ``Constraints`` be tested with three arguments and no environment at all.
#: * **the pipeline never pays.** ``BuyAgent.run`` ends at the ranking; paying is
#:   asked for afterwards, by the CLI's prompt or the card's button (ADR-0046),
#:   and a step that could reach it would be a run that could spend money without
#:   being asked to.
#: * **paying never asks the model.** A cart is built from a grounded product and
#:   never from a prompt, which is the whole of "never pay on an unverified
#:   number".
#: * **the model seam knows nothing about products.** ``chat.py``,
#:   ``providers.py`` and ``cache.py`` carry a prompt, a schema and an answer;
#:   what an answer *means* is the extractor's business (ADR-0038).
_MAY_DEPEND_ON: dict[str, tuple[str, ...]] = {
    "entry points": (
        "web",
        "orchestration",
        "pipeline",
        "paying",
        "model access",
        "settings",
        "domain",
    ),
    "web": ("orchestration", "pipeline", "paying", "model access", "settings", "domain"),
    "orchestration": ("pipeline", "model access", "settings", "domain"),
    "pipeline": ("pipeline", "model access", "domain"),
    "paying": ("paying", "domain"),
    "model access": ("model access",),
    "settings": ("pipeline", "paying", "model access", "domain"),
    "domain": (),
}


#: The four modules ``buy_agent/__init__.py`` re-exports from, off the six names
#: ``CLAUDE.md`` says a Python caller gets: ``BuyAgent`` (``agent``),
#: ``AgentConfig`` (``config``), ``Product`` and ``RankedProduct`` (``models``),
#: ``RankingWeights`` and ``rank_products`` (``ranking``). "Anything else is
#: reached by its module" is a rule about what this file *imports*, which is a
#: different thing from what it lists in ``__all__`` -- and the half that costs
#: something.
_RE_EXPORTED: tuple[str, ...] = ("agent.py", "config.py", "models.py", "ranking.py")


#: Every module of the package, off the directory rather than out of a list here.
#: The two helpers below are checked against it, because a filter naming a module
#: that is not there matches nothing, and a rule whose subject matches nothing is
#: a rule that passes -- silently, and for as long as nobody looks. That is the
#: one way a file of architecture tests can be worse than no file at all, and a
#: renamed module is how it would happen.
_MODULES = frozenset(path.name for path in Path(_PACKAGE).glob("*.py"))


def _known(names: tuple[str, ...]) -> tuple[str, ...]:
    """``names``, having checked that each one is a module of this package."""
    missing = sorted(set(names) - _MODULES)
    assert not missing, f"no such module in buy_agent/: {', '.join(missing)}"
    return names


def only(*names: str) -> re.Pattern[str]:
    """A filename pattern matching these modules of the package and no others."""
    return re.compile(rf"^(?:{'|'.join(re.escape(name) for name in _known(names))})$")


def every_module_but(*names: str) -> re.Pattern[str]:
    """A filename pattern matching every module of the package except ``names``.

    "Only ``mandates.py`` imports the SDK" is a rule about everything *else*, and
    the fluent API's filters are an AND of patterns with no way to spell "not
    this one". A negative lookahead is that way, and it keeps the rule readable
    as the sentence it came from: *the modules that are not* ``mandates.py``
    *should not depend on* ``ap2``.
    """
    excluded = "|".join(re.escape(name) for name in _known(names))
    return re.compile(rf"^(?!(?:{excluded})$).+\.py$")


def third_party_imports() -> list[str]:
    """Every distribution the package imports that Python did not come with.

    Read off the graph rather than off ``requirements.txt``, which names
    distributions rather than modules, is under no obligation to agree with them,
    and does not name the optional AP2 stack at all. What this answers is what a
    module claiming to be stdlib-only must not import, and it grows by itself: a
    dependency added tomorrow is in this list the moment something imports it,
    with nobody having to write it down here as well.
    """
    graph = extract_graph(_PACKAGE, options=_OPTIONS)
    imported = {edge.target.split(".")[0] for edge in graph if edge.external}
    return sorted(imported - set(sys.stdlib_module_names) - {"__future__"})


# -- the graph as a whole ------------------------------------------------------


def test_the_package_has_no_import_cycles() -> None:
    """The pipeline is a line -- refine, search, fetch, extract, ground, rank --
    and so is the package under it: every module in the table in ``CLAUDE.md``
    names something the ones below it do not know about.

    A cycle is what makes that untrue without anybody deciding it. It imports
    fine, runs fine, and is noticed the day somebody tries to test one of the two
    modules without the other, or to move either one out. The two places this
    package would grow one are the tables and the config they are read from --
    ``providers`` and ``rails`` are given an ``AgentConfig`` and ``config``
    resolves its defaults off their rows -- and both are already written the way
    that avoids it: the tables name the type under ``TYPE_CHECKING`` and the
    dependency runs one way at runtime (ADR-0029).

    ``__init__.py`` sits outside this the way it sits outside the layers, and for
    a sharper reason: ``from buy_agent import mandates`` -- the deferred import
    ``api``, ``payment`` and ``rails`` each use -- reads as an edge onto the
    package, and the package re-exports ``BuyAgent`` (ADR-0021), so the graph
    grows a loop through the top of the tree that says nothing about import
    order. Importing *any* submodule runs ``__init__.py`` first, before a line of
    the submodule's own body: there is no order in which one of those edges is
    the one that goes round.
    """
    assert_passes(
        project_files(_PACKAGE)
        .with_name(every_module_but("__init__.py"))
        .should()
        .have_no_cycles()
        .because("a cycle is two modules nobody can take apart again"),
        _OPTIONS,
    )


def test_every_module_sits_in_a_layer_and_reaches_only_downward() -> None:
    """The one rule the module table in ``CLAUDE.md`` implies and never states.

    Reading that table top to bottom: the entry points build a config and call a
    run; the web tier turns a request into one; the agent orchestrates the steps;
    the steps take values and answer values; the seams are where a model server, a
    counterparty and the disk are spoken to; and the domain types are what all of
    them pass around. Each tier may know about the ones under it, and ``_LAYERS``
    and ``_MAY_DEPEND_ON`` say which those are.
    """
    architecture = project_layers(_PACKAGE)
    for layer, modules in _LAYERS.items():
        for module in modules:
            architecture = architecture.layer(layer).defined_by(f"*/{module}")
    for layer, allowed in _MAY_DEPEND_ON.items():
        architecture = architecture.where_layer(layer).may_only_depend_on_layers(*allowed)

    assert_passes(architecture, _OPTIONS)


def test_every_module_of_the_package_is_in_a_layer() -> None:
    """An edge to or from a file in no layer is skipped, so a module left out of
    ``_LAYERS`` is exempt from the rule above -- silently, and while the test that
    should have caught it goes on passing. Placing a new module is therefore part
    of adding one, and this is what says so.

    ``__init__.py`` is the one exemption, and it is deliberate. It is the
    re-export surface (ADR-0021), and ``from buy_agent import mandates`` -- the
    deferred import ``api``, ``payment`` and ``rails`` each use -- reads as an edge
    onto the *package* rather than onto the module, which would put an arrow from
    three different tiers onto the top of the stack and say nothing true about any
    of them. It is not left unchecked, though:
    ``test_the_re_export_surface_imports_only_what_it_re_exports`` is the rule it
    gets instead.

    A module named in *two* layers is the same silence the other way about, and
    the set that would catch a module named in none swallows it: it is placed,
    twice, and what it may reach is then the union of both rows -- which is a
    permission nobody wrote down and one no violation would ever point at.
    """
    placed = [module for modules in _LAYERS.values() for module in modules]
    named = {layer for allowed in _MAY_DEPEND_ON.values() for layer in allowed}

    assert len(placed) == len(set(placed)), "a module in two layers may reach what either may"
    assert set(placed) | {"__init__.py"} == _MODULES
    assert set(_MAY_DEPEND_ON) == set(_LAYERS), "a layer with no row is a layer with no rule"
    assert named <= set(_LAYERS), "a row may only allow layers that exist"


def test_the_re_export_surface_imports_only_what_it_re_exports() -> None:
    """``buy_agent/__init__.py`` re-exports "the small surface a Python caller
    needs -- ``BuyAgent``, ``AgentConfig``, ``Product``, ``RankedProduct``,
    ``RankingWeights``, ``rank_products``; anything else is reached by its
    module". Those six names come from four modules, and this is the rule that
    the file imports those four and nothing besides.

    It is the one file the two rules above let off, so it is the one file that
    needs a rule of its own. Every other module pays for an import in the layer
    it is placed in; this one is in no layer and outside the cycle check, because
    ``from buy_agent import mandates`` -- the deferred import ``api``,
    ``payment`` and ``rails`` each use -- reads as an edge onto the package.

    That exemption is exactly why the imports here are not free. Importing *any*
    submodule runs this file first, before a line of the submodule's own body, so
    a ``from`` line added here is paid by every caller of every module and by
    each of those deferred imports -- and paid at the wrong moment. A single
    ``from buy_agent.payment import pay_for`` would put the AP2 stack behind
    ``import buy_agent``, which is the import a checkout without the optional SDK
    makes work (ADR-0046); one naming ``server`` would put a socket module behind
    ``python -m buy_agent``. Neither shows up as a cycle, neither breaks a layer,
    and both are a line long.
    """
    assert_passes(
        project_files(_PACKAGE)
        .with_name(only("__init__.py"))
        .should_not()
        .depend_on_files()
        .with_name(every_module_but("__init__.py", *_RE_EXPORTED))
        .because("what the package imports is what importing anything of it costs"),
        _OPTIONS,
    )


def test_the_package_imports_none_of_the_trees_that_import_it() -> None:
    """``tests/``, ``integration/``, ``benchmark/``, ``demo/`` and ``scripts/``
    all import ``buy_agent``, and nothing there is a dependency of it.

    Each is one import away from being one, and each of those imports would be
    invisible: a corpus borrowed from ``benchmark/`` (which ``integration/`` reads
    back, ADR-0036), a fake borrowed from ``demo/``, a helper borrowed from
    ``tests/``. Every one of them runs green here and none of them is in the
    release archive or the image (``.dockerignore``), so the first report is an
    ``ImportError`` from a container.
    """
    assert_passes(
        project_files(_PACKAGE)
        .should_not()
        .depend_on_external_modules()
        .matching("*tests*")
        .matching("*integration*")
        .matching("*benchmark*")
        .matching("*demo*")
        .matching("*scripts*")
        .because("what ships is the package; those five are what is left behind"),
        _OPTIONS,
    )


# -- one seam, one module ------------------------------------------------------


def test_only_mandates_imports_the_ap2_sdk() -> None:
    """ADR-0046: ``buy_agent.mandates`` is the AP2 seam and the only module that
    imports ``ap2``, which is why the import is deferred into the functions that
    need it and why a checkout without the optional SDK still has a working
    ``--help``. A second module importing it is a second module that stops
    importing when it is not installed -- and the SDK is installed with
    ``--no-deps`` from somebody else's git repository, so "not installed" is the
    ordinary case rather than the strange one.

    ``tests/test_conventions.py`` asserts the ``ap2`` half of this against the
    source text, line by line. This one asserts it against the graph, which is
    where the *rest* of the deferred stack shows up: ``mandates.py`` reaches
    ``jwcrypto`` for a key and ``cryptography`` for the curve, and both are as
    optional as the SDK that brings them -- an ``importlib.import_module`` naming
    any of the three counts here too.
    """
    assert_passes(
        project_files(_PACKAGE)
        .with_name(every_module_but("mandates.py"))
        .should_not()
        .depend_on_external_modules()
        .matching("ap2*")
        .matching("jwcrypto*")
        .matching("cryptography*")
        .because("an optional dependency imported anywhere else is not optional"),
        _OPTIONS,
    )


def test_only_providers_imports_a_model_client() -> None:
    """ADR-0028 and ADR-0029: everything that differs between Ollama and vLLM is
    one row in ``providers.PROVIDERS``, and the two clients are the difference at
    its widest. A module that imports ``ollama`` or ``openai`` for itself has
    started a second table -- the one the ``if provider == ...`` this project does
    not have would be written against.

    It is also what the suite's fakes rest on: both clients are patched where
    ``buy_agent.providers`` imported them, so a third import elsewhere is a real
    client in a test that thinks it has a fake.
    """
    assert_passes(
        project_files(_PACKAGE)
        .with_name(every_module_but("providers.py"))
        .should_not()
        .depend_on_external_modules()
        .matching("ollama*")
        .matching("openai*")
        .because("a model server is one row in one table, reached one way"),
        _OPTIONS,
    )


def test_only_search_imports_the_search_backend() -> None:
    """ADR-0021: ``search.py`` is a DuckDuckGo wrapper and nothing else. The
    fan-out over named sources lives in ``agent.py`` deliberately (ADR-0027), so
    that patching ``buy_agent.agent.search_web`` is the whole of "this suite does
    not touch the network" -- a second module reaching ``ddgs`` is a second call
    site nobody patched and a test that silently asks the real thing.
    """
    assert_passes(
        project_files(_PACKAGE)
        .with_name(every_module_but("search.py"))
        .should_not()
        .depend_on_external_modules()
        .matching("ddgs*")
        .because("one search backend, one wrapper, one thing to patch"),
        _OPTIONS,
    )


def test_only_fetch_imports_the_html_parser() -> None:
    """Reading a page is ``fetch.py``'s whole job: stream it to a ceiling, keep
    the lines that quote a figure or pass judgement, and tally how the rest
    failed. A second module parsing HTML is a second idea of what a page says --
    and extraction and verification have to be given *the same text* or the
    grounding check rejects everything it is shown (ADR-0006).
    """
    assert_passes(
        project_files(_PACKAGE)
        .with_name(every_module_but("fetch.py"))
        .should_not()
        .depend_on_external_modules()
        .matching("lxml*")
        .because("one reading of a page, shared by the extractor and the check"),
        _OPTIONS,
    )


def test_only_the_three_modules_that_speak_to_somebody_import_httpx() -> None:
    """``fetch.py`` reads pages, ``providers.py`` asks a model server what it is
    serving, ``rails.py`` presents an authorisation to a counterparty. Those are
    the three things this project talks to over HTTP, each of them named in a
    table or a docstring as the module that does it, and each patched in the
    suite at the module that imported the transport.

    A fourth is a request nobody knows about from a module nobody thought made
    any -- which is the one failure a stdlib server, a faked search and a faked
    fetch were all arranged to make impossible.
    """
    assert_passes(
        project_files(_PACKAGE)
        .with_name(every_module_but("fetch.py", "providers.py", "rails.py"))
        .should_not()
        .depend_on_external_modules()
        .matching("httpx*")
        .because("three modules reach the network, and the suite patches all three"),
        _OPTIONS,
    )


def test_the_server_is_stdlib_only() -> None:
    """ADR-0010: the dependency list is already the interesting part of this
    project, and a run that takes a minute and serves one person does not need a
    framework under it. ``server.py`` is a ``ThreadingHTTPServer``, a router and a
    handful of headers, and the day it imports a web framework is the day that
    decision is reversed without a record superseding it.

    What it must not import is read off the graph rather than listed here, so a
    dependency added tomorrow is covered by this rule the moment anything imports
    it.
    """
    assert_passes(
        project_files(_PACKAGE)
        .with_name(only("server.py"))
        .should_not()
        .depend_on_external_modules()
        .matching(re.compile(rf"^(?:{'|'.join(third_party_imports())})\b"))
        .because("no framework between the request and the run"),
        _OPTIONS,
    )


def test_only_the_entry_points_parse_a_command_line() -> None:
    """``argparse`` belongs to the two modules that are handed an ``argv``:
    ``__main__.build_parser`` for the run and ``server.build_parser`` for the
    server it is served from. Everything below them is given values.

    ``api.py`` is the module this is really about. "The CLI and the API are two
    ways of filling in the same ``AgentConfig``", and the difference between them
    is precisely what ``argparse`` cannot express: over the wire "unset" is
    spelled by a blank, so ``parse_options`` reads a missing key and an empty
    string alike (ADR-0012), while on a command line it is spelled by leaving the
    flag off -- which is why ``--source`` naming nothing is a usage error and an
    empty ``sources`` field is the whole web (ADR-0027). A web tier reaching for a
    parser would be a third set of defaults -- and one the convention test that
    holds those two doors to the same ranges would read straight past.

    A parser anywhere further down is worse than a duplicate: ``argparse``
    answers a bad value by writing to stderr and exiting the process, which is
    not a thing a step of a pipeline, a table or a seam may do to a run.
    """
    assert_passes(
        project_files(_PACKAGE)
        .with_name(every_module_but("__main__.py", "server.py"))
        .should_not()
        .depend_on_external_modules()
        .matching("argparse*")
        .because("an argv is the entry points' to read; everything else is given values"),
        _OPTIONS,
    )


# -- what each module is allowed to know ---------------------------------------


def test_the_tables_know_nothing_about_the_config_they_are_read_from() -> None:
    """``providers.py`` "imports nothing from ``config``; the dependency runs the
    other way" (ADR-0029), and ``rails.py`` is that same table for counterparties
    (ADR-0046). Both are handed an ``AgentConfig`` and name it under
    ``TYPE_CHECKING``, so the arrow points one way at runtime: ``config`` reads
    the rows, the rows read nothing.

    Turned around, it is a cycle -- and one nothing would notice, since Python
    resolves it whichever module is imported first and fails only for whoever
    imports the other one first.
    """
    assert_passes(
        project_files(_PACKAGE)
        .with_name(only("providers.py", "rails.py"))
        .should_not()
        .depend_on_files()
        .with_name(only("config.py"))
        .because("config resolves its defaults off the rows, not the other way about"),
        _OPTIONS,
    )


def test_search_is_a_wrapper_over_one_library_and_nothing_else() -> None:
    """ADR-0021, from the other side: ``search.py`` carries no export the pipeline
    does not use, and it knows about no other module of this package. It is given
    a query and answers ``SearchResult``s; what a source is (``sources.py``), what
    a page says (``fetch.py``) and what any of it means (``extraction.py``) are
    all somebody else's business, which is what makes replacing the backend one
    file's worth of work.
    """
    assert_passes(
        project_files(_PACKAGE)
        .with_name(only("search.py"))
        .should_not()
        .depend_on_files()
        .in_path("*.py")
        .because("the search backend is replaceable exactly as long as it is alone"),
        _OPTIONS,
    )


def test_sources_decides_what_a_source_is_and_does_no_io() -> None:
    """``CLAUDE.md``, on the module: *sources.py does no I/O -- it decides what a
    source is, and agent.py does the searching, which is also what keeps search.py
    a DuckDuckGo wrapper.* Both halves of that are checkable: no page fetcher, no
    search backend, no HTTP client, and no module of this package either. What is
    left is a domain, a term, a ``site:`` query and ``covers()``.
    """
    assert_passes(
        project_files(_PACKAGE)
        .with_name(only("sources.py"))
        .should_not()
        .depend_on_files()
        .in_path("*.py")
        .because("what a source is has to be decidable without asking the web"),
        _OPTIONS,
    )
    assert_passes(
        project_files(_PACKAGE)
        .with_name(only("sources.py"))
        .should_not()
        .depend_on_external_modules()
        .matching("httpx*")
        .matching("ddgs*")
        .matching("lxml*")
        .because("deciding is not fetching"),
        _OPTIONS,
    )


def test_the_ap2_seam_knows_nothing_about_this_package() -> None:
    """The other half of ADR-0046's seam. ``test_only_mandates_imports_the_ap2_sdk``
    says the SDK stops here; this says the package does too, so ``mandates.py``
    is a leaf of the graph the way ``search.py`` and ``sources.py`` are.

    That is what makes it a translation rather than a layer of the pipeline: it
    is given the amounts, the merchant and the key, and answers a signed chain
    and a verdict on one. What it must not do is reach back up for a ``Product``,
    a ``Cart`` or a rail's row -- "everything above this line deals in carts and
    receipts; everything AP2 calls a mandate, a disclosure, an SD-JWT or a
    ``vct`` stops here", and a seam that knew both vocabularies would be the one
    place a change to either is felt.

    It is also what keeps the optional dependency optional in practice.
    ``payment.py`` and ``rails.py`` reach *down* to it through the deferred
    ``from buy_agent import mandates``, so an import back would be a cycle
    through the AP2 stack -- resolved differently depending on which module a
    checkout imports first, and only on the checkouts that installed the SDK at
    all.
    """
    assert_passes(
        project_files(_PACKAGE)
        .with_name(only("mandates.py"))
        .should_not()
        .depend_on_files()
        .in_path("*.py")
        .because("the seam translates between two vocabularies and speaks neither back"),
        _OPTIONS,
    )


def test_the_web_tier_is_split_at_the_payload_and_the_api_speaks_no_http() -> None:
    """The module table: ``api.py`` is "request options in, ranked products out --
    the web-facing half worth testing", and ``server.py`` is "a stdlib HTTP
    server". The split is the reason the first half *is* testable: options arrive
    as a mapping and answers leave as payloads, so every one of its rules --
    ``parse_options``, ``_STATUS``, ``PAY_STATUS``, ``results_payload`` -- is
    asserted by calling a function, and the socket, the status line, the worker
    thread and the event stream stay on the other side of it.

    So the sockets, the threads and the queue are ``server.py``'s, along with
    ``_CONTENT_TYPES``, which spells out what ``ng build`` emits rather than
    asking ``mimetypes`` (ADR-0020). An ``api.py`` that imported any of them
    would have started answering requests instead of shaping answers -- and the
    first thing it would take with it is the streaming half, which is exactly the
    part that has to be able to fail without a status line to say so.
    """
    assert_passes(
        project_files(_PACKAGE)
        .with_name(only("api.py"))
        .should_not()
        .depend_on_external_modules()
        .matching("http")
        .matching("http.*")
        .matching("socket")
        .matching("socketserver*")
        .matching("ssl")
        .matching("mimetypes*")
        .matching("threading*")
        .matching("queue*")
        .matching("urllib.request*")
        .because("options in and payloads out is what makes the web tier testable"),
        _OPTIONS,
    )


def test_the_steps_take_values_and_answer_values() -> None:
    """The pipeline and the domain are the part of this package that is a
    function: given the same arguments they answer the same thing, and the
    arguments are all there is.

    "The pipeline never reads the config" is the layer rule that says half of it,
    "which is what lets ``rank_products``, ``ground`` and ``Constraints`` be
    tested with three arguments and no environment at all". This is the other
    half, which no layer can state, because what a step would reach for is not a
    module of this package but the machine underneath it: an environment
    variable, a file, a clock or a random number. A settings module is a place
    ``$BUY_AGENT_CACHE_DIR`` may be read (``cache.py``, ADR-0040) and a step is
    not, whatever the variable is called.

    The clock is the half worth spelling out, because it is the one that would
    look harmless. ``cache.py`` remembers what the model said and hands it back
    within the TTL (ADR-0044), and what makes that sound is that asking twice is
    the same question: a run at ``temperature`` above 0 is deliberately never
    remembered, "it has no one answer to remember". A step whose answer moved
    with the time of day, or with a coin, would be remembered wrong and stay
    wrong for the life of the entry -- and it would be the kind of test failure
    that arrives once a week and passes on a re-run.
    """
    steps = only(*_LAYERS["pipeline"], *_LAYERS["domain"])

    assert_passes(
        project_files(_PACKAGE)
        .with_name(steps)
        .should_not()
        .depend_on_external_modules()
        .matching("os*")
        .matching("pathlib*")
        .matching("tempfile*")
        .matching("shutil*")
        .because("a step is given its settings; it does not go and look them up"),
        _OPTIONS,
    )
    assert_passes(
        project_files(_PACKAGE)
        .with_name(steps)
        .should_not()
        .depend_on_external_modules()
        .matching("time*")
        .matching("datetime*")
        .matching("random*")
        .matching("secrets*")
        .matching("uuid*")
        .because("an answer that moves on its own is one the cache would keep wrongly"),
        _OPTIONS,
    )


def test_the_steps_do_not_chain_themselves() -> None:
    """The order of the pipeline lives in ``BuyAgent.run`` and nowhere else.

    "That order is load-bearing in three joints" -- ``clean_products`` before
    ``ground`` so a name still wearing its publisher suffix is not failed by the
    coverage check, ``ground`` before ``deduplicate`` so ``_combine`` only merges
    figures the sources back, and the shopper's bounds between ``deduplicate``
    and ``rank_products`` (ADR-0039) -- and a joint that is argued in one place
    is a joint that can be moved. A step that called the next one would settle
    the order where nobody is reading, and it would have to be unpicked before
    any of the three could be argued again.

    ``verification.py`` importing ``extraction.py`` is the one edge inside this
    layer, and it is deliberate: ``GENERIC_WORDS``, ``NAME_TOKENS`` and
    ``SUPERLATIVES`` are shared so that merging and grounding "agree word for
    word on what a name's words are", and a second copy would be two bars where
    the rule wants one. It is a vocabulary and not a call, which is why the rule
    is written as every other step and not as every pair.
    """
    assert_passes(
        project_files(_PACKAGE)
        .with_name(only(*(step for step in _LAYERS["pipeline"] if step != "verification.py")))
        .should_not()
        .depend_on_files()
        .with_name(only(*_LAYERS["pipeline"]))
        .because("the order of the steps is the orchestrator's to know"),
        _OPTIONS,
    )


def test_nothing_that_decides_the_answer_asks_the_model() -> None:
    """ADR-0002 and ADR-0007, as an import rule: "anything that decides the
    answer -- filtering, scoring, ordering -- belongs in Python, where it is
    testable."

    ``ranking.py`` scores, ``constraints.py`` applies the shopper's bounds,
    ``verification.py`` drops what the sources do not back, and ``models.py``
    holds the types all three answer with. None of them may reach the model seam,
    the page fetcher or the search backend: a judgement that asked the model
    would be one no test could pin down, and a ranking that fetched a page would
    be a scoring pass that could fail.

    ``verification.py`` importing ``extraction.py`` is deliberately still allowed
    -- ``GENERIC_WORDS``, ``NAME_TOKENS`` and ``SUPERLATIVES`` are shared on
    purpose, so that merging and grounding agree word for word on what a name is.
    What it may not do is import what ``extraction.py`` asks.
    """
    assert_passes(
        project_files(_PACKAGE)
        .with_name(only("ranking.py", "constraints.py", "verification.py", "models.py"))
        .should_not()
        .depend_on_files()
        .with_name(only("chat.py", "providers.py", "cache.py", "fetch.py", "search.py"))
        .because("what decides the answer is ordinary Python, and stays testable"),
        _OPTIONS,
    )
