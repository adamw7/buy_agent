"""Rules that hold between modules, where no single module's tests can protect them.

Most of what this codebase gets wrong twice is a list that exists in three places.
``BuyAgent.run`` raises exactly three things, ``__main__.main`` catches exactly
those three, and ``api._STATUS`` maps exactly those three onto HTTP statuses --
and each of the three is tested only against its own idea of the list, so adding a
fourth failure mode to two of them leaves the suite green while the user gets a
traceback and the browser gets a 500.

The same shape recurs for the sort criteria and for the payloads
``ui/src/app/agent.types.ts`` mirrors: a field added on the Python side and
forgotten on the TypeScript side is a runtime ``undefined`` in the browser that
neither suite can see, because neither suite can see the other language.

The ``Dockerfile`` is the same shape again, across a file no import reaches: it
pins the versions CI tests against, copies the built UI to the path
``server.DEFAULT_UI_DIR`` names, and publishes the port the server binds -- three
agreements whose failure shows up only in a built container.

The decision log has the same shape: ``docs/adr/README.md`` indexes records that
live in files beside it, so an unindexed decision -- or an index row still quoting
a title the record has since changed -- is invisible to every other test here.

The nightly integration run is the shape across a directory this suite cannot
enter: ``integration/`` is deliberately outside ``testpaths``, so nothing in a
``python -m pytest`` run collects it, names the model it wants, or notices that
the workflow pulls a different one.

The Saturday mutation run is the shape at its sharpest: mutmut tests a *copy* of
the tree, so a file this suite reads or imports and ``setup.cfg`` does not list
is missing only there. Every path resolves in a normal run, and the weekly one
dies at collection with nobody watching.

These tests read the declarations themselves rather than exercising behaviour,
which is the only way to check that two lists agree about what is *not* in them.
"""

from __future__ import annotations

import ast
import inspect
import re
import sys
from configparser import ConfigParser
from pathlib import Path, PurePosixPath
from typing import get_args

import pytest

from buy_agent.__main__ import build_parser
from buy_agent.agent import BuyAgent
from buy_agent.api import (
    PROVIDER_OPTIONS,
    SORT_OPTIONS,
    _STATUS,
    ApiError,
    defaults_payload,
    product_payload,
    run_search,
)
from buy_agent.config import AgentConfig
from buy_agent.providers import PROVIDERS, provider_options
from buy_agent.models import Product, RankedProduct
from buy_agent.ranking import SortBy
from buy_agent.server import DEFAULT_UI_DIR
from buy_agent.server import build_parser as build_server_parser
import integration
from integration import REQUIRE_ENV_VAR, TINY_MODEL

_ROOT = Path(__file__).resolve().parents[1]
_TYPES_TS = _ROOT / "ui" / "src" / "app" / "agent.types.ts"

RANKED = RankedProduct(
    product=Product(name="Sony WH-1000XM5", price=328.0, rating=4.7), score=0.9, rank=1
)


def caught_by_main() -> set[str]:
    """The exception names ``__main__.main`` lists in its ``except`` tuple.

    Read from the source rather than triggered one at a time: what matters is the
    membership of the tuple, and a behavioural test can only ever confirm the
    entries it already knows to try.

    The handlers of the ``try`` that wraps ``BuyAgent(...).run(...)``, rather than
    every handler in the function. ``main`` also guards writing the ``--json``
    file, and an ``OSError`` from a path the user mistyped is not one of the
    pipeline's failure modes -- the report has already been logged by then.
    """
    tree = ast.parse((_ROOT / "buy_agent" / "__main__.py").read_text(encoding="utf-8"))
    main = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    guarding_the_run = next(
        node
        for node in ast.walk(main)
        if isinstance(node, ast.Try)
        and any(
            isinstance(call.func, ast.Attribute) and call.func.attr == "run"
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
        )
    )
    caught: set[str] = set()
    for handler in guarding_the_run.handlers:
        named = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
        caught.update(node.id for node in named if isinstance(node, ast.Name))
    return caught - {"KeyboardInterrupt"}


