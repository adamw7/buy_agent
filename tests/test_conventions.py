"""Rules that hold between modules, where no single module's tests can protect them."""

from __future__ import annotations

import argparse
import ast
import builtins
import fnmatch
import inspect
import json
import os
import re
import sys
from configparser import ConfigParser
from functools import cache
from importlib.metadata import packages_distributions
from pathlib import Path, PurePosixPath
from typing import get_args

import pytest

from buy_agent.__main__ import build_parser
from buy_agent.__main__ import main as cli_main
from buy_agent.agent import BuyAgent
from buy_agent.api import (
    PAY_STATUS,
    PROVIDER_OPTIONS,
    RAIL_OPTIONS,
    SORT_OPTIONS,
    _STATUS,
    ApiError,
    defaults_payload,
    limits_payload,
    model_payload,
    parse_options,
    pay_now,
    product_payload,
    rank_again,
    results_payload,
    run_search,
    sources_payload,
)
from buy_agent.config import LIMITS, AgentConfig, parse_region
import buy_agent.fetch as fetch_module
import buy_agent.mandates as mandates_module
import buy_agent.money as money
import buy_agent.providers as providers_module
from buy_agent.payment import PaymentError
from buy_agent.providers import PROVIDERS, InstalledModel, provider_options
from buy_agent.rails import RAILS, rail_options
from buy_agent.models import Product, Removal
from tests.conftest import SOURCE_ROOT, needs_ap2, payable_product, ranked_product, said
from buy_agent.ranking import ORDERINGS, SortBy
from buy_agent.server import DEFAULT_UI_DIR
from buy_agent.server import build_parser as build_server_parser
import integration
from integration import LIVE_TIMEOUT_SECONDS, REQUIRE_ENV_VAR, TINY_MODEL

_ROOT = Path(__file__).resolve().parents[1]

#: The package these rules are read off, which is the one under ``_ROOT`` every
#: day but Saturday: a mutation run copies the tree to mutants/ and puts every
#: mutant of every module into the copy at once, and what is written down here is
#: what the code says rather than what one mutant of it would (``SOURCE_ROOT``).
_PACKAGE = SOURCE_ROOT / "buy_agent"

_TYPES_TS = _ROOT / "ui" / "src" / "app" / "agent.types.ts"
_FORM_TS = _ROOT / "ui" / "src" / "app" / "search-form" / "search-form.ts"
_FORM_HTML = _ROOT / "ui" / "src" / "app" / "search-form" / "search-form.html"

RANKED = ranked_product(
    Product(
        name="Sony WH-1000XM5",
        price=328.0,
        rating=4.7,
        # Quoted, so the payload these tests read carries an opinion to mirror.
        opinions=said("the noise cancelling is uncanny", page="https://audio.example/xm5"),
    ),
    score=0.9,
    rank=1,
)


def caught_by_main() -> set[str]:
    """The exception names ``__main__.main`` lists in its ``except`` tuple."""
    tree = ast.parse((_PACKAGE / "__main__.py").read_text(encoding="utf-8"))
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
    """The exception names ``BuyAgent.run`` promises in its docstring."""
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
            def run(self, request, *, sort_by="score", checkpoint=None, record=None):
                raise kind("no")

        return Agent()

    with pytest.raises(ApiError) as excinfo:
        run_search("headphones", AgentConfig(), agent_factory=failing_agent)

    assert excinfo.value.status == _STATUS[kind]


# -- the model servers ---------------------------------------------------------


def test_every_provider_is_offered_everywhere_it_can_be_asked_for() -> None:
    """Three doors onto one registry -- the flag, the API's check, and the rows the form
    builds its picker from."""
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


# -- the ranges a request is held to -------------------------------------------


@pytest.mark.parametrize(
    ("field", "flag", "key"),
    [
        ("num_products", "--results", "results"),
        ("top_n", "--top", "top"),
        ("temperature", "--temperature", "temperature"),
        ("num_ctx", "--num-ctx", "num_ctx"),
        ("model_timeout", "--model-timeout", "model_timeout"),
    ],
)
def test_both_front_doors_hold_a_number_to_the_same_range(
    field: str, flag: str, key: str
) -> None:
    """A bound written down twice is a CLI that accepts what the API refuses."""
    minimum, maximum = LIMITS[field]

    for outside in (minimum - 1, maximum + 1):
        with pytest.raises(ApiError):
            parse_options({key: outside})
        with pytest.raises(SystemExit):
            cli_main(["headphones", flag, str(outside)])


def test_a_run_of_the_defaults_is_inside_every_range() -> None:
    """A default the front ends would refuse is one nobody can accept either."""
    defaults = AgentConfig()

    for field, (minimum, maximum) in LIMITS.items():
        value = getattr(defaults, field)
        assert value is None or minimum <= value <= maximum, field


def test_the_form_is_shipped_a_range_for_every_number_it_holds_to_one() -> None:
    """The other half of the rule above, across the language boundary."""
    source = _FORM_TS.read_text(encoding="utf-8")
    match = re.search(r"numberFields: NumberField\[\] = \[(.*?)\n  \];", source, re.DOTALL)
    assert match, "no table of number fields in search-form.ts"

    assert set(re.findall(r"field\('(\w+)'", match.group(1))) == set(limits_payload())


def test_every_number_the_form_bounds_is_one_the_defaults_name() -> None:
    """The other thing a number box needs from the server, under the same key."""
    assert set(limits_payload()) <= set(defaults_payload())


def test_the_form_takes_its_bounds_from_the_server_rather_than_the_markup() -> None:
    """A `min="1" max="50"` written into the template is a second copy of
    ``config.LIMITS`` -- one no test of either suite would see go stale, since
    both ends stay perfectly valid while they disagree."""
    written = re.findall(r'\b(?:min|max)="[^"]*"', _FORM_HTML.read_text(encoding="utf-8"))

    assert written == [], "bind these from the limits the server ships"


def test_every_key_a_refusal_can_name_is_one_the_form_sends() -> None:
    """A refusal names the request key its value arrived under, and the page marks the box
    that key came from."""
    keys = _keys_read_by(parse_options)

    # ``request`` is the one thing that is not an option, and ``sources`` is the
    # one option that does not go through ``_read`` -- it is a list, which that
    # helper would render as a Python repr.
    assert keys | {"request", "sources"} == set(ts_interface("SearchOptions"))


def _keys_read_by(reader) -> set[str]:
    """The request keys a reading function names, read off its ``_read`` calls."""
    parsed = ast.parse(inspect.getsource(reader))
    return {
        call.args[1].value
        for call in ast.walk(parsed)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "_read"
        and isinstance(call.args[1], ast.Constant)
    }


# -- the shape a region is held to ---------------------------------------------


@pytest.mark.parametrize("typo", ["us_en", "en", "us-en-x", "united states"])
def test_both_front_doors_refuse_the_same_regions(typo: str) -> None:
    """The other half of the rule ``LIMITS`` carries for the numbers: a shape checked on
    one door only is a CLI that searches on what the API refuses -- and this one fails
    by returning nothing, so it would look like the web."""
    with pytest.raises(ApiError):
        parse_options({"region": typo})
    with pytest.raises(SystemExit):
        cli_main(["headphones", "--region", typo])


def test_the_default_region_is_one_both_front_doors_take() -> None:
    """A default the doors refuse is one nobody could ask for either."""
    region = AgentConfig().region

    assert parse_region(region) == region
    assert parse_options({"region": region})[0].region == region


# -- the currencies a price may be read in -------------------------------------


#: A currency code, which is what everything downstream of the extraction compares
#: prices by.
_ISO_CODE = re.compile(r"[A-Z]{3}")


def _scanned() -> list[str]:
    """Every spelling that makes :mod:`buy_agent.fetch` keep a price line -- all three
    halves of the scan, since a half left out here is one nothing holds to the rule."""
    return [*money.SIGNS, *money.WORDS, *money.SCANNED_CODES]


@pytest.mark.parametrize("spelling", _scanned())
def test_every_currency_a_price_is_read_in_is_one_the_run_can_place(spelling: str) -> None:
    """A price kept off a page has to be one the run can then do something with."""
    placed = money.code_for(spelling)

    if spelling in money.UNPLACEABLE:
        assert placed == spelling, "an ambiguous sign is left as written, never guessed"
    else:
        assert placed is not None and _ISO_CODE.fullmatch(placed), (
            f"{spelling!r} is read as a price and placed as {placed!r}"
        )


@pytest.mark.parametrize(
    "spelling", sorted((money.ALIASES.keys() | money.CODES) - money.UNSCANNED)
)
def test_every_currency_the_run_can_place_is_one_a_price_is_read_in(spelling: str) -> None:
    """The same rule the other way round, which is the direction that was wrong."""
    assert fetch_module.quotes_a_figure(f"it sells for {spelling}99") or (
        fetch_module.quotes_a_figure(f"it sells for 99 {spelling}")
    ), f"{spelling!r} is a currency this run can place and no price is ever read in"


def test_the_unplaceable_signs_are_ones_a_price_is_actually_read_in() -> None:
    """One exemption read the other way, so it cannot outlive its reason: a sign
    dropped from the scan leaves a row excusing nothing."""
    assert money.UNPLACEABLE <= set(_scanned())


def test_the_unscanned_spellings_are_ones_the_run_can_still_place() -> None:
    """The other exemption, the same way."""
    assert money.UNSCANNED <= money.ALIASES.keys()
    for spelling in money.UNSCANNED:
        assert _ISO_CODE.fullmatch(money.code_for(spelling) or "")


# -- the payloads the browser is typed against ---------------------------------


def test_the_form_defaults_are_mirrored_field_for_field_in_typescript() -> None:
    """A default added in api.py and forgotten here is an undefined in the form."""
    assert set(ts_interface("AgentDefaults")) == set(defaults_payload())


