"""The tone and the level reading the audible bench uses, held on synthetic
signals before any window.
"""

import math

import pytest

from brenn_lab.testing import signals
from brenn_lab.testing.signals import RATE_HZ


def test_level_db() -> None:
    full = [math.sin(2 * math.pi * 1000 * i / RATE_HZ) * 32768 for i in range(RATE_HZ)]
    assert signals.level_db(full, 1000) == pytest.approx(0.0, abs=0.01)
    quarter = [v / 4 for v in full]
    assert signals.level_db(quarter, 1000) == pytest.approx(-12.04, abs=0.01)
    other = [math.sin(2 * math.pi * 1500 * i / RATE_HZ) * 32768 for i in range(RATE_HZ)]
    assert signals.level_db(other, 1000) < -100
    assert signals.level_db([0.0] * RATE_HZ, 1000) == -math.inf


def test_the_tone_is_tapered_and_reads_at_its_amplitude() -> None:
    one = signals.tone(2.0)
    assert len(one) == 2 * RATE_HZ
    assert one[0] == 0.0 and abs(one[-1]) < 1e-3
    assert max(one) == 1.0
    span = [signals.AMPLITUDE * v for v in one[RATE_HZ // 2 : RATE_HZ // 2 + RATE_HZ]]
    assert signals.level_db(span, signals.TONE_HZ) == pytest.approx(-6.02, abs=0.01)
