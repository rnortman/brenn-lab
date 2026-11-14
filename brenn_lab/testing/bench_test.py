"""The audible bench test's decisions, held on synthetic inputs."""

import math
import tomllib
import wave
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from brenn_lab import labconfig, marker
from brenn_lab.testing import bench, signals
from brenn_lab.testing.bench import FlexOut, Heard, SinkOut

RATE = signals.RATE_HZ
EASTERN = timezone(timedelta(hours=-5))


def at(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 11, 14, hour, minute, second, tzinfo=EASTERN)


@pytest.mark.parametrize(
    ("text", "now", "inside"),
    [
        ("06:31-20:15", at(6, 31), True),
        ("06:31-20:15", at(13, 0), True),
        ("06:31-20:15", at(20, 14, 59), True),
        ("06:31-20:15", at(20, 15), False),
        ("06:31-20:15", at(6, 30), False),
        ("22:00-02:00", at(23, 0), True),
        ("22:00-02:00", at(1, 59), True),
        ("22:00-02:00", at(2, 0), False),
        ("22:00-02:00", at(12, 0), False),
    ],
)
def test_the_window_judges_the_time(text: str, now: datetime, inside: bool) -> None:
    assert bench.window(text, now).inside is inside


def test_the_window_gives_the_rest_of_the_day_as_quiet() -> None:
    agreed = bench.window("06:31-20:15", at(12, 0))
    assert (agreed.start, agreed.end, agreed.quiet) == ("06:31", "20:15", ("20:15", "06:31"))


@pytest.mark.parametrize(
    "text",
    [
        "",
        "06:31",
        "6:31-20:15",
        "06:31-24:00",
        "06:60-20:15",
        "06:31-20:15-21:00",
        "06:31 - 20:15",
        "07:00-07:00",
    ],
)
def test_a_malformed_window_is_refused(text: str) -> None:
    with pytest.raises(ValueError):
        bench.window(text, at(12, 0))


def test_the_sink_schedule() -> None:
    levels = bench.schedule(-28899, bench.SINK_CEILING_MB)
    assert levels == [-28899, *range(-12000, -2399, 600), -2000]
    assert len(levels) == 19


def test_the_flex_schedule() -> None:
    levels = bench.schedule(-6000, bench.FLEX_CEILING_MB)
    assert levels == [-6000, -5400, -4800, -4200, -3600, -3000, -2400, -1800]


def test_a_minimum_on_a_step_is_not_repeated_and_the_ceiling_ends_it() -> None:
    assert bench.schedule(-3000, -2000) == [-3000, -2400, -2000]
    assert bench.schedule(-2100, -2000) == [-2100, -2000]


def test_a_minimum_at_the_ceiling_is_refused() -> None:
    with pytest.raises(ValueError):
        bench.schedule(-1800, -1800)


def channel(name: str, start: str = "found", end: str = "found") -> dict[str, Any]:
    return {
        "channel": name,
        "start": {"outcome": start, "score": 0.8 if start == "found" else 0.1},
        "end": {"outcome": end, "score": 0.8 if end == "found" else 0.1},
    }


def alignment(chans: list[dict[str, Any]], tried: bool = True, play: int = 2) -> dict[str, Any]:
    return {
        "pairs": [
            {
                "play": {"role": "speaker", "play": n},
                "capture": {"role": "mic", "capture": 1},
                "tried": tried if n == play else False,
                "channels": chans if n == play else [],
            }
            for n in (1, 2)
        ]
    }


RAWS = [f"raw_mic_{i}" for i in range(4)]


def test_detected_when_every_raw_microphone_finds_both_markers() -> None:
    chans = [channel("conference_beam", "not_found"), channel("asr_beam", end="not_found")]
    assert bench.detected(alignment(chans + [channel(r) for r in RAWS]), "speaker", 2)


@pytest.mark.parametrize(("start", "end"), [("not_found", "found"), ("found", "ambiguous")])
def test_not_detected_when_one_raw_microphone_misses_a_marker(start: str, end: str) -> None:
    chans = [channel(r) for r in RAWS[:3]] + [channel(RAWS[3], start, end)]
    assert not bench.detected(alignment(chans), "speaker", 2)


