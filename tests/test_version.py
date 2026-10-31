"""Which build is running: a release's stamp, else a checkout's commit (ADR-0083)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest

from buy_agent import version
from buy_agent.__main__ import build_parser
from buy_agent.api import defaults_payload
from buy_agent.server import BuyAgentHandler
from buy_agent.server import build_parser as build_server_parser
from buy_agent.version import DEVELOPMENT, STAMP, Version, checkout_commit, current, read_version
from tests.conftest import SOURCE_ROOT

if TYPE_CHECKING:
    import argparse
    from collections.abc import Callable
    from pathlib import Path

COMMIT = "0123456789abcdef0123456789abcdef01234567"
OTHER = "fedcba9876543210fedcba9876543210fedcba98"


def package_in(root: Path) -> Path:
    package = root / "buy_agent"
    package.mkdir()
    return package


def git_dir(root: Path, head: str) -> Path:
    git = root / ".git"
    git.mkdir()
    (git / "HEAD").write_text(head, encoding="utf-8")
    return git


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# -- the label ----------------------------------------------------------------


def test_a_release_names_its_number_and_the_commit_it_was_built_from() -> None:
    assert Version("1.2.0", COMMIT).label == "1.2.0+0123456789ab"


def test_a_build_with_no_commit_is_its_number_alone() -> None:
    assert Version(DEVELOPMENT).label == "dev"


# -- a release's stamp --------------------------------------------------------


def test_a_stamped_package_says_what_it_was_stamped_with(tmp_path: Path) -> None:
    package = package_in(tmp_path)
    # A checkout around it says something else, and the stamp wins.
    git_dir(tmp_path, f"{OTHER}\n")
    (package / STAMP).write_text(f"1.2.0\n{COMMIT}\n", encoding="utf-8")

    assert read_version(package) == Version("1.2.0", COMMIT)


@pytest.mark.parametrize(
    ("stamp", "expected"),
    [
        ("1.2.0\n", Version("1.2.0")),
        ("1.2.0", Version("1.2.0")),
        ("1.2.0\nnot-a-commit\n", Version("1.2.0")),
        # A build with no version argument: the Dockerfile's default.
        ("dev\n\n", Version(DEVELOPMENT)),
        ("\n\n", Version(DEVELOPMENT)),
        (f"  1.2.0  \r\n{COMMIT}\r\n", Version("1.2.0", COMMIT)),
    ],
)
def test_a_stamp_is_read_for_what_it_holds(tmp_path: Path, stamp: str, expected: Version) -> None:
    package = package_in(tmp_path)
    (package / STAMP).write_text(stamp, encoding="utf-8")

    assert read_version(package) == expected


def test_an_unreadable_stamp_counts_as_none(tmp_path: Path) -> None:
    package = package_in(tmp_path)
    (package / STAMP).write_bytes(b"\xff\xfe\x00")
    git_dir(tmp_path, f"{COMMIT}\n")

    assert read_version(package) == Version(DEVELOPMENT, COMMIT)


# -- a checkout ---------------------------------------------------------------


def test_a_package_outside_any_checkout_is_dev_alone(tmp_path: Path) -> None:
    assert read_version(package_in(tmp_path)) == Version(DEVELOPMENT)


def test_a_detached_head_is_its_own_commit(tmp_path: Path) -> None:
    package = package_in(tmp_path)
    git_dir(tmp_path, f"{COMMIT}\n")

    assert read_version(package) == Version(DEVELOPMENT, COMMIT)


def test_a_branch_is_the_commit_its_ref_names(tmp_path: Path) -> None:
    git = git_dir(tmp_path, "ref: refs/heads/main\n")
    write(git / "refs" / "heads" / "main", f"{COMMIT}\n")

    assert checkout_commit(git) == COMMIT


def test_a_packed_branch_is_read_off_packed_refs(tmp_path: Path) -> None:
    git = git_dir(tmp_path, "ref: refs/heads/main\n")
    write(
        git / "packed-refs",
        "# pack-refs with: peeled fully-peeled sorted\n"
        f"{OTHER} refs/heads/other\n"
        f"{COMMIT} refs/heads/main\n"
        f"^{OTHER}\n",
    )

    assert checkout_commit(git) == COMMIT


def test_a_loose_ref_wins_over_a_stale_packed_one(tmp_path: Path) -> None:
    git = git_dir(tmp_path, "ref: refs/heads/main\n")
    write(git / "refs" / "heads" / "main", f"{COMMIT}\n")
    write(git / "packed-refs", f"{OTHER} refs/heads/main\n")

    assert checkout_commit(git) == COMMIT


def test_a_worktree_follows_its_pointer_and_reads_the_shared_refs(tmp_path: Path) -> None:
    common = tmp_path / "main" / ".git"
    write(common / "refs" / "heads" / "feature", f"{COMMIT}\n")
    own = common / "worktrees" / "feature"
    write(own / "HEAD", "ref: refs/heads/feature\n")
    write(own / "commondir", "../..\n")
    checkout = tmp_path / "feature"
    write(checkout / ".git", f"gitdir: {own}\n")

    assert checkout_commit(checkout / ".git") == COMMIT


def test_a_submodule_s_relative_pointer_is_read_from_where_it_sits(tmp_path: Path) -> None:
    write(tmp_path / ".git" / "modules" / "sub" / "HEAD", f"{COMMIT}\n")
    write(tmp_path / "sub" / ".git", "gitdir: ../.git/modules/sub\n")

    assert checkout_commit(tmp_path / "sub" / ".git") == COMMIT


@pytest.mark.parametrize(
    "head",
    [
        "ref: refs/heads/unborn\n",  # a branch with no commit yet
        "not a commit\n",
        "",
    ],
)
def test_a_head_naming_no_commit_is_none(tmp_path: Path, head: str) -> None:
    git = git_dir(tmp_path, head)
    write(git / "packed-refs", f"not-a-commit refs/heads/unborn\n{OTHER}\n")

    assert checkout_commit(git) is None


def test_a_git_file_that_points_nowhere_is_none(tmp_path: Path) -> None:
    write(tmp_path / ".git", "something else\n")

    assert checkout_commit(tmp_path / ".git") is None


def test_a_git_directory_without_a_head_is_none(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()

    assert checkout_commit(tmp_path / ".git") is None


# -- where it is said ---------------------------------------------------------


def test_this_package_is_read_once(monkeypatch: pytest.MonkeyPatch) -> None:
    current.cache_clear()
    calls: list[object] = []

    def counted(package: object) -> Version:
        calls.append(package)
        return Version("9.9.9")

    monkeypatch.setattr(version, "read_version", counted)
    try:
        assert current() == current() == Version("9.9.9")
        assert len(calls) == 1
    finally:
        current.cache_clear()


@pytest.mark.parametrize(
    ("parser", "prog"), [(build_parser, "buy_agent"), (build_server_parser, "buy_agent.server")]
)
def test_both_entry_points_say_which_build_they_are(
    parser: Callable[[], argparse.ArgumentParser], prog: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as stopped:
        parser().parse_args(["--version"])

    assert stopped.value.code == 0
    assert capsys.readouterr().out == f"{prog} {current().label}\n"


def test_the_form_is_told_which_build_answered() -> None:
    assert defaults_payload()["version"] == current().label


def test_the_server_header_names_the_build() -> None:
    assert BuyAgentHandler.server_version == f"buy_agent/{current().label}"


# -- who writes the stamp -----------------------------------------------------

#: The one way a stamp is written: the number, then the commit, a line each.
_WRITTEN = re.compile(
    r"""printf '%s\\n%s\\n' "(?P<number>[^"]+)" "(?P<commit>[^"]+)" > buy_agent/(?P<stamp>\S+)"""
)


@pytest.mark.parametrize(
    ("path", "number", "commit"),
    [
        ("Dockerfile", "$VERSION", "$REVISION"),
        (".github/workflows/release.yml", "${TAG#v}", "$(git rev-parse HEAD)"),
    ],
)
def test_both_releases_write_the_stamp_this_module_reads(path: str, number: str, commit: str) -> None:
    """The archive and the image are stamped by shell, not by this module, so the two
    lines that write it are held to the name and the order it reads them in."""
    written = _WRITTEN.search((SOURCE_ROOT / path).read_text(encoding="utf-8"))

    assert written, f"{path} writes no stamp"
    assert (written["number"], written["commit"], written["stamp"]) == (number, commit, STAMP)


def test_a_stamp_is_never_committed() -> None:
    """A checkout says ``dev``; a stamp in git would make every checkout a release."""
    ignored = (SOURCE_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert f"buy_agent/{STAMP}" in ignored