def test_a_shipped_range_is_mirrored_field_for_field_in_typescript() -> None:
    """The form holds its number fields to these, so a half-read range is a field
    with one end and no other."""
    assert set(ts_interface("Limit")) == set(limits_payload()["results"])


def test_the_sources_check_is_mirrored_field_for_field_in_typescript() -> None:
    """The answer names the spec it was about as well as what is wrong with it, and a form
    that read only the second would mark a field for text it no longer holds."""
    assert set(ts_interface("SourcesCheck")) == set(sources_payload(""))


def test_a_ranked_product_is_mirrored_field_for_field_in_typescript() -> None:
    assert set(ts_interface("RankedProduct")) == set(product_payload(RANKED))


def test_a_quoted_opinion_is_mirrored_field_for_field_in_typescript() -> None:
    """The card links each quote to the page that printed it, so a field added in
    Python and forgotten here is an undefined in an ``href`` (ADR-0042)."""
    quoted = product_payload(RANKED)["opinions"]
    assert quoted, "the fixture has to carry a quote for this to check anything"

    assert set(ts_interface("Opinion")) == set(quoted[0])


def test_a_score_s_parts_are_mirrored_field_for_field_in_typescript() -> None:
    """The card draws one share per criterion and marks the assumed ones, so a part added
    in Python and forgotten here is an undefined in a percentage (ADR-0041)."""
    assert set(ts_interface("ScoreParts")) == set(product_payload(RANKED)["breakdown"])


def test_the_weights_a_run_reports_are_mirrored_field_for_field_in_typescript() -> None:
    """The card draws each criterion's weight beside its share, since three shares under a
    total they do not add up to read as parts of it."""
    ran = run_search("headphones", AgentConfig(), agent_factory=lambda _config: _StubAgent())

    assert set(ts_interface("ScoreWeights")) == set(ran["weights"])


def test_an_installed_model_is_mirrored_field_for_field_in_typescript() -> None:
    """The picker reads these to decide what to mark, so a field added on the
    Python side and forgotten here is an undefined deciding a dropdown entry."""
    assert set(ts_interface("InstalledModel")) == set(
        model_payload(InstalledModel("gemma4:12b", completion=True))
    )


def test_a_finished_run_is_mirrored_field_for_field_in_typescript() -> None:
    payload = run_search(
        "headphones", AgentConfig(), agent_factory=lambda _config: _StubAgent()
    )

    assert set(ts_interface("SearchResult")) == set(payload)


def test_a_re_sort_answers_the_shape_a_finished_run_answers_with() -> None:
    """The page shows a re-sorted run through the same view it showed the run
    through, so the two payloads are one shape or the second is a view's worth of
    fields quietly going undefined (ADR-0035)."""
    ran = run_search("headphones", AgentConfig(), agent_factory=lambda _config: _StubAgent())
    reordered = rank_again({"request": "headphones", "products": results_payload([RANKED])})

    assert set(reordered) == set(ran)


def test_a_removal_is_mirrored_field_for_field_in_typescript() -> None:
    """A run says why it is short, and a field the browser cannot read is a reason
    nobody is given (ADR-0055)."""
    ran = run_search("headphones", AgentConfig(), agent_factory=lambda _config: _StubAgent())

    assert ran["dropped"], "the stub took something away and the payload lost it"
    assert set(ts_interface("Removal")) == set(ran["dropped"][0])


def test_a_re_sort_request_is_mirrored_field_for_field_in_typescript() -> None:
    """The other direction of the same rule as ``SearchOptions``: a key the
    browser never sends is a default nothing can move, and one it sends that
    nothing reads is a setting that quietly does nothing."""
    # ``products`` is the one key that does not go through ``_read`` -- it is a
    # list, which that helper would render as a Python repr.
    assert _keys_read_by(rank_again) | {"products"} == set(ts_interface("RankOptions"))


