"""The search for markers in one channel of a capture.

The channel is band-passed to 500-3500 Hz, correlated with each marker's
complex template, and scored by `rho`: the correlation's magnitude times the
square root of two, divided by the template's norm and by the band-passed
channel's norm over the template's length. `rho` is 1 for a clean marker
alone, does not depend on level, and `rho**2` is the share of the energy over
the marker's length that the marker accounts for. A position scoring at least
`THRESHOLD` is an instance; the strongest suppresses every other within one
marker length either side.

The channel is read twice, block by block, and nothing of its length is held:
the first pass finds the largest window energy, which the quiet rule needs
before any position is scored; the second scores every position and keeps
only those at or above the threshold, their two neighbours and the largest
score.

This is the one module of the harness that imports numpy.
"""

import array
import math
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from itertools import chain

import numpy as np
import numpy.typing as npt

from brenn_lab import marker

THRESHOLD = 0.25
"""The score at and above which a position is an instance of a marker."""
BAND_HZ = (500, 3500)
FIR_S = 0.016
"""The band-pass filter's length in seconds."""
BLOCK = 1 << 18
"""The FFT length of the block convolutions, unless a template needs more."""
QUIET = 1e-9
"""Positions whose energy is below this share of the channel's largest score 0."""

type Floats = npt.NDArray[np.float64]
type Complexes = npt.NDArray[np.complex128]
type Blocks = Callable[[], Iterable[array.array[int] | Floats]]
"""A fresh pass over one channel's samples, in consecutive blocks of any lengths."""


@dataclass(frozen=True)
class Instance:
    """A marker found in a channel. `first` is the frame at which the template
    starts at the score's peak, `midpoint` the marker's midpoint, refined
    between frames, and `score` the peak's `rho`.
    """

    first: int
    midpoint: float
    score: float


@dataclass(frozen=True)
class Found:
    """What a search found for one template: its instances in channel order,
    and the largest `rho` anywhere in the channel.
    """

    instances: tuple[Instance, ...]
    best: float


def template(chips: Sequence[int], rate_hz: int) -> Complexes:
    """A marker's complex template at `rate_hz`: `marker.value` at `n / rate`
    for `n` from 0 to `floor(0.512 * rate)`.
    """
    return np.array(
        [marker.value(chips, n / rate_hz) for n in range(marker.length(rate_hz))],
        dtype=np.complex128,
    )


def bandpass_taps(rate_hz: int) -> Floats:
    """The band-pass filter: a windowed sinc of `round(0.016 * rate) | 1` taps,
    halves rounded away from zero, under a Hann window.
    """
    taps = math.floor(FIR_S * rate_hz + 0.5) | 1
    k = np.arange(taps, dtype=np.float64) - taps // 2
    low, high = BAND_HZ
    ideal = (2 * high / rate_hz) * np.sinc(2 * high * k / rate_hz) - (2 * low / rate_hz) * np.sinc(
        2 * low * k / rate_hz
    )
    window: Floats = np.hanning(taps)
    return ideal * window


def search(blocks: Blocks, rate_hz: int, templates: Sequence[Complexes]) -> list[Found]:
    """Search one channel at `rate_hz` for each of `templates`, which are all
    one length; one `Found` per template, in order. `blocks` is called twice.
    """
    if not templates:
        return []
    length = len(templates[0])
    if any(len(t) != length for t in templates):
        raise ValueError("the templates are not all one length")
    nfft = _fft_size(length)
    positions = 0
    loudest = 0.0
    for _, energy in _windows_scored(blocks, rate_hz, length, nfft):
        positions += len(energy)
        loudest = max(loudest, float(energy.max()))
    if positions == 0:
        return [Found((), 0.0) for _ in templates]
    responses = [np.fft.fft(np.conj(t[::-1]), nfft) for t in templates]
    t_norms = [math.sqrt(float(np.sum(np.abs(t) ** 2))) for t in templates]
    kept = [_Kept() for _ in templates]
    start = 0
    for window, energy in _windows_scored(blocks, rate_hz, length, nfft):
        count = len(energy)
        loud = (energy > 0) & (energy >= QUIET * loudest)
        norm = np.sqrt(np.where(loud, energy, 1.0))
        forward = np.fft.fft(window, nfft)
        for k, response in enumerate(responses):
            magnitude = np.abs(np.fft.ifft(forward * response)[length - 1 : length - 1 + count])
            rho: Floats = np.where(loud, math.sqrt(2) * magnitude / (t_norms[k] * norm), 0.0)
            kept[k].add(start, rho)
        start += count
    if start != positions:
        raise RuntimeError(f"the channel held {positions} positions, then {start}")
    return [k.instances(positions, length, rate_hz) for k in kept]


