import array
import errno
import hashlib
import wave
from pathlib import Path

import pytest

from brenn_lab.data import Failure
from brenn_lab.playback import source
from brenn_lab.playback.source import Judged


def write_wav(path: Path, samples: list[int], channels: int, width: int, rate: int) -> bytes:
    data = array.array({2: "h", 4: "i"}[width], samples)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(data.tobytes())
    return path.read_bytes()


def judged(root: Path, name: str) -> Judged:
    result = source.judge(str(root), name)
    assert isinstance(result, Judged), result
    return result


def failure(result: object) -> str:
    assert isinstance(result, Failure), result
    return result.reason


def test_a_16_bit_source(tmp_path: Path) -> None:
    raw = write_wav(tmp_path / "a.wav", [0, 100, -16384, 3, 7, -2], 2, 2, 16000)
    s = judged(tmp_path, "a.wav").source
    assert s.name == "a.wav"
    assert s.sha256 == hashlib.sha256(raw).hexdigest()
    assert (s.frames, s.rate_hz, s.channels, s.sample_format) == (3, 16000, 2, "s16_le")
    assert s.peak_mb == -602


def test_a_32_bit_source(tmp_path: Path) -> None:
    raw = write_wav(tmp_path / "b.wav", [1 << 30, -(1 << 31) + 1, 5], 1, 4, 48000)
    s = judged(tmp_path, "b.wav").source
    assert s.sha256 == hashlib.sha256(raw).hexdigest()
    assert (s.frames, s.rate_hz, s.channels, s.sample_format) == (3, 48000, 1, "s32_le")
    assert s.peak_mb == 0


def test_the_most_negative_sample_is_full_scale(tmp_path: Path) -> None:
    write_wav(tmp_path / "a.wav", [-32768, 0], 1, 2, 16000)
    assert judged(tmp_path, "a.wav").source.peak_mb == 0


def test_a_silent_source_has_no_peak(tmp_path: Path) -> None:
    write_wav(tmp_path / "a.wav", [0] * 64, 2, 2, 16000)
    assert judged(tmp_path, "a.wav").source.peak_mb is None


def test_a_source_in_a_subdirectory(tmp_path: Path) -> None:
    (tmp_path / "clips").mkdir()
    write_wav(tmp_path / "clips" / "a.wav", [1, 2], 1, 2, 16000)
    assert judged(tmp_path, "clips/a.wav").path == (tmp_path / "clips" / "a.wav").resolve()


def test_the_hash_covers_chunks_after_the_data(tmp_path: Path) -> None:
    raw = write_wav(tmp_path / "a.wav", [1, 2], 1, 2, 16000)
    tail = b"LIST\x04\x00\x00\x00abcd"
    (tmp_path / "a.wav").write_bytes(raw + tail)
    s = judged(tmp_path, "a.wav").source
    assert s.sha256 == hashlib.sha256(raw + tail).hexdigest()
    assert s.frames == 2