def test_a_run_leaves_the_process_in_one_shape_however_it_leaves() -> None:
    """``--json``, the API's answer and the page's Download results button hand over the
    same document, because all three are ``results_payload``."""
    written = ast.parse((_PACKAGE / "__main__.py").read_text(encoding="utf-8"))
    called = {
        node.func.id
        for node in ast.walk(written)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert "results_payload" in called, "--json must not shape a run of its own"


class _StubAgent:
    """Answers with one ranked product, so run_search's own keys can be read off --
    and takes one away, so the keys of a removal can be read off the same payload."""

    def run(self, request, *, sort_by="score", checkpoint=None, record=None):
        if record is not None:
            record(Removal(name="A headline", step="clean", reason="Not a product."))
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


# -- the dependency list -------------------------------------------------------

_REQUIREMENTS = _ROOT / "requirements.txt"

#: The two files paying is installed from.
_AP2_REQUIREMENTS = (_ROOT / "requirements-ap2.txt", _ROOT / "requirements-ap2-deps.txt")


def distribution(name: str) -> str:
    """A package name as pip and ``importlib.metadata`` spell it between them."""
    return re.sub(r"[-_.]+", "-", name).lower()


def pinned_in(path: Path) -> set[str]:
    """The distributions one requirements file names, its comments taken out."""
    names = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        # A `-r other.txt` line names a file rather than a package.
        if not line or line.startswith("-"):
            continue
        names.add(distribution(re.split(r"[<>=!~;@\[ ]", line, maxsplit=1)[0].strip()))
    return names


def distributions_the_package_imports() -> set[str]:
    """Every installed distribution ``buy_agent`` has an ``import`` for."""
    installed = packages_distributions()
    found: set[str] = set()
    for path in sorted(_PACKAGE.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [node.module]
            else:
                continue
            for module in modules:
                found.update(distribution(dist) for dist in installed.get(module.split(".")[0], ()))
    return found


def test_every_runtime_dependency_is_one_the_package_imports() -> None:
    """A pin nothing imports is weight in the image and a surface to patch."""
    pinned = pinned_in(_REQUIREMENTS)
    optional = set().union(*(pinned_in(path) for path in _AP2_REQUIREMENTS))
    imported = distributions_the_package_imports()

    assert not pinned - imported, "pinned in requirements.txt and imported by nothing"
    assert not imported - pinned - optional, "imported by the package and pinned nowhere"


# -- the container image -------------------------------------------------------

_DOCKERFILE = _ROOT / "Dockerfile"
_DOCKERIGNORE = _ROOT / ".dockerignore"
_GITIGNORE = _ROOT / ".gitignore"
_CI = _ROOT / ".github" / "workflows" / "ci.yml"
_MUTATION = _ROOT / ".github" / "workflows" / "mutation.yml"
_INTEGRATION = _ROOT / ".github" / "workflows" / "integration.yml"
_MUTMUT = _ROOT / "setup.cfg"
_COVERAGERC = _ROOT / ".coveragerc"
_PYTEST_INI = _ROOT / "pytest.ini"
_PYLINTRC = _ROOT / ".pylintrc"


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


# -- what the build is shown ---------------------------------------------------


def ignore_lines(path: Path) -> list[str]:
    """Every pattern one ignore file declares, its comments and blanks taken out."""
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def ignore_patterns(path: Path) -> set[str]:
    """The same, spelled so two ignore files can be compared."""
    return {line.rstrip("/").removeprefix("**/").removeprefix("/") for line in ignore_lines(path)}


def test_the_build_context_leaves_out_what_working_here_leaves_behind() -> None:
    """`.dockerignore` says it is read off `.gitignore`; this is that sentence."""
    missing = ignore_patterns(_GITIGNORE) - ignore_patterns(_DOCKERIGNORE)

    assert not missing, f"git leaves these behind, the build context takes them: {sorted(missing)}"


def context_copies() -> list[str]:
    """What the image copies out of the build context, off the ``COPY`` lines."""
    sources = [
        source
        for line in dockerfile().splitlines()
        if line.startswith("COPY ") and not line.startswith("COPY --from=")
        for source in line.split()[1:-1]
    ]
    assert sources, "the image copies nothing out of the build context"
    return sources


def _matches(pattern: list[str], segments: list[str]) -> bool:
    """Whether one pattern, split on ``/``, matches a path split the same way."""
    if pattern and pattern[0] == "**":
        return any(_matches(pattern[1:], segments[at:]) for at in range(len(segments) + 1))
    return len(pattern) == len(segments) and all(
        fnmatch.fnmatchcase(segment, part)
        for part, segment in zip(pattern, segments, strict=True)
    )


def excluded_from_the_build_context(path: str) -> str | None:
    """The `.dockerignore` pattern keeping ``path`` out, or None for one it lets in."""
    segments = path.strip("/").split("/")
    for pattern in ignore_lines(_DOCKERIGNORE):
        parts = pattern.rstrip("/").split("/")
        if any(_matches(parts, segments[:depth]) for depth in range(1, len(segments) + 1)):
            return pattern
    return None


def test_the_build_context_holds_everything_the_image_copies() -> None:
    """The same file read the other way, where being wrong stops the build outright."""
    for source in context_copies():
        pattern = excluded_from_the_build_context(source)

        assert pattern is None, f"the image copies {source}, dropped by {pattern!r}"


def test_every_top_level_directory_is_either_copied_or_left_out() -> None:
    """The hole the two above leave between them. Neither says anything about a
    directory nobody has written a line about, so `demo/` -- a video the size of the
    rest of this put together -- stayed out of the context because somebody remembered
    to name it, and the next directory like it is the same bet. This is the tripwire
    rather than a fix: everything here is accounted for today.

    Directories only, and the working tree's rather than the tracked set's, since asking
    git means spawning it from a test that has no business doing so. A stray directory
    failing here is the right answer -- it really would be uploaded whole -- while a
    stray file is a few kilobytes and is what the documented commands leave behind
    (`--json score.json`, `> top.txt`), which is a failure on one machine and nowhere
    else.
    """
    copied = {source.strip("/").split("/")[0] for source in context_copies()}

    for entry in sorted(path.name for path in _ROOT.iterdir() if path.is_dir()):
        assert entry in copied or excluded_from_the_build_context(f"{entry}/"), (
            f"{entry}/ is uploaded whole with the build context: copy it in the "
            f"Dockerfile or name it in .dockerignore"
        )


# -- the two runners -----------------------------------------------------------

#: A runner label names its platform first: ``windows-latest``, ``ubuntu-latest``.
_PLATFORMS = ("windows", "ubuntu")

#: The events Windows is run for, and the only ones a job may name (ADR-0037).
_WEEKLY_EVENTS = frozenset({"schedule", "workflow_dispatch"})

#: ``os: ${{ fromJSON(<condition> && '<weekly>' || '<merge>') }}``, one line per job.
_MATRIX = re.compile(
    r"^\s+os: \$\{\{ fromJSON\((?P<condition>.+?)"
    r" && '(?P<weekly>\[.*?\])' \|\| '(?P<merge>\[.*?\])'\) \}\}$",
    re.M,
)


def ci_matrices() -> list[re.Match[str]]:
    """The runner matrix each job in ci.yml picks, as the halves it picks between."""
    found = list(_MATRIX.finditer(_CI.read_text(encoding="utf-8")))
    assert found, "no job in ci.yml names the runners it uses"
    return found


def runner_platforms(runners: str) -> set[str]:
    """The platforms one branch of a matrix expression names, off its JSON array."""
    return {label.split("-")[0] for label in json.loads(runners)}


def test_every_job_runs_on_windows_as_well_as_linux() -> None:
    """This project is written on Windows and its jobs run on Linux, so either one alone
    is a platform nobody checks against."""
    for matrix in ci_matrices():
        assert runner_platforms(matrix["weekly"]).issuperset(_PLATFORMS), matrix[0]


def test_no_job_holds_a_merge_up_for_windows() -> None:
    """The other half of ADR-0037: a push and a pull request are gated on Linux alone."""
    for matrix in ci_matrices():
        assert runner_platforms(matrix["merge"]) == {"ubuntu"}, matrix[0]


def test_windows_runs_on_the_schedule_and_on_a_manual_run() -> None:
    """Weekly, and on demand: a manual run is how a branch that touched a path, an
    encoding or a socket asks for Windows before it is merged rather than after."""
    triggers = re.search(r"^on:\n(?:[ -].*\n|\n)*", _CI.read_text(encoding="utf-8"), re.M)
    assert triggers, "ci.yml says nothing about when it runs"

    for matrix in ci_matrices():
        named = set(re.findall(r"github\.event_name == '(\w+)'", matrix["condition"]))

        assert named == _WEEKLY_EVENTS, matrix[0]
        for event in named:
            assert re.search(rf"^  {event}:", triggers.group(0), re.M), event


def test_the_windows_run_is_scheduled_for_saturdays() -> None:
    """The day docs/testing.md and CLAUDE.md both name, and the day the mutation run
    already uses -- cron counts from Sunday, so Saturday is 6. Off the hour, where
    scheduled runs do not queue behind everyone else's, and off the mutation run's
    own minute so the two weekly jobs are not waiting on the same runners."""
    minute, hour, day_of_month, month, day_of_week = cron(_CI)

    assert (day_of_week, day_of_month, month) == ("6", "*", "*")
    assert minute != "0", "the top of the hour is where scheduled runs queue"
    assert (hour, minute) != cron(_MUTATION)[1::-1], "both weekly runs at one time"


def test_ci_sets_up_one_python_and_one_node() -> None:
    """The Dockerfile, `scripts/start.ps1` and docs/testing.md all pin themselves to the
    version ci.yml sets up, and each reads it as the one this file names."""
    source = _CI.read_text(encoding="utf-8")

    for key in ("python-version", "node-version"):
        assert source.count(f"{key}: ") == 1, f"ci.yml sets up more than one {key}"


def workflows() -> list[Path]:
    """Every workflow in ``.github/workflows``, found rather than listed."""
    found = sorted((_ROOT / ".github" / "workflows").glob("*.yml"))
    assert found, "no workflows; this section has outlived its rule"
    return found


@pytest.mark.parametrize("workflow", workflows(), ids=lambda path: path.stem)
def test_every_workflow_sets_up_the_python_the_tests_run_on(workflow: Path) -> None:
    """One Python across the repository, checked per workflow rather than per rule."""
    for version in re.findall(
        r'^\s+python-version: "([^"]+)"', workflow.read_text(encoding="utf-8"), re.M
    ):
        assert version == ci_version("python-version"), workflow.name


@pytest.mark.parametrize("workflow", workflows(), ids=lambda path: path.stem)
def test_every_workflow_builds_the_ui_with_the_node_ci_builds_it_with(workflow: Path) -> None:
    """And the same rule for the other toolchain, which a second workflow now
    builds with: `.github/workflows/release.yml` packages the build it ships, so
    on another Node it would publish an app assembled by a compiler nothing has
    tested against -- and the Angular CLI refuses an older one outright, which is
    a red release rather than a quiet difference only after somebody notices."""
    for version in re.findall(
        r'^\s+node-version: "([^"]+)"', workflow.read_text(encoding="utf-8"), re.M
    ):
        assert version == ci_version("node-version"), workflow.name


#: ``pip install [--flag ...] -r <file>``, wherever a workflow runs one.
_PIP_INSTALL = re.compile(r"pip install (?:-[-\w]+ )*-r (\S+)")


def requirements_installed_by(workflow: Path) -> set[str]:
    """Every requirements file a workflow hands pip, and every file those include: pip
    reads an ``-r`` line whether or not the workflow wrote that name down."""
    found: set[str] = set()
    pending = _PIP_INSTALL.findall(workflow.read_text(encoding="utf-8"))
    while pending:
        name = pending.pop()
        if name in found:
            continue
        found.add(name)
        pending += [
            line.removeprefix("-r").strip()
            for line in (_ROOT / name).read_text(encoding="utf-8").splitlines()
            if line.startswith("-r ")
        ]
    return found


def cache_key_files(workflow: Path) -> set[str]:
    """Every path the workflow's ``cache-dependency-path`` settings name, written as one
    value or as a block of them."""
    lines = workflow.read_text(encoding="utf-8").splitlines()
    named: set[str] = set()

    for index, line in enumerate(lines):
        setting = line.strip()
        if not setting.startswith("cache-dependency-path:"):
            continue
        value = setting.removeprefix("cache-dependency-path:").strip()
        if value != "|":
            named.add(value)
            continue
        indent = len(line) - len(line.lstrip())
        for entry in lines[index + 1 :]:
            if not entry.strip() or len(entry) - len(entry.lstrip()) <= indent:
                break
            named.add(entry.strip())
    return named


@pytest.mark.parametrize("workflow", workflows(), ids=lambda path: path.stem)
def test_every_pip_cache_is_keyed_on_every_file_it_installs_from(workflow: Path) -> None:
    """A key naming fewer files than the job installs is a cache that goes on being
    restored after one of the others has moved -- so it silently stops covering that
    step, which is the case Renovate makes: it bumps one of these files at a time. Not
    a correctness problem either way, pip resolving against the files themselves.

    Per workflow rather than per job, which is as far as reading this without a YAML
    parser goes: a key in one job naming another job's file would pass. Both keys are
    on the same step as the toolchain they cache for, so that is a shape nothing here
    is close to."""
    installed = requirements_installed_by(workflow)
    keyed = cache_key_files(workflow)

    if "cache: pip" in workflow.read_text(encoding="utf-8"):
        assert installed and keyed, f"{workflow.name} caches pip and neither reads"
    assert not installed - keyed, (
        f"{workflow.name} installs {sorted(installed - keyed)} and keys its pip cache "
        f"on {sorted(keyed)}"
    )


@pytest.mark.parametrize("workflow", workflows(), ids=lambda path: path.stem)
def test_every_workflow_starts_from_a_read_only_token(workflow: Path) -> None:
    """A workflow that declares nothing is handed whatever the repository's default is,
    which is a setting nothing in here can see and which grants write on plenty of
    repositories."""
    source = workflow.read_text(encoding="utf-8")
    match = re.search(r"^permissions:\n((?:  \w[^\n]*\n)+)", source, re.M)
    assert match, f"{workflow.name} declares no top-level permissions"

    assert match.group(1).strip() == "contents: read", (
        f"{workflow.name}'s floor is not read-only; widen a job, not the workflow"
    )


def action_versions(workflow: Path) -> dict[str, str]:
    """The ref each ``uses: owner/action@ref`` step of a workflow pins."""
    steps = re.findall(r"uses: (\S+?)@(\S+)$", workflow.read_text(encoding="utf-8"), re.M)
    versions: dict[str, str] = {}

    for action, ref in steps:
        assert versions.setdefault(action, ref) == ref, f"{workflow.name} pins two {action}"
    return versions


def test_every_workflow_pins_the_same_version_of_a_shared_action() -> None:
    """`actions/checkout` and `actions/setup-python` are used by all three workflows, and
    an update that reached only one of them is invisible: each file is valid on its own
    and every job goes green."""
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
    """It ends by launching a browser at a literal URL."""
    match = re.search(r"^\$url = '(\S+)'$", start_script(), re.M)
    assert match, "the startup script names no URL to open"

    assert match.group(1) == f"http://{server_default('host')}:{server_default('port')}"


def test_the_startup_script_asks_python_for_the_model_and_the_server() -> None:
    """An ``AgentConfig`` already answers to $BUY_AGENT_PROVIDER, $OLLAMA_MODEL,
    $OLLAMA_HOST, $VLLM_MODEL and $VLLM_HOST, so a tag or a URL copied into the script
    is a second default that goes stale silently: the script would pull one model and
    the run would ask for another, or it would wait on a server nothing intends to use."""
    source = start_script()

    assert "from buy_agent.config import AgentConfig" in source
    for server in PROVIDERS.values():
        for value in (server.model, server.base_url):
            assert value not in source, f"{value} is the provider table's to say"


def test_the_startup_script_looks_for_the_build_the_server_serves() -> None:
    """It skips the Angular build when one is already there, and the server answers with a
    503 telling you to build the UI when ``DEFAULT_UI_DIR`` is empty."""
    match = re.search(r"^\$built = Join-Path \$root '(\S+)'$", start_script(), re.M)
    assert match, "the startup script probes nothing before rebuilding the UI"

    probed = PurePosixPath(match.group(1).replace("\\", "/"))
    assert probed.parent.as_posix() == DEFAULT_UI_DIR.relative_to(_ROOT).as_posix()
    assert probed.name == "index.html", "a build is a directory; a page is what proves it"


def test_the_startup_script_names_the_toolchains_ci_pins() -> None:
    """The two things it will not install, it says where to get -- and a version named
    there is a fourth copy of what ci.yml sets up, the Dockerfile pins and
    docs/testing.md quotes."""
    source = start_script()

    assert f"Python {ci_version('python-version')}" in source
    assert f"Node {ci_version('node-version')}" in source


#: The flags the startup script adds to every pip command of its own and
#: ``mandates.INSTALL`` does not: one quietens a step whose output is noise
#: beside the rest of the console, the other a message about pip itself.
_PIP_NOISE = {"--quiet", "--disable-pip-version-check"}


def script_pip_installs() -> list[list[str]]:
    """Every ``pip install`` the startup script runs, as its own argument list."""
    blocks = re.findall(r"Run \$python @\((.*?)\)", start_script(), re.S)
    quoted = [re.findall(r"'([^']*)'", block) for block in blocks]
    return [
        [word for word in call[3:] if word not in _PIP_NOISE]
        for call in quoted
        if call[:3] == ["-m", "pip", "install"]
    ]


def test_the_startup_script_installs_the_ap2_sdk_the_way_mandates_says_to() -> None:
    """``mandates.INSTALL`` is the one line this project tells anybody to type when paying
    will not import, and it is two commands rather than one because ``--no-deps`` is
    not a per-line option: applied to the file naming the SDK it skips a pydantic pin
    that collides with this project's, and applied to the file naming what the SDK
    imports it leaves ``cryptography`` without ``cffi`` and nothing able to sign."""
    wanted = [command.split()[2:] for command in mandates_module.INSTALL.split(" && ")]
    ran = script_pip_installs()

    assert wanted, "mandates.INSTALL no longer names anything to install"
    for command in wanted:
        assert command in ran, f"the startup script never runs: pip install {' '.join(command)}"


def test_the_startup_script_asks_python_whether_paying_is_available() -> None:
    """``mandates.available()`` is what both front doors ask before offering to pay, so it
    is what the script reports too."""
    assert "from buy_agent.mandates import available" in start_script()


def environment_settings_the_package_reads() -> set[str]:
    """Every ``$BUY_AGENT_*`` the package actually reads: those named where they are
    read, plus the two ``mandates`` holds as constants because a secret's variable is
    quoted in the sentence about it as often as it is read."""
    named = {
        name
        for module in sorted(_PACKAGE.glob("*.py"))
        for name in re.findall(
            r'os\.(?:getenv|environ(?:\.get)?)\(\s*"(BUY_AGENT_[A-Z0-9_]+)"',
            module.read_text(encoding="utf-8"),
        )
    }
    return named | {mandates_module.KEY_PATH, mandates_module.MANDATE_PATH}


def test_the_startup_script_names_settings_that_exist() -> None:
    """It reads the environment for one thing only -- whether this machine means to
    pay, which is what decides the optional install -- and a variable misspelled
    there is not an error: it is a run that quietly decides paying was not wanted,
    every time, with the console saying to set the name it is already set to."""
    named = set(re.findall(r"\$env:(BUY_AGENT_[A-Z0-9_]+)", start_script()))

    assert named, "the startup script names no setting of this project's at all"
    assert named <= environment_settings_the_package_reads()


# -- the session hook ----------------------------------------------------------

_HOOK = _ROOT / ".claude" / "hooks" / "session-start.sh"


def session_hook() -> str:
    return _HOOK.read_text(encoding="utf-8")


def hook_pip_installs() -> list[list[str]]:
    """Every ``pip install`` the session hook runs, as its own argument list."""
    return [
        [
            word.replace('"', "").replace("$root/", "")
            for word in call.split()
            if word not in _PIP_NOISE
        ]
        for call in re.findall(r"-m pip install ([^;>]+)", session_hook())
    ]


def test_the_session_hook_installs_the_ap2_sdk_the_way_mandates_says_to() -> None:
    """The rule `scripts/start.ps1` is held to, for the same reason and one shell over."""
    wanted = [command.split()[2:] for command in mandates_module.INSTALL.split(" && ")]
    ran = hook_pip_installs()

    assert wanted, "mandates.INSTALL no longer names anything to install"
    for command in wanted:
        assert command in ran, f"the session hook never runs: pip install {' '.join(command)}"


def test_the_session_hook_installs_requirements_files_that_are_there() -> None:
    """Every file it hands pip, including the dev requirements the suite itself needs."""
    named = [
        word
        for call in hook_pip_installs()
        for previous, word in zip(call, call[1:])
        if previous == "-r"
    ]

    assert "requirements-dev.txt" in named, "the session hook installs no dev requirements"
    for name in named:
        assert (_ROOT / name).is_file(), f"the session hook installs a file that is not there: {name}"


def test_the_session_hook_reads_both_toolchain_pins_out_of_ci() -> None:
    """`ci.yml` is the one pin the Dockerfile, the startup script and docs/testing.md
    already chase, and a hook writing either version down again is a fifth copy -- one
    that quietly sets a session up on a toolchain no job has run."""
    source = session_hook()

    for key in ("node-version", "python-version"):
        assert key in source, f"the session hook never reads {key} out of ci.yml"
        assert ci_version(key) not in source, (
            f"{ci_version(key)} is ci.yml's to say, not the session hook's"
        )


# -- the nightly integration run -----------------------------------------------

#: The five minutes `.github/workflows/integration.yml` gives itself, which
#: docs/testing.md, the README and CLAUDE.md all quote.
_NIGHTLY_BUDGET_MINUTES = 5

#: Where the live tests live.
_LIVE_TESTS = Path(integration.__file__).resolve().parent


def integration_workflow() -> str:
    return _INTEGRATION.read_text(encoding="utf-8")


def test_a_normal_run_cannot_collect_the_tests_that_need_ollama() -> None:
    """The whole reason ``integration/`` is a directory and not a marker."""
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
    """Two names for one model, in a workflow and in a package that never import each
    other."""
    assert re.search(
        rf"^\s+run: ollama pull {re.escape(TINY_MODEL)}$", integration_workflow(), re.M
    )


def test_the_nightly_run_refuses_to_pass_by_skipping() -> None:
    """A live test whose model is absent skips, which is right on a developer's machine
    and worthless on a schedule: an Ollama that failed to install would give a green
    nightly job that checked nothing at all."""
    assert re.search(rf'^\s+{REQUIRE_ENV_VAR}: "1"$', integration_workflow(), re.M)


def test_the_nightly_run_is_nightly_and_capped() -> None:
    """Every day, off the hour, and bounded."""
    minute, _hour, day_of_month, month, day_of_week = cron(_INTEGRATION)

    assert (day_of_week, day_of_month, month) == ("*", "*", "*")
    assert minute != "0", "the top of the hour is where scheduled runs queue"
    assert re.search(
        rf"^\s+timeout-minutes: {_NIGHTLY_BUDGET_MINUTES}$", integration_workflow(), re.M
    )


def test_a_stopped_model_fails_a_live_test_before_it_fails_the_job() -> None:
    """The live tests' own cap, held against the two numbers it sits between."""
    unit_cap = int(ini_values(_PYTEST_INI, "pytest", "timeout")[0])

    assert unit_cap < LIVE_TIMEOUT_SECONDS < _NIGHTLY_BUDGET_MINUTES * 60


def test_the_nightly_run_is_never_a_gate_on_a_pull_request() -> None:
    """Like the mutation run and for the same reason: it takes minutes where the suite
    takes seconds, and it depends on a third party's install script and a model tag
    that can be re-pulled under it."""
    for workflow in (_INTEGRATION, _MUTATION):
        assert "pull_request" not in workflow.read_text(encoding="utf-8"), workflow.name


# -- the release ---------------------------------------------------------------

_RELEASE = _ROOT / ".github" / "workflows" / "release.yml"


def release_workflow() -> str:
    return _RELEASE.read_text(encoding="utf-8")


def test_the_release_archive_carries_the_built_ui_where_the_server_looks() -> None:
    """The `Dockerfile`'s agreement again, in a third place and with a slower failure: an
    archive whose UI landed anywhere else unpacks perfectly, installs perfectly, and
    serves the 503 that says to build a UI its downloader has no Node to build."""
    match = re.search(r"^\s+UI_DIST: (\S+)$", release_workflow(), re.M)
    assert match, "the release workflow names no destination for the UI build"

    assert match.group(1) == DEFAULT_UI_DIR.relative_to(_ROOT).as_posix()


def test_the_release_packages_the_tag_it_uploads_to() -> None:
    """Both jobs check out ``$TAG`` -- the release's own tag, or the one a manual re-run
    names -- and not the branch the workflow file happens to sit on."""
    source = release_workflow()
    checkouts = len(re.findall(r"^\s+- uses: actions/checkout@", source, re.M))

    assert checkouts, "the release workflow checks nothing out"
    assert source.count("ref: ${{ env.TAG }}") == checkouts


# -- the suites themselves -----------------------------------------------------

#: Both suites, as directories: everything under them is a module pytest imports
#: whole, which is what the rule below is about.
_UNIT_TESTS = _ROOT / "tests"


def shadowed_names(source: Path) -> list[str]:
    """Every top-level name ``source`` binds twice, the second hiding the first."""
    tree = ast.parse(source.read_text(encoding="utf-8"))
    defined: list[str] = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    return sorted({name for name in defined if defined.count(name) > 1})


def test_no_test_is_hidden_by_another_of_the_same_name() -> None:
    """A test module is imported like any other, so a second ``def`` of a name replaces
    the first and pytest collects only what is left."""
    for suite in (_UNIT_TESTS, _LIVE_TESTS):
        for module in sorted(suite.rglob("*.py")):
            shadowed = shadowed_names(module)
            assert not shadowed, f"{module.relative_to(_ROOT)} defines {shadowed} twice"


#: The one test module allowed to sleep, and the reason the rule below is not
#: "nobody sleeps": three server tests need a run to still be going while a
#: second request arrives, which is a wall clock and nothing else.
_MAY_SLEEP = "test_server.py"


def suite_modules() -> list[Path]:
    """Every module of both Python suites, found rather than listed."""
    found = sorted(
        module for suite in (_UNIT_TESTS, _LIVE_TESTS) for module in suite.rglob("*.py")
    )
    assert found, "no test modules; this section has outlived its rules"
    return found


def attribute_calls(tree: ast.AST, receiver: str, methods: set[str]) -> list[ast.Call]:
    """Every ``receiver.method(...)`` in one parsed module."""
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in methods
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == receiver
    ]


def test_no_test_is_switched_off_where_nobody_will_look() -> None:
    """A skip that asks no question is a test nobody is running and nobody is told about."""
    for module in suite_modules():
        tree = ast.parse(module.read_text(encoding="utf-8"))
        off = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr in {"skip", "xfail"}
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "mark"
        ]
        assert not off, (
            f"{module.relative_to(_ROOT)}:{off[0]} switches a test off outright; "
            "skipif names what is missing instead"
        )