def documented_raises() -> set[str]:
    """The exception names ``BuyAgent.run`` promises in its docstring.

    ``inspect.cleandoc`` first, rather than matching the indentation as written:
    Python 3.13 strips a docstring's common leading whitespace at compile time and
    earlier versions do not, so the entries sit twelve columns in on 3.12 and four
    on 3.13. Cleaned, a section heading is flush left and its entries are indented,
    which is what tells the two apart here.
    """
    body = inspect.cleandoc(BuyAgent.run.__doc__ or "").partition("Raises:")[2]
    return set(re.findall(r"^\s+(\w+):", body, re.MULTILINE))


def ts_interface(name: str) -> list[str]:
    """The field names of one exported TypeScript interface, in declaration order."""
    source = _TYPES_TS.read_text(encoding="utf-8")
    match = re.search(rf"export interface {name} \{{(.*?)\n\}}", source, re.DOTALL)
    assert match, f"no interface {name} in {_TYPES_TS.name}"
    return re.findall(r"^\s+(\w+)\??:", match.group(1), re.MULTILINE)


def ini_values(path: Path, section: str, key: str) -> list[str]:
    """One whitespace-separated setting, from an ini file neither tool exports."""
    parser = ConfigParser()
    parser.read(path, encoding="utf-8")
    return parser.get(section, key).split()


def cron(workflow: Path) -> tuple[str, str, str, str, str]:
    """The five fields of a workflow's schedule: minute, hour, day, month, weekday."""
    match = re.search(r'^\s+- cron: "([^"]+)"$', workflow.read_text(encoding="utf-8"), re.M)
    assert match, f"no cron schedule in {workflow.name}"
    minute, hour, day_of_month, month, day_of_week = match.group(1).split()
    return minute, hour, day_of_month, month, day_of_week


# -- the three failure modes ---------------------------------------------------


def test_the_agent_the_cli_and_the_api_agree_on_the_failure_modes() -> None:
    """A failure mode missing from any one of the three reaches someone raw."""
    by_status = {kind.__name__ for kind in _STATUS}

    assert by_status == caught_by_main(), "api._STATUS and __main__.main disagree"
    assert by_status == documented_raises(), "BuyAgent.run's docstring disagrees"


@pytest.mark.parametrize("kind", list(_STATUS), ids=lambda kind: kind.__name__)
def test_every_failure_mode_has_a_status_that_is_not_a_server_error(kind: type) -> None:
    """A 5xx in the 500 sense means "we crashed"; these are all understood failures."""
    assert _STATUS[kind] in {400, 502, 503}


@pytest.mark.parametrize("kind", list(_STATUS), ids=lambda kind: kind.__name__)
def test_run_search_gives_each_failure_mode_its_own_status(kind: type) -> None:
    """Parametrized over the live mapping, so a fourth entry is exercised too."""

    def failing_agent(_config):
        class Agent:
            def run(self, request, *, sort_by="score"):
                raise kind("no")

        return Agent()

    with pytest.raises(ApiError) as excinfo:
        run_search("headphones", AgentConfig(), agent_factory=failing_agent)

    assert excinfo.value.status == _STATUS[kind]


# -- the model servers ---------------------------------------------------------


def test_every_provider_is_offered_everywhere_it_can_be_asked_for() -> None:
    """Three doors onto one registry -- the flag, the API's check, and the rows the
    form builds its picker from. A provider missing from one of them is one the
    other two will happily hand to a config that then refuses it."""
    names = set(PROVIDERS)
    cli = {action.dest: action for action in build_parser()._actions}["provider"]

    assert set(PROVIDER_OPTIONS) == names
    assert set(cli.choices) == names
    assert {option["name"] for option in defaults_payload()["provider_options"]} == names


def test_a_provider_option_is_mirrored_field_for_field_in_typescript() -> None:
    """The form reads these to fill the model and the server fields in, so a key
    added on the Python side and forgotten here is an undefined in the box."""
    assert set(ts_interface("ProviderOption")) == set(provider_options()[0])


# -- the sort criteria ---------------------------------------------------------


def test_every_sort_criterion_is_offered_everywhere_it_can_be_asked_for() -> None:
    """``rank_products`` has a branch per criterion; all three doors must match it."""
    criteria = set(get_args(SortBy))
    cli = {action.dest: action for action in build_parser()._actions}["sort_by"]

    assert set(SORT_OPTIONS) == criteria
    assert set(cli.choices) == criteria
    assert set(defaults_payload()["sort_options"]) == criteria
    assert set(re.findall(r"'(\w+)'", _typescript_sort_union())) == criteria


