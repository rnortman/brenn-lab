"""The marker a source carries so that it can be found in a capture: definition
`gold-bpsk-1`.

A marker is 0.512 s of a 1023-chip binary code, each chip a Hann pulse two
chips wide, phase-keyed onto a 2000 Hz carrier at half of full scale. A marked
source holds a start marker before its content and an end marker after it,
with different codes. Member `m` (0 to 510) of the family uses code `2m` to
start and `2m + 1` to end. The definition is in seconds, so a marker exists at
every rate from 8000 Hz up. This module is its one statement in code.
"""

import array
import functools
import math
import sys
import wave
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from brenn_lab.data import Failure
from brenn_lab.sound import FORMAT_OF_WIDTH, SAMPLE_BYTES, TYPECODES

DEFINITION = "gold-bpsk-1"
CHIPS = 1023
CHIP_HZ = 2000
"""Chips per second, and the carrier's frequency."""
DURATION_MS = 512
AMPLITUDE = 0.5
"""The marker's peak, as a fraction of full scale."""
MEMBERS = 511
MIN_RATE_HZ = 8000
LEAD_S = 0.5
GAP_S = 0.25
TAIL_S = 0.25

_G1_TAPS = (3, 10)
_G2_TAPS = (2, 3, 6, 8, 9, 10)

type Which = Literal["start", "end"]


@dataclass(frozen=True)
class Span:
    """A marker's first frame in a marked file, and its length in frames."""

    frame: int
    frames: int


@dataclass(frozen=True)
class Carried:
    """The marker a file carries: the definition's identifier, the member, its
    two codes, and where each marker lies in the file.
    """

    definition: str
    member: int
    start_code: int
    end_code: int
    start: Span
    end: Span


@dataclass(frozen=True)
class Marked:
    """What `mark` read of the source, and the marker the file it wrote
    carries.
    """

    frames: int
    rate_hz: int
    channels: int
    sample_format: str
    marker: Carried


def _register(taps: tuple[int, ...]) -> tuple[int, ...]:
    """A ten-stage shift register's 1023 outputs: it starts all ones, outputs
    stage 10, and shifts the xor of `taps` into stage 1.
    """
    stages = [1] * 10
    out = []
    for _ in range(CHIPS):
        out.append(stages[9])
        feedback = 0
        for t in taps:
            feedback ^= stages[t - 1]
        stages = [feedback, *stages[:9]]
    return tuple(out)


_G1 = _register(_G1_TAPS)
_G2 = _register(_G2_TAPS)


@functools.cache
def code(shift: int) -> tuple[int, ...]:
    """Code `shift` (0 to 1021): chip `k` is +1 for bit 0 and -1 for bit 1 of
    `G1[k] xor G2[(k + shift) mod 1023]`.
    """
    if not 0 <= shift <= CHIPS - 2:
        raise ValueError(f"code {shift} is outside 0 to {CHIPS - 2}")
    return tuple(1 - 2 * (_G1[k] ^ _G2[(k + shift) % CHIPS]) for k in range(CHIPS))


def codes(member: int) -> tuple[int, int]:
    """The start and end codes of `member`."""
    return 2 * member, 2 * member + 1


def _baseband(chips: Sequence[int], chip: int, fraction: float) -> float:
    """`b(t)` at `fraction` of the way from chip centre `chip` to the next: the
    two pulses that are not zero there.
    """
    total = 0.0
    if 0 <= chip < CHIPS:
        total += chips[chip] * math.cos(math.pi * fraction / 2) ** 2
    if 0 <= chip + 1 < CHIPS:
        total += chips[chip + 1] * math.cos(math.pi * (fraction - 1) / 2) ** 2
    return total


def value(chips: Sequence[int], t: float) -> complex:
    """`b(t) * exp(2 pi i 2000 t)` at `t` seconds from the marker's start, unit
    amplitude; the marker is `AMPLITUDE` times its real part. Zero outside the
    marker.
    """
    if not 0.0 <= t <= DURATION_MS / 1000:
        return 0j
    u = t * CHIP_HZ - 1
    chip = math.floor(u)
    fraction = u - chip
    angle = 2 * math.pi * fraction
    return _baseband(chips, chip, fraction) * complex(math.cos(angle), math.sin(angle))


