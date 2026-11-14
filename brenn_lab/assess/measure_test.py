"""Measuring a channel, on pieces built here and on exports of the stand-in
reader.
"""

import array
import json
import math
import os
import wave
from pathlib import Path

import pytest

from brenn_lab.align import capture
from brenn_lab.align.capture import RunBlock
from brenn_lab.assess import measure
from brenn_lab.assess.measure import Span

READER = os.path.abspath(os.environ["EXPORT_STAND_IN"])
LOW16, HIGH16 = -32768, 32767


def piece(first: int, samples: list[int], continues: bool = False, code: str = "h") -> RunBlock:
    return RunBlock(first, array.array(code, samples), continues)


def one(samples: list[int], sample_format: str = "s16_le") -> measure.Measured:
    code = "h" if sample_format == "s16_le" else "i"
    return measure.measure("c", [piece(0, samples, code=code)], sample_format)


def sine(amplitude: float, frames: int = 16000, period: int = 16) -> list[int]:
    return [round(amplitude * math.sin(2 * math.pi * n / period)) for n in range(frames)]


def test_silence_is_silent_with_no_level() -> None:
    got = one([0] * 100)
    assert (got.frames, got.min, got.max, got.mean) == (100, 0, 0, 0.0)
    assert (got.peak_mb, got.rms_mb) == (None, None)
    assert (got.silent, got.constant) == (True, False)
    assert (got.limit_samples, got.limit_spans, got.longest_limit_span) == (0, 0, 0)


def test_a_constant_other_than_zero_is_constant_not_silent() -> None:
    got = one([-7] * 50)
    assert (got.silent, got.constant) == (False, True)
    assert (got.min, got.max, got.mean) == (-7, -7, -7.0)
    assert got.rms_mb == round(2000 * math.log10(7 / 32768))
    assert got.peak_mb == math.ceil(2000 * math.log10(7 / 32768))


def test_a_full_scale_square_is_0_mb() -> None:
    got = one([HIGH16, LOW16] * 500)
    assert got.rms_mb == 0
    assert got.peak_mb == 0
    assert got.mean == -0.5


@pytest.mark.parametrize(("amplitude", "rms_mb"), [(32767, -301), (16384, -903)])
def test_a_sine_has_its_known_level(amplitude: int, rms_mb: int) -> None:
    got = one(sine(amplitude))
    assert got.rms_mb == rms_mb
    assert (got.silent, got.constant) == (False, False)


def test_the_mean_is_the_offset() -> None:
    got = one([s + 100 for s in sine(1000)])
    assert got.mean == pytest.approx(100.0)


@pytest.mark.parametrize("value", [LOW16, HIGH16])
def test_one_sample_at_either_limit_is_one_span(value: int) -> None:
    got = one([0, 5, value, -5, 0])
    assert (got.limit_samples, got.limit_spans, got.longest_limit_span) == (1, 1, 1)
    assert got.first_limit_spans == (Span(2, 1),)


def test_a_sample_short_of_the_limit_is_not_at_it() -> None:
    got = one([HIGH16 - 1, LOW16 + 1])
    assert (got.limit_samples, got.limit_spans) == (0, 0)


def test_a_flat_top_is_placed_and_measured() -> None:
    got = measure.measure("c", [piece(1000, [0] * 10 + [HIGH16] * 7 + [0] * 3)], "s16_le")
    assert got.first_limit_spans == (Span(1010, 7),)
    assert (got.limit_samples, got.limit_spans, got.longest_limit_span) == (7, 1, 7)


def test_a_flat_top_runs_on_across_pieces_of_one_run() -> None:
    pieces = [
        piece(0, [0, HIGH16, HIGH16]),
        piece(3, [HIGH16, HIGH16, 0], continues=True),
        piece(6, [0, 0, 0], continues=True),
    ]
    got = measure.measure("c", pieces, "s16_le")
    assert got.first_limit_spans == (Span(1, 4),)
    assert (got.limit_spans, got.longest_limit_span) == (1, 4)


def test_a_flat_top_ends_where_a_run_ends() -> None:
    pieces = [piece(0, [0, HIGH16, HIGH16]), piece(3, [HIGH16, 0])]
    got = measure.measure("c", pieces, "s16_le")
    assert got.first_limit_spans == (Span(1, 2), Span(3, 1))
    assert (got.limit_samples, got.limit_spans, got.longest_limit_span) == (3, 2, 2)


def test_a_flat_top_ends_at_a_piece_with_no_sample_at_a_limit() -> None:
    pieces = [piece(0, [0, LOW16]), piece(2, [0, 0], True), piece(4, [LOW16], True)]
    got = measure.measure("c", pieces, "s16_le")
    assert got.first_limit_spans == (Span(1, 1), Span(4, 1))