def test_8_and_24_bit_sources_are_refused(tmp_path: Path) -> None:
    for width in (1, 3):
        with wave.open(str(tmp_path / f"{width}.wav"), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(width)
            w.setframerate(16000)
            w.writeframes(b"\x00" * width * 4)
        assert failure(source.judge(str(tmp_path), f"{width}.wav")) == "source_format"


def test_a_file_that_is_not_a_wav_is_refused(tmp_path: Path) -> None:
    (tmp_path / "a.wav").write_bytes(b"not a wav at all")
    assert failure(source.judge(str(tmp_path), "a.wav")) == "source_format"


def test_a_float_wav_is_refused(tmp_path: Path) -> None:
    raw = bytearray(write_wav(tmp_path / "a.wav", [0, 0], 1, 4, 16000))
    raw[20:22] = (3).to_bytes(2, "little")
    (tmp_path / "a.wav").write_bytes(bytes(raw))
    assert failure(source.judge(str(tmp_path), "a.wav")) == "source_format"


def test_a_truncated_wav_is_refused(tmp_path: Path) -> None:
    raw = write_wav(tmp_path / "a.wav", list(range(100)), 1, 2, 16000)
    (tmp_path / "a.wav").write_bytes(raw[:-10])
    assert failure(source.judge(str(tmp_path), "a.wav")) == "source_format"


@pytest.mark.parametrize(
    ("samples", "channels", "width", "extra"),
    [
        (list(range(1000)), 2, 2, 2),
        (list(range(1000)), 1, 2, 1),
        (list(range(1000)), 1, 4, 2),
        ([7] * 200_000, 2, 2, 2),
    ],
)
def test_a_data_chunk_of_a_partial_frame_is_refused(
    tmp_path: Path, samples: list[int], channels: int, width: int, extra: int
) -> None:
    raw = bytearray(write_wav(tmp_path / "a.wav", samples, channels, width, 16000))
    data_at = raw.index(b"data")
    size = int.from_bytes(raw[data_at + 4 : data_at + 8], "little")
    raw[data_at + 4 : data_at + 8] = (size + extra).to_bytes(4, "little")
    raw[4:8] = (int.from_bytes(raw[4:8], "little") + extra).to_bytes(4, "little")
    (tmp_path / "a.wav").write_bytes(bytes(raw) + b"\x01" * extra)
    assert failure(source.judge(str(tmp_path), "a.wav")) == "source_format"


@pytest.mark.parametrize(
    "name", ["../a.wav", "/etc/passwd", "", "x/../../a.wav", "a\0.wav", "a\ud800.wav"]
)
def test_a_path_that_leaves_the_source_directory_is_refused(tmp_path: Path, name: str) -> None:
    root = tmp_path / "sources"
    root.mkdir()
    write_wav(tmp_path / "a.wav", [1], 1, 2, 16000)
    assert failure(source.judge(str(root), name)) == "source_outside"


def test_a_link_out_of_the_source_directory_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "sources"
    root.mkdir()
    write_wav(tmp_path / "a.wav", [1], 1, 2, 16000)
    (root / "a.wav").symlink_to(tmp_path / "a.wav")
    assert failure(source.judge(str(root), "a.wav")) == "source_outside"


def test_a_missing_file_or_a_directory_is_refused(tmp_path: Path) -> None:
    (tmp_path / "d").mkdir()
    assert failure(source.judge(str(tmp_path), "missing.wav")) == "source_missing"
    assert failure(source.judge(str(tmp_path), "d")) == "source_missing"


def frames_of(path: Path) -> tuple[wave._wave_params, bytes]:
    with wave.open(str(path), "rb") as r:
        return r.getparams(), r.readframes(r.getnframes())


@pytest.mark.parametrize(("channels", "width", "rate"), [(2, 2, 16000), (1, 4, 48000)])
def test_the_copy_holds_exactly_the_sources_frames(
    tmp_path: Path, channels: int, width: int, rate: int
) -> None:
    samples = [(i * 7919) % 20000 - 10000 for i in range(200000 * channels)]
    write_wav(tmp_path / "a.wav", samples, channels, width, rate)
    copy = tmp_path / "copy.wav"
    assert source.write_copy(judged(tmp_path, "a.wav"), copy) is None
    params, frames = frames_of(copy)
    want_params, want_frames = frames_of(tmp_path / "a.wav")
    assert params == want_params
    assert frames == want_frames
    assert judged(tmp_path, "copy.wav").source.frames == 200000


def test_the_copy_of_a_source_with_trailing_chunks_is_canonical(tmp_path: Path) -> None:
    raw = write_wav(tmp_path / "a.wav", [1, 2, 3], 1, 2, 16000)
    (tmp_path / "a.wav").write_bytes(raw + b"LIST\x04\x00\x00\x00abcd")
    copy = tmp_path / "copy.wav"
    assert source.write_copy(judged(tmp_path, "a.wav"), copy) is None
    assert copy.read_bytes() == raw


def test_a_source_changed_after_it_was_judged_leaves_no_copy(tmp_path: Path) -> None:
    write_wav(tmp_path / "a.wav", [1, 2, 3], 1, 2, 16000)
    j = judged(tmp_path, "a.wav")
    write_wav(tmp_path / "a.wav", [1, 2, 4], 1, 2, 16000)
    copy = tmp_path / "copy.wav"
    assert failure(source.write_copy(j, copy)) == "source_changed"
    assert not copy.exists()


def test_a_source_reshaped_after_it_was_judged_leaves_no_copy(tmp_path: Path) -> None:
    write_wav(tmp_path / "a.wav", [1, 2, 3, 4], 1, 2, 16000)
    j = judged(tmp_path, "a.wav")
    write_wav(tmp_path / "a.wav", [1, 2, 3, 4], 2, 2, 16000)
    copy = tmp_path / "copy.wav"
    assert failure(source.write_copy(j, copy)) == "source_changed"
    assert not copy.exists()


def test_a_source_removed_after_it_was_judged_leaves_no_copy(tmp_path: Path) -> None:
    write_wav(tmp_path / "a.wav", [1, 2, 3], 1, 2, 16000)
    j = judged(tmp_path, "a.wav")
    (tmp_path / "a.wav").unlink()
    copy = tmp_path / "copy.wav"
    removed = source.write_copy(j, copy)
    assert failure(removed) == "source_changed"
    assert isinstance(removed, Failure) and removed.data == {"sha256": j.source.sha256}
    assert not copy.exists()


def test_a_copy_that_cannot_be_written_is_role_io(tmp_path: Path) -> None:
    write_wav(tmp_path / "a.wav", [1, 2, 3], 1, 2, 16000)
    j = judged(tmp_path, "a.wav")
    assert failure(source.write_copy(j, tmp_path / "no" / "copy.wav")) == "role_io"


def test_an_existing_file_is_not_overwritten_or_removed(tmp_path: Path) -> None:
    write_wav(tmp_path / "a.wav", [1, 2, 3], 1, 2, 16000)
    copy = tmp_path / "copy.wav"
    copy.write_bytes(b"someone else's")
    assert failure(source.write_copy(judged(tmp_path, "a.wav"), copy)) == "role_io"
    assert copy.read_bytes() == b"someone else's"


def test_a_write_that_fails_midway_is_role_io_and_leaves_no_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_wav(tmp_path / "a.wav", list(range(30000)), 1, 2, 16000)
    j = judged(tmp_path, "a.wav")

    def full(self: wave.Wave_write, data: bytes) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(wave.Wave_write, "writeframesraw", full)
    copy = tmp_path / "copy.wav"
    assert failure(source.write_copy(j, copy)) == "role_io"
    assert not copy.exists()