def test_an_untried_pair_is_not_detected() -> None:
    assert not bench.detected(alignment([], tried=False), "speaker", 2)


def test_a_capture_without_raw_microphones_is_refused() -> None:
    with pytest.raises(ValueError):
        bench.detected(alignment([channel("asr_beam")]), "speaker", 2)


def test_a_play_in_no_pair_is_refused() -> None:
    with pytest.raises(ValueError):
        bench.detected(alignment([channel(r) for r in RAWS]), "talker", 1)


def test_play_level_is_one_step_up_and_never_over_the_ceiling() -> None:
    assert bench.play_level(-4200, bench.FLEX_CEILING_MB) == -3600
    assert bench.play_level(-2400, bench.FLEX_CEILING_MB) == -1800
    assert bench.play_level(-1800, bench.FLEX_CEILING_MB) == -1800
    assert bench.play_level(-2400, bench.SINK_CEILING_MB) == -2000


def record(**changes: Any) -> dict[str, Any]:
    return {"ended": "completed", "underruns": [], "restored": True, "failure": None} | changes


def test_a_completed_play_with_nothing_wrong_is_healthy() -> None:
    assert bench.healthy(record())


@pytest.mark.parametrize(
    "changes",
    [
        {"ended": "failed"},
        {"ended": "stopped"},
        {"underruns": [{"at_frame": 3}]},
        {"restored": False},
        {"restored": None},
        {"failure": {"reason": "stream_died"}},
    ],
)
def test_unhealthy(changes: dict[str, Any]) -> None:
    assert not bench.healthy(record(**changes))


def reading(us: int, machine: str = "bench") -> dict[str, Any]:
    return {"machine": machine, "monotonic": {"clock": "CLOCK_MONOTONIC", "us": us}}


def mark(us: int, machine: str = "bench") -> dict[str, Any]:
    return {"frames_handed": 1600, "delay_frames": 320, "at": reading(us, machine)}


def test_the_first_mark_is_the_results_record_s() -> None:
    records = [
        {"role": "talker", "first": mark(10)},
        {"role": "speaker", "first": mark(20)},
    ]
    assert bench.first_mark(records, "speaker") == mark(20)


@pytest.mark.parametrize(
    "records",
    [
        [],
        [{"role": "speaker", "first": mark(1)}, {"role": "speaker", "first": mark(2)}],
        [{"role": "speaker", "first": None}],
    ],
)
def test_no_one_first_mark_is_refused(records: list[Any]) -> None:
    with pytest.raises(ValueError):
        bench.first_mark(records, "speaker")


def clock_reply(role: str, us: int) -> dict[str, Any]:
    return {"role": role, "command": "playback.clock", "ok": True, "result": {"at": reading(us)}}


def play_reply(role: str, asked: int | None) -> dict[str, Any]:
    asked_start = None if asked is None else reading(asked)
    return {
        "role": role,
        "command": "playback.play",
        "ok": True,
        "result": {"asked_start": asked_start},
    }


def test_the_stated_offset_is_each_role_s_lead_from_its_own_reading() -> None:
    journal = [
        {"role": "talker", "command": "playback.clock", "by": "script"},
        clock_reply("talker", 1_000_000),
        # The speaker's clock is read 30 ms later, and reads otherwise.
        clock_reply("speaker", 50_030_000),
        play_reply("talker", 3_000_000),
        play_reply("speaker", 53_030_000),
    ]
    assert bench.stated_offset_us(journal, "talker", "speaker") == 1_000_000


@pytest.mark.parametrize(
    "journal",
    [
        [clock_reply("talker", 0), play_reply("talker", 1)],
        [clock_reply("talker", 0), play_reply("talker", 1), play_reply("speaker", 2)],
        [clock_reply("talker", 0), play_reply("talker", 1)]
        + [clock_reply("speaker", 0), play_reply("speaker", None)],
    ],
)
def test_a_stated_offset_with_no_stated_start_is_refused(journal: list[Any]) -> None:
    with pytest.raises(ValueError):
        bench.stated_offset_us(journal, "talker", "speaker")


