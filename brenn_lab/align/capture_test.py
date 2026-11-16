"""Reading a capture through a reader, against the stand-in reader."""

import array
import errno
import json
import os
import wave
from pathlib import Path

import pytest

from brenn_lab.align import capture
from brenn_lab.data import Failure

READER = os.path.abspath(os.environ["EXPORT_STAND_IN"])
LAYOUT = ("a", "b")


def write_wav(
    path: Path, frames: list[tuple[int, int]], rate_hz: int = 16000, width: int = 2
) -> None:
    samples = array.array("h" if width == 2 else "i", [s for frame in frames for s in frame])
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(width)
        w.setframerate(rate_hz)
        w.writeframes(samples.tobytes())


def run_(wav_frame: int, first_frame: int, frames: int) -> dict[str, int]:
    return {"wav_frame": wav_frame, "first_frame": first_frame, "frames": frames}


def hole(wav_frame: int, at_frame: int, filled: int, basis: object) -> dict[str, object]:
    return {"wav_frame": wav_frame, "at_frame": at_frame, "filled_frames": filled, "basis": basis}


def export_map(
    wav_frames: int, runs: list[dict[str, int]], holes: list[dict[str, object]]
) -> dict[str, object]:
    return {
        "wav_frames": wav_frames,
        "runs": runs,
        "holes": holes,
        "resent": [],
        "tail": {"verdict": "ended", "offset": 0, "reason": "", "open_segment": None},
    }


def log_of(tmp: Path, told: dict[str, object]) -> Path:
    log = tmp / "0001.framelog"
    log.write_text(json.dumps(told))
    return log


def read(tmp: Path, log: Path, reader: str = READER) -> capture.Exported | Failure:
    return capture.read(log, tmp / "out.wav", reader, 16000, "s16_le", LAYOUT)


# Frames 0-9 of the export are capture frames 0-9; 4 frames of measured
# silence stand for capture frames 10-13; frames 14-19 are capture frames
# 14-19; 3 frames of clock-estimated silence stand for an unmeasured loss
# across which the index did not advance; frames 23-27 are capture frames
# 20-24; an unknown loss has no silence; frames 28-29 are capture frames
# 25-26.
HOLED_RUNS = [run_(0, 0, 10), run_(14, 14, 6), run_(23, 20, 5), run_(28, 25, 2)]
HOLED_HOLES = [
    hole(10, 10, 4, "measured"),
    hole(20, 20, 3, "clock_estimate"),
    hole(28, 25, 0, "unknown"),
]


def holed(tmp: Path) -> capture.Exported:
    write_wav(tmp / "prepared.wav", [(n, -n) for n in range(30)])
    log = log_of(tmp, {"wav": "prepared.wav", "map": export_map(30, HOLED_RUNS, HOLED_HOLES)})
    exported = read(tmp, log)
    assert isinstance(exported, capture.Exported), exported
    return exported


def samples_of(exported: capture.Exported, channel: int) -> list[int]:
    return [s for block in exported.blocks(channel) for s in block]


def test_samples_are_read_per_channel_on_each_pass_and_the_export_stays(tmp_path: Path) -> None:
    exported = holed(tmp_path)
    assert exported.rate_hz == 16000
    assert exported.frames == 30
    assert exported.path == tmp_path / "out.wav"
    assert exported.path.is_file()
    for _ in range(2):
        assert samples_of(exported, 0) == list(range(30))
        assert samples_of(exported, 1) == [-n for n in range(30)]
    assert exported.tail == "ended"
    assert [h.basis for h in exported.holes] == ["measured", "clock_estimate", "unknown"]


def test_a_long_export_is_handed_out_in_blocks(tmp_path: Path) -> None:
    frames = 2 * capture.BLOCK_FRAMES + 5
    write_wav(tmp_path / "prepared.wav", [(n % 1000, -(n % 1000)) for n in range(frames)])
    told: dict[str, object] = {
        "wav": "prepared.wav",
        "map": export_map(frames, [run_(0, 0, frames)], []),
    }
    exported = read(tmp_path, log_of(tmp_path, told))
    assert isinstance(exported, capture.Exported), exported
    blocks = list(exported.blocks(1))
    assert [len(b) for b in blocks] == [capture.BLOCK_FRAMES, capture.BLOCK_FRAMES, 5]
    assert [s for b in blocks for s in b] == [-(n % 1000) for n in range(frames)]