def test_nothing_in_the_suite_sleeps_but_the_one_that_has_to() -> None:
    """The suite takes about eight seconds and every second of that is somebody's wait."""
    for module in suite_modules():
        tree = ast.parse(module.read_text(encoding="utf-8"))
        slept = [call.lineno for call in attribute_calls(tree, "time", {"sleep"})]
        assert not slept or module.name == _MAY_SLEEP, (
            f"{module.relative_to(_ROOT)}:{slept[0]} sleeps; only {_MAY_SLEEP} may"
        )


def names_the_environment(node: ast.AST) -> bool:
    """Whether ``node`` is ``os.environ`` under either of its two spellings."""
    return (isinstance(node, ast.Attribute) and node.attr == "environ") or (
        isinstance(node, ast.Name) and node.id == "environ"
    )


def environment_writes(tree: ast.AST) -> list[int]:
    """Every line of one module that changes the process environment itself."""
    changed = {"pop", "update", "clear", "setdefault"}
    lines: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and (
                (node.func.attr in changed and names_the_environment(node.func.value))
                or node.func.attr in {"putenv", "unsetenv"}
            )
        ):
            lines.append(node.lineno)
        targets = (
            node.targets
            if isinstance(node, (ast.Assign, ast.Delete))
            else [node.target]
            if isinstance(node, ast.AugAssign)
            else []
        )
        lines += [
            node.lineno
            for target in targets
            if isinstance(target, ast.Subscript) and names_the_environment(target.value)
        ]
    return sorted(lines)