def test_the_gap_between_first_marks() -> None:
    assert bench.marks_gap_us(mark(2_000_000), mark(3_004_000)) == 1_004_000
    with pytest.raises(ValueError):
        bench.marks_gap_us(mark(0), mark(1, machine="other"))


def located(offsets: dict[str, float | None]) -> dict[str, Any]:
    return {"channels": [{"channel": c, "offset_frames": o} for c, o in offsets.items()]}


def test_raw_offsets_pair_the_raw_microphones() -> None:
    early = located({"raw_mic_0": 1000.0, "raw_mic_1": 1002.0, "asr_beam": None})
    late = located({"raw_mic_0": 13000.0, "raw_mic_1": 13010.0, "asr_beam": 0.0})
    offsets = bench.raw_offsets(early, late)
    assert offsets == {"raw_mic_0": (1000.0, 13000.0), "raw_mic_1": (1002.0, 13010.0)}
    assert bench.capture_gap_s(offsets, 16000) == pytest.approx(12004.0 / 16000)


@pytest.mark.parametrize(
    ("early", "late"),
    [
        ({"raw_mic_0": 1.0, "raw_mic_1": 2.0}, {"raw_mic_0": 1.0, "raw_mic_1": None}),
        ({"raw_mic_0": None}, {"raw_mic_0": 1.0}),
        ({"raw_mic_0": 1.0, "raw_mic_1": 2.0}, {"raw_mic_0": 1.0}),
        ({"asr_beam": 1.0}, {"asr_beam": 1.0}),
    ],
)
def test_raw_offsets_refuse_a_microphone_that_did_not_locate_both(
    early: dict[str, float | None], late: dict[str, float | None]
) -> None:
    with pytest.raises(ValueError):
        bench.raw_offsets(located(early), located(late))


def entry(level: int, held: int = 0) -> dict[str, Any]:
    def control(part: str, volume: int) -> dict[str, Any]:
        channels = [{"channel": c, "volume_mb": volume, "switch": True} for c in ("l", "r")]
        return {"element": "PCM", "index": 1, "part": part, "channels": channels}

    return {"controls": [control("level", level), control("held", held)]}


def test_played_when_both_plays_ran_as_asked() -> None:
    assert bench.played([record(), record()], [entry(-3000), entry(-3050)], -3000)


@pytest.mark.parametrize(
    ("records", "entries"),
    [
        ([record(ended="failed"), record()], [entry(-3000), entry(-3000)]),
        ([record(), record(underruns=[{"at_frame": 3}])], [entry(-3000), entry(-3000)]),
        ([record(), record(restored=False)], [entry(-3000), entry(-3000)]),
        ([record(), record(failure={"reason": "x"})], [entry(-3000), entry(-3000)]),
        ([record(), record()], [entry(-3000), entry(-2900)]),
        ([record(), record()], [entry(-3000), entry(-3000, held=100)]),
        ([record()], [entry(-3000), entry(-3000)]),
        ([], []),
    ],
)
def test_not_played(records: list[Any], entries: list[Any]) -> None:
    assert not bench.played(records, entries, -3000)


def heard(primer: float = 20.0, band: float = 1.0) -> Heard:
    return Heard("raw_mic_0", -60.0 + primer, -60.0, -50.0 + band, -50.0)


def test_checkpoint_not_arrived_on_every_microphone() -> None:
    assert bench.checkpoint([heard(primer=9.9), heard(primer=2.0)]) == "not_arrived"
    assert bench.checkpoint([heard(primer=9.9), heard(primer=10.0)]) == "faint"


def silent_mic(name: str = "raw_mic_0") -> Heard:
    """A raw microphone that read all zeros over every span measured."""
    return Heard(name, -math.inf, -math.inf, -math.inf, -math.inf)