def _typescript_sort_union() -> str:
    source = _TYPES_TS.read_text(encoding="utf-8")
    match = re.search(r"export type SortBy = ([^;]+);", source)
    assert match, "no SortBy union in agent.types.ts"
    return match.group(1)


# -- the payloads the browser is typed against ---------------------------------


def test_the_form_defaults_are_mirrored_field_for_field_in_typescript() -> None:
    """A default added in api.py and forgotten here is an undefined in the form."""
    assert set(ts_interface("AgentDefaults")) == set(defaults_payload())


def test_a_ranked_product_is_mirrored_field_for_field_in_typescript() -> None:
    assert set(ts_interface("RankedProduct")) == set(product_payload(RANKED))


def test_a_finished_run_is_mirrored_field_for_field_in_typescript() -> None:
    payload = run_search(
        "headphones", AgentConfig(), agent_factory=lambda _config: _StubAgent()
    )

    assert set(ts_interface("SearchResult")) == set(payload)


class _StubAgent:
    """Answers with one ranked product, so run_search's own keys can be read off."""

    def run(self, request, *, sort_by="score"):
        return [RANKED]


# -- the decision log ----------------------------------------------------------

_ADR = _ROOT / "docs" / "adr"
_ADR_INDEX = _ADR / "README.md"
_ADR_SECTIONS = ("## Context", "## Decision", "## Consequences")
_ADR_ROW = re.compile(r"^\| \[(\d{4})\]\((\d{4}-[a-z0-9-]+\.md)\) \| (.+?) \| (.+?) \|$", re.M)


def adr_files() -> list[Path]:
    """Every accepted record, template excluded, in numbered order."""
    return sorted(path for path in _ADR.glob("[0-9][0-9][0-9][0-9]-*.md") if path.stem[:4] != "0000")


def adr_heading(path: Path) -> tuple[str, str]:
    """One record's number and title, read off its ``# ADR-NNNN: Title`` line."""
    first = path.read_text(encoding="utf-8").splitlines()[0]
    match = re.fullmatch(r"# ADR-(\d{4}): (.+)", first)
    assert match, f"{path.name} does not open with '# ADR-NNNN: Title'"
    return match.group(1), match.group(2)


def adr_status(path: Path) -> str:
    """One record's status, from its ``- **Status:**`` line."""
    match = re.search(r"^- \*\*Status:\*\* (.+)$", path.read_text(encoding="utf-8"), re.M)
    assert match, f"{path.name} has no Status line"
    return match.group(1)


def test_the_adr_index_lists_every_record_and_nothing_else() -> None:
    """An unindexed decision is one nobody reading docs/adr/ will find."""
    indexed = [row[1] for row in _ADR_ROW.findall(_ADR_INDEX.read_text(encoding="utf-8"))]

    assert indexed == [path.name for path in adr_files()]


@pytest.mark.parametrize("row", _ADR_ROW.findall(_ADR_INDEX.read_text(encoding="utf-8")))
def test_each_index_row_says_what_the_record_says(row: tuple[str, str, str, str]) -> None:
    """Title and status live in two places; the index is the one that goes stale."""
    number, filename, title, status = row
    path = _ADR / filename

    assert filename.startswith(f"{number}-"), f"row {number} links to {filename}"
    assert adr_heading(path) == (number, title)
    assert adr_status(path) == status


@pytest.mark.parametrize("path", adr_files(), ids=lambda path: path.stem[:4])
def test_every_record_has_a_status_a_date_and_the_three_sections(path: Path) -> None:
    """The shape ADR-0001 asks for: context, the decision, and its consequences."""
    text = path.read_text(encoding="utf-8")

    assert adr_heading(path)[0] == path.stem[:4], "the heading and the filename disagree"
    assert re.search(r"^- \*\*Date:\*\* \d{4}-\d{2}-\d{2}$", text, re.M), "no ISO date"
    assert re.fullmatch(r"Accepted|Proposed|Superseded by \[ADR-\d{4}\]\(.+\)", adr_status(path))
    for section in _ADR_SECTIONS:
        assert f"\n{section}\n" in text, f"{path.name} has no {section} section"