def test_run_blocks_hold_the_runs_alone_on_the_capture_index(tmp_path: Path) -> None:
    exported = holed(tmp_path)
    got = [(b.first_frame, list(b.samples), b.continues) for b in exported.run_blocks(1)]
    assert got == [
        (0, [-n for n in range(10)], False),
        (14, [-n for n in range(14, 20)], False),
        (20, [-n for n in range(23, 28)], False),
        (25, [-28, -29], False),
    ]


def test_a_run_across_blocks_continues_and_one_block_can_hold_two_runs(tmp_path: Path) -> None:
    frames = 2 * capture.BLOCK_FRAMES + 5
    split = capture.BLOCK_FRAMES + 3
    write_wav(tmp_path / "prepared.wav", [(n % 1000, 0) for n in range(frames)])
    runs = [run_(0, 0, split), run_(split + 2, split, frames - split - 2)]
    holes = [hole(split, split, 2, "clock_estimate")]
    told: dict[str, object] = {"wav": "prepared.wav", "map": export_map(frames, runs, holes)}
    exported = read(tmp_path, log_of(tmp_path, told))
    assert isinstance(exported, capture.Exported), exported
    got = [(b.first_frame, len(b.samples), b.continues) for b in exported.run_blocks(0)]
    assert got == [
        (0, capture.BLOCK_FRAMES, False),
        (capture.BLOCK_FRAMES, 3, True),
        (split, capture.BLOCK_FRAMES - 5, False),
        (split + capture.BLOCK_FRAMES - 5, 5, True),
    ]
    samples = [s for b in exported.run_blocks(0) for s in b.samples]
    assert samples == [n % 1000 for n in range(frames) if not split <= n < split + 2]


def truncate(path: Path, frames: int) -> None:
    """Cut the last `frames` frames and half of one more off the two-channel,
    16-bit file at `path`, leaving its header as it was.
    """
    data = path.read_bytes()
    path.write_bytes(data[: len(data) - 4 * frames - 2])


def test_an_export_whose_data_ends_short_of_its_header_is_named(tmp_path: Path) -> None:
    write_wav(tmp_path / "prepared.wav", [(1, 2)] * 8)
    truncate(tmp_path / "prepared.wav", 2)
    failed = read(tmp_path, log_of(tmp_path, {"wav": "prepared.wav", "map": GOOD_MAP}))
    assert isinstance(failed, Failure), failed
    assert failed.reason == "reader_contract"
    assert "holds 5 frames" in failed.detail
    assert not (tmp_path / "out.wav").exists()


def test_a_pass_over_an_export_cut_after_it_was_read_raises(tmp_path: Path) -> None:
    exported = holed(tmp_path)
    truncate(exported.path, 3)
    with pytest.raises(RuntimeError, match="held 26 frames, not 30"):
        samples_of(exported, 0)


def test_positions_map_through_runs_to_the_capture_index(tmp_path: Path) -> None:
    exported = holed(tmp_path)
    assert exported.capture_frame(3.25) == 3.25
    assert exported.capture_frame(15.5) == 15.5
    assert exported.capture_frame(24.0) == 21.0
    assert exported.capture_frame(29.0) == 26.0
    with pytest.raises(ValueError):
        exported.capture_frame(11.0)
    with pytest.raises(ValueError):
        exported.capture_frame(30.0)


