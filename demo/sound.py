"""Turn a recording's cues into the WAV track that goes under it.

``demo/record.mjs`` writes down *when* each thing happened -- a key pressed, the
button clicked, a progress line arriving, the results landing -- and this turns
that list into a waveform. Nothing is sampled, mixed or licensed from anywhere:
every sound here is a few sine waves under an envelope, which is why a recording
can be taken again on any machine and come out the same.

The point of it is not decoration. Six of the log lines in either demo are the
pipeline catching the fake model out (``demo/README.md`` has the table), and
they get a note of their own -- so the soundtrack says, without the viewer
reading the panel, which lines are the agent finding a mistake.

``python -m demo.sound --duration 13.4 --out track.wav`` reads the cues as JSON
on stdin: ``[{"at": 1.25, "kind": "key"}, ...]``, seconds from the first frame.
"""

from __future__ import annotations

import argparse
import array
import json
import math
import sys
import wave
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

#: CD rate, which is one of the handful MP2 is allowed to carry.
SAMPLE_RATE = 44_100

#: Peak the finished track is held under, leaving a little headroom so the MP2
#: encoder's own overshoot has somewhere to go.
PEAK = 0.82

#: What the mix is lifted by before it is limited. The voices below are written
#: at the level they sound right *against each other*; this is the one number
#: that says how loud the track is.
GAIN = 2.3

#: Equal temperament, for the notes the cues below are written in.
NOTES = {
    "G3": 196.00,
    "C4": 261.63,
    "G4": 392.00,
    "C5": 523.25,
    "E5": 659.25,
    "G5": 783.99,
    "C6": 1046.50,
}


class Voice:
    """One sound: partials under a plucked envelope, plus optional noise.

    ``decay`` is the time constant of the exponential tail rather than a length,
    so a voice is described by how fast it dies away and rendered until it is
    inaudible. ``attack`` keeps the onset off the sample boundary, without which
    every one of these would carry a click of its own.
    """

    def __init__(
        self,
        partials: Sequence[tuple[float, float]],
        *,
        amplitude: float,
        decay: float,
        attack: float = 0.004,
        noise: float = 0.0,
    ) -> None:
        self.partials = partials
        self.amplitude = amplitude
        self.decay = decay
        self.attack = attack
        self.noise = noise

    def render(self, buffer: array.array, at: float, seed: int) -> None:
        """Sum this voice into ``buffer``, starting at ``at`` seconds."""
        start = max(0, int(at * SAMPLE_RATE))
        # Six time constants is about -52 dB, which is under the noise floor of
        # anything this is going to be played back through.
        length = int(self.decay * 6 * SAMPLE_RATE)
        state = seed * 2_654_435_761 % 2**32 or 1
        for index in range(length):
            position = start + index
            if position >= len(buffer):
                return
            seconds = index / SAMPLE_RATE
            envelope = math.exp(-seconds / self.decay)
            if seconds < self.attack:
                envelope *= seconds / self.attack
            value = sum(
                weight * math.sin(2 * math.pi * frequency * seconds)
                for frequency, weight in self.partials
            )
            if self.noise:
                # xorshift, so the grain of a keystroke is the same on every
                # machine that takes this recording again.
                state ^= (state << 13) & 0xFFFFFFFF
                state ^= state >> 17
                state ^= (state << 5) & 0xFFFFFFFF
                value += self.noise * (state / 2**31 - 1)
            buffer[position] += self.amplitude * envelope * value


#: A key going down: mostly grain, gone in a few hundredths of a second. Quiet,
#: because there is one of these per character of the request.
KEY = Voice([(2100.0, 0.35)], amplitude=0.16, decay=0.011, attack=0.001, noise=0.9)

#: The button. Firmer and lower than a key, so the recording's one deliberate
#: press does not sound like another letter.
CLICK = Voice(
    [(880.0, 0.6), (1320.0, 0.25)], amplitude=0.40, decay=0.045, attack=0.002, noise=0.5
)

#: A step of the pipeline reporting in: one soft note, well under the others.
STEP = Voice([(NOTES["G5"], 0.7), (NOTES["G4"], 0.3)], amplitude=0.17, decay=0.10)

#: ...and a step that *took something away* -- a headline discarded, a figure
#: blanked, a quote dropped, a link refused, a duplicate merged. A fifth lower
#: and twice as long, which is the one thing in the track a viewer is meant to
#: learn to recognise.
CAUGHT = Voice(
    [(NOTES["C5"], 0.55), (NOTES["G3"], 0.3), (NOTES["E5"], 0.2)],
    amplitude=0.26,
    decay=0.22,
)