@pytest.mark.parametrize("path", adr_files(), ids=lambda path: path.stem[:4])
def test_every_record_a_record_points_at_exists(path: Path) -> None:
    """Records supersede and cite each other by number; a dangling one reads as a gap."""
    numbers = {other.stem[:4] for other in adr_files()}

    assert set(re.findall(r"ADR-(\d{4})", path.read_text(encoding="utf-8"))) <= numbers


# -- the container image -------------------------------------------------------

_DOCKERFILE = _ROOT / "Dockerfile"
_CI = _ROOT / ".github" / "workflows" / "ci.yml"
_MUTATION = _ROOT / ".github" / "workflows" / "mutation.yml"
_INTEGRATION = _ROOT / ".github" / "workflows" / "integration.yml"
_MUTMUT = _ROOT / "setup.cfg"
_COVERAGERC = _ROOT / ".coveragerc"
_PYTEST_INI = _ROOT / "pytest.ini"


def dockerfile() -> str:
    return _DOCKERFILE.read_text(encoding="utf-8")


def base_image_tag(image: str) -> str:
    """The tag one stage's ``FROM <image>:<tag>`` pins, e.g. ``3.13-slim``."""
    match = re.search(rf"^FROM {image}:(\S+)", dockerfile(), re.M)
    assert match, f"no stage builds on {image} in the Dockerfile"
    return match.group(1)


def workflow_version(workflow: Path, key: str) -> str:
    """A version a workflow sets up, e.g. ``node-version: "22.22.3"``."""
    match = re.search(rf'^\s+{key}: "([^"]+)"', workflow.read_text(encoding="utf-8"), re.M)
    assert match, f"no {key} in {workflow.name}"
    return match.group(1)


def ci_version(key: str) -> str:
    """A version `.github/workflows/ci.yml` sets up."""
    return workflow_version(_CI, key)


def server_default(dest: str) -> object:
    """One default `buy_agent.server`'s parser fills in, read off the parser itself."""
    return {action.dest: action for action in build_server_parser()._actions}[dest].default


def test_the_image_pins_the_versions_ci_tests_against() -> None:
    """Two toolchains, now in a third place: an image built on another Python or
    another Node is running code no job has run."""
    assert base_image_tag("python").startswith(ci_version("python-version"))
    assert base_image_tag("node").startswith(ci_version("node-version"))


def test_the_image_puts_the_built_ui_where_the_server_looks() -> None:
    """Copied anywhere else, the container serves the 503 that says to build the UI."""
    match = re.search(r"^COPY --from=ui \S+ \./(\S+?)/?$", dockerfile(), re.M)
    assert match, "the runtime stage copies nothing from the ui stage"

    assert match.group(1) == DEFAULT_UI_DIR.relative_to(_ROOT).as_posix()
    assert re.search(r"^WORKDIR /app$", dockerfile(), re.M), "the copy is relative to /app"


def test_the_image_publishes_the_port_it_binds_on_every_interface() -> None:
    """Loopback inside a container is the container's own: a published port would
    reach nothing, and an EXPOSE of another port would document a lie."""
    assert re.search(rf"^EXPOSE {server_default('port')}$", dockerfile(), re.M)
    assert '"--host", "0.0.0.0"' in dockerfile(), "the default command binds loopback"


def test_the_image_installs_the_runtime_dependencies_only() -> None:
    """pytest and coverage in an image are weight, and a wider surface to patch."""
    assert "requirements-dev.txt" not in dockerfile()
    assert re.search(r"^RUN pip install .* -r requirements\.txt$", dockerfile(), re.M)


# -- the two runners -----------------------------------------------------------

#: A runner label names its platform first: ``windows-latest``, ``ubuntu-latest``.
_PLATFORMS = ("windows", "ubuntu")


def ci_matrices() -> list[list[str]]:
    """The runner labels each job in ci.yml spreads itself over."""
    lists = re.findall(r"^\s+os: \[([^\]]+)\]$", _CI.read_text(encoding="utf-8"), re.M)
    assert lists, "no job in ci.yml names the runners it uses"
    return [[label.strip() for label in group.split(",")] for group in lists]