def test_a_digitally_silent_microphone_has_not_heard_the_primer() -> None:
    assert not bench.arrived(silent_mic())
    assert bench.checkpoint([silent_mic()]) == "not_arrived"
    assert bench.checkpoint([heard(primer=2.0), silent_mic("raw_mic_1")]) == "not_arrived"
    assert bench.checkpoint([heard(primer=10.0), silent_mic("raw_mic_1")]) == "faint"


def test_checkpoint_not_recognised_on_any_microphone() -> None:
    assert bench.checkpoint([heard(band=1.0), heard(band=3.0)]) == "not_recognised"


def test_checkpoint_faint() -> None:
    assert bench.checkpoint([heard(band=2.9), heard(band=-1.0)]) == "faint"


def test_checkpoint_with_no_microphone_is_refused() -> None:
    with pytest.raises(ValueError):
        bench.checkpoint([])


def test_the_overlap_schedule_on_the_bench() -> None:
    assert bench.overlap_schedule(-6000, bench.FLEX_CEILING_MB) == [
        -6000,
        -5400,
        -4800,
        -4200,
        -3600,
        -3000,
        -2400,
        -1800,
    ]


def test_the_overlap_schedule_ends_at_the_ceiling() -> None:
    assert bench.overlap_schedule(-2300, -1800) == [-2300, -1800]
    assert bench.overlap_schedule(-1800, -1800) == [-1800]
    with pytest.raises(ValueError):
        bench.overlap_schedule(-1700, -1800)


def noise(seconds: float, sd: float) -> np.ndarray[Any, np.dtype[np.float64]]:
    return np.random.default_rng(7).normal(0.0, sd, round(seconds * RATE))


def test_noise_has_no_tone_standing_out() -> None:
    read = bench.standout("raw_mic_0", noise(bench.MEASURE_S, 3000.0))
    assert not bench.stands_out(read)
    assert bench.overlap_checkpoint([read], -3600, -6000) == "not_arrived"


def test_a_tone_under_noise_does_not_stand_out() -> None:
    span = noise(bench.MEASURE_S, 3000.0) + sine(1000, bench.MEASURE_S, 30.0)
    assert not bench.stands_out(bench.standout("raw_mic_0", span))


def test_a_tone_over_noise_stands_out() -> None:
    span = noise(bench.MEASURE_S, 3000.0) + sine(1000, bench.MEASURE_S, 2000.0)
    read = bench.standout("raw_mic_0", span)
    assert read.tone_db == pytest.approx(20 * math.log10(2000 / 32768), abs=0.5)
    assert bench.stands_out(read)


def test_a_digitally_silent_span_does_not_stand_out() -> None:
    read = bench.standout("raw_mic_0", np.zeros(round(bench.MEASURE_S * RATE)))
    assert not bench.stands_out(read)


def line(above: float) -> bench.Standout:
    return bench.Standout("raw_mic_0", -60.0 + above, -60.0)


def test_a_tone_stands_out_at_ten_db() -> None:
    assert bench.stands_out(line(10.0))
    assert not bench.stands_out(line(9.9))


def test_overlap_checkpoint_not_arrived_on_every_microphone() -> None:
    assert bench.overlap_checkpoint([line(9.9), line(2.0)], -3600, -6000) == "not_arrived"


def test_overlap_checkpoint_by_how_far_the_step_rose() -> None:
    read = [line(9.9), line(10.0)]
    assert bench.overlap_checkpoint(read, -3601, -5600) == "faint"
    assert bench.overlap_checkpoint(read, -3600, -5600) == "not_recognised"
    assert bench.overlap_checkpoint(read, -3600, -3600) == "faint"


def test_overlap_checkpoint_with_no_microphone_is_refused() -> None:
    with pytest.raises(ValueError):
        bench.overlap_checkpoint([], -3600, -6000)