#: The run finishing: a major triad, spread so it reads as an arrival.
ARRIVAL = [
    (0.00, Voice([(NOTES["C5"], 0.6), (NOTES["C6"], 0.15)], amplitude=0.26, decay=0.35)),
    (0.11, Voice([(NOTES["E5"], 0.6)], amplitude=0.24, decay=0.38)),
    (0.22, Voice([(NOTES["G5"], 0.6), (NOTES["G4"], 0.2)], amplitude=0.28, decay=0.75)),
]

#: The results being read: a page settling, quieter than anything but a key.
SCROLL = Voice([(NOTES["C4"], 0.5), (NOTES["G4"], 0.2)], amplitude=0.09, decay=0.16)

#: What a cue's ``kind`` plays. ``arrival`` is the one that is three sounds, so
#: every value here is a list.
VOICES: dict[str, list[tuple[float, Voice]]] = {
    "key": [(0.0, KEY)],
    "click": [(0.0, CLICK)],
    "step": [(0.0, STEP)],
    "caught": [(0.0, CAUGHT)],
    "scroll": [(0.0, SCROLL)],
    "arrival": ARRIVAL,
}

#: The room the demo is in: a hum too quiet to hear on its own, there so the
#: gaps between cues are a recording rather than a dead channel.
ROOM_HZ = 58.0
ROOM_AMPLITUDE = 0.004

#: The least time between two notes for lines of the progress panel.
#:
#: Most of a run's log lines arrive in one instant -- the model answers and then
#: five heuristics report in the same millisecond -- and twenty notes struck
#: together are one loud chord that says nothing and swamps the rest of the
#: track. Spread, the same twenty read as the flurry they are, and each line
#: still sounds in the order it arrived.
LINE_GAP = 0.055

#: The kinds :data:`LINE_GAP` applies to. Keys are typed at their own pace and
#: the rest are a person clicking, so neither needs help.
LINE_KINDS = frozenset({"step", "caught"})

#: Fades at the two ends, so the file neither starts nor stops on a step.
EDGE_FADE = 0.25


def spread(cues: Iterable[dict]) -> list[dict]:
    """Push apart the line cues that arrived together, keeping their order.

    Only the panel's own lines move, by :data:`LINE_GAP` at a time, and only
    ever later -- a note is allowed to land after the thing it is about, never
    before it.
    """
    ordered = sorted(cues, key=lambda cue: float(cue["at"]))
    last = -1.0
    spaced = []
    for cue in ordered:
        at = float(cue["at"])
        if str(cue["kind"]) in LINE_KINDS:
            at = max(at, last + LINE_GAP)
            last = at
        spaced.append({"kind": cue["kind"], "at": at})
    return spaced


def render(cues: Iterable[dict], duration: float) -> array.array:
    """Mix ``cues`` into ``duration`` seconds of samples."""
    total = int(duration * SAMPLE_RATE)
    buffer = array.array("d", bytes(8 * total))
    for index in range(total):
        seconds = index / SAMPLE_RATE
        buffer[index] = ROOM_AMPLITUDE * (
            math.sin(2 * math.pi * ROOM_HZ * seconds)
            + 0.4 * math.sin(2 * math.pi * ROOM_HZ * 1.5 * seconds)
        )
    for seed, cue in enumerate(spread(cues)):
        for offset, voice in VOICES.get(str(cue["kind"]), []):
            voice.render(buffer, cue["at"] + offset, seed + 1)
    return buffer


def finish(buffer: array.array) -> array.array:
    """Lift the mix, limit it, and fade the two ends.

    Limited rather than normalised: scaling the whole track by its loudest
    moment would let one pile-up of notes decide how loud everything else is,
    which is exactly what a run's log lines arriving together produce. ``tanh``
    leaves anything well under :data:`PEAK` where it was written and bends only
    what would have clipped.
    """
    for index, sample in enumerate(buffer):
        buffer[index] = PEAK * math.tanh(GAIN * sample / PEAK)
    edge = int(EDGE_FADE * SAMPLE_RATE)
    for index in range(min(edge, len(buffer))):
        gain = index / edge
        buffer[index] *= gain
        buffer[-1 - index] *= gain
    return buffer


def write(buffer: array.array, path: Path) -> None:
    """Write ``buffer`` out as 16-bit mono PCM."""
    samples = array.array(
        "h", (max(-32768, min(32767, int(sample * 32767))) for sample in buffer)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(samples.tobytes())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="demo.sound",
        description="Synthesise the soundtrack for a recorded demo, from its cues.",
    )
    parser.add_argument(
        "--duration", type=float, required=True, help="Length of the video, in seconds."
    )
    parser.add_argument("--out", type=Path, required=True, help="WAV file to write.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cues = json.load(sys.stdin)
    write(finish(render(cues, args.duration)), args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
