"""Speak one line of a recording's narration into a WAV, and say how long it runs.

    echo "This is the benchmark page." | python -m demo.narration --out line.wav

The recorder asks for each line at the moment it is about to be said, and waits as
long as the answer says before doing the next thing, so the picture never gets ahead
of the voice. The voice is whichever text-to-speech engine is installed: SVOX Pico
(``pico2wave``, in Debian and Ubuntu's ``libttspico-utils``) if it is, since it is the
least robotic of the free ones, else ``espeak-ng``.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import wave
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

#: Each engine, best first, as the command that writes ``{out}`` saying ``{text}``.
ENGINES: dict[str, list[str]] = {
    "pico2wave": ["pico2wave", "-l", "en-US", "-w", "{out}", "{text}"],
    "espeak-ng": ["espeak-ng", "-v", "en-us", "-s", "160", "-w", "{out}", "{text}"],
}


def engine() -> str:
    """The best engine on PATH.

    Raises:
        SystemExit: when there is none, naming what to install.
    """
    for name in ENGINES:
        if shutil.which(name):
            return name
    raise SystemExit(
        "No text-to-speech engine found: install pico2wave (libttspico-utils) or espeak-ng."
    )


def speak(text: str, out: Path) -> float:
    """Say ``text`` into ``out``; how many seconds it runs for."""
    out.parent.mkdir(parents=True, exist_ok=True)
    command = [part.format(out=out, text=text) for part in ENGINES[engine()]]
    subprocess.run(command, check=True, capture_output=True)
    with wave.open(str(out), "rb") as clip:
        return clip.getnframes() / clip.getframerate()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="demo.narration",
        description="Speak one line of narration, read from stdin, into a WAV.",
    )
    parser.add_argument("--out", type=Path, required=True, help="WAV file to write.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    text = " ".join(sys.stdin.read().split())
    print(f"{speak(text, args.out):.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