def test_a_test_changes_the_environment_through_the_fixture_that_undoes_it() -> None:
    """``os.environ`` is one dictionary for the whole process, so a test that writes it
    directly writes it for every test after it."""
    for module in suite_modules():
        tree = ast.parse(module.read_text(encoding="utf-8"))
        written = environment_writes(tree)
        assert not written, (
            f"{module.relative_to(_ROOT)}:{written[0]} writes the environment itself; "
            "monkeypatch is what puts it back"
        )

# -- the Saturday mutation run -------------------------------------------------

# mutmut copies these two into the tree it tests without being asked, as it does
# the package it is mutating; everything else the suite reaches for has to be
# named in ``also_copy``.
_COPIED_ANYWAY = ("tests", "setup.cfg")


def test_the_mutation_run_mutates_what_coverage_measures() -> None:
    """Coverage says which lines ran; mutation testing says whether anything would have
    noticed had they run differently."""
    assert ini_values(_MUTMUT, "mutmut", "source_paths") == ini_values(_COVERAGERC, "run", "source")


def test_the_mutation_run_is_scheduled_for_saturdays() -> None:
    """Weekly and off the hour, as docs/testing.md and CLAUDE.md both say: cron counts
    days from Sunday, so Saturday is 6, and a run that quietly moved to another
    day would leave the two of them describing a schedule that is not this one."""
    minute, _hour, day_of_month, month, day_of_week = cron(_MUTATION)

    assert (day_of_week, day_of_month, month) == ("6", "*", "*")
    assert minute != "0", "the top of the hour is where scheduled runs queue"


#: The one test module that reloads a module of the package under test.
_RELOADS = _ROOT / "tests" / "test_providers.py"


def names_imported_from(source: Path, module: str) -> list[str]:
    """Every name ``source`` binds at import time out of ``module``."""
    tree = ast.parse(source.read_text(encoding="utf-8"))
    return [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == module
        for alias in node.names
    ]


def test_the_module_that_reloads_providers_binds_nothing_a_reload_replaces() -> None:
    """The suite must not care what order it runs in, and this is the one place it could."""
    imported = names_imported_from(_RELOADS, "buy_agent.providers")

    assert imported, "the module no longer imports from providers; this rule has moved"
    for name in imported:
        attribute = getattr(providers_module, name)
        assert inspect.isfunction(attribute), (
            f"{name} is imported by name into the module that reloads providers, and a"
            " reload replaces it -- read it off the module instead"
        )


def files_read() -> list[Path]:
    """The paths these tests open, off the ``_NAME`` constants declared above."""
    return [
        value
        for name, value in globals().items()
        if name.startswith("_")
        and isinstance(value, Path)
        and value != _ROOT
        and value.is_relative_to(_ROOT)
    ]


def files_imported() -> list[Path]:
    """Every module the suite has imported from a file in this repository."""
    mutated = [_ROOT / name for name in ini_values(_MUTMUT, "mutmut", "source_paths")]
    files = (getattr(module, "__file__", None) for module in list(sys.modules.values()))
    return [
        path
        for path in map(Path, filter(None, files))
        if path.is_relative_to(_ROOT)
        and "site-packages" not in path.parts
        and not any(path.is_relative_to(package) for package in mutated)
    ]


def files_named_at_the_root() -> list[Path]:
    """Every file at the top of the repository whose name these tests say out loud."""
    at_the_root = {path.name: path for path in _ROOT.iterdir() if path.is_file()}
    return [
        at_the_root[node.value]
        for path in sorted((_ROOT / "tests").glob("*.py"))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Constant) and node.value in at_the_root
    ]


def files_the_skills_name() -> list[Path]:
    """Every path a skill in `.claude/skills` points at, as this suite resolves it."""
    return [
        _ROOT / token
        for skill in skills()
        for token in paths_a_skill_names(skill)
        if "/" in token
    ]


def test_a_mutation_run_copies_everything_the_tests_reach_for() -> None:
    """A mutation run tests a copy of the tree under mutants/, and this suite both reads
    files rather than importing them and imports from outside the package being
    mutated."""
    also_copy = (
        ini_values(_MUTMUT, "mutmut", "also_copy")
        + list(_COPIED_ANYWAY)
        # The package being mutated is the one thing a run cannot be missing.
        + ini_values(_MUTMUT, "mutmut", "source_paths")
    )
    copied = [_ROOT / name for name in also_copy]
    needed = files_read() + files_imported() + files_named_at_the_root() + files_the_skills_name()

    assert needed, "the suite reads and imports nothing; this test has outlived its rule"
    for path in needed:
        assert any(path.is_relative_to(destination) for destination in copied), path


# -- paying --------------------------------------------------------------------


def test_every_rail_is_offered_everywhere_it_can_be_asked_for() -> None:
    """The same three doors onto one registry the providers have, for the same
    reason: a rail missing from one of them is one the other two will happily
    hand to a config that then refuses it."""
    names = set(RAILS)
    cli = {action.dest: action for action in build_parser()._actions}["rail"]

    assert set(RAIL_OPTIONS) == names
    assert set(cli.choices) == names
    assert {option["name"] for option in defaults_payload()["rail_options"]} == names


def test_a_rail_option_is_mirrored_field_for_field_in_typescript() -> None:
    """The form reads these to fill the address field in and to say whether
    anybody is about to be charged, so a key added in Python and forgotten here
    is an undefined deciding that sentence."""
    assert set(ts_interface("RailOption")) == set(rail_options()[0])


_PAYABLE = payable_product()


@needs_ap2
def test_a_receipt_is_mirrored_field_for_field_in_typescript() -> None:
    """The card draws what came of a payment, so a field added in Python and
    forgotten here is an undefined on a receipt."""
    receipt = pay_now(
        {
            "products": [_PAYABLE.model_dump()],
            "approved": {"title": _PAYABLE.name, "price": _PAYABLE.price, "currency": "USD"},
        }
    )["receipt"]

    assert set(ts_interface("Receipt")) == set(receipt)