def test_capture_frames_map_back_through_runs_to_the_export(tmp_path: Path) -> None:
    exported = holed(tmp_path)
    assert exported.position(3) == 3
    assert exported.position(15) == 15
    assert exported.position(21) == 24
    assert exported.position(26) == 29
    for run in HOLED_RUNS:
        for wav_frame in range(run["wav_frame"], run["wav_frame"] + run["frames"]):
            assert exported.position(int(exported.capture_frame(wav_frame))) == wav_frame
    # Capture frames 10-13 were lost and measured; 27 is past the last run.
    for frame in (10, 13, 27, -1):
        with pytest.raises(ValueError, match="in no run"):
            exported.position(frame)


def test_a_span_touches_a_hole_it_overlaps_or_straddles(tmp_path: Path) -> None:
    exported = holed(tmp_path)
    assert not exported.touches_hole(0, 10)
    assert exported.touches_hole(5, 6)
    assert exported.touches_hole(12, 1)
    assert not exported.touches_hole(14, 6)
    assert exported.touches_hole(19, 2)
    assert not exported.touches_hole(23, 5)
    assert exported.touches_hole(26, 3)
    assert not exported.touches_hole(28, 2)


def test_only_an_unmeasured_hole_is_one_the_index_skipped(tmp_path: Path) -> None:
    exported = holed(tmp_path)
    assert not exported.unmeasured_between(2, 16)
    assert exported.unmeasured_between(16, 24)
    assert exported.unmeasured_between(24, 16)
    assert exported.unmeasured_between(25, 29)
    assert not exported.unmeasured_between(23, 27)


def failure_of(tmp: Path, told: dict[str, object], reader: str = READER) -> Failure:
    write_wav(tmp / "prepared.wav", [(1, 2)] * 8)
    failed = read(tmp, log_of(tmp, told), reader)
    assert isinstance(failed, Failure), failed
    assert not (tmp / "out.wav").exists()
    return failed


GOOD_MAP = export_map(8, [run_(0, 0, 8)], [])


@pytest.mark.parametrize(
    ("told", "reason"),
    [
        ({"status": 1, "stderr": "error: overlapping segments"}, "export_refused"),
        ({"status": 2, "stderr": "usage"}, "reader_usage"),
        ({"status": 101, "stderr": "panicked"}, "reader_contract"),
        ({"stdout": "not json"}, "reader_contract"),
        ({"wav": "prepared.wav", "map": {"wav_frames": 8}}, "reader_contract"),
        ({"wav": "prepared.wav", "map": export_map(8, [run_(0, 0, 6)], [])}, "reader_contract"),
        ({"wav": "prepared.wav", "map": export_map(8, [run_(1, 0, 7)], [])}, "reader_contract"),
        ({"wav": "prepared.wav", "map": export_map(9, [run_(0, 0, 9)], [])}, "reader_contract"),
        ({"wav": "prepared.wav", "map": export_map(8, [run_(0, -1, 8)], [])}, "reader_contract"),
        (
            {
                "wav": "prepared.wav",
                "map": export_map(8, [run_(0, 0, 8)], [hole(8, 8, 0, "guessed")]),
            },
            "reader_contract",
        ),
        (
            {
                "wav": "prepared.wav",
                "map": export_map(8, [run_(0, 0, 8)], [hole(8, 8, 0, ["measured"])]),
            },
            "reader_contract",
        ),
    ],
)
def test_each_failure_of_the_reader_is_named(
    tmp_path: Path, told: dict[str, object], reason: str
) -> None:
    failed = failure_of(tmp_path, told)
    assert failed.reason == reason, failed
    if "stderr" in told and reason != "reader_contract":
        assert failed.detail == told["stderr"]


def test_a_missing_capture_is_named(tmp_path: Path) -> None:
    failed = read(tmp_path, tmp_path / "absent.framelog")
    assert isinstance(failed, Failure)
    assert failed.reason == "missing"


def test_a_missing_reader_is_named(tmp_path: Path) -> None:
    failed = failure_of(tmp_path, {"wav": "prepared.wav", "map": GOOD_MAP}, str(tmp_path / "nope"))
    assert failed.reason == "reader_missing"


