"""The shape of the package, read off its imports rather than off its prose."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from archunitpython import (
    CheckOptions,
    assert_passes,
    extract_graph,
    project_files,
    project_layers,
)

from tests.conftest import SOURCE_ROOT

if TYPE_CHECKING:
    from collections.abc import Iterator

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
#: a search backend, a browser and the disk are spoken to, the settings are what a
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
        "alerts.py",
    ),
    "paying": ("payment.py", "rails.py", "mandates.py"),
    # ``search.py`` is here and not among the steps: it is the table a backend is one
    # row of (ADR-0057), so it reads its rows' addresses and keys off the environment,
    # which is the one thing a step may never do.
    "seams": (
        "chat.py",
        "providers.py",
        "cache.py",
        "search.py",
        "journal.py",
        "screenshots.py",
    ),
    "settings": ("config.py", "logging_setup.py"),
    "domain": ("models.py", "money.py", "sources.py", "bounds.py", "structured.py"),
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
    # The one row that names the domain: ``journal.py`` writes down what a run reported,
    # which is products, and the domain types are the vocabulary every layer already
    # passes around (ADR-0060). The seams still reach nothing above them, and the three
    # tables keep the stricter rule of their own below.
    "seams": ("seams", "domain"),
    "settings": ("pipeline", "paying", "seams", "domain"),
    "domain": (),
}


#: The four modules ``buy_agent/__init__.py`` re-exports from, off the six names
#: ``CLAUDE.md`` says a Python caller gets: ``BuyAgent`` (``agent``), ``AgentConfig``
#: (``config``), ``Product`` and ``RankedProduct`` (``models``), ``RankingWeights`` and
#: ``rank_products`` (``ranking``).
_RE_EXPORTED: tuple[str, ...] = ("agent.py", "config.py", "models.py", "ranking.py")


#: Every ``from buy_agent import <module>`` in the package, as importer and module. The
#: graph draws each onto ``__init__.py`` rather than onto the module it names (ADR-0047),
#: and ``__init__.py`` is in no layer, so every rule in this file skips it: one of these in
#: a step could name any module at all and pass. Listed, each is one somebody placed.
_THROUGH_THE_PACKAGE: frozenset[tuple[str, str]] = frozenset(
    {
        ("__main__.py", "mandates.py"),
        ("__main__.py", "payment.py"),
        ("api.py", "mandates.py"),
        ("payment.py", "mandates.py"),
        ("rails.py", "mandates.py"),
    }
)


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


def layer_of(module: str) -> str:
    """The layer ``_LAYERS`` puts ``module`` in."""
    layers = [layer for layer, modules in _LAYERS.items() if module in modules]
    assert layers, f"{module} is in no layer"
    return layers[0]


def _imports_that_run(node: ast.AST) -> Iterator[ast.ImportFrom]:
    """Every ``from ... import`` in ``node`` but those under ``if TYPE_CHECKING:``, which
    never run and which every rule here skips too (``_OPTIONS``)."""
    if isinstance(node, ast.ImportFrom):
        yield node
    elif isinstance(node, ast.If) and ast.unparse(node.test) == "TYPE_CHECKING":
        for statement in node.orelse:
            yield from _imports_that_run(statement)
    else:
        for child in ast.iter_child_nodes(node):
            yield from _imports_that_run(child)


def reached_through_the_package() -> set[tuple[str, str]]:
    """Every ``(importer, module)`` that a ``from buy_agent import module`` -- or a
    ``from . import module`` -- names, deferred imports included."""
    return {
        (path.name, f"{alias.name}.py")
        for path in Path(_PACKAGE).glob("*.py")
        for node in _imports_that_run(ast.parse(path.read_text(encoding="utf-8")))
        if (node.level, node.module) in {(0, "buy_agent"), (1, None)}
        for alias in node.names
    }


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


def test_every_import_through_the_package_is_placed_and_reaches_only_downward() -> None:
    """The other way an edge goes unchecked. ADR-0047 leaves ``__init__.py`` out of the
    layers because ``from buy_agent import mandates`` "reads as an edge onto the package
    rather than onto the module" -- and an edge onto a file in no layer is skipped by every
    rule in this file, not by the layer rule alone. ``from buy_agent import screenshots``
    in a step would pass all of them. So each such import is a row of
    ``_THROUGH_THE_PACKAGE``, held against the source in both directions, and a row is only
    an edge ``_MAY_DEPEND_ON`` names outright -- not one it lets pass for being inside a
    layer, which is where the rules of their own live. Even that is not all a row answers
    to: ``mandates.py`` may know nothing of the package, though ``paying`` may reach
    ``paying``. Adding one is a decision, and this is where it is made."""
    found = reached_through_the_package()
    unplaced = sorted(found - _THROUGH_THE_PACKAGE)
    gone = sorted(_THROUGH_THE_PACKAGE - found)

    assert not unplaced, (
        "an import through the package is an edge no rule here sees: import from the "
        f"module itself, or place it in _THROUGH_THE_PACKAGE: {unplaced}"
    )
    assert not gone, f"no longer imported that way: {gone}"
    for importer, module in sorted(_THROUGH_THE_PACKAGE):
        source, target = layer_of(importer), layer_of(module)
        assert target in _MAY_DEPEND_ON[source], (
            f"{importer} ({source}) may not reach {module} ({target})"
        )


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
    the six names ``__init__`` re-exports. The one exception is a browser, and it is not
    started through any of these: Playwright launches it for ``screenshots.py``, which
    only the server asks, and which the rule below keeps the only module that can
    (ADR-0065)."""
    imports_none_of(
        EVERY_MODULE,
        "subprocess*",
        "multiprocessing*",
        "webbrowser*",
        because="the package is a guest in whatever process runs it",
    )