def length(rate_hz: int) -> int:
    """A marker's frames at `rate_hz`: `floor(0.512 * rate) + 1`."""
    return rate_hz * DURATION_MS // 1000 + 1


def _round(x: float) -> int:
    """`x` to the nearest integer, halves away from zero."""
    return math.floor(x + 0.5) if x >= 0 else -math.floor(-x + 0.5)


def _frames_of(seconds: float, rate_hz: int) -> int:
    return _round(seconds * rate_hz)


def frames(member: int, which: Which, rate_hz: int, sample_format: str) -> array.array[int]:
    """One channel of `member`'s `which` marker at `rate_hz`, quantised: frame
    `n` is `round(s(n / rate) * 2^(bits - 1))`, halves away from zero.
    """
    start_code, end_code = codes(member)
    chips = code(start_code if which == "start" else end_code)
    scale = AMPLITUDE * 2 ** (8 * SAMPLE_BYTES[sample_format] - 1)
    out = array.array(TYPECODES[sample_format])
    for n in range(length(rate_hz)):
        # t = n / rate exactly: the chip index and the fraction come from
        # integers, and the carrier's phase is the fraction's.
        whole, rest = divmod(CHIP_HZ * n, rate_hz)
        fraction = rest / rate_hz
        b = _baseband(chips, whole - 1, fraction)
        out.append(_round(scale * b * math.cos(2 * math.pi * fraction)))
    return out


def layout(rate_hz: int, total_frames: int) -> tuple[Span, Span] | None:
    """Where the start and end markers lie in a marked file of `total_frames`
    at `rate_hz`; None when no marked file has that length or rate.
    """
    if rate_hz < MIN_RATE_HZ:
        return None
    lead, gap, tail = _padding(rate_hz)
    m = length(rate_hz)
    content = total_frames - (lead + m + gap + gap + m + tail)
    if content < 0:
        return None
    return Span(lead, m), Span(lead + m + gap + content + gap, m)


def _padding(rate_hz: int) -> tuple[int, int, int]:
    return (
        _frames_of(LEAD_S, rate_hz),
        _frames_of(GAP_S, rate_hz),
        _frames_of(TAIL_S, rate_hz),
    )


def head_frames(rate_hz: int) -> int:
    """The frames at a marked file's start that hold its lead and start marker."""
    return _padding(rate_hz)[0] + length(rate_hz)


def tail_frames(rate_hz: int) -> int:
    """The frames at a marked file's end that hold its end marker and tail."""
    return length(rate_hz) + _padding(rate_hz)[2]


@functools.cache
def _start_members() -> dict[tuple[bool, ...], int]:
    """Each member by the signs of its start code's chips: True for -1."""
    return {tuple(c < 0 for c in code(codes(m)[0])): m for m in range(MEMBERS)}


def recognise(
    head: Sequence[int],
    tail: Sequence[int],
    rate_hz: int,
    channels: int,
    sample_format: str,
    total_frames: int,
) -> Carried | None:
    """The marker a file carries, from its samples alone, or None.

    `head` holds the file's first `head_frames(rate_hz)` frames and `tail` its
    last `tail_frames(rate_hz)`, interleaved. The member is read from the
    signs of channel 0 at the start marker's chip centres; both of that
    member's markers are then regenerated, and every sample of both, on every
    channel, must be within one least significant bit of the file's.
    """
    spans = layout(rate_hz, total_frames)
    if spans is None or sample_format not in TYPECODES or channels < 1:
        return None
    start, end = spans
    if len(head) != channels * head_frames(rate_hz):
        return None
    if len(tail) != channels * tail_frames(rate_hz):
        return None
    signs = []
    for k in range(CHIPS):
        n = start.frame + _round((k + 1) * rate_hz / CHIP_HZ)
        sample = head[n * channels]
        if sample == 0:
            return None
        signs.append(sample < 0)
    member = _start_members().get(tuple(signs))
    if member is None:
        return None
    tail_first = total_frames - tail_frames(rate_hz)
    checks: tuple[tuple[Which, Sequence[int], int], ...] = (
        ("start", head, start.frame),
        ("end", tail, end.frame - tail_first),
    )
    for which, samples, first in checks:
        expected = frames(member, which, rate_hz, sample_format)
        for n, want in enumerate(expected):
            base = (first + n) * channels
            for c in range(channels):
                if abs(samples[base + c] - want) > 1:
                    return None
    start_code, end_code = codes(member)
    return Carried(DEFINITION, member, start_code, end_code, start, end)