def test_every_key_a_payment_reads_is_one_the_page_sends() -> None:
    """``PayOptions`` is what the browser posts; a key ``pay_now`` reads and the page
    never sends is a refusal for a box that is not there (ADR-0033)."""
    sent = set(ts_interface("PayOptions")) | set(ts_interface("SearchOptions"))

    assert _keys_read_by(pay_now) <= sent


def test_the_payment_failures_the_cli_catches_are_the_ones_the_api_maps() -> None:
    """A payment fails at its own door, not the run's -- so these are their own table
    rather than three more rows in ``_STATUS``."""
    # One name on the CLI side, because every payment failure is a
    # ``PaymentError`` -- ``RailUnreachableError`` is a subclass, which is the
    # whole point of it: the API can answer 502 for the one failure that is
    # nothing to do with the request, and the CLI still catches both.
    assert _payment_handlers() == {"PaymentError"}
    assert all(issubclass(kind, PaymentError) for kind in PAY_STATUS)


def test_every_payment_failure_has_a_status_that_is_not_a_server_error() -> None:
    """A 5xx in the 500 sense means "we crashed"; these are understood failures."""
    assert set(PAY_STATUS.values()) <= {400, 409, 502, 503}


def test_the_payment_statuses_are_ordered_subclass_first() -> None:
    """``pay_now`` takes the first row an exception is an instance of, so a
    parent listed above its subclass would swallow it."""
    kinds = list(PAY_STATUS)
    for index, kind in enumerate(kinds):
        assert not any(issubclass(kind, earlier) for earlier in kinds[:index]), (
            f"{kind.__name__} sits below a parent that would match it first"
        )


def _payment_handlers() -> set[str]:
    """What ``main`` catches around the payment, read off the source."""
    tree = ast.parse((_PACKAGE / "__main__.py").read_text(encoding="utf-8"))
    bought = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_bought"
    )
    caught: set[str] = set()
    for guard in (node for node in ast.walk(bought) if isinstance(node, ast.Try)):
        for handler in guard.handlers:
            named = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
            caught.update(node.id for node in named if isinstance(node, ast.Name))
    return caught


def test_only_the_mandates_module_imports_the_ap2_sdk() -> None:
    """The seam rule this project applies to a model server and a search backend, applied
    to the protocol: everything above `mandates` deals in carts and receipts."""
    importers = {
        path.name
        for path in _PACKAGE.glob("*.py")
        if re.search(r"^\s*(from ap2|import ap2)", path.read_text(encoding="utf-8"), re.M)
    }

    assert importers == {"mandates.py"}


def test_the_payment_settings_are_offered_at_both_doors() -> None:
    """The rule `.claude/skills/add-option` writes down, checked for the four settings
    paying adds: a flag, a request key, and a default the form is seeded from."""
    flags = {action.dest for action in build_parser()._actions}
    defaults = defaults_payload()

    for field, key in (("pay", "pay"), ("rail", "rail"), ("merchant_url", "merchant_url"),
                       ("spend_limit", "spend_limit")):
        assert field in flags, f"--{field.replace('_', '-')} is missing from the CLI"
        assert key in defaults, f"{key} is missing from the form's defaults"
        assert key in ts_interface("SearchOptions"), f"{key} is missing from SearchOptions"


# -- the skills ----------------------------------------------------------------

_SKILLS = _ROOT / ".claude" / "skills"

#: What a path in a skill's prose looks like: a directory, or a file of a kind
#: this project has. Anything else in backticks is an identifier or a command.
_PATH_SUFFIXES = (
    ".md", ".py", ".ts", ".html", ".css", ".json", ".yml", ".cfg", ".ini",
    ".ps1", ".mjs", ".png", ".txt", ".sh",
)

#: Placeholders a skill writes into a path it is telling somebody to create:
#: `docs/adr/NNNN-slug.md` names no file and is not meant to.
_PLACEHOLDERS = ("NNNN", "<", "*", "[")


def skills() -> list[Path]:
    """Every skill in `.claude/skills`, found rather than listed."""
    found = sorted(_SKILLS.glob("*/SKILL.md"))
    assert found, "no skills; this section has outlived its rule"
    return found


def skill_body(path: Path) -> str:
    """A skill's prose, with its fenced code blocks removed."""
    return re.sub(r"^```.*?^```", "", path.read_text(encoding="utf-8"), flags=re.M | re.S)


def frontmatter(path: Path) -> dict[str, str]:
    """The `key: value` lines of a skill's YAML header."""
    header = re.match(r"---\n(.*?)\n---\n", path.read_text(encoding="utf-8"), re.S)
    assert header, f"{path} has no frontmatter"
    return dict(
        re.findall(r"^([a-z-]+): (.+)$", header.group(1), re.M),
    )


def quoted(text: str) -> list[str]:
    """Everything in backticks, which is how these files name a thing in the code."""
    return re.findall(r"`([^`\n]+)`", text)


def paths_a_skill_names(path: Path) -> list[str]:
    """Every file and directory a skill points at, written the way it writes it."""
    return [
        token
        for token in quoted(skill_body(path))
        if " " not in token
        and not any(mark in token for mark in _PLACEHOLDERS)
        and (token.endswith("/") or token.endswith(_PATH_SUFFIXES))
    ]


#: Directories a walk of this repository has no business entering: a virtual environment
#: and an npm tree are somebody else's files, and the other three are this project's own
#: output.
_NOT_THE_REPOSITORY = frozenset(
    {".git", ".venv", "node_modules", "mutants", "__pycache__", ".angular", "dist"}
)


@cache
def repo_filenames() -> frozenset[str]:
    """The name of every file this repository keeps, for a skill that names one
    without its directory (`api.py`, `agent.ts`) because the section it sits in
    already said where."""
    found: set[str] = set()
    for _, subdirectories, files in os.walk(_ROOT):
        subdirectories[:] = [name for name in subdirectories if name not in _NOT_THE_REPOSITORY]
        found.update(files)
    return frozenset(found)


@cache
def suite_test_names() -> frozenset[str]:
    """Every test the Python suite defines, by name."""
    found = frozenset(
        name
        for path in (_ROOT / "tests").glob("test_*.py")
        for name in re.findall(r"^def (test_\w+)", path.read_text(encoding="utf-8"), re.M)
    )
    assert found, "no tests found; this section cannot check what a skill names"
    return found


#: What a skill's prose is read against when it names something that is not a file: the
#: source this repository keeps, minus the trees ``_NOT_THE_REPOSITORY`` prunes.
_CODE_SUFFIXES = (".py", ".ts", ".html", ".json", ".yml", ".cfg", ".ini", ".ps1", ".mjs", ".sh")


@cache
def names_in_the_code() -> frozenset[str]:
    """Every word this project's own source spells."""
    found: set[str] = set()
    for directory, subdirectories, files in os.walk(SOURCE_ROOT):
        subdirectories[:] = [name for name in subdirectories if name not in _NOT_THE_REPOSITORY]
        for name in files:
            if name.endswith(_CODE_SUFFIXES):
                text = (Path(directory) / name).read_text(encoding="utf-8")
                found.update(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text))
    assert found, "no source found; this section cannot check what a skill names"
    return frozenset(found)


def names_a_skill_names(path: Path) -> list[str]:
    """Every function, field and table a skill points at, as it writes them."""
    return [
        token.removesuffix("()")
        for token in quoted(skill_body(path))
        if re.fullmatch(r"_?[A-Za-z][A-Za-z0-9_]*(\(\))?", token)
    ]


@pytest.mark.parametrize("path", skills(), ids=lambda path: path.parent.name)
def test_every_skill_is_named_after_the_directory_it_is_in(path: Path) -> None:
    """A skill is invoked by the name in its frontmatter and edited by its path, so a
    mismatch is a file somebody corrects while the thing that runs goes on saying what
    it said."""
    header = frontmatter(path)

    assert header.get("name") == path.parent.name, path
    assert len(header.get("description", "")) > 40, f"{path} barely describes itself"


@pytest.mark.parametrize("path", skills(), ids=lambda path: path.parent.name)
def test_every_file_a_skill_names_exists(path: Path) -> None:
    """A skill is a checklist over files, and a renamed file turns one step of it into a
    search for something that is not there."""
    names = repo_filenames()

    for token in paths_a_skill_names(path):
        if "/" in token:
            assert (_ROOT / token).exists(), f"{path.parent.name} names {token}, which is gone"
        else:
            assert token in names, f"{path.parent.name} names {token}, which is gone"


@pytest.mark.parametrize("path", skills(), ids=lambda path: path.parent.name)
def test_every_test_a_skill_names_exists(path: Path) -> None:
    """The step a skill ends on is usually "and this test will fail if you skipped it"."""
    body = skill_body(path)
    names = suite_test_names()

    # Not a module: `tests/test_conventions.py` is a path, checked as one above.
    for named in re.findall(r"\btest_[a-z_]+\b(?!\.)", body):
        assert named in names, f"{path.parent.name} names {named}, which no longer exists"
    for selector in re.findall(r"-k (\w+)", body):
        assert any(selector in name for name in names), (
            f"{path.parent.name} selects tests with -k {selector}, which now matches none"
        )


@pytest.mark.parametrize("path", skills(), ids=lambda path: path.parent.name)
def test_every_name_a_skill_names_is_one_the_code_spells(path: Path) -> None:
    """A step reads "edit this function", and the reader greps for it."""
    spelled = names_in_the_code()

    for name in names_a_skill_names(path):
        assert name in spelled, f"{path.parent.name} names {name}, which the code does not"


