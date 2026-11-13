"""The synthetic clips the audible test plays, and the standard-library signal
search it reads the capture with.

A clip is 16 kHz, two identical channels, 16-bit, built from a 100 ms linear
chirp and a 1 kHz tone, each at half of full scale, with silence between.
Every clip is computed from its description alone, with nothing random in it.
Its samples come from the C library's sine and cosine, so two machines may
round one differently; the audible test compares a kept copy with the clip
computed in the same run.
"""

import array
import cmath
import math
import wave
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

RATE_HZ = 16000
CHANNELS = 2
SAMPLE_BYTES = 2
AMPLITUDE = 16384  # half of full scale: a peak of -602 mB
PEAK_MB = -602
CHIRP_S = 0.1
CHIRP_FROM_HZ = 500.0
CHIRP_TO_HZ = 4000.0
TONE_HZ = 1000
RAMP_S = 0.005  # the raised-cosine taper at each edge of a chirp and a tone


@dataclass(frozen=True)
class Clip:
    """A clip by its content: `chirps` are the chirps' start times, `tone` the
    tone's start and end, all in seconds from the clip's first frame.
    """

    name: str
    seconds: float
    chirps: tuple[float, ...]
    tone: tuple[float, float]

    @property
    def frames(self) -> int:
        return frame_at(self.seconds)


CLIP_A = Clip("a.wav", 19.5, (0.5, 19.0), (1.5, 3.5))
CLIP_B = Clip("b.wav", 3.5, (0.5,), (1.0, 3.0))


def frame_at(seconds: float) -> int:
    """The frame at `seconds` from the start, at RATE_HZ."""
    return round(seconds * RATE_HZ)


def _taper(i: int, n: int) -> float:
    """The raised-cosine gain of sample `i` of a burst of `n` samples."""
    ramp = frame_at(RAMP_S)
    edge = min(i, n - 1 - i)
    if edge >= ramp:
        return 1.0
    return 0.5 - 0.5 * math.cos(math.pi * edge / ramp)


def chirp() -> list[float]:
    """The chirp at unit amplitude: a linear sweep from CHIRP_FROM_HZ to
    CHIRP_TO_HZ over CHIRP_S, tapered at both ends.
    """
    n = frame_at(CHIRP_S)
    sweep = (CHIRP_TO_HZ - CHIRP_FROM_HZ) / CHIRP_S
    out = []
    for i in range(n):
        t = i / RATE_HZ
        phase = 2 * math.pi * (CHIRP_FROM_HZ * t + sweep * t * t / 2)
        out.append(math.sin(phase) * _taper(i, n))
    return out


def tone(seconds: float) -> list[float]:
    """The 1 kHz tone at unit amplitude for `seconds`, tapered at both ends.
    Sample 4 of every 16 is the sine's crest, 1.0 exactly.
    """
    n = frame_at(seconds)
    return [math.sin(2 * math.pi * TONE_HZ * i / RATE_HZ) * _taper(i, n) for i in range(n)]


def mono(clip: Clip) -> array.array[int]:
    """One channel of `clip` as 16-bit samples."""
    out = [0.0] * clip.frames
    bursts = [(at, chirp()) for at in clip.chirps]
    bursts.append((clip.tone[0], tone(clip.tone[1] - clip.tone[0])))
    for at, burst in bursts:
        start = frame_at(at)
        assert start + len(burst) <= clip.frames, (clip.name, at)
        for i, v in enumerate(burst):
            assert out[start + i] == 0.0, (clip.name, at)
            out[start + i] = v
    return array.array("h", [round(AMPLITUDE * v) for v in out])


def frames(clip: Clip) -> bytes:
    """`clip`'s frames as a WAV holds them: two identical channels,
    interleaved, little-endian.
    """
    one = mono(clip)
    both = array.array("h", bytes(2 * len(one) * SAMPLE_BYTES))
    both[0::2] = one
    both[1::2] = one
    assert both.itemsize == SAMPLE_BYTES
    return both.tobytes()


def write(clip: Clip, directory: Path) -> Path:
    """Write `clip` as a WAV file named for it into `directory`."""
    path = directory / clip.name
    with wave.open(str(path), "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(SAMPLE_BYTES)
        w.setframerate(RATE_HZ)
        w.writeframes(frames(clip))
    return path


def _fft(values: list[complex], inverse: bool) -> list[complex]:
    """The discrete Fourier transform of `values`, whose length is a power of
    two, unscaled in both directions.
    """
    n = len(values)
    assert n > 0 and n & (n - 1) == 0, n
    out = list(values)
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            out[i], out[j] = out[j], out[i]
    sign = 1.0 if inverse else -1.0
    size = 2
    while size <= n:
        half = size // 2
        step = cmath.exp(sign * 2j * math.pi / size)
        twiddles = [step**k for k in range(half)]
        for start in range(0, n, size):
            for k in range(half):
                a = out[start + k]
                b = out[start + k + half] * twiddles[k]
                out[start + k] = a + b
                out[start + k + half] = a - b
        size *= 2
    return out


def correlation(signal: Sequence[float], template: Sequence[float]) -> list[float]:
    """The cross-correlation of `template` against `signal` at every lag at
    which the template lies wholly inside the signal: entry `i` is the sum of
    `signal[i + k] * template[k]` over the template.
    """
    lags = len(signal) - len(template) + 1
    assert lags > 0, (len(signal), len(template))
    n = 1
    while n < len(signal) + len(template):
        n *= 2
    a = _fft([complex(v) for v in signal] + [0j] * (n - len(signal)), inverse=False)
    b = _fft([complex(v) for v in template] + [0j] * (n - len(template)), inverse=False)
    product = [x * y.conjugate() for x, y in zip(a, b, strict=True)]
    return [v.real / n for v in _fft(product, inverse=True)[:lags]]


def find(signal: Sequence[float], template: Sequence[float]) -> int:
    """The lag at which `template` best matches `signal`, either polarity."""
    values = correlation(signal, template)
    return max(range(len(values)), key=lambda i: abs(values[i]))


def level_db(signal: Sequence[float], hz: float) -> float:
    """The level of the component at `hz` in `signal`, sampled at RATE_HZ, in
    dB relative to a sine of full 16-bit scale: the Goertzel filter's
    amplitude estimate. Compared across spans of one length only, since the
    noise in it falls with the span's length.
    """
    n = len(signal)
    assert n > 0
    w = 2 * math.pi * hz / RATE_HZ
    coeff = 2 * math.cos(w)
    s1 = s2 = 0.0
    for x in signal:
        s1, s2 = x + coeff * s1 - s2, s1
    power = s1 * s1 + s2 * s2 - coeff * s1 * s2
    amplitude = 2 * math.sqrt(max(power, 0.0)) / n
    if amplitude == 0.0:
        return -math.inf
    return 20 * math.log10(amplitude / 32768)