def test_every_job_runs_on_windows_as_well_as_linux() -> None:
    """This project is written on Windows and its jobs run on Linux, so either one
    alone is a platform nobody checks against. The differences are not exotic --
    a path separator, a default encoding, a socket that resets where the other
    closes, a ``mimetypes`` lookup that reads the registry -- and every one of
    them surfaces on exactly one of the two."""
    for runners in ci_matrices():
        platforms = {label.split("-")[0] for label in runners}

        assert platforms.issuperset(_PLATFORMS), runners


def test_ci_sets_up_one_python_and_one_node() -> None:
    """The Dockerfile, `scripts/start.ps1` and docs/testing.md all pin themselves
    to the version ci.yml sets up, and each reads it as the one this file names. A job
    matrixed over two Pythons would leave those three agreeing with whichever
    happened to be written first, and silently untested against the other."""
    source = _CI.read_text(encoding="utf-8")

    for key in ("python-version", "node-version"):
        assert source.count(f"{key}: ") == 1, f"ci.yml sets up more than one {key}"


def workflows() -> list[Path]:
    """Every workflow in ``.github/workflows``, found rather than listed.

    A fourth workflow is then covered by the rules below on the day it is added,
    which is the only time anybody would think to check them.
    """
    found = sorted((_ROOT / ".github" / "workflows").glob("*.yml"))
    assert found, "no workflows; this section has outlived its rule"
    return found


@pytest.mark.parametrize("workflow", workflows(), ids=lambda path: path.stem)
def test_every_workflow_sets_up_the_python_the_tests_run_on(workflow: Path) -> None:
    """One Python across the repository, checked per workflow rather than per rule.

    Mutants are tested by the same suite CI runs, and the nightly integration run
    puts that suite in front of a real model; on another interpreter either one is
    a report about a Python nothing else in this project uses -- and a scheduled
    job is the worst place to discover a version difference, because it reproduces
    on nobody's machine and nobody is watching when it does not.
    """
    for version in re.findall(
        r'^\s+python-version: "([^"]+)"', workflow.read_text(encoding="utf-8"), re.M
    ):
        assert version == ci_version("python-version"), workflow.name


def action_versions(workflow: Path) -> dict[str, str]:
    """The ref each ``uses: owner/action@ref`` step of a workflow pins."""
    steps = re.findall(r"uses: (\S+?)@(\S+)$", workflow.read_text(encoding="utf-8"), re.M)
    versions: dict[str, str] = {}

    for action, ref in steps:
        assert versions.setdefault(action, ref) == ref, f"{workflow.name} pins two {action}"
    return versions


def test_every_workflow_pins_the_same_version_of_a_shared_action() -> None:
    """`actions/checkout` and `actions/setup-python` are used by all three workflows,
    and an update that reached only one of them is invisible: each file is valid on
    its own and every job goes green. What it costs is a scheduled run drifting onto
    an older action than the one every pull request is checked with -- so a failure
    that is the action's, not the code's, arrives overnight and reproduces nowhere."""
    pinned: dict[str, dict[str, str]] = {}
    for workflow in workflows():
        for action, ref in action_versions(workflow).items():
            pinned.setdefault(action, {})[workflow.name] = ref

    shared = {action: refs for action, refs in pinned.items() if len(refs) > 1}
    assert shared, "no action is used twice; this test has outlived its rule"
    for action, refs in shared.items():
        assert len(set(refs.values())) == 1, (action, refs)


# -- the startup script --------------------------------------------------------

_START = _ROOT / "scripts" / "start.ps1"


def start_script() -> str:
    return _START.read_text(encoding="utf-8")


def test_the_startup_script_opens_the_page_the_server_binds() -> None:
    """It ends by launching a browser at a literal URL. Bound anywhere else, the
    script's last act is a browser sitting on a dead page while the console beside
    it fills with the log lines of a server that came up perfectly well."""
    match = re.search(r"^\$url = '(\S+)'$", start_script(), re.M)
    assert match, "the startup script names no URL to open"

    assert match.group(1) == f"http://{server_default('host')}:{server_default('port')}"


def test_the_startup_script_asks_python_for_the_model_and_the_server() -> None:
    """An ``AgentConfig`` already answers to $BUY_AGENT_PROVIDER, $OLLAMA_MODEL,
    $OLLAMA_HOST, $VLLM_MODEL and $VLLM_HOST, so a tag or a URL copied into the
    script is a second default that goes stale silently: the script would pull one
    model and the run would ask for another, or it would wait on a server nothing
    intends to use. It is read whole, off one config, because the pair belongs to
    the provider -- reading a model from one variable and an address from another
    is how the two come to disagree."""
    source = start_script()

    assert "from buy_agent.config import AgentConfig" in source
    for server in PROVIDERS.values():
        for value in (server.model, server.base_url):
            assert value not in source, f"{value} is the provider table's to say"


