"""Turn a recording's cues into the WAV track that goes under it.

A cue of kind ``say`` carries a ``clip``: a spoken line ``demo.narration`` wrote. It is
laid over everything else, which is turned down while it plays.
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

#: What the mix is lifted by before it is limited.
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
    """One sound: partials under a plucked envelope, plus optional noise."""

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


#: A key going down: mostly grain, gone in a few hundredths of a second.
KEY = Voice([(2100.0, 0.35)], amplitude=0.16, decay=0.011, attack=0.001, noise=0.9)

#: The button.
CLICK = Voice(
    [(880.0, 0.6), (1320.0, 0.25)], amplitude=0.40, decay=0.045, attack=0.002, noise=0.5
)

#: A step of the pipeline reporting in: one soft note, well under the others.
STEP = Voice([(NOTES["G5"], 0.7), (NOTES["G4"], 0.3)], amplitude=0.17, decay=0.10)

#: ...and a step that *took something away* -- a headline discarded, a figure blanked, a
#: quote dropped, a link refused, a duplicate merged.
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

#: What a cue's ``kind`` plays.
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
LINE_GAP = 0.055

#: The kinds :data:`LINE_GAP` applies to.
LINE_KINDS = frozenset({"step", "caught"})

#: Fades at the two ends, so the file neither starts nor stops on a step.
EDGE_FADE = 0.25


def spread(cues: Iterable[dict]) -> list[dict]:
    """Push apart the line cues that arrived together, keeping their order."""
    ordered = sorted(cues, key=lambda cue: float(cue["at"]))
    last = -1.0
    spaced = []
    for cue in ordered:
        at = float(cue["at"])
        if str(cue["kind"]) in LINE_KINDS:
            at = max(at, last + LINE_GAP)
            last = at
        spaced.append({**cue, "at": at})
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
    """Lift the mix, limit it, and fade the two ends."""
    for index, sample in enumerate(buffer):
        buffer[index] = PEAK * math.tanh(GAIN * sample / PEAK)
    edge = int(EDGE_FADE * SAMPLE_RATE)
    for index in range(min(edge, len(buffer))):
        gain = index / edge
        buffer[index] *= gain
        buffer[-1 - index] *= gain
    return buffer


#: Peak each spoken line is brought to: over the ducked bed, still under full scale.
SPEECH_PEAK = 0.74

#: What the bed is turned down to while somebody is talking.
DUCK = 0.3

#: How long the bed takes to dip before a line and come back after it.
DUCK_RAMP = 0.15


def read_clip(path: Path) -> array.array:
    """A 16-bit WAV as samples at :data:`SAMPLE_RATE`, brought to :data:`SPEECH_PEAK`."""
    with wave.open(str(path), "rb") as clip:
        if clip.getsampwidth() != 2:
            raise ValueError(f"{path} is not 16-bit PCM")
        channels = clip.getnchannels()
        rate = clip.getframerate()
        raw = array.array("h", clip.readframes(clip.getnframes()))
    if sys.byteorder == "big":
        raw.byteswap()
    mono = [
        sum(raw[index : index + channels]) / channels for index in range(0, len(raw), channels)
    ]
    peak = max((abs(sample) for sample in mono), default=0.0) or 1.0
    # Linear interpolation is plenty for a voice going from 16 kHz to 44.1 kHz.
    step = rate / SAMPLE_RATE
    length = int(len(mono) / step)
    samples = array.array("d", bytes(8 * length))
    for index in range(length):
        position = index * step
        left = int(position)
        right = min(left + 1, len(mono) - 1)
        fraction = position - left
        samples[index] = (
            SPEECH_PEAK * (mono[left] * (1 - fraction) + mono[right] * fraction) / peak
        )
    return samples


def voice_over(buffer: array.array, cues: Iterable[dict]) -> array.array:
    """Lay every ``say`` cue's clip over ``buffer``, ducking the rest beneath it."""
    lines = [
        (int(float(cue["at"]) * SAMPLE_RATE), read_clip(Path(cue["clip"])))
        for cue in cues
        if str(cue["kind"]) == "say"
    ]
    ramp = int(DUCK_RAMP * SAMPLE_RATE)
    gain = array.array("d", [1.0]) * len(buffer)
    for start, clip in lines:
        end = start + len(clip)
        for index in range(max(0, start - ramp), min(len(buffer), end + ramp)):
            if index < start:
                level = 1 - (1 - DUCK) * (index - start + ramp) / ramp
            elif index >= end:
                level = DUCK + (1 - DUCK) * (index - end) / ramp
            else:
                level = DUCK
            gain[index] = min(gain[index], level)
    for index, level in enumerate(gain):
        buffer[index] *= level
    for start, clip in lines:
        for offset, sample in enumerate(clip[: max(0, len(buffer) - start)]):
            buffer[start + offset] += sample
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
    write(voice_over(finish(render(cues, args.duration)), cues), args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
