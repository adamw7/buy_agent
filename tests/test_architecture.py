"""The shape of the package, read off its imports rather than off its prose."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from archunitpython import (
    CheckOptions,
    assert_passes,
    extract_graph,
    project_files,
    project_layers,
)

from tests.conftest import SOURCE_ROOT

#: The package under analysis, as an absolute path: these tests run from wherever pytest
#: was started, and the Saturday mutation run starts them from a copy of the tree under
#: ``mutants/`` (``setup.cfg``) whose every module opens with an import of the tester's
#: own trampoline.
_PACKAGE = str(SOURCE_ROOT / "buy_agent")

#: An import under ``if TYPE_CHECKING:`` is a name, not a dependency -- see the
#: module docstring. Every rule here is checked with these.
_OPTIONS = CheckOptions(ignore_type_checking_imports=True)


#: The module table in ``CLAUDE.md``, read as the stack it implies: the entry points build
#: a config and call a run, the web tier turns a request into one, the agent orchestrates
#: the steps, the steps take values and answer values, the seams are where a model server,
#: a search backend, a counterparty and the disk are spoken to, the settings are what a
#: run is assembled from, and the domain types are what everything passes around.
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
    ),
    "paying": ("payment.py", "rails.py", "mandates.py"),
    # ``search.py`` is here and not among the steps: it is the table a backend is one
    # row of (ADR-0057), so it reads its rows' addresses and keys off the environment,
    # which is the one thing a step may never do.
    "seams": ("chat.py", "providers.py", "cache.py", "search.py"),
    "settings": ("config.py", "logging_setup.py"),
    "domain": ("models.py", "money.py", "sources.py"),
}

#: Which layer may reach which, and nothing else.
_MAY_DEPEND_ON: dict[str, tuple[str, ...]] = {
    "entry points": (
        "web",
        "orchestration",
        "pipeline",
        "paying",
        "seams",
        "settings",
        "domain",
    ),
    "web": ("orchestration", "pipeline", "paying", "seams", "settings", "domain"),
    "orchestration": ("pipeline", "seams", "settings", "domain"),
    "pipeline": ("pipeline", "seams", "domain"),
    "paying": ("paying", "domain"),
    "seams": ("seams",),
    "settings": ("pipeline", "paying", "seams", "domain"),
    "domain": (),
}


#: The four modules ``buy_agent/__init__.py`` re-exports from, off the six names
#: ``CLAUDE.md`` says a Python caller gets: ``BuyAgent`` (``agent``), ``AgentConfig``
#: (``config``), ``Product`` and ``RankedProduct`` (``models``), ``RankingWeights`` and
#: ``rank_products`` (``ranking``).
_RE_EXPORTED: tuple[str, ...] = ("agent.py", "config.py", "models.py", "ranking.py")


#: Every module of the package, off the directory rather than out of a list here.
_MODULES = frozenset(path.name for path in Path(_PACKAGE).glob("*.py"))

#: Every module of the package, for a rule that lets none of them off.
EVERY_MODULE = re.compile(r".+\.py$")


def _known(names: tuple[str, ...]) -> tuple[str, ...]:
    """``names``, having checked that each one is a module of this package."""
    missing = sorted(set(names) - _MODULES)
    assert not missing, f"no such module in buy_agent/: {', '.join(missing)}"
    return names


def only(*names: str) -> re.Pattern[str]:
    """A filename pattern matching these modules of the package and no others."""
    return re.compile(rf"^(?:{'|'.join(re.escape(name) for name in _known(names))})$")


def every_module_but(*names: str) -> re.Pattern[str]:
    """A filename pattern matching every module of the package except ``names``."""
    excluded = "|".join(re.escape(name) for name in _known(names))
    return re.compile(rf"^(?!(?:{excluded})$).+\.py$")


def third_party_imports() -> list[str]:
    """Every distribution the package imports that Python did not come with."""
    graph = extract_graph(_PACKAGE, options=_OPTIONS)
    imported = {edge.target.split(".")[0] for edge in graph if edge.external}
    return sorted(imported - set(sys.stdlib_module_names) - {"__future__"})


def imports_none_of(within: re.Pattern[str], *matching: Any, because: str) -> None:
    """Assert that the modules ``within`` names import none of these distributions."""
    rule = (
        project_files(_PACKAGE).with_name(within).should_not().depend_on_external_modules()
    )
    for pattern in matching:
        rule = rule.matching(pattern)
    assert_passes(rule.because(because), _OPTIONS)


def knows_nothing_of(
    within: re.Pattern[str], target: re.Pattern[str] | None = None, *, because: str
) -> None:
    """Assert that ``within`` imports no module of ``target`` -- or, given none, no module
    of this package at all."""
    rule = project_files(_PACKAGE).with_name(within).should_not().depend_on_files()
    rule = rule.in_path("*.py") if target is None else rule.with_name(target)
    assert_passes(rule.because(because), _OPTIONS)


# -- the graph as a whole ------------------------------------------------------


def test_the_package_has_no_import_cycles() -> None:
    """The pipeline is a line -- refine, search, fetch, extract, ground, rank -- and so is
    the package under it: every module in the table in ``CLAUDE.md`` names something
    the ones below it do not know about."""
    assert_passes(
        project_files(_PACKAGE)
        .with_name(every_module_but("__init__.py"))
        .should()
        .have_no_cycles()
        .because("a cycle is two modules nobody can take apart again"),
        _OPTIONS,
    )


def test_every_module_sits_in_a_layer_and_reaches_only_downward() -> None:
    """The one rule the module table in ``CLAUDE.md`` implies and never states."""
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
    should have caught it goes on passing."""
    placed = [module for modules in _LAYERS.values() for module in modules]
    named = {layer for allowed in _MAY_DEPEND_ON.values() for layer in allowed}

    assert len(placed) == len(set(placed)), "a module in two layers may reach what either may"
    assert set(placed) | {"__init__.py"} == _MODULES
    assert set(_MAY_DEPEND_ON) == set(_LAYERS), "a layer with no row is a layer with no rule"
    assert named <= set(_LAYERS), "a row may only allow layers that exist"