@pytest.mark.parametrize("path", skills(), ids=lambda path: path.parent.name)
def test_every_record_a_skill_points_at_exists(path: Path) -> None:
    """A skill cites a record for the argument behind a step it is asking for, the
    way a record cites another -- and a number that resolves to nothing is worse
    here than there, since a reader following it is mid-change."""
    numbers = {record.stem[:4] for record in adr_files()}

    cited = set(re.findall(r"ADR-(\d{4})", path.read_text(encoding="utf-8")))
    assert cited <= numbers, (
        f"{path.parent.name} cites {sorted(cited - numbers)}, which no record answers to"
    )


def test_the_option_skill_names_the_tables_a_new_setting_joins() -> None:
    """The form declares one table of number boxes and one of remembered settings, and
    `.claude/skills/add-option` is a walk through both."""
    form = _FORM_TS.read_text(encoding="utf-8")
    body = skill_body(_SKILLS / "add-option" / "SKILL.md")

    declarations = (r"readonly (\w+): NumberField\[\]", r"readonly (\w+): Record<string, Setting>")
    for pattern in declarations:
        match = re.search(pattern, form)
        assert match, f"the form no longer declares {pattern}; this rule has moved"
        assert f"`{match.group(1)}`" in body, (
            f"add-option does not name the form's {match.group(1)} table"
        )


def test_the_preflight_skill_runs_what_ci_runs() -> None:
    """`.claude/skills/preflight` claims to be the CI gate, locally."""
    steps = re.findall(
        r"^      - name: (.+)\n        run: (.+)$", _CI.read_text(encoding="utf-8"), re.M
    )
    checks = [command for name, command in steps if not name.startswith("Install")]
    written = (_SKILLS / "preflight" / "SKILL.md").read_text(encoding="utf-8")

    assert checks, "no checking step in ci.yml; this test has outlived its rule"
    for command in checks:
        assert command in written, f"preflight does not run `{command}`, which ci.yml does"


def test_the_preflight_skill_names_the_toolchains_ci_pins() -> None:
    """It says which Python and which Node it is the gate for, and a version named
    there is another copy of ci.yml's pin -- the one the startup script, the
    Dockerfile and docs/testing.md already chase."""
    written = (_SKILLS / "preflight" / "SKILL.md").read_text(encoding="utf-8")

    assert f"Python {ci_version('python-version')}" in written
    assert f"Node {ci_version('node-version')}" in written


def test_every_skill_is_one_the_project_documents() -> None:
    """CLAUDE.md introduces `.claude/skills/` by naming what is in it, which is where
    anybody reads about them before there is a reason to invoke one."""
    described = (_ROOT / "CLAUDE.md").read_text(encoding="utf-8")

    for path in skills():
        assert f"`{path.parent.name}`" in described, f"CLAUDE.md does not name {path.parent.name}"


#: How CLAUDE.md counts its own conventions: a number spelt out, in the heading over them.
_NUMBER_WORDS = {
    "Ten": 10, "Eleven": 11, "Twelve": 12, "Thirteen": 13, "Fourteen": 14,
    "Fifteen": 15, "Sixteen": 16, "Seventeen": 17, "Eighteen": 18, "Nineteen": 19,
    "Twenty": 20,
}


def test_the_conventions_heading_counts_the_conventions_under_it() -> None:
    """CLAUDE.md numbers that section in its heading, and the number is the one thing
    there no reader can check without counting."""
    written = (_ROOT / "CLAUDE.md").read_text(encoding="utf-8")

    heading = re.search(r"^### (\w+) conventions$", written, re.M)
    assert heading, "CLAUDE.md no longer heads its conventions with a count"
    claimed = _NUMBER_WORDS.get(heading.group(1))
    assert claimed, f"{heading.group(1)!r} is not a number this can read; add it above"

    section = written[heading.end() : written.index("### Failures", heading.end())]
    assert len(re.findall(r"^- \*\*", section, re.M)) == claimed, (
        f"CLAUDE.md heads that section {heading.group(1)!r} and lists another number"
    )


# -- what the run says, and where it says it -----------------------------------

#: The methods a logger answers to.
_LEVELS = ("debug", "info", "warning", "error", "exception", "critical", "warn")

#: The one whose message is its *second* argument, the first being the level.
_LEVEL_FIRST = "log"


def package_modules() -> list[Path]:
    """Every module in the package, found rather than listed."""
    found = sorted(_PACKAGE.glob("*.py"))
    assert found, "no package modules; this section has outlived its rule"
    return found


@cache
def module_tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def logging_calls(path: Path) -> list[ast.Call]:
    """Every ``logger.<level>(...)`` in one module."""
    return [
        node
        for node in ast.walk(module_tree(path))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in (*_LEVELS, _LEVEL_FIRST)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "logger"
    ]


def module_logger(path: Path) -> ast.Call | None:
    """What a module's own ``logger = ...`` was got from, if it has one."""
    for node in module_tree(path).body:
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "logger"
                for target in node.targets
            )
            and isinstance(node.value, ast.Call)
        ):
            return node.value
    return None


def names_attribute(node: ast.AST, attribute: str) -> bool:
    return any(
        isinstance(child, ast.Attribute) and child.attr == attribute
        for child in ast.walk(node)
    )


@pytest.mark.parametrize("path", package_modules(), ids=lambda path: path.name)
def test_every_module_logs_under_the_package_name(path: Path) -> None:
    """Every line a run writes has to reach the ``buy_agent`` logger."""
    got_from = module_logger(path)
    if got_from is None:
        assert not logging_calls(path), f"{path.name} logs without a logger of its own"
        return

    assert names_attribute(got_from, "getLogger"), f"{path.name}'s logger is not one"
    named = got_from.args[0] if got_from.args else None
    assert (isinstance(named, ast.Name) and named.id == "__name__") or (
        isinstance(named, ast.Constant) and named.value == "buy_agent"
    ), f"{path.name} names its logger something outside the package"


@pytest.mark.parametrize("path", package_modules(), ids=lambda path: path.name)
def test_every_log_line_leaves_its_formatting_to_the_logger(path: Path) -> None:
    """A message is a format string with its arguments beside it, never an f-string."""
    for call in logging_calls(path):
        assert call.args, f"{path.name}:{call.lineno} logs nothing"
        message = call.args[1] if call.func.attr == _LEVEL_FIRST else call.args[0]
        if isinstance(message, ast.Name) and any(
            isinstance(argument, ast.Starred) for argument in call.args
        ):
            # A message passed on with its own ``*args`` behind it -- which is
            # ``_report``, the one function whose whole job is to hand a caller's line to
            # the logger with the mark on it.
            continue
        assert isinstance(message, ast.Constant) and isinstance(message.value, str), (
            f"{path.name}:{call.lineno} formats its own message"
        )


@pytest.mark.parametrize("path", package_modules(), ids=lambda path: path.name)
def test_nothing_but_the_report_handler_writes_to_stdout(path: Path) -> None:
    """stdout is the report's, and a ``> top.txt`` catches whatever else lands there."""
    for node in ast.walk(module_tree(path)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "print", f"{path.name}:{node.lineno} prints"
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "stdout"
            and isinstance(node.value, ast.Name)
            and node.value.id == "sys"
        ):
            assert path.name == "logging_setup.py", (
                f"{path.name}:{node.lineno} names the stream the report is redirected off"
            )


@pytest.mark.parametrize("path", package_modules(), ids=lambda path: path.name)
def test_only_the_report_marks_a_record_as_the_report(path: Path) -> None:
    """``extra={"report": True}`` is what sends a line to stdout instead of stderr."""
    for call in logging_calls(path):
        marked = [keyword for keyword in call.keywords if keyword.arg == "extra"]
        assert not marked or path.name == "logging_setup.py", (
            f"{path.name}:{call.lineno} marks a record itself"
        )


@pytest.mark.parametrize("path", package_modules(), ids=lambda path: path.name)
def test_only_the_logging_module_configures_logging(path: Path) -> None:
    """One function decides how loud a process is, and it is not a library's call."""
    if path.name == "logging_setup.py":
        return

    tree = module_tree(path)
    assert not names_attribute(tree, "basicConfig"), f"{path.name} configures logging"


@pytest.mark.parametrize("entry_point", ["__main__.py", "server.py"])
def test_every_entry_point_wires_its_verbose_flag_to_the_level(entry_point: str) -> None:
    """``-v`` is a flag on two parsers and a level in one function, and the wiring between
    them is a line each that nothing else would miss."""
    tree = module_tree(_PACKAGE / entry_point)
    main = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    configured = [
        node
        for node in ast.walk(main)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "configure_logging"
    ]

    assert configured, f"{entry_point} never configures logging"
    assert any(
        keyword.arg == "verbose" and names_attribute(keyword.value, "verbose")
        for call in configured
        for keyword in call.keywords
    ), f"{entry_point} has a --verbose flag that changes nothing"


# -- what a failure is called, and how a run ends ------------------------------


def declared_classes() -> dict[str, tuple[str, ...]]:
    """Every class the package declares, against the names it derives from."""
    declared: dict[str, tuple[str, ...]] = {}
    for path in package_modules():
        for node in ast.walk(module_tree(path)):
            if isinstance(node, ast.ClassDef):
                declared[node.name] = tuple(
                    base.id for base in node.bases if isinstance(base, ast.Name)
                )
    return declared


def raisable(
    name: str, declared: dict[str, tuple[str, ...]], seen: tuple[str, ...] = ()
) -> bool:
    """Whether ``name`` reaches ``BaseException`` through what the package declares."""
    builtin = getattr(builtins, name, None)
    if isinstance(builtin, type) and issubclass(builtin, BaseException):
        return True
    if name in seen or name not in declared:
        return False
    return any(raisable(base, declared, (*seen, name)) for base in declared[name])


