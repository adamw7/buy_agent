"""Speak one line of a recording's narration into a WAV, and say how long it runs.

    echo "This is the benchmark page." | python -m demo.narration --out line.wav

The recorder asks for each line at the moment it is about to be said, and waits as
long as the answer says before doing the next thing, so the picture never gets ahead
of the voice. The voice is the best text-to-speech engine installed. Kokoro (the
``kokoro-onnx`` package and its two model files) is a neural voice that sounds like a
person reading; without it, SVOX Pico (``pico2wave``, in Debian and Ubuntu's
``libttspico-utils``), the least robotic of the older ones, else ``espeak-ng``.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
import wave
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

#: Where Kokoro's model and voices are looked for; ``$KOKORO_DIR`` moves it.
KOKORO_DIR = Path(os.environ.get("KOKORO_DIR") or Path.home() / ".cache" / "kokoro-onnx")
KOKORO_FILES = ("kokoro-v1.0.onnx", "voices-v1.0.bin")
#: Kokoro's warmest American voice, and a touch slower than its default reading pace.
KOKORO_VOICE = os.environ.get("KOKORO_VOICE") or "af_heart"
KOKORO_SPEED = 0.95

#: The older engines, best first, as the command that writes ``{out}`` saying ``{text}``.
ENGINES: dict[str, list[str]] = {
    "pico2wave": ["pico2wave", "-l", "en-US", "-w", "{out}", "{text}"],
    "espeak-ng": ["espeak-ng", "-v", "en-us", "-s", "160", "-w", "{out}", "{text}"],
}

INSTALL = (
    "No text-to-speech engine found: pip install kokoro-onnx soundfile and put "
    f"{' and '.join(KOKORO_FILES)} in {KOKORO_DIR} (from github.com/thewh1teagle/"
    "kokoro-onnx/releases), or install pico2wave (libttspico-utils) or espeak-ng."
)


def has_kokoro() -> bool:
    """Whether Kokoro and both of its model files are here."""
    if not all(importlib.util.find_spec(name) for name in ("kokoro_onnx", "soundfile")):
        return False
    return all((KOKORO_DIR / name).is_file() for name in KOKORO_FILES)


def engine() -> str:
    """The best engine available.

    Raises:
        SystemExit: when there is none, naming what to install.
    """
    if has_kokoro():
        return "kokoro"
    for name in ENGINES:
        if shutil.which(name):
            return name
    raise SystemExit(INSTALL)


def kokoro(text: str, out: Path) -> None:
    """Say ``text`` into ``out`` with Kokoro, as 16-bit PCM."""
    # Imported here so the older engines need neither package.
    # pylint: disable=import-outside-toplevel
    import soundfile
    from kokoro_onnx import Kokoro

    model, voices = (str(KOKORO_DIR / name) for name in KOKORO_FILES)
    samples, rate = Kokoro(model, voices).create(
        text, voice=KOKORO_VOICE, speed=KOKORO_SPEED, lang="en-us"
    )
    soundfile.write(str(out), samples, rate, subtype="PCM_16")


def speak(text: str, out: Path) -> float:
    """Say ``text`` into ``out``; how many seconds it runs for."""
    out.parent.mkdir(parents=True, exist_ok=True)
    name = engine()
    if name == "kokoro":
        kokoro(text, out)
    else:
        command = [part.format(out=out, text=text) for part in ENGINES[name]]
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