@dataclass
class _Kept:
    """What one template's scores leave once the channel has passed: the
    largest, every position at or above the threshold in channel order, and
    the scores of those positions and of their neighbours.
    """

    best: float = 0.0
    candidates: list[int] = field(default_factory=list)
    scores: dict[int, float] = field(default_factory=dict)
    last: float | None = None
    """The score of the previous block's last position."""
    wants_next: bool = False
    """Whether the previous block's last position is a candidate."""

    def add(self, start: int, rho: Floats) -> None:
        """The scores of positions `start` to `start + len(rho)`, the block
        after the last one added.
        """
        if self.wants_next:
            self.scores[start] = float(rho[0])
            self.wants_next = False
        for i in np.flatnonzero(rho >= THRESHOLD).tolist():
            p = start + i
            self.candidates.append(p)
            self.scores[p] = float(rho[i])
            if i > 0:
                self.scores[p - 1] = float(rho[i - 1])
            elif self.last is not None:
                self.scores[p - 1] = self.last
            if i + 1 < len(rho):
                self.scores[p + 1] = float(rho[i + 1])
            else:
                self.wants_next = True
        self.best = max(self.best, float(rho.max()))
        self.last = float(rho[-1])

    def instances(self, positions: int, length: int, rate_hz: int) -> Found:
        """The instances among the candidates, strongest first, equal scores
        in channel order, each suppressing every position within `length` of
        it; each midpoint refined by a parabola through the peak and its two
        neighbours, except at the channel's first and last position.
        """
        order = sorted(self.candidates, key=lambda p: -self.scores[p])
        taken: list[int] = []
        for p in order:
            if all(abs(p - q) > length for q in taken):
                taken.append(p)
        found = []
        for p in sorted(taken):
            shift = 0.0
            if 0 < p < positions - 1:
                before, peak, after = self.scores[p - 1], self.scores[p], self.scores[p + 1]
                curve = before - 2 * peak + after
                if curve < 0:
                    shift = 0.5 * (before - after) / curve
            midpoint = p + shift + marker.DURATION_MS * rate_hz / 2000
            found.append(Instance(p, midpoint, self.scores[p]))
        return Found(tuple(found), self.best)


def _fft_size(length: int) -> int:
    return max(BLOCK, 1 << (2 * length - 1).bit_length())


def _windows_scored(
    blocks: Blocks, rate_hz: int, length: int, nfft: int
) -> Iterator[tuple[Floats, Floats]]:
    """The band-passed channel in windows of `nfft` frames, one every
    `nfft - length + 1`, each with the energy over `[p, p + length)` at each
    position `p` whose span lies inside the window. Positions follow one
    another from 0 across the windows.
    """
    for window in _windows(_bandpassed(blocks, rate_hz), nfft, nfft - length + 1):
        count = len(window) - length + 1
        sums = np.concatenate([[0.0], np.cumsum(window * window)])
        energy: Floats = np.maximum(sums[length : length + count] - sums[:count], 0.0)
        yield window, energy


def _bandpassed(blocks: Blocks, rate_hz: int) -> Iterator[Floats]:
    """The channel through the band-pass filter, in consecutive pieces,
    aligned with it: output frame `n` is centred on input frame `n`, with
    zeros before the first frame and after the last.
    """
    h = bandpass_taps(rate_hz)
    taps = len(h)
    nfft = _fft_size(taps)
    response = np.fft.rfft(h, nfft)
    zeros = np.zeros(taps // 2)
    floats = (np.asarray(b, dtype=np.float64) for b in blocks())
    for window in _windows(chain([zeros], floats, [zeros]), nfft, nfft - taps + 1):
        out: Floats = np.fft.irfft(np.fft.rfft(window, nfft) * response, nfft)
        yield out[taps - 1 : len(window)]


def _windows(pieces: Iterable[Floats], size: int, hop: int) -> Iterator[Floats]:
    """Windows of `size` frames of the stream `pieces` make, one every `hop`
    frames from its first, and then the shorter window at its end if that
    holds frames the last full one did not. Each window is valid until the
    next is asked for.
    """
    overlap = size - hop
    held: Floats = np.zeros(0)
    pending: list[Floats] = []
    waiting = 0
    for piece in pieces:
        pending.append(piece)
        waiting += len(piece)
        if len(held) + waiting < size:
            continue
        held = np.concatenate([held, *pending])
        pending, waiting = [], 0
        while len(held) >= size:
            yield held[:size]
            held = held[hop:]
    held = np.concatenate([held, *pending])
    if len(held) > overlap:
        yield held