def test_every_type_named_for_a_failure_is_one() -> None:
    """A name ending in ``Error`` is this package's one promise about a type made in the
    name itself: that it can be raised."""
    declared = declared_classes()
    misnamed = [
        name
        for name in declared
        if name.endswith("Error") and not raisable(name, declared)
    ]

    assert not misnamed, f"named for a failure without being one: {misnamed}"


def names_a_stop(raised: ast.expr | None) -> bool:
    """Whether a ``raise`` names ``SystemExit``, called or bare."""
    named = raised.func if isinstance(raised, ast.Call) else raised
    return isinstance(named, ast.Name) and named.id == "SystemExit"


def guarded_lines(tree: ast.Module) -> range | None:
    """The lines of ``if __name__ == "__main__":``, if a module has that guard."""
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name)
            and node.test.left.id == "__name__"
        ):
            return range(node.lineno, (node.body[-1].end_lineno or node.lineno) + 1)
    return None


@pytest.mark.parametrize("path", package_modules(), ids=lambda path: path.name)
def test_nothing_but_the_guard_ends_the_process(path: Path) -> None:
    """A run answers with a code; it does not spend one."""
    tree = module_tree(path)
    guard = guarded_lines(tree)
    ending = [
        node.lineno
        for node in ast.walk(tree)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"exit", "_exit"}
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in {"sys", "os"}
        )
        or (isinstance(node, ast.Raise) and names_a_stop(node.exc))
    ]

    for line in ending:
        assert guard is not None and line in guard, (
            f"{path.name}:{line} ends the process outside the __main__ guard"
        )


# -- the linter ----------------------------------------------------------------

#: ``python -m pylint <target>``, wherever a workflow or a skill runs it.
_PYLINT_RUN = re.compile(r"python -m pylint ([\w/ .-]+)")

#: A suppression written into the package: ``# pylint: disable=`` or its
#: ``disable-next`` form, whatever it goes on to name.
_SUPPRESSION = re.compile(r"^\s*#\s*pylint:\s*disable(-next)?=")

#: A pylint message named by its code -- ``W0718`` -- rather than by its name.
_MESSAGE_CODE = re.compile(r"^[CEFIRW]\d{4},?$")


def test_the_linter_checks_what_coverage_measures() -> None:
    """Three tools now read the same package, and each says so in its own file:
    `.coveragerc` measures it, `setup.cfg` mutates it, and `ci.yml` lints it."""
    linted = _PYLINT_RUN.findall(_CI.read_text(encoding="utf-8"))

    assert linted, "ci.yml no longer runs pylint; this rule has outlived it"
    for target in linted:
        assert target.split() == ini_values(_COVERAGERC, "run", "source")


def test_the_linter_is_configured_where_it_is_run_from() -> None:
    """pylint reads `.pylintrc` out of the working directory, and every command that runs
    it here runs from the repository root -- so the file has to be at the root and not
    beside the package."""
    assert _PYLINTRC.exists(), "the linter's settings are gone; ci.yml still runs it"

    settings = ini_values(_PYLINTRC, "MESSAGES CONTROL", "disable")
    assert settings, "nothing is turned off, so the reasons for it went with the file"


def test_the_linter_is_configured_in_the_spelling_it_demands() -> None:
    """`use-symbolic-message-instead` is on, so every pragma in the package names its
    check as a name and never as a code."""
    for key in ("enable", "disable"):
        for message in ini_values(_PYLINTRC, "MESSAGES CONTROL", key):
            assert not _MESSAGE_CODE.match(message), (
                f".pylintrc {key}s {message}, which says nothing about what it is"
            )


@pytest.mark.parametrize("path", package_modules(), ids=lambda path: path.name)
def test_every_suppression_says_why(path: Path) -> None:
    """The rule every heuristic in this package already follows, applied to the linter's
    own: a suppression takes a check away, so it says what for."""
    lines = path.read_text(encoding="utf-8").splitlines()

    for number, line in enumerate(lines):
        if not _SUPPRESSION.match(line):
            continue
        above = lines[number - 1].strip() if number else ""
        assert above.startswith("#") and not _SUPPRESSION.match(above), (
            f"{path.name}:{number + 1} suppresses a check and says nothing about why"
        )


#: Flags this project's own messages may name whatever door they arrive at, because they
#: are not this CLI's: ``--max-model-len`` and ``--api-key`` are typed at ``vllm serve``,
#: ``--no-deps`` at ``pip``.
_OTHER_PROGRAMS_FLAGS = frozenset({"--max-model-len", "--api-key", "--no-deps"})

#: The two modules handed an ``argv``, and so the two allowed to name the flags
#: they parse. Everything below them is read at *both* doors.
_ARGV_MODULES = frozenset({"__main__.py", "server.py"})

_FLAG = re.compile(r"--[a-z][a-z0-9-]*")


def _cli_flags() -> set[str]:
    """Every flag the two parsers offer, read off the parsers rather than listed."""
    flags: set[str] = set()
    for parser in (build_parser(), build_server_parser()):
        for action in parser._actions:  # pylint: disable=protected-access
            flags.update(option for option in action.option_strings if option.startswith("--"))
    return flags - _OTHER_PROGRAMS_FLAGS


def _spoken_strings(tree: ast.Module) -> list[ast.Constant]:
    """Every string literal in a module that is not a docstring."""
    docstrings = {
        ast.get_docstring(node, clean=False)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    spoken = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value not in docstrings:
                spoken.append(node)
    return spoken


def test_no_sentence_below_the_two_doors_tells_a_reader_to_type_a_flag() -> None:
    """A hint the browser shows must not name a command line it does not have."""
    flags = _cli_flags()
    assert "--num-ctx" in flags and "--merchant-url" in flags, "the parsers were read"

    for module in package_modules():
        if module.name in _ARGV_MODULES:
            continue
        for node in _spoken_strings(module_tree(module)):
            named = sorted(set(_FLAG.findall(node.value)) & flags)
            assert not named, (
                f"{module.name}:{node.lineno} tells the reader to type {named[0]}, "
                f"which the browser has no command line for; name the setting instead"
            )


def test_every_sort_criterion_has_an_ordering_the_report_can_name() -> None:
    """A fourth criterion needs a phrase, or the report raises a ``KeyError`` on it."""
    assert set(ORDERINGS) == set(get_args(SortBy)), (
        "every criterion --sort-by offers has a phrase, and no phrase names one it "
        "does not"
    )
    for criterion, phrase in ORDERINGS.items():
        assert "first" in phrase, (
            f"{criterion!r} reads {phrase!r}; a criterion names a direction, since "
            '"by price" does not say cheapest from dearest'
        )


#: Flags whose default is the absence of the flag rather than a value: a switch is off
#: until it is given, and a repeatable one collects nothing until it is.
def _takes_a_value(action: argparse.Action) -> bool:
    return action.nargs != 0 and not isinstance(action, argparse._AppendAction)


@pytest.mark.parametrize(
    "parser",
    [pytest.param(build_parser(), id="cli"), pytest.param(build_server_parser(), id="server")],
)
def test_every_flag_that_takes_a_value_names_the_default_it_has(
    parser: argparse.ArgumentParser,
) -> None:
    """``--help`` is the CLI's only documentation, so a default left out is a fact with
    nowhere else to be read."""
    for action in parser._actions:  # pylint: disable=protected-access
        if not action.option_strings or isinstance(action, argparse._HelpAction):
            continue
        if not _takes_a_value(action) or action.default is None:
            continue
        assert "default" in (action.help or ""), (
            f"{action.option_strings[0]} takes a value and defaults to "
            f"{action.default!r} without saying so in its help"
        )


@pytest.mark.parametrize(
    "parser",
    [pytest.param(build_parser(), id="cli"), pytest.param(build_server_parser(), id="server")],
)
def test_every_switch_that_is_already_on_names_that_default(
    parser: argparse.ArgumentParser,
) -> None:
    """A switch that is off until it is given needs no default written down -- leaving
    it off is what turns it off. One that is *on* is the other thing: ``--no-fetch`` is
    the only spelling of it, and a reader who has never seen the flag has nothing to
    tell them the pages are read unless they say otherwise."""
    for action in parser._actions:  # pylint: disable=protected-access
        if not action.option_strings or isinstance(action, argparse._HelpAction):
            continue
        if _takes_a_value(action) or action.default is not True:
            continue
        assert "default" in (action.help or ""), (
            f"{action.option_strings[0]} is on unless it is given and does not say "
            f"so in its help"
        )


#: How wide argparse wraps its own help to on a terminal that does not say: the
#: formatter takes ``shutil.get_terminal_size().columns`` and subtracts two, and 80 is
#: what that falls back to.
_HELP_WIDTH = 78


@pytest.mark.parametrize(
    "parser",
    [pytest.param(build_parser(), id="cli"), pytest.param(build_server_parser(), id="server")],
)
def test_the_help_text_argparse_prints_raw_is_wrapped_by_hand(
    parser: argparse.ArgumentParser,
) -> None:
    """``RawDescriptionHelpFormatter`` is how the exit codes keep their own layout, and
    the price of it is that every other line in those two blocks is printed exactly as
    written. A sentence left as one long line is the one part of ``--help`` a terminal
    breaks mid-word, which is why this is held here rather than remembered."""
    for block, text in (("description", parser.description), ("epilog", parser.epilog)):
        for line in (text or "").splitlines():
            assert len(line) <= _HELP_WIDTH, (
                f"{parser.prog}'s {block} has a {len(line)}-character line, which "
                f"argparse prints as written: wrap it at {_HELP_WIDTH}"
            )