def test_only_the_first_spans_are_placed_and_all_are_counted() -> None:
    got = one([HIGH16, 0] * 20 + [LOW16] * 3)
    assert got.limit_spans == 21
    assert got.limit_samples == 23
    assert got.longest_limit_span == 3
    assert len(got.first_limit_spans) == measure.FIRST_SPANS
    assert got.first_limit_spans[-1] == Span(30, 1)


def test_32_bit_samples_have_their_own_limits() -> None:
    low, high = -(1 << 31), (1 << 31) - 1
    got = one([high, low, LOW16, HIGH16], "s32_le")
    assert (got.limit_samples, got.limit_spans) == (2, 1)
    assert got.rms_mb == round(
        1000 * math.log10((high**2 + low**2 + LOW16**2 + HIGH16**2) / (4 << 62))
    )
    assert got.peak_mb == 0


def test_32_bit_squares_are_summed_exactly() -> None:
    got = one([(1 << 31) - 1] * 4096, "s32_le")
    assert got.rms_mb == 0
    assert got.constant is True


@pytest.mark.parametrize("pieces", [[], [piece(0, [])]])
def test_a_channel_of_no_frames_has_no_level_and_no_flag(pieces: list[RunBlock]) -> None:
    got = measure.measure("c", pieces, "s16_le")
    assert got == measure.Measured("c", 0, None, None, None, None, None, None, None, 0, 0, 0, ())


def export(
    tmp: Path, samples: list[int], runs: list[dict[str, int]], holes: list[dict[str, object]]
) -> Path:
    with wave.open(str(tmp / "prepared.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(array.array("h", samples).tobytes())
    told = {
        "wav": "prepared.wav",
        "map": {
            "wav_frames": len(samples),
            "runs": runs,
            "holes": holes,
            "resent": [],
            "tail": {"verdict": "ended", "offset": 0, "reason": "", "open_segment": None},
        },
    }
    log = tmp / "0001.framelog"
    log.write_text(json.dumps(told))
    return log


def read(tmp: Path, log: Path) -> capture.Exported:
    exported = capture.read(log, tmp / "out.wav", READER, 16000, "s16_le", ("c",))
    assert isinstance(exported, capture.Exported), exported
    return exported


def test_a_holes_fill_is_not_measured_and_a_flat_top_breaks_at_a_hole(tmp_path: Path) -> None:
    # Capture frames 0-3, then 4 frames of measured silence for capture
    # frames 4-7, then capture frames 8-11. The flat top on either side of
    # the hole is two spans; the silence is not a sample of the channel.
    samples = [5, 5, HIGH16, HIGH16] + [0] * 4 + [HIGH16, 5, 5, 5]
    runs = [
        {"wav_frame": 0, "first_frame": 0, "frames": 4},
        {"wav_frame": 8, "first_frame": 8, "frames": 4},
    ]
    holes = [{"wav_frame": 4, "at_frame": 4, "filled_frames": 4, "basis": "measured"}]
    exported = read(tmp_path, export(tmp_path, samples, runs, holes))
    got = measure.measure("c", exported.run_blocks(0), "s16_le")
    assert got.frames == 8
    assert got.min == 5
    assert got.silent is False
    assert got.first_limit_spans == (Span(2, 2), Span(8, 1))


def test_a_place_past_an_unmeasured_hole_is_on_the_captures_index(tmp_path: Path) -> None:
    # Capture frames 0-3, 3 frames of clock-estimated silence across which the
    # index did not advance, then capture frames 4-7, then an unknown hole
    # with no silence, then capture frames 8-9.
    samples = [1, 1, 1, HIGH16] + [0] * 3 + [HIGH16, 1, LOW16, 1] + [1, LOW16]
    runs = [
        {"wav_frame": 0, "first_frame": 0, "frames": 4},
        {"wav_frame": 7, "first_frame": 4, "frames": 4},
        {"wav_frame": 11, "first_frame": 8, "frames": 2},
    ]
    holes = [
        {"wav_frame": 4, "at_frame": 4, "filled_frames": 3, "basis": "clock_estimate"},
        {"wav_frame": 11, "at_frame": 8, "filled_frames": 0, "basis": "unknown"},
    ]
    exported = read(tmp_path, export(tmp_path, samples, runs, holes))
    got = measure.measure("c", exported.run_blocks(0), "s16_le")
    assert got.frames == 10
    assert got.first_limit_spans == (Span(3, 1), Span(4, 1), Span(6, 1), Span(9, 1))
    assert got.limit_samples == 4