def test_the_re_export_surface_imports_only_what_it_re_exports() -> None:
    """``buy_agent/__init__.py`` re-exports "the small surface a Python caller needs --
    ``BuyAgent``, ``AgentConfig``, ``Product``, ``RankedProduct``, ``RankingWeights``,
    ``rank_products``; anything else is reached by its module"."""
    knows_nothing_of(
        only("__init__.py"),
        every_module_but("__init__.py", *_RE_EXPORTED),
        because="what the package imports is what importing anything of it costs",
    )


def test_the_package_imports_none_of_the_trees_that_import_it() -> None:
    """``tests/``, ``integration/``, ``benchmark/``, ``demo/`` and ``scripts/`` all import
    ``buy_agent``, and nothing there is a dependency of it."""
    imports_none_of(
        EVERY_MODULE,
        "*tests*",
        "*integration*",
        "*benchmark*",
        "*demo*",
        "*scripts*",
        because="what ships is the package; those five are what is left behind",
    )


def test_the_package_starts_no_process() -> None:
    """Every way this project runs puts the package inside somebody else's process:
    ``python -m buy_agent``, a ``ThreadingHTTPServer`` serving one person, a container
    whose ``ENTRYPOINT`` is the interpreter, and a caller who imported ``BuyAgent`` for
    the six names ``__init__`` re-exports."""
    imports_none_of(
        EVERY_MODULE,
        "subprocess*",
        "multiprocessing*",
        "webbrowser*",
        because="the package is a guest in whatever process runs it",
    )

# -- one seam, one module ------------------------------------------------------


def test_only_mandates_imports_the_ap2_sdk() -> None:
    """ADR-0046: ``buy_agent.mandates`` is the AP2 seam and the only module that imports
    ``ap2``, which is why the import is deferred into the functions that need it and
    why a checkout without the optional SDK still has a working ``--help``."""
    imports_none_of(
        every_module_but("mandates.py"),
        "ap2*",
        "jwcrypto*",
        "cryptography*",
        because="an optional dependency imported anywhere else is not optional",
    )


def test_only_providers_imports_a_model_client() -> None:
    """ADR-0028 and ADR-0029: everything that differs between Ollama and vLLM is one row
    in ``providers.PROVIDERS``, and the two clients are the difference at its widest."""
    imports_none_of(
        every_module_but("providers.py"),
        "ollama*",
        "openai*",
        because="a model server is one row in one table, reached one way",
    )


def test_only_search_imports_the_search_backend() -> None:
    """ADR-0021 and ADR-0057: ``search.py`` is the table a search backend is one row of,
    and the one library any of them is reached through is that table's."""
    imports_none_of(
        every_module_but("search.py"),
        "ddgs*",
        because="a search backend is one row in one table, reached one way",
    )


def test_only_fetch_imports_the_html_parser() -> None:
    """Reading a page is ``fetch.py``'s whole job: stream it to a ceiling, keep the lines
    that quote a figure or pass judgement, and tally how the rest failed."""
    imports_none_of(
        every_module_but("fetch.py"),
        "lxml*",
        because="one reading of a page, shared by the extractor and the check",
    )


def test_only_the_four_modules_that_speak_to_somebody_import_httpx() -> None:
    """``fetch.py`` reads pages, ``providers.py`` asks a model server what it is serving,
    ``rails.py`` presents an authorisation to a counterparty, and ``search.py`` asks
    whichever backend is not reached through a library of its own (ADR-0057)."""
    imports_none_of(
        every_module_but("fetch.py", "providers.py", "rails.py", "search.py"),
        "httpx*",
        because="four modules reach the network, and the suite patches all four",
    )