def marked_one_second_tone(folder: Path) -> np.ndarray[Any, np.dtype[np.float64]]:
    """The first channel of a one-second tone marked as the bench marks it."""
    tone = bench.write_tone(folder / "tone.wav", 1.0)
    assert isinstance(marker.mark(tone, folder / "own.wav", 4), marker.Marked)
    with wave.open(str(folder / "own.wav"), "rb") as w:
        data = w.readframes(w.getnframes())
    return np.frombuffer(data, dtype="<i2").astype(np.float64)[::2]


def test_the_marked_tone_span_lies_inside_the_tone(tmp_path: Path) -> None:
    own = marked_one_second_tone(tmp_path)
    inside = bench.span(own, RATE, bench.MARKED_TONE_FROM_S * RATE, bench.MEASURE_S)
    pure = np.array(signals.tone(bench.MEASURE_S)) * signals.AMPLITUDE
    assert bench.tone_db(inside) == pytest.approx(bench.tone_db(pure), abs=0.1)


def capture_with(
    own: np.ndarray[Any, np.dtype[np.float64]], at_frame: int
) -> dict[str, np.ndarray[Any, np.dtype[np.float64]]]:
    """Six seconds of noise on two raw microphones and a beam, the marked
    tone played into each from `at_frame` at a tenth and a twentieth of its
    level on the microphones.
    """
    room = noise(6.0, 300.0)
    played = np.zeros(len(room))
    played[at_frame : at_frame + len(own)] = own
    return {
        "conference_beam": room + played,
        "raw_mic_0": room + played / 10,
        "raw_mic_1": room + played / 20,
    }


def first_mark(frames_handed: int, delay_frames: int, us: int) -> Any:
    return {
        "frames_handed": frames_handed,
        "delay_frames": delay_frames,
        "at": {"monotonic": {"us": us}},
    }


def test_the_marked_tone_is_read_where_the_tie_places_it(tmp_path: Path) -> None:
    own = marked_one_second_tone(tmp_path)
    # The first mark: 3200 frames handed, 1600 still ahead of the output,
    # read 2.6 s after the capture's start, so copy frame 0 is at 40000.
    first = first_mark(3200, 1600, 102_600_000)
    capture_start = {"monotonic": {"us": 100_000_000}}
    mics = capture_with(own, 40000)
    read = bench.marked_tone(first, capture_start, mics, RATE)
    assert [r.channel for r in read] == ["raw_mic_0", "raw_mic_1"]
    assert all(bench.stands_out(r) for r in read), read
    assert bench.overlap_checkpoint(read, -3600, -6000) == "not_recognised"


def test_the_marked_tone_is_not_read_from_the_capture_start(tmp_path: Path) -> None:
    own = marked_one_second_tone(tmp_path)
    mics = capture_with(own, 40000)
    at_start = first_mark(0, 0, 100_000_000)
    read = bench.marked_tone(at_start, {"monotonic": {"us": 100_000_000}}, mics, RATE)
    assert not any(bench.stands_out(r) for r in read), read


@dataclass(frozen=True)
class Taken:
    level_mb: int
    detected: bool
    played: bool


D = (True, True)
"""A run whose play was detected and ran as asked."""
M = (False, True)
"""A run whose play ran as asked and was missed."""


class Stepped:
    """A stand-in for a ramp's runs: each level's runs in order, each a
    `(detected, played)` pair or the text of a run that could not be read; a
    level not listed misses every run. Records every `(index, level)` taken
    and every level read, in order; a level asked for more runs than it has
    fails the test.
    """

    def __init__(
        self,
        runs: dict[int, list[tuple[bool, bool] | str]],
        stops: dict[int, bench.Stop],
    ) -> None:
        self.runs = {level: iter(answers) for level, answers in runs.items()}
        self.stops = stops
        self.taken: list[tuple[int, int]] = []
        self.read: list[int] = []

    def take(self, index: int, level: int) -> Taken | str:
        self.taken.append((index, level))
        answers = self.runs.get(level)
        run = M if answers is None else next(answers)
        if isinstance(run, str):
            return run
        detected, played = run
        return Taken(level, detected, played)

    def checkpoint(self, step: Taken) -> bench.Stop:
        self.read.append(step.level_mb)
        return self.stops.get(step.level_mb, "faint")

    def climb(self, levels: list[int], silent_min: bool = False) -> bench.Ramp[Taken]:
        return bench.climb("out", levels, self.take, self.checkpoint, silent_min)