def mark(source: Path, out: Path, member: int) -> Marked | Failure:
    """Write at `out` the source with `member`'s markers around it: lead,
    start marker, gap, the source's frames unchanged, gap, end marker, tail.
    Same rate, channels and format as the source. Nothing is written on a
    refusal: `member_range`, `unreadable`, `source_format`,
    `rate_unsupported` or `out_exists`; nor is anything left when writing
    `out` raises.
    """
    if not 0 <= member < MEMBERS:
        return Failure("member_range", f"member {member} is outside 0 to {MEMBERS - 1}")
    read = _read_source(source)
    if isinstance(read, Failure):
        return read
    rate_hz, channels, sample_format, content = read
    if rate_hz < MIN_RATE_HZ:
        return Failure(
            "rate_unsupported",
            f"{source} is at {rate_hz} Hz; a marker needs at least {MIN_RATE_HZ} Hz",
        )
    width = SAMPLE_BYTES[sample_format]
    content_frames = len(content) // (width * channels)
    lead, gap, tail = _padding(rate_hz)
    m = length(rate_hz)
    total = lead + m + gap + content_frames + gap + m + tail
    try:
        file = out.open("xb")
    except FileExistsError:
        return Failure("out_exists", f"{out} already exists")
    try:
        with file, wave.open(file, "wb") as writer:
            writer.setnchannels(channels)
            writer.setsampwidth(width)
            writer.setframerate(rate_hz)
            writer.setnframes(total)
            for part in (
                bytes(width * channels * lead),
                _interleaved(frames(member, "start", rate_hz, sample_format), channels),
                bytes(width * channels * gap),
                content,
                bytes(width * channels * gap),
                _interleaved(frames(member, "end", rate_hz, sample_format), channels),
                bytes(width * channels * tail),
            ):
                writer.writeframesraw(part)
    except BaseException:
        out.unlink(missing_ok=True)
        raise
    spans = layout(rate_hz, total)
    assert spans is not None
    start_code, end_code = codes(member)
    carried = Carried(DEFINITION, member, start_code, end_code, *spans)
    return Marked(content_frames, rate_hz, channels, sample_format, carried)


def _interleaved(channel: array.array[int], channels: int) -> bytes:
    out = array.array(channel.typecode, [0]) * (len(channel) * channels)
    for c in range(channels):
        out[c::channels] = channel
    if sys.byteorder == "big":
        out.byteswap()
    return out.tobytes()


def _read_source(source: Path) -> tuple[int, int, str, bytes] | Failure:
    """Rate, channels, format and frame bytes of the WAV at `source`, refused
    as the playback role refuses a source it cannot play.
    """
    try:
        with wave.open(str(source), "rb") as reader:
            params = reader.getparams()
            # One frame more than declared, so that a partial frame at the end
            # of the data chunk is read and refused.
            data = reader.readframes(params.nframes + 1)
    except OSError as e:
        return Failure("unreadable", f"{source} is not a readable file: {e.strerror}")
    except (wave.Error, EOFError) as e:
        return Failure("source_format", f"{source} is not a WAV of integer PCM: {e}")
    sample_format = FORMAT_OF_WIDTH.get(params.sampwidth)
    if sample_format is None:
        return Failure(
            "source_format",
            f"{source} holds {8 * params.sampwidth}-bit samples; "
            "a source is 16- or 32-bit integer PCM",
        )
    frame_bytes = params.sampwidth * params.nchannels
    if len(data) % frame_bytes:
        return Failure(
            "source_format", f"{source} has a data chunk that is not a whole number of frames"
        )
    if len(data) != params.nframes * frame_bytes:
        return Failure(
            "source_format",
            f"{source} declares {params.nframes} frames and holds {len(data) // frame_bytes}",
        )
    return params.framerate, params.nchannels, sample_format, data
