"""Which build of this package is running, so a bug report can say (ADR-0083).

A release writes its version and commit into :data:`STAMP` beside this module: the
archive's job in ``release.yml`` and the image's ``Dockerfile``, from build arguments.
The file is git-ignored, so a checkout has none and reads ``dev`` plus the commit its
``.git`` names, if it has one. Nothing here spawns git: the commit is read off the files
git keeps, and anything unreadable leaves the commit out rather than failing a run.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path
from typing import NamedTuple

#: The release stamp: the version on its first line and the commit on its second.
STAMP = "_version.txt"

#: What a build that was never released calls itself.
DEVELOPMENT = "dev"

#: How much of a commit the label carries, as ``git log --abbrev=12`` would print it.
SHORT = 12

#: A commit as git writes one into a ref: forty hexadecimal digits.
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


class Version(NamedTuple):
    """A release number (or :data:`DEVELOPMENT`) and the commit it was built from."""

    number: str
    commit: str | None = None

    @property
    def label(self) -> str:
        """``1.2.0+0123456789ab``, ``dev+0123456789ab`` or ``dev``: semver's build
        metadata, with no spaces, so it fits a ``Server`` header as it is."""
        commit = self.commit or ""
        return f"{self.number}+{commit[:SHORT]}" if commit else self.number


def read_version(package: Path) -> Version:
    """The version a package directory was stamped with, else the checkout's."""
    stamped = _read(package / STAMP)
    if stamped is not None:
        number, _, commit = stamped.partition("\n")
        commit = commit.strip()
        return Version(number.strip() or DEVELOPMENT, commit if _COMMIT.match(commit) else None)
    return Version(DEVELOPMENT, checkout_commit(package.parent / ".git"))


def checkout_commit(git: Path) -> str | None:
    """The commit ``HEAD`` names in the repository at ``git``, or None.

    ``git`` is a directory, or -- in a worktree or a submodule -- a file saying where the
    directory is, whose shared refs sit wherever its ``commondir`` points."""
    if git.is_file():
        pointer = _read(git) or ""
        if not pointer.startswith("gitdir:"):
            return None
        git = (git.parent / pointer.removeprefix("gitdir:").strip()).resolve()
    head = _read(git / "HEAD")
    if head is None:
        return None
    head = head.strip()
    if not head.startswith("ref:"):
        return head if _COMMIT.match(head) else None
    ref = head.removeprefix("ref:").strip()
    common = _read(git / "commondir")
    for directory in (git, *([(git / common.strip()).resolve()] if common else [])):
        commit = _ref(directory, ref)
        if commit is not None:
            return commit
    return None


def _ref(git: Path, ref: str) -> str | None:
    """The commit ``ref`` names in one git directory: its own file, else packed."""
    loose = (_read(git / ref) or "").strip()
    if _COMMIT.match(loose):
        return loose
    for line in (_read(git / "packed-refs") or "").splitlines():
        commit, _, name = line.partition(" ")
        if name.strip() == ref and _COMMIT.match(commit):
            return commit
    return None


def _read(path: Path) -> str | None:
    """A small text file's contents, or None for one that is missing or unreadable."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


@cache
def current() -> Version:
    """This package's version, read once per process."""
    return read_version(Path(__file__).resolve().parent)