def test_three_detections_in_a_row_end_the_ramp_found() -> None:
    stepped = Stepped({-5400: [D, D, D], -4800: [D, D, D]}, {})
    result = stepped.climb([-6000, -5400, -4800])
    assert (result.stop, result.found_mb, result.detail) == ("found", -5400, "")
    assert stepped.taken == [(0, -6000), (1, -5400), (2, -5400), (3, -5400)]
    assert [s.level_mb for s in result.steps] == [-6000, -5400, -5400, -5400]
    assert stepped.read == []


def test_detected_missed_is_not_a_level_and_the_next_run_is_one_level_up() -> None:
    stepped = Stepped({-5400: [D, M, D], -4800: [D, D, D]}, {})
    result = stepped.climb([-6000, -5400, -4800])
    assert (result.stop, result.found_mb) == ("found", -4800)
    assert stepped.taken == [(0, -6000), (1, -5400), (2, -5400), (3, -4800), (4, -4800), (5, -4800)]
    assert stepped.read == []


def test_a_miss_after_two_detections_ends_the_level() -> None:
    stepped = Stepped({-5400: [D, D, M], -4800: [D, D, D]}, {})
    result = stepped.climb([-6000, -5400, -4800])
    assert (result.stop, result.found_mb) == ("found", -4800)
    assert stepped.taken[1:5] == [(1, -5400), (2, -5400), (3, -5400), (4, -4800)]
    assert len(result.steps) == 7


def test_detections_do_not_carry_from_one_level_to_the_next() -> None:
    stepped = Stepped({-6000: [D, M], -5400: [D, D, M]}, {})
    result = stepped.climb([-6000, -5400, -4800])
    assert (result.stop, result.found_mb) == ("ceiling", None)
    assert stepped.taken == [
        (0, -6000),
        (1, -6000),
        (2, -5400),
        (3, -5400),
        (4, -5400),
        (5, -4800),
    ]


def test_a_detection_at_a_silent_minimum_is_not_a_level() -> None:
    silent_ramp = Stepped({-28899: [D]}, {})
    silent = silent_ramp.climb([-28899, -12000], silent_min=True)
    assert (silent.stop, silent.found_mb) == ("detected_in_silence", None)
    assert silent_ramp.taken == [(0, -28899)]
    alone_ramp = Stepped({-6000: [D, D, D]}, {})
    alone = alone_ramp.climb([-6000, -5400])
    assert (alone.stop, alone.found_mb) == ("found", -6000)
    assert alone_ramp.taken == [(0, -6000), (1, -6000), (2, -6000)]
    later = Stepped({-12000: [D, D, D]}, {}).climb([-28899, -12000], silent_min=True)
    assert (later.stop, later.found_mb) == ("found", -12000)


def test_a_run_that_could_not_be_read_ends_the_ramp() -> None:
    stepped = Stepped({-5400: ["run exited 1: no alignment"]}, {})
    result = stepped.climb([-6000, -5400, -4800])
    assert (result.stop, result.detail) == (
        "not_played",
        "run 1, -5400 mB: run exited 1: no alignment",
    )
    assert [s.level_mb for s in result.steps] == [-6000]
    assert stepped.taken == [(0, -6000), (1, -5400)]


def test_a_confirming_run_that_could_not_be_read_ends_the_ramp() -> None:
    stepped = Stepped({-5400: [D, "no alignment"]}, {})
    result = stepped.climb([-6000, -5400, -4800])
    assert (result.stop, result.detail, result.found_mb) == (
        "not_played",
        "run 2, -5400 mB: no alignment",
        None,
    )
    assert stepped.taken == [(0, -6000), (1, -5400), (2, -5400)]