@pytest.mark.parametrize(("rate_hz", "width"), [(48000, 2), (16000, 4)])
def test_an_export_unlike_the_grant_is_named(tmp_path: Path, rate_hz: int, width: int) -> None:
    write_wav(tmp_path / "prepared.wav", [(1, 2)] * 8, rate_hz, width)
    failed = read(tmp_path, log_of(tmp_path, {"wav": "prepared.wav", "map": GOOD_MAP}))
    assert isinstance(failed, Failure)
    assert failed.reason == "capture_differs"


def test_an_export_with_another_channel_count_is_named(tmp_path: Path) -> None:
    write_wav(tmp_path / "prepared.wav", [(1, 2)] * 8)
    log = log_of(tmp_path, {"wav": "prepared.wav", "map": GOOD_MAP})
    failed = capture.read(log, tmp_path / "out.wav", READER, 16000, "s16_le", ("a", "b", "c"))
    assert isinstance(failed, Failure)
    assert failed.reason == "capture_differs"


def test_the_scratch_directory_is_cleared_first_and_removed_after(tmp_path: Path) -> None:
    leftover = tmp_path / capture.SCRATCH
    leftover.mkdir()
    (leftover / "mic-0001.wav").write_bytes(b"left by a killed align")
    with capture.scratch(tmp_path) as scratch:
        assert scratch == leftover
        assert list(scratch.iterdir()) == []
        (scratch / "mic-0002.wav").write_bytes(b"")
    assert not leftover.exists()


def test_the_scratch_directory_is_removed_when_the_block_raises(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError), capture.scratch(tmp_path):
        raise RuntimeError
    assert not (tmp_path / capture.SCRATCH).exists()


@pytest.mark.parametrize("leftover", ["link", "file"])
def test_a_leftover_link_or_file_under_the_scratch_name_is_replaced(
    tmp_path: Path, leftover: str
) -> None:
    run = tmp_path / "run"
    run.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "kept").write_bytes(b"not the run's")
    if leftover == "link":
        (run / capture.SCRATCH).symlink_to(outside, target_is_directory=True)
    else:
        (run / capture.SCRATCH).write_bytes(b"left in the way")
    with capture.scratch(run) as scratch:
        assert not scratch.is_symlink()
        assert scratch.is_dir()
        assert list(scratch.iterdir()) == []
    assert not (run / capture.SCRATCH).exists()
    assert [p.name for p in outside.iterdir()] == ["kept"]
    assert (outside / "kept").read_bytes() == b"not the run's"


def test_a_scratch_directory_that_cannot_be_made_raises_unwritable(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    run.chmod(0o555)
    try:
        assert not os.access(run, os.W_OK), "the test's process can write a 0o555 directory"
        with pytest.raises(capture.Unwritable) as raised, capture.scratch(run):
            pytest.fail("the block ran")
    finally:
        run.chmod(0o755)
    assert raised.value.reason == os.strerror(errno.EACCES)
    assert raised.value.leftover is None
    assert list(run.iterdir()) == []


def test_a_leftover_that_cannot_be_cleared_raises_unwritable_naming_it(tmp_path: Path) -> None:
    held = tmp_path / capture.SCRATCH / "held"
    held.mkdir(parents=True)
    (held / "mic-0002.wav").write_bytes(b"")
    held.chmod(0o555)
    try:
        assert not os.access(held, os.W_OK), "the test's process can write a 0o555 directory"
        assert os.access(tmp_path, os.W_OK)
        with pytest.raises(capture.Unwritable) as raised, capture.scratch(tmp_path):
            pytest.fail("the block ran")
    finally:
        held.chmod(0o755)
    assert raised.value.reason == os.strerror(errno.EACCES)
    assert raised.value.leftover == held / "mic-0002.wav"


def test_an_os_error_in_the_block_is_not_unwritable(tmp_path: Path) -> None:
    with pytest.raises(OSError) as raised, capture.scratch(tmp_path) as scratch:
        (scratch / "absent").unlink()
    assert not isinstance(raised.value, capture.Unwritable)
    assert not (tmp_path / capture.SCRATCH).exists()
