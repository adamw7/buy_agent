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

The decision log has the same shape: ``docs/adr/README.md`` indexes records that
live in files beside it, so an unindexed decision -- or an index row still quoting
a title the record has since changed -- is invisible to every other test here.

These tests read the declarations themselves rather than exercising behaviour,
which is the only way to check that two lists agree about what is *not* in them.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path
from typing import get_args

import pytest

from buy_agent.__main__ import build_parser
from buy_agent.agent import BuyAgent
from buy_agent.api import (
    SORT_OPTIONS,
    _STATUS,
    ApiError,
    defaults_payload,
    product_payload,
    run_search,
)
from buy_agent.config import AgentConfig
from buy_agent.models import Product, RankedProduct
from buy_agent.ranking import SortBy

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
    """
    tree = ast.parse((_ROOT / "buy_agent" / "__main__.py").read_text(encoding="utf-8"))
    main = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    caught: set[str] = set()
    for handler in (n for n in ast.walk(main) if isinstance(n, ast.ExceptHandler)):
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