def test_a_run_not_played_as_asked_ends_the_ramp_before_its_detection() -> None:
    stepped = Stepped({-5400: [(True, False)]}, {})
    result = stepped.climb([-6000, -5400, -4800])
    assert (result.stop, result.detail, result.found_mb) == ("not_played", "run 1, -5400 mB", None)
    high = Stepped({-3400: [(False, False)]}, {})
    assert high.climb([-4000, -3400, -2800]).stop == "not_played"
    assert high.read == []
    confirming = Stepped({-5400: [D, D, (True, False)]}, {})
    ended = confirming.climb([-6000, -5400])
    assert (ended.stop, ended.detail, ended.found_mb) == ("not_played", "run 3, -5400 mB", None)


def test_the_checkpoint_reads_undetected_runs_above_its_level() -> None:
    stepped = Stepped({}, {-2800: "not_recognised"})
    result = stepped.climb([-4600, -4000, -3400, -2800, -2200])
    assert stepped.read == [-3400, -2800]
    assert (result.stop, result.detail, result.found_mb) == (
        "not_recognised",
        "run 3, -2800 mB",
        None,
    )
    assert stepped.taken[-1] == (3, -2800)


def test_the_checkpoint_reads_a_miss_after_a_detection_at_its_level() -> None:
    stopped = Stepped({-3400: [D, M]}, {-3400: "not_recognised"})
    result = stopped.climb([-4000, -3400, -2800])
    assert stopped.read == [-3400]
    assert (result.stop, result.detail) == ("not_recognised", "run 2, -3400 mB")
    assert stopped.taken == [(0, -4000), (1, -3400), (2, -3400)]
    going = Stepped({-3400: [D, M], -2800: [D, D, D]}, {})
    found = going.climb([-4000, -3400, -2800])
    assert going.read == [-3400]
    assert (found.stop, found.found_mb) == ("found", -2800)
    assert going.taken[3] == (3, -2800)


def test_detected_runs_above_the_checkpoint_are_not_read() -> None:
    stepped = Stepped({-3400: [D, D, D]}, {})
    assert stepped.climb([-4000, -3400]).found_mb == -3400
    assert stepped.read == []


def test_detected_then_missed_at_the_last_level_faint_ends_at_the_ceiling() -> None:
    stepped = Stepped({-1800: [D, D, M]}, {})
    result = stepped.climb([-2400, -1800])
    assert (result.stop, result.found_mb) == ("ceiling", None)
    assert stepped.read == [-2400, -1800]
    assert stepped.taken == [(0, -2400), (1, -1800), (2, -1800), (3, -1800)]


def test_a_ramp_faint_to_its_last_level_ends_at_the_ceiling() -> None:
    stepped = Stepped({}, {})
    result = stepped.climb([-3000, -2400, -1800])
    assert (result.stop, result.found_mb) == ("ceiling", None)
    assert stepped.read == [-3000, -2400, -1800]
    assert len(result.steps) == 3


def test_judged() -> None:
    assert bench.judged(-45.0, -60.0)
    assert not bench.judged(-45.1, -60.0)


def test_silence_is_judged_against_a_primer_clear_of_the_room() -> None:
    reference = heard(primer=15.0)
    assert bench.silence(reference, [-55.0, -80.0]) is True
    assert bench.silence(reference, [-80.0, -54.9]) is False


def test_silence_is_not_judged_against_a_primer_in_the_room() -> None:
    assert bench.silence(heard(primer=14.9), [-20.0]) is None


def test_silence_with_no_span_is_refused() -> None:
    with pytest.raises(ValueError):
        bench.silence(heard(), [])


AGREED = bench.window("06:31-20:15", at(12, 0))


def test_the_lab_text_gives_every_playback_role_the_quiet_hours(tmp_path: Path) -> None:
    text = bench.lab_text(
        AGREED,
        "Flex",
        tmp_path,
        [FlexOut("Flex", -2400), SinkOut("talker", "LG", -3000)],
    )
    parsed = labconfig.parse(text.encode())
    assert isinstance(parsed, labconfig.LabConfig), parsed
    data = tomllib.loads(text)
    assert data["machines"]["bench"]["programs"] == {
        "pod_capture": {"pinned": True},
        "pulse_client": {"pinned": True},
    }
    for role, cap in (("speaker", -2400), ("talker", -3000)):
        device = data["roles"][role]["device"]
        assert device["quiet_hours"] == [["20:15", "06:31"]]
        assert device["cap_mb"] == cap
    assert data["roles"]["talker"]["device"]["sink"] == "LG"