def test_the_startup_script_looks_for_the_build_the_server_serves() -> None:
    """It skips the Angular build when one is already there, and the server answers
    with a 503 telling you to build the UI when ``DEFAULT_UI_DIR`` is empty. Two
    paths, so two ways to disagree: probe a path the build no longer writes and
    every run rebuilds it; probe one the server does not read and the script opens
    a browser at that 503, with nothing on the console to say why."""
    match = re.search(r"^\$built = Join-Path \$root '(\S+)'$", start_script(), re.M)
    assert match, "the startup script probes nothing before rebuilding the UI"

    probed = PurePosixPath(match.group(1).replace("\\", "/"))
    assert probed.parent.as_posix() == DEFAULT_UI_DIR.relative_to(_ROOT).as_posix()
    assert probed.name == "index.html", "a build is a directory; a page is what proves it"


def test_the_startup_script_names_the_toolchains_ci_pins() -> None:
    """The two things it will not install, it says where to get -- and a version
    named there is a fourth copy of what ci.yml sets up, the Dockerfile pins and
    docs/testing.md quotes. Sending someone to install a Python or a Node no job has run is
    the one kind of stale that costs a download to find out about."""
    source = start_script()

    assert f"Python {ci_version('python-version')}" in source
    assert f"Node {ci_version('node-version')}" in source


# -- the nightly integration run -----------------------------------------------

#: The five minutes `.github/workflows/integration.yml` gives itself, which
#: docs/testing.md, the README and CLAUDE.md all quote. Everything is inside it:
#: installing Ollama, pulling the model, and inference on a runner with no GPU.
_NIGHTLY_BUDGET_MINUTES = 5

#: Where the live tests live. Read off the package rather than written down, so
#: renaming the directory fails here rather than in a scheduled run.
_LIVE_TESTS = Path(integration.__file__).resolve().parent


def integration_workflow() -> str:
    return _INTEGRATION.read_text(encoding="utf-8")


def test_a_normal_run_cannot_collect_the_tests_that_need_ollama() -> None:
    """The whole reason ``integration/`` is a directory and not a marker.

    ``pytest.ini`` points ``testpaths`` at ``tests``, and everything under it is
    promised to touch neither the network nor Ollama -- a promise the README,
    docs/testing.md and CLAUDE.md all repeat. A marker would leave that resting
    on ``addopts`` and on nobody forgetting to apply one; a directory outside
    ``testpaths`` cannot be collected by accident at all.
    """
    testpaths = ini_values(_PYTEST_INI, "pytest", "testpaths")

    assert testpaths, "pytest.ini names no testpaths, so a bare run collects everything"
    for path in testpaths:
        assert not _LIVE_TESTS.is_relative_to(_ROOT / path), path


def test_the_nightly_run_runs_the_tests_a_normal_run_leaves_out() -> None:
    """...which is the other half of it: outside ``testpaths``, they are collected
    only by being named, so a workflow that ran a bare ``pytest`` would go green
    having run the unit suite a second time and the live tests never."""
    named = _LIVE_TESTS.relative_to(_ROOT).as_posix()

    assert re.search(rf"^\s+run: python -m pytest {named}$", integration_workflow(), re.M)


def test_the_nightly_run_pulls_the_model_the_live_tests_ask_for() -> None:
    """Two names for one model, in a workflow and in a package that never import
    each other. Pull a different tag and every test skips -- or, with
    :data:`REQUIRE_ENV_VAR` set, every test fails on a machine that has Ollama
    running perfectly well."""
    assert re.search(
        rf"^\s+run: ollama pull {re.escape(TINY_MODEL)}$", integration_workflow(), re.M
    )


def test_the_nightly_run_refuses_to_pass_by_skipping() -> None:
    """A live test whose model is absent skips, which is right on a developer's
    machine and worthless on a schedule: an Ollama that failed to install would
    give a green nightly job that checked nothing at all. The workflow sets this,
    and it is the only thing that does."""
    assert re.search(rf'^\s+{REQUIRE_ENV_VAR}: "1"$', integration_workflow(), re.M)


