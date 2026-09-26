"""``.claude/hooks/session-start.sh``: which Python it builds ``.venv`` from.

The rest of the hook is installing, and what it installs is a rule between it and
``ci.yml`` that ``tests/test_conventions.py`` reads off the source. This is the one
thing in it that decides something, so it is lifted out and run: its two functions
in bash, against a PATH of stand-ins that each say which version they are and
nothing else. Bash on a POSIX system, because that is the only place the hook runs
-- a web session's Linux container -- and a stand-in is a ``#!/bin/sh`` script.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_HOOK = _ROOT / ".claude" / "hooks" / "session-start.sh"

_BASH = shutil.which("bash")

#: What the function needs from outside bash, which is all a PATH of stand-ins holds
#: besides them -- a real ``/usr/bin`` would bring the machine's own interpreters in.
_TOOLS = ("sort", "head")

needs_posix_bash = pytest.mark.skipif(
    os.name == "nt" or _BASH is None or not all(map(shutil.which, _TOOLS)),
    reason="the session hook runs under bash in a Linux container, and nowhere else",
)


def hook_functions() -> str:
    """``at_least`` and ``newest_python``, as the hook defines them."""
    source = _HOOK.read_text(encoding="utf-8")
    compare = re.search(r"^at_least\(\) \{.*\}$", source, re.M)
    newest = re.search(r"^newest_python\(\) \{\n.*?^\}$", source, re.M | re.S)
    assert compare and newest, "the session hook no longer defines what this runs"
    return f"{compare.group(0)}\n{newest.group(0)}\n"


def newest(tmp_path: Path, installed: dict[str, str]) -> str:
    """What ``newest_python`` answers on a PATH of ``.venv/bin``, ``local`` and
    ``usr``, in that order, holding ``installed`` -- each a path under ``tmp_path``
    and the ``version releaselevel`` that stand-in prints."""
    tools = tmp_path / "tools"
    tools.mkdir()
    for tool in _TOOLS:
        (tools / tool).symlink_to(shutil.which(tool) or tool)
    for where, answer in installed.items():
        stand_in = tmp_path / where
        stand_in.parent.mkdir(parents=True, exist_ok=True)
        stand_in.write_text(f"#!/bin/sh\necho '{answer}'\n", encoding="utf-8")
        stand_in.chmod(0o755)

    searched = [tmp_path / ".venv" / "bin", tmp_path / "local", tmp_path / "usr", tools]
    script = f"{hook_functions()}venv={shlex.quote(str(tmp_path / '.venv'))}\nnewest_python\n"
    finished = subprocess.run(
        [_BASH or "bash", "-c", script],
        env={"PATH": os.pathsep.join(map(str, searched))},
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return finished.stdout.strip().replace(f"{tmp_path}/", "")


@needs_posix_bash
@pytest.mark.parametrize(
    ("installed", "chosen"),
    [
        pytest.param(
            {
                "usr/python3": "3.11.15 final",
                "usr/python3.11": "3.11.15 final",
                "usr/python3.12": "3.12.3 final",
                "usr/python3.13": "3.13.12 final",
                # Not an interpreter, whatever it prints.
                "usr/python3.13-config": "9.9.9 final",
            },
            "usr/python3.13 3.13.12",
            id="the image as it ships, python3 naming the oldest",
        ),
        pytest.param(
            {"usr/python3.13": "3.13.12 final", "local/python3.99": "3.99.0 candidate"},
            "usr/python3.13 3.13.12",
            id="a final release over a newer release candidate",
        ),
        pytest.param(
            {"local/python3.99": "3.99.0 candidate"},
            "local/python3.99 3.99.0",
            id="a release candidate where there is nothing else",
        ),
        pytest.param(
            {
                ".venv/bin/python3": "3.13.12 final",
                ".venv/bin/python3.99": "3.99.0 final",
                "usr/python3.13": "3.13.12 final",
            },
            "usr/python3.13 3.13.12",
            id="never the venv's own, first on PATH or newest",
        ),
        pytest.param(
            {"local/python3.13": "3.13.12 final", "usr/python3.13": "3.13.12 final"},
            "local/python3.13 3.13.12",
            id="the earlier on PATH between two of one version",
        ),
        pytest.param({}, "", id="nothing at all"),
    ],
)
def test_the_venv_is_built_from_the_newest_python_on_path(
    tmp_path: Path, installed: dict[str, str], chosen: str
) -> None:
    """``python3`` is 3.11 on the images a web session starts in, with a 3.12 and a 3.13
    beside it, and 3.11 cannot parse the suite: a hook that took ``python3`` built a
    venv no test could be collected in."""
    assert newest(tmp_path, installed) == chosen
