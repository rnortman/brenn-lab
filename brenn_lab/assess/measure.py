"""One channel of a capture, measured over the samples of its runs.

Only the frames the recorder captured are measured: the silence an export
writes for a hole is never read. Every sum is an exact integer, taken with the
standard library over each block.
"""

import array
import math
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from brenn_lab.align.capture import RunBlock
from brenn_lab.playback.limits import peak_mb
from brenn_lab.sound import SAMPLE_BYTES

FIRST_SPANS = 16
"""How many spans at a limit are placed, from the first."""


@dataclass(frozen=True)
class Span:
    """`frames` consecutive samples at a limit of the format, from capture
    frame `at_frame`.
    """

    at_frame: int
    frames: int


@dataclass(frozen=True)
class Measured:
    """The measurement of one channel. With no frame measured, every level
    and both flags are None and the limit counts are 0.
    """

    channel: str
    frames: int
    min: int | None
    max: int | None
    mean: float | None
    peak_mb: int | None
    rms_mb: int | None
    silent: bool | None
    constant: bool | None
    limit_samples: int
    limit_spans: int
    longest_limit_span: int
    first_limit_spans: tuple[Span, ...]


class _Spans:
    """The spans at a limit, met piece by piece; a span may run on across
    pieces of one run and ends at a piece that does not continue it.
    """

    def __init__(self) -> None:
        self.count = 0
        self.longest = 0
        self.first: list[Span] = []
        self._at_frame = 0
        self._frames = 0  # the open span's length; none is open while it is 0

    def close(self) -> None:
        if not self._frames:
            return
        self.count += 1
        self.longest = max(self.longest, self._frames)
        if len(self.first) < FIRST_SPANS:
            self.first.append(Span(self._at_frame, self._frames))
        self._frames = 0

    def scan(self, piece: RunBlock, low: int, high: int) -> None:
        """Place every sample of `piece` at `low` or `high`. Only those
        samples are visited; a span open from the
        previous piece runs on only if the piece's first sample is at a limit.
        """
        samples = piece.samples
        after = 0  # the offset just past the open span, while it reaches into the piece
        for offset in sorted([*_offsets(samples, low), *_offsets(samples, high)]):
            if offset != after:
                self.close()
            if not self._frames:
                self._at_frame = piece.first_frame + offset
            self._frames += 1
            after = offset + 1
        if after != len(samples):
            self.close()


def _offsets(samples: array.array[int], value: int) -> Iterator[int]:
    """Every offset in `samples` that holds `value`, in order."""
    offset = -1
    while True:
        try:
            offset = samples.index(value, offset + 1)
        except ValueError:
            return
        yield offset


def measure(channel: str, pieces: Iterable[RunBlock], sample_format: str) -> Measured:
    """The measurement of `channel` from its samples `pieces`, of the format
    `sample_format`.
    """
    bits = 8 * SAMPLE_BYTES[sample_format]
    full_scale = 1 << (bits - 1)
    low, high = -full_scale, full_scale - 1
    frames = 0
    smallest: int | None = None
    largest: int | None = None
    total = 0
    squares = 0
    at_limit = 0
    spans = _Spans()
    for piece in pieces:
        samples = piece.samples
        if not piece.continues:
            spans.close()
        if not samples:
            continue
        frames += len(samples)
        least, most = min(samples), max(samples)
        smallest = least if smallest is None else min(smallest, least)
        largest = most if largest is None else max(largest, most)
        total += sum(samples)
        # `sumprod` of integers is an exact int; `int` only tells the checker.
        squares += int(math.sumprod(samples, samples))
        here = samples.count(low) + samples.count(high)
        if here:
            at_limit += here
            spans.scan(piece, low, high)
        else:
            spans.close()
    spans.close()
    if smallest is None or largest is None:
        return Measured(channel, 0, None, None, None, None, None, None, None, 0, 0, 0, ())
    silent = smallest == largest == 0
    return Measured(
        channel,
        frames,
        smallest,
        largest,
        total / frames,
        peak_mb(max(-smallest, largest), bits),
        None if silent else round(1000 * math.log10(squares / (frames * full_scale**2))),
        silent,
        smallest == largest != 0,
        at_limit,
        spans.count,
        spans.longest,
        tuple(spans.first),
    )