def test_the_nightly_run_is_nightly_and_capped() -> None:
    """Every day, off the hour, and bounded. The cap is the load-bearing half: the
    model is small enough to pull and answer inside it, and the day it is not, a
    red run says so instead of the job spending runner minutes nobody reads."""
    minute, _hour, day_of_month, month, day_of_week = cron(_INTEGRATION)

    assert (day_of_week, day_of_month, month) == ("*", "*", "*")
    assert minute != "0", "the top of the hour is where scheduled runs queue"
    assert re.search(
        rf"^\s+timeout-minutes: {_NIGHTLY_BUDGET_MINUTES}$", integration_workflow(), re.M
    )


def test_the_nightly_run_is_never_a_gate_on_a_pull_request() -> None:
    """Like the mutation run and for the same reason: it takes minutes where the
    suite takes seconds, and it depends on a third party's install script and a
    model tag that can be re-pulled under it. Neither is something a merge should
    wait on."""
    for workflow in (_INTEGRATION, _MUTATION):
        assert "pull_request" not in workflow.read_text(encoding="utf-8"), workflow.name


# -- the Saturday mutation run -------------------------------------------------

# mutmut copies these two into the tree it tests without being asked; everything
# else the suite reaches for has to be named in ``also_copy``.
_COPIED_ANYWAY = ("tests", "setup.cfg")


def test_the_mutation_run_mutates_what_coverage_measures() -> None:
    """Coverage says which lines ran; mutation testing says whether anything would
    have noticed had they run differently. A package measured by one and not the
    other has the reassuring number and none of the checking behind it."""
    assert ini_values(_MUTMUT, "mutmut", "source_paths") == ini_values(_COVERAGERC, "run", "source")


def test_the_mutation_run_is_scheduled_for_saturdays() -> None:
    """Weekly and off the hour, as docs/testing.md and CLAUDE.md both say: cron counts
    days from Sunday, so Saturday is 6, and a run that quietly moved to another
    day would leave the two of them describing a schedule that is not this one."""
    minute, _hour, day_of_month, month, day_of_week = cron(_MUTATION)

    assert (day_of_week, day_of_month, month) == ("6", "*", "*")
    assert minute != "0", "the top of the hour is where scheduled runs queue"


def files_read() -> list[Path]:
    """The paths these tests open, off the ``_NAME`` constants declared above.

    A Path that arrived by import -- ``DEFAULT_UI_DIR`` -- is one they compare
    against rather than read, so only this module's own constants count.
    """
    return [
        value
        for name, value in globals().items()
        if name.startswith("_") and isinstance(value, Path) and value != _ROOT
    ]


def files_imported() -> list[Path]:
    """Every module the suite has imported from a file in this repository.

    ``sys.modules`` is the honest answer to "what does the suite need on disk":
    pytest imports every test module before it runs the first test, so a helper
    imported by any of them is in here. Installed packages are not -- except in a
    virtual environment inside the repository, hence the site-packages check.
    """
    mutated = [_ROOT / name for name in ini_values(_MUTMUT, "mutmut", "source_paths")]
    files = (getattr(module, "__file__", None) for module in list(sys.modules.values()))
    return [
        path
        for path in map(Path, filter(None, files))
        if path.is_relative_to(_ROOT)
        and "site-packages" not in path.parts
        and not any(path.is_relative_to(package) for package in mutated)
    ]


def test_a_mutation_run_copies_everything_the_tests_reach_for() -> None:
    """A mutation run tests a copy of the tree under mutants/, and this suite both
    reads files rather than importing them and imports from outside the package
    being mutated. Whatever ``also_copy`` leaves behind is missing only there, so
    the whole Saturday run dies at collection -- and nothing in a normal run,
    where every path resolves, can see it coming."""
    also_copy = ini_values(_MUTMUT, "mutmut", "also_copy") + list(_COPIED_ANYWAY)
    copied = [_ROOT / name for name in also_copy]
    needed = files_read() + files_imported()

    assert needed, "the suite reads and imports nothing; this test has outlived its rule"
    for path in needed:
        assert any(path.is_relative_to(destination) for destination in copied), path