def test_the_standard_library_s_network_is_the_server_s_alone() -> None:
    """The rule above says the three modules that reach out reach out through ``httpx``,
    which is what makes all three patchable in one line each."""
    imports_none_of(
        every_module_but("server.py"),
        "socket*",
        "ssl*",
        "http.client*",
        "http.server*",
        "urllib.request*",
        "urllib.error*",
        "ftplib*",
        "smtplib*",
        "xmlrpc*",
        because="one module listens; the three that call out are the patched three",
    )


def test_the_server_is_stdlib_only() -> None:
    """ADR-0010: the dependency list is already the interesting part of this project, and
    a run that takes a minute and serves one person does not need a framework under it."""
    imports_none_of(
        only("server.py"),
        re.compile(rf"^(?:{'|'.join(third_party_imports())})\b"),
        because="no framework between the request and the run",
    )


def test_only_the_entry_points_parse_a_command_line() -> None:
    """``argparse`` belongs to the two modules that are handed an ``argv``:
    ``__main__.build_parser`` for the run and ``server.build_parser`` for the server it
    is served from."""
    imports_none_of(
        every_module_but("__main__.py", "server.py"),
        "argparse*",
        because="an argv is the entry points' to read; everything else is given values",
    )


# -- what each module is allowed to know ---------------------------------------


def test_the_tables_know_nothing_about_the_config_they_are_read_from() -> None:
    """``providers.py`` "imports nothing from ``config``; the dependency runs the other
    way" (ADR-0029); ``rails.py`` is that same table for counterparties (ADR-0046) and
    ``search.py`` for search backends (ADR-0057)."""
    knows_nothing_of(
        only("providers.py", "rails.py", "search.py"),
        only("config.py"),
        because="config resolves its defaults off the rows, not the other way about",
    )


def test_search_knows_nothing_of_the_package_it_is_asked_from() -> None:
    """ADR-0021 and ADR-0057, from the other side: the table carries no export the
    pipeline does not use, and it knows about no other module of this package --
    ``config.py`` reads the rows, exactly as it reads the providers' and the rails'."""
    knows_nothing_of(
        only("search.py"),
        because="a backend is replaceable exactly as long as the table is alone",
    )


def test_sources_decides_what_a_source_is_and_does_no_io() -> None:
    """``CLAUDE.md``, on the module: *sources.py does no I/O -- it decides what a source
    is, and agent.py does the searching, which is also what keeps search.py a
    DuckDuckGo wrapper.* Both halves of that are checkable: no page fetcher, no search
    backend, no HTTP client, and no module of this package either."""
    knows_nothing_of(
        only("sources.py"),
        because="what a source is has to be decidable without asking the web",
    )
    imports_none_of(
        only("sources.py"),
        "httpx*",
        "ddgs*",
        "lxml*",
        because="deciding is not fetching",
    )


def test_the_ap2_seam_knows_nothing_about_this_package() -> None:
    """The other half of ADR-0046's seam."""
    knows_nothing_of(
        only("mandates.py"),
        because="the seam translates between two vocabularies and speaks neither back",
    )


def test_the_web_tier_is_split_at_the_payload_and_the_api_speaks_no_http() -> None:
    """The module table: ``api.py`` is "request options in, ranked products out -- the
    web-facing half worth testing", and ``server.py`` is "a stdlib HTTP server"."""
    imports_none_of(
        only("api.py"),
        "http",
        "http.*",
        "socket",
        "socketserver*",
        "ssl",
        "mimetypes*",
        "threading*",
        "queue*",
        "urllib.request*",
        because="options in and payloads out is what makes the web tier testable",
    )


def test_the_steps_take_values_and_answer_values() -> None:
    """The pipeline and the domain are the part of this package that is a function: given
    the same arguments they answer the same thing, and the arguments are all there is."""
    steps = only(*_LAYERS["pipeline"], *_LAYERS["domain"])

    imports_none_of(
        steps,
        "os*",
        "pathlib*",
        "tempfile*",
        "shutil*",
        because="a step is given its settings; it does not go and look them up",
    )
    imports_none_of(
        steps,
        "time*",
        "datetime*",
        "random*",
        "secrets*",
        "uuid*",
        because="an answer that moves on its own is one the cache would keep wrongly",
    )


def test_the_steps_do_not_chain_themselves() -> None:
    """The order of the pipeline lives in ``BuyAgent.run`` and nowhere else."""
    knows_nothing_of(
        only(*(step for step in _LAYERS["pipeline"] if step != "verification.py")),
        only(*_LAYERS["pipeline"]),
        because="the order of the steps is the orchestrator's to know",
    )


def test_nothing_that_decides_the_answer_asks_the_model() -> None:
    """ADR-0002 and ADR-0007, as an import rule: "anything that decides the answer --
    filtering, scoring, ordering -- belongs in Python, where it is testable."
    """
    knows_nothing_of(
        only("ranking.py", "constraints.py", "verification.py", "models.py"),
        only("chat.py", "providers.py", "cache.py", "fetch.py", "search.py"),
        because="what decides the answer is ordinary Python, and stays testable",
    )
