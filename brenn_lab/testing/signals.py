"""The 1 kHz tone the audible bench plays, and the level it reads a capture's
tone with.

The tone is 16 kHz, computed from its length alone, with nothing random in
it. Its samples come from the C library's sine, so two machines may round one
differently.
"""

import math
from collections.abc import Sequence

RATE_HZ = 16000
AMPLITUDE = 16384  # half of full scale: a peak of -602 mB
TONE_HZ = 1000
RAMP_S = 0.005  # the raised-cosine taper at each edge of the tone


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


def tone(seconds: float) -> list[float]:
    """The 1 kHz tone at unit amplitude for `seconds`, tapered at both ends.
    Sample 4 of every 16 is the sine's crest, 1.0 exactly.
    """
    n = frame_at(seconds)
    return [math.sin(2 * math.pi * TONE_HZ * i / RATE_HZ) * _taper(i, n) for i in range(n)]


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
