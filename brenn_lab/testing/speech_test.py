"""The known speech clip: its bytes, and its markers found around it in a
capture that holds it.
"""

import array
import hashlib
import os
import sys
import wave
from pathlib import Path

import numpy as np

from brenn_lab import marker
from brenn_lab.align import search

SPEECH = Path(os.environ["SPEECH"])
SHA256 = "2594562568c97203d6c3c2c2ff87b9a7c62389100a014934ab0ad39d15af0384"
RATE = 16000
FRAMES = 51761
MEMBER = 7
SEED = 20261114


def samples(path: Path) -> array.array[int]:
    with wave.open(str(path), "rb") as reader:
        data = reader.readframes(reader.getnframes())
    out = array.array("h", data)
    if sys.byteorder == "big":
        out.byteswap()
    return out


def test_the_clip_is_the_one_named() -> None:
    assert hashlib.sha256(SPEECH.read_bytes()).hexdigest() == SHA256
    with wave.open(str(SPEECH), "rb") as reader:
        assert reader.getframerate() == RATE
        assert reader.getnchannels() == 1
        assert reader.getsampwidth() == 2
        assert reader.getnframes() == FRAMES


def test_the_marked_clip_is_found_around_its_speech_in_a_capture(tmp_path: Path) -> None:
    marked = marker.mark(SPEECH, tmp_path / "talker.wav", MEMBER)
    assert isinstance(marked, marker.Marked), marked
    assert (marked.frames, marked.rate_hz, marked.channels, marked.sample_format) == (
        FRAMES,
        RATE,
        1,
        "s16_le",
    )
    carried = marked.marker
    played = np.array(samples(tmp_path / "talker.wav"), dtype=np.float64) / 32768
    speech_first = carried.start.frame + carried.start.frames + round(marker.GAP_S * RATE)
    assert np.array_equal(
        played[speech_first : speech_first + FRAMES],
        np.array(samples(SPEECH), dtype=np.float64) / 32768,
    )

    # The marked clip at half its level, 1.3 s into a capture of quiet noise.
    at = 20800
    rng = np.random.default_rng(SEED)
    capture = rng.normal(0.0, 0.001, at + len(played) + RATE)
    capture[at : at + len(played)] += 0.5 * played

    templates = [
        search.template(marker.code(c), RATE) for c in (carried.start_code, carried.end_code)
    ]
    start, end = search.search(lambda: [capture], RATE, templates)
    [found_start] = start.instances
    [found_end] = end.instances
    assert abs(found_start.first - (at + carried.start.frame)) <= 1
    assert abs(found_end.first - (at + carried.end.frame)) <= 1
    assert found_start.score > 0.9 and found_end.score > 0.9
    assert found_start.first + carried.start.frames <= at + speech_first
    assert at + speech_first + FRAMES <= found_end.first