def test_nothing_here_awaits_and_the_threads_are_the_four_that_wait() -> None:
    """A run is a minute of waiting on somebody else, and every one of those waits is a
    thread: the server ADR-0010 settled is a ``ThreadingHTTPServer``, and an ``async def``
    anywhere below it would want an event loop under the whole package before anybody
    could await it -- including the CLI, the tests and the Python caller who imported
    ``BuyAgent`` for the six names ``__init__`` re-exports. The four that wait are
    ``fetch.py``, which reads the result pages in a pool, ``providers.py``, whose listing
    asks ``ollama show`` once per tag, ``server.py``, which runs a request in a worker
    thread and routes its log lines by the context that thread began in, and
    ``screenshots.py``, whose browser belongs to the one thread that launched it and is
    handed every picture to take (ADR-0065). Playwright runs a loop of its own under its
    synchronous API, inside that thread, and nothing here awaits it."""
    imports_none_of(
        EVERY_MODULE,
        "asyncio*",
        "anyio*",
        "trio*",
        because="nothing here awaits, so nothing here needs a loop underneath it",
    )
    imports_none_of(
        every_module_but("fetch.py", "providers.py", "server.py", "screenshots.py"),
        "threading*",
        "concurrent*",
        "queue*",
        "contextvars*",
        because="four modules wait on somebody else; the rest are handed the answer",
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


def test_only_screenshots_imports_playwright() -> None:
    """ADR-0065: ``buy_agent.screenshots`` is the browser seam and the only module that
    imports Playwright -- deferred, so a checkout without the optional install still
    imports every module -- and so the only one that can start the browser the rule
    about processes above makes its one exception for."""
    imports_none_of(
        every_module_but("screenshots.py"),
        "playwright*",
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


def test_only_the_cache_puts_a_file_on_disk_the_one_way() -> None:
    """ADR-0060: what the cache and the journal share "is how a file is put there:
    ``cache.write_atomically`` and ``cache.file_for`` are one temporary-file dance and
    one hashed name", which ``journal.py`` imports rather than repeats -- "not a second
    copy of the code that keeps it, which was the half either module could have got
    subtly wrong on its own". A ``tempfile`` or a ``hashlib`` anywhere else is that
    second copy, and the half it would get wrong is the half nothing reads back."""
    imports_none_of(
        every_module_but("cache.py"),
        "tempfile*",
        "hashlib*",
        because="one temporary-file dance and one hashed name, imported rather than repeated",
    )


def test_the_environment_is_read_where_a_setting_is_declared() -> None:
    """Every ``$BUY_AGENT_*`` in ``CLAUDE.md`` is read in one of six places: ``config.py``,
    for the settings a door can fill in too; the three tables, for the address and the key
    each row needs (ADR-0029, ADR-0046, ADR-0057); ``cache.py``, for the directory a run
    may reuse (ADR-0040); and ``mandates.py``, for the key and the open mandate that
    authorise a payment (ADR-0046). Neither door is on that list, and that is the rule:
    "every other CLI flag defaults to the matching ``AgentConfig`` field, so a new setting
    is added in ``config.py`` and picked up rather than repeated" -- a second reading of
    the environment below either door is a setting the other one does not have."""
    imports_none_of(
        every_module_but(
            "cache.py",
            "config.py",
            "mandates.py",
            "providers.py",
            "rails.py",
            "search.py",
        ),
        "os*",
        "dotenv*",
        because="a setting reaches a run as a field, not as a second reading of the machine",
    )


def test_the_two_modules_that_ask_the_web_twice_are_handed_their_clock() -> None:
    """ADR-0053: "The waiting is a value the step is handed, not a module it imports."
    ``search_web`` and ``enrich`` each take a ``wait``, ``BuyAgent`` passes ``time.sleep``,
    and every test passes ``None`` to ask once -- which holds only while neither module can
    reach a clock of its own, and is why a ``Retry-After`` written as a date counts as
    unreadable. ``fetch.py`` is held to this by the rule about the steps as well;
    ``search.py`` is a seam, and this is the rule that holds it."""
    imports_none_of(
        only("fetch.py", "search.py"),
        "time*",
        "datetime*",
        because="a retry that keeps its own clock is a test that waits",
    )


def test_only_money_turns_a_price_into_what_is_charged() -> None:
    """ADR-0054: ``money.py`` holds every currency table, down to "how many of the
    currency's smallest units it comes to (``minor_units``)" -- a ``Decimal`` rounded half
    up, and the whole number a ``Cart`` carries as ``amount`` and a mandate signs.
    ``payment.cart_for`` asks it and translates its ``ValueError``; a ``decimal`` anywhere
    else is a second way of counting the same price's cents, and the one place two
    roundings disagreeing would cost somebody money."""
    imports_none_of(
        every_module_but("money.py"),
        "decimal*",
        "fractions*",
        because="an amount is counted out in one place, the place a currency is added",
    )


def test_the_steps_and_the_orchestrator_declare_no_schema() -> None:
    """``models.py`` is "``ExtractedProduct`` (LLM-facing) vs ``Product`` (domain)", and
    both schemas the model is asked for, ``SearchQuery`` and ``ProductList``, are declared
    there beside the sentinels ADR-0004 asks of them and the ``to_product()`` that turns
    those into ``None``. ``extraction.py`` holds "both prompts, both chains" and binds them
    to those schemas by name. And reading an answer back as its schema is ``chat.py``'s,
    whose ``read_answer`` turns pydantic's ``ValidationError`` into
    ``UnreadableAnswerError`` (ADR-0038). A step that imported pydantic would be declaring
    a schema outside the module whose tests say what one may hold, or catching a failure
    the seam had already translated."""
    imports_none_of(
        only("agent.py", *_LAYERS["pipeline"]),
        "pydantic*",
        because="what the model is asked for is the domain's, and reading it back chat's",
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


def test_a_model_server_and_a_rail_are_reached_through_the_config() -> None:
    """The other side of the rule above. ``AgentConfig.model_server`` "is the *only* place
    a provider name becomes behaviour: no ``if provider == ...`` above the table, no
    module-level wrappers", and ``AgentConfig.rail_used`` is the same for a rail. Below
    the doors -- which list the rows for a picker and refuse a bad ``$BUY_AGENT_PROVIDER``
    or ``$BUY_AGENT_RAIL`` before binding -- the config is the one module that reads
    either table. The orchestrator is handed a chat model and never learns which row built
    it; the layer table lets it reach any seam, and this is the seam it may not."""
    for table in ("providers.py", "rails.py"):
        knows_nothing_of(
            every_module_but(table, "config.py", "__main__.py", "api.py", "server.py"),
            only(table),
            because="a row becomes behaviour in one place, and that place is the config",
        )


def test_the_web_is_asked_from_the_orchestrator_alone() -> None:
    """The suite's network patch points are ``buy_agent.agent.search_web`` and
    ``buy_agent.agent.enrich``, and "``search_web`` is patched only on ``agent``, which is
    why the source fan-out lives there". A step that imported either for itself would be a
    call site no stand-in reaches, and a test that quietly asks the real web. The doors
    and the config read the search table as well: for its picker, for a bad
    ``$BUY_AGENT_BACKEND`` refused before binding, and for the row
    ``AgentConfig.search_backend`` hands the run (ADR-0057)."""
    knows_nothing_of(
        every_module_but(
            "search.py", "agent.py", "config.py", "__main__.py", "api.py", "server.py"
        ),
        only("search.py"),
        because="one call site, on the module the suite patches",
    )
    knows_nothing_of(
        every_module_but("fetch.py", "agent.py"),
        only("fetch.py"),
        because="one call site, on the module the suite patches",
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


def test_the_browser_seam_knows_nothing_about_this_package() -> None:
    """The other half of ADR-0065's: an address in and a JPEG out. What a product is, and
    which page it links to, are decided long before anybody asks for a picture of it --
    which is also why the camera can be handed a stand-in with one method."""
    knows_nothing_of(
        only("screenshots.py"),
        because="a picture of a page, and no idea what the page was found for",
    )


def test_only_the_web_tier_knows_about_pictures() -> None:
    """ADR-0065, from the other side: "The picture is asked for by the card, not taken by
    the run", "so nothing in the pipeline, the run payload, the journal or ``--json``
    changes, the CLI pays nothing". The server owns the camera and ``api.py`` names its
    one failure; nothing else knows a browser exists. The layer table cannot say this --
    the browser is a seam, and the orchestrator and every step may reach a seam."""
    knows_nothing_of(
        every_module_but("screenshots.py", "server.py", "api.py"),
        only("screenshots.py"),
        because="a picture is asked for by the card, not taken by the run",
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


def test_the_server_sends_what_the_api_shaped() -> None:
    """The other half of the split above. ``api.results_payload`` "is the one shaping of a
    run's products (API, ``--json``, Download)", and "the browser decides nothing"
    (ADR-0012) -- nor does the module in front of it. ``server.py`` hands options to
    ``api.py`` and writes back what it is given: it names no step, no product, no price,
    no cart and no run's history. It does read the three tables, to refuse a bad
    ``$BUY_AGENT_PROVIDER``, ``RAIL`` or ``BACKEND`` before binding, which is why
    ``rails.py`` is the one module of the paying layer left off the list."""
    knows_nothing_of(
        only("server.py"),
        only(
            *_LAYERS["pipeline"],
            *_LAYERS["domain"],
            "payment.py",
            "mandates.py",
            "journal.py",
        ),
        because="a payload is shaped where it can be tested without a socket",
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


def test_the_model_seam_knows_nothing_about_products() -> None:
    """ADR-0038, which the layer table can no longer state on its own: the seams may name
    the domain types, ``journal.py`` writing products down (ADR-0060), so the edge the
    model seam is held to is this one. ``chat.py`` is "a prompt, a chain, an answer read
    back as its schema" -- the schema is the caller's, which is what lets a stand-in for
    the model be a class with one ``answer`` method and nothing about shopping in it."""
    knows_nothing_of(
        only("chat.py"),
        because="a prompt and a schema it was handed, and no idea what either is about",
    )


def test_of_the_seams_only_the_journal_names_the_domain() -> None:
    """The layer table opens the seams' row onto the domain for one module: "``journal.py``
    is the first module in the seams layer that names the domain types, because what it
    writes down is products" (ADR-0060). A row cannot say *one*, so this does. Every other
    seam carries what its caller hands it -- messages and a schema, a page's text, a query,
    an address -- and a ``Product`` reaching one is shopping leaking into the plumbing."""
    knows_nothing_of(
        only(*(seam for seam in _LAYERS["seams"] if seam != "journal.py")),
        only(*_LAYERS["domain"]),
        because="a seam carries its caller's values, and only the journal writes products",
    )


def test_nothing_in_the_journal_reaches_the_pipeline() -> None:
    """ADR-0060's first obligation: "Nothing in the journal may reach the pipeline. It is
    opened before a run and asked after it, by the two doors" -- "which is what keeps the
    answer to a run the ranked products and nothing else". The seams' row lets a step
    import ``journal.py``, and this is the edge that row was never meant to open."""
    knows_nothing_of(
        only(*_LAYERS["pipeline"]),
        only("journal.py"),
        because="what moved since the last run is reported, never ranked on",
    )


def test_the_answer_cache_is_invisible_to_who_asks_and_who_answers() -> None:
    """ADR-0044: ``RememberedAnswers`` "is a ``ChatModel`` wrapping a ``ChatModel``, so
    everything above it asks its one question and cannot tell ... That is the only way
    this is allowed to work", and "the wrapping happens in ``agent.py``, not in
    ``providers.py``" -- neither row has to declare it. So the module that asks, through
    its chains, and the table that answers know nothing of the cache between them."""
    knows_nothing_of(
        only("extraction.py", "providers.py"),
        only("cache.py"),
        because="a remembered answer is one neither side of it can tell from a fresh one",
    )


def test_the_two_doors_do_not_know_about_each_other() -> None:
    """``CLAUDE.md``, on the two front doors: they are "two ways of filling in the same
    ``AgentConfig``" -- two, and what they share is the config and the payload below them.
    The layer rule cannot say this: both are entry points, and an edge inside a layer is
    not a cross-layer edge. What it costs is what ``__init__.py``'s rule costs -- the
    server imported from ``__main__.py`` puts a socket module behind ``python -m
    buy_agent``, and ``__main__.py`` imported from the server puts an ``argv``'s worth of
    defaults behind a form."""
    knows_nothing_of(
        only("__main__.py", "server.py"),
        only("__main__.py", "server.py"),
        because="two doors onto one config, and neither is reached through the other",
    )


def test_nothing_above_the_orchestrator_runs_a_step_of_its_own() -> None:
    """The order of the pipeline is ``BuyAgent.run``'s to know, which the rule above says
    from inside the line: the steps do not chain themselves. This is the other side of it
    -- the doors, the payload and the settings do not call into the middle of it either.
    ``ranking.py`` is the exception at every one of them, and the same exception: a
    finished run put in another order without being run again (ADR-0035), the criteria
    ``--sort-by`` offers, and the ``RankingWeights`` an ``AgentConfig`` carries."""
    knows_nothing_of(
        only("__main__.py", "server.py", "api.py", "config.py", "logging_setup.py"),
        only("extraction.py", "verification.py", "constraints.py", "fetch.py"),
        because="a step is reached by running the pipeline, not by calling into it",
    )


def test_verification_shares_only_the_extractors_vocabulary() -> None:
    """The exemption in the rule above, stated rather than assumed: "the one edge inside
    that layer is ``verification.py`` sharing ``extraction.py``'s vocabulary" --
    ``GENERIC_WORDS``, ``NAME_TOKENS`` and ``SUPERLATIVES``, so merging and grounding agree
    on what a name's words are (ADR-0008). One edge: left out of the subject of the
    chaining rule, ``verification.py`` is out of it for every other step too."""
    knows_nothing_of(
        only("verification.py"),
        only("constraints.py", "fetch.py", "ranking.py"),
        because="one shared vocabulary is an edge; a second is the line chaining itself",
    )


def test_the_currency_table_is_the_leaf_of_the_package() -> None:
    """ADR-0054: every currency table is ``money.py``'s, and "a currency is added there and
    nowhere else" -- which holds only while the six modules that read a derivation off it
    are six modules it has never heard of. It is the bottom of the domain, so it is the one
    module whose rule is that it reaches nothing at all, installed or not."""
    knows_nothing_of(
        only("money.py"),
        because="the table everything reads is the one thing that reads nothing",
    )
    imports_none_of(
        only("money.py"),
        re.compile(rf"^(?:{'|'.join(third_party_imports())})\b"),
        because="a currency is a table and a rounding rule, and needs nothing installed",
    )


def test_a_bound_in_the_request_is_read_beside_the_money_and_applied_by_nobody() -> None:
    """ADR-0059: ``bounds.notice`` reads "under $200" out of the request in ordinary Python
    and both doors *offer* what it found. "Nothing may ever apply one: the moment one is
    applied unseen, the silent empty report is back" -- and "``bounds.py`` knows nothing of
    ``config.LIMITS``, so a figure the setting would refuse is dropped at the door rather
    than pre-filled into a box the form would then mark". So it reaches ``money.py``, for
    the marks that make a figure a budget, and nothing else of this package: not the
    constraints that do apply a bound, and not the settings that bound the setting."""
    knows_nothing_of(
        only("bounds.py"),
        every_module_but("bounds.py", "money.py"),
        because="what the request asks for is read beside the money and applied by nobody",
    )


def test_a_bound_in_the_request_is_offered_at_the_doors_alone() -> None:
    """The other half of the rule above: ``bounds.py`` is "read in Python and offered at
    both doors (ADR-0059) -- never applied". "Nothing in the pipeline reads it.
    ``Constraints`` is untouched." The CLI logs a line naming the flag and ``GET
    /api/bounds`` pre-fills an empty box, once, and a bound read anywhere below them is
    one applied without being seen -- "the failure this ADR exists to avoid is back, and
    it is silent"."""
    knows_nothing_of(
        every_module_but("bounds.py", "__main__.py", "api.py"),
        only("bounds.py"),
        because="what the request asks for is offered to the shopper, and set only by them",
    )