def test_a_sink_may_be_the_speaker(tmp_path: Path) -> None:
    data = tomllib.loads(
        bench.lab_text(AGREED, "Flex", tmp_path, [SinkOut("speaker", "LG", -2000)])
    )
    assert data["roles"]["speaker"]["device"]["output"] == "pulse"


@pytest.mark.parametrize(
    "output", [FlexOut("Flex", -1700), SinkOut("talker", "LG", -1900)], ids=["flex", "sink"]
)
def test_a_cap_over_its_ceiling_is_refused(tmp_path: Path, output: FlexOut | SinkOut) -> None:
    with pytest.raises(ValueError):
        bench.lab_text(AGREED, "Flex", tmp_path, [output])


def test_the_caps_at_the_ceilings_are_written(tmp_path: Path) -> None:
    text = bench.lab_text(
        AGREED,
        "Flex",
        tmp_path,
        [FlexOut("Flex", bench.FLEX_CEILING_MB), SinkOut("talker", "LG", bench.SINK_CEILING_MB)],
    )
    assert isinstance(labconfig.parse(text.encode()), labconfig.LabConfig)


def test_two_roles_of_one_name_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        bench.lab_text(
            AGREED, "Flex", tmp_path, [FlexOut("Flex", -2400), SinkOut("speaker", "LG", -3000)]
        )


def test_tie() -> None:
    first = {"frames_handed": 1600, "delay_frames": 800, "at": {"monotonic": {"us": 3_000_000}}}
    start = {"monotonic": {"us": 1_000_000}}
    assert bench.tie(first, start, RATE) == 2 * RATE - 800
    with pytest.raises(ValueError):
        bench.tie(first | {"delay_frames": None}, start, RATE)


def sine(hz: float, seconds: float, amplitude: float) -> np.ndarray[Any, np.dtype[np.float64]]:
    n = round(seconds * RATE)
    return amplitude * np.sin(2 * np.pi * hz * np.arange(n) / RATE)


def test_band_db_reads_the_band_and_not_outside_it() -> None:
    inside = bench.band_db(sine(1000, 1.0, 32768), RATE)
    assert inside == pytest.approx(20 * math.log10(1 / math.sqrt(2)), abs=0.1)
    assert bench.band_db(sine(100, 1.0, 32768), RATE) < inside - 30
    assert bench.band_db(np.zeros(RATE), RATE) == -math.inf
    with pytest.raises(ValueError):
        bench.band_db(np.zeros(100), RATE)


def test_windows_db_and_span() -> None:
    rising = np.concatenate([sine(1000, 0.5, 3276.8), sine(1000, 0.5, 32768)])
    levels = bench.windows_db(rising, 0.25)
    assert [round(v) for v in levels] == [-20, -20, 0, 0]
    assert len(bench.span(rising, RATE, 4000, 0.25)) == 4000
    with pytest.raises(ValueError):
        bench.span(rising, RATE, 12001, 0.25)


def test_the_primer_tone_and_channels(tmp_path: Path) -> None:
    path = bench.write_tone(tmp_path / "primer.wav", 4.0)
    with wave.open(str(path), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (2, 2, RATE)
        assert w.getnframes() == 4 * RATE
    rate, chans = bench.channels(path, ["left", "right"])
    assert rate == RATE
    assert np.array_equal(chans["left"], chans["right"])
    assert np.max(np.abs(chans["left"])) == signals.AMPLITUDE
    assert bench.tone_db(chans["left"][RATE : 2 * RATE]) == pytest.approx(-6.02, abs=0.01)
    with pytest.raises(ValueError):
        bench.channels(path, ["one"])
