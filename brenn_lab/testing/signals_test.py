"""The synthetic clips, and the signal search the audible test reads its
capture with, held on synthetic signals before any window.
"""

import array
import math
import random
import wave
from pathlib import Path

import pytest

from brenn_lab.playback.limits import peak_mb
from brenn_lab.testing import signals
from brenn_lab.testing.signals import CLIP_A, CLIP_B, RATE_HZ


@pytest.mark.parametrize("clip", [CLIP_A, CLIP_B], ids=lambda c: c.name)
def test_a_clip_has_its_length_and_peak(clip: signals.Clip, tmp_path: Path) -> None:
    path = signals.write(clip, tmp_path)
    with wave.open(str(path), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (2, 2, RATE_HZ)
        assert w.getnframes() == clip.frames
        data = w.readframes(w.getnframes())
    assert data == signals.frames(clip)
    samples = array.array("h", data)
    assert samples[0::2] == samples[1::2]
    assert max(abs(v) for v in samples) == signals.AMPLITUDE
    assert peak_mb(signals.AMPLITUDE, 16) == signals.PEAK_MB


def test_the_clips_hold_what_they_say() -> None:
    assert (CLIP_A.frames, CLIP_B.frames) == (312000, 56000)
    one = signals.mono(CLIP_A)
    chirp = signals.chirp()
    for at in CLIP_A.chirps:
        start = signals.frame_at(at)
        assert any(one[start : start + len(chirp)])
    assert not any(one[: signals.frame_at(0.5)])
    assert not any(one[signals.frame_at(0.6) : signals.frame_at(1.5)])
    assert not any(one[signals.frame_at(3.5) : signals.frame_at(19.0)])
    assert not any(one[signals.frame_at(19.1) :])


def test_correlation_matches_the_direct_sum() -> None:
    rng = random.Random(1)
    signal = [rng.uniform(-1, 1) for _ in range(300)]
    template = [rng.uniform(-1, 1) for _ in range(37)]
    got = signals.correlation(signal, template)
    want = [
        sum(signal[i + k] * template[k] for k in range(len(template)))
        for i in range(len(signal) - len(template) + 1)
    ]
    assert len(got) == len(want)
    for a, b in zip(got, want, strict=True):
        assert a == pytest.approx(b, abs=1e-9)


@pytest.mark.parametrize("polarity", [1.0, -1.0])
def test_the_chirp_is_found_in_noise_and_beside_the_tone(polarity: float) -> None:
    """The chirp at a twentieth of the amplitude, in noise of the same RMS as
    the chirp's, half a second before a tone ten times louder, is found to
    the frame, either way up.
    """
    rng = random.Random(2)
    chirp = signals.chirp()
    at = 7123
    signal = [rng.gauss(0, 0.05 / math.sqrt(2)) for _ in range(2 * RATE_HZ)]
    for i, v in enumerate(chirp):
        signal[at + i] += polarity * 0.05 * v
    tone = signals.tone(1.0)
    begin = at + signals.frame_at(0.5)
    for i, v in enumerate(tone[: len(signal) - begin]):
        signal[begin + i] += 0.5 * v
    assert signals.find(signal, chirp) == at


def test_level_db() -> None:
    full = [math.sin(2 * math.pi * 1000 * i / RATE_HZ) * 32768 for i in range(RATE_HZ)]
    assert signals.level_db(full, 1000) == pytest.approx(0.0, abs=0.01)
    quarter = [v / 4 for v in full]
    assert signals.level_db(quarter, 1000) == pytest.approx(-12.04, abs=0.01)
    other = [math.sin(2 * math.pi * 1500 * i / RATE_HZ) * 32768 for i in range(RATE_HZ)]
    assert signals.level_db(other, 1000) < -100
    assert signals.level_db([0.0] * RATE_HZ, 1000) == -math.inf


def test_the_tone_of_a_clip_reads_at_its_amplitude() -> None:
    one = signals.mono(CLIP_B)
    start = signals.frame_at(CLIP_B.tone[0] + 0.5)
    span = [float(v) for v in one[start : start + RATE_HZ]]
    assert signals.level_db(span, 1000) == pytest.approx(-6.02, abs=0.01)
