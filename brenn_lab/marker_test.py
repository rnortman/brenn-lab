import array
import math
import sys
import wave
from pathlib import Path

import pytest

from brenn_lab import marker
from brenn_lab.data import Failure, decode_value, encode_value
from brenn_lab.marker import Carried, Marked, Span

FORMATS = {"s16_le": ("h", 2), "s32_le": ("i", 4)}


def write_wav(path: Path, samples: list[int], channels: int, sample_format: str, rate: int) -> None:
    typecode, width = FORMATS[sample_format]
    data = array.array(typecode, samples)
    if sys.byteorder == "big":
        data.byteswap()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(data.tobytes())


def read_wav(path: Path) -> tuple[int, int, str, array.array[int]]:
    with wave.open(str(path), "rb") as r:
        sample_format = {2: "s16_le", 4: "s32_le"}[r.getsampwidth()]
        samples = array.array(FORMATS[sample_format][0], r.readframes(r.getnframes()))
        if sys.byteorder == "big":
            samples.byteswap()
        return r.getframerate(), r.getnchannels(), sample_format, samples


def recognised(path: Path) -> Carried | None:
    rate, channels, sample_format, samples = read_wav(path)
    total = len(samples) // channels
    head = samples[: channels * marker.head_frames(rate)]
    tail = samples[len(samples) - channels * marker.tail_frames(rate) :]
    return marker.recognise(head, tail, rate, channels, sample_format, total)


def content(frames: int, channels: int) -> list[int]:
    """Frames that are not silence and differ from channel to channel."""
    return [((7919 * i) % 20001) - 10000 for i in range(frames * channels)]


def test_the_codes_are_the_published_family() -> None:
    # Shift 1018 is GPS PRN 1, whose first ten chips are 1100100000 (octal 1440).
    prn1 = marker.code(1018)
    assert "".join("1" if c < 0 else "0" for c in prn1[:10]) == "1100100000"
    assert len(prn1) == marker.CHIPS
    assert len({marker.code(s) for s in range(40)}) == 40


def test_a_code_outside_the_family_is_refused() -> None:
    with pytest.raises(ValueError):
        marker.code(1022)
    with pytest.raises(ValueError):
        marker.code(-1)


def test_a_member_has_two_codes() -> None:
    assert marker.codes(0) == (0, 1)
    assert marker.codes(510) == (1020, 1021)


@pytest.mark.parametrize("rate", [8000, 16000, 48000])
def test_a_chip_centre_is_half_scale_with_the_chips_sign(rate: int) -> None:
    samples = marker.frames(5, "start", rate, "s16_le")
    chips = marker.code(10)
    for k in range(marker.CHIPS):
        n = (k + 1) * rate // marker.CHIP_HZ
        assert samples[n] == 16384 * chips[k]


def test_a_chip_centre_at_44100_hz_has_the_chips_sign() -> None:
    rate = 44100
    samples = marker.frames(5, "end", rate, "s32_le")
    chips = marker.code(11)
    for k in range(marker.CHIPS):
        n = math.floor((k + 1) * rate / marker.CHIP_HZ + 0.5)
        assert samples[n] * chips[k] >= 0.98 * 2**30


def test_literal_samples_of_member_0_at_16_khz() -> None:
    # Code 0's first ten chips are +1 (both registers start all ones). At
    # 16 kHz a chip is 8 frames: frame 8 is chip 0's centre on a carrier peak,
    # frame 4 a carrier trough halfway up chip 0's pulse, frame 12 the trough
    # where chips 0 and 1 meet, frame 2 a carrier zero.
    samples = marker.frames(0, "start", 16000, "s16_le")
    assert len(samples) == 8193 == marker.length(16000)
    assert samples[0] == 0
    assert abs(samples[2]) <= 1
    assert samples[4] == -8192
    assert samples[8] == 16384
    assert samples[12] == -16384


def test_value_is_the_unquantised_marker() -> None:
    chips = marker.code(0)
    for n in (0, 4, 8, 12, 1001, 8192):
        t = n / 16000
        assert marker.value(chips, t).real * 0.5 * 32768 == pytest.approx(
            marker.frames(0, "start", 16000, "s16_le")[n], abs=0.5
        )
    # At a chip centre the pulse is whole and the carrier at phase zero.
    assert marker.value(chips, 0.0005) == pytest.approx(1 + 0j)
    assert abs(marker.value(chips, 0.00075)) == pytest.approx(1.0)
    assert marker.value(chips, -0.001) == 0
    assert marker.value(chips, 0.6) == 0


def test_the_layout_of_a_marked_file() -> None:
    m = marker.length(16000)
    total = 8000 + m + 4000 + 100 + 4000 + m + 4000
    assert marker.layout(16000, total) == (Span(8000, m), Span(8000 + m + 8100, m))
    assert marker.layout(16000, total - 101) is None
    assert marker.layout(7999, 10**6) is None
    # Odd rates round the silences halves away from zero.
    assert marker.layout(44101, 10**6) is not None
    lead = marker.layout(44101, 10**6)
    assert lead is not None and lead[0].frame == 22051


@pytest.mark.parametrize(
    ("rate", "channels", "sample_format", "frames"),
    [
        (16000, 1, "s16_le", 1600),
        (16000, 2, "s32_le", 0),
        (8000, 2, "s16_le", 7),
        (44100, 1, "s32_le", 300),
        (48000, 2, "s16_le", 4800),
    ],
)
def test_mark_then_recognise(
    tmp_path: Path, rate: int, channels: int, sample_format: str, frames: int
) -> None:
    source = tmp_path / "in.wav"
    write_wav(source, content(frames, channels), channels, sample_format, rate)
    out = tmp_path / "out.wav"
    marked = marker.mark(source, out, 17)
    assert isinstance(marked, Marked)
    assert (marked.frames, marked.rate_hz, marked.channels, marked.sample_format) == (
        frames,
        rate,
        channels,
        sample_format,
    )
    carried = marked.marker
    assert (carried.definition, carried.member) == ("gold-bpsk-1", 17)
    assert (carried.start_code, carried.end_code) == (34, 35)
    out_rate, out_channels, out_format, samples = read_wav(out)
    assert (out_rate, out_channels, out_format) == (rate, channels, sample_format)
    m = marker.length(rate)
    assert carried.start.frames == carried.end.frames == m
    # The content lies unchanged between the gaps.
    gap = round(0.25 * rate)
    first = carried.start.frame + m + gap
    assert list(samples[first * channels : (first + frames) * channels]) == content(
        frames, channels
    )
    assert carried.end.frame == first + frames + gap
    assert len(samples) // channels == carried.end.frame + m + gap
    assert recognised(out) == carried


def test_a_carried_record_round_trips_through_the_codec(tmp_path: Path) -> None:
    carried = Carried("gold-bpsk-1", 2, 4, 5, Span(8000, 8193), Span(28193, 8193))
    assert decode_value(Carried, encode_value(carried), "marker") == carried


@pytest.fixture
def marked(tmp_path: Path) -> Path:
    source = tmp_path / "in.wav"
    write_wav(source, content(800, 2), 2, "s16_le", 16000)
    out = tmp_path / "marked.wav"
    assert isinstance(marker.mark(source, out, 4), Marked)
    return out


def rewrite(path: Path, change: int, at_frame: int, channel: int) -> None:
    rate, channels, sample_format, samples = read_wav(path)
    samples[at_frame * channels + channel] += change
    path.unlink()
    write_wav(path, list(samples), channels, sample_format, rate)


def test_an_unmarked_file_carries_none(tmp_path: Path) -> None:
    path = tmp_path / "a.wav"
    write_wav(path, content(40000, 1), 1, "s16_le", 16000)
    assert recognised(path) is None


def test_a_file_one_frame_short_carries_none(marked: Path) -> None:
    rate, channels, sample_format, samples = read_wav(marked)
    marked.unlink()
    write_wav(marked, list(samples[:-channels]), channels, sample_format, rate)
    assert recognised(marked) is None


def test_a_sample_changed_by_one_is_still_the_marker(marked: Path) -> None:
    rewrite(marked, 1, 8000 + 100, 1)
    assert recognised(marked) is not None


@pytest.mark.parametrize("which", ["start", "end"])
def test_a_sample_changed_by_two_carries_none(marked: Path, which: str) -> None:
    carried = recognised(marked)
    assert carried is not None
    span = carried.start if which == "start" else carried.end
    rewrite(marked, 2, span.frame + 4001, 0)
    assert recognised(marked) is None


def test_one_channel_altered_carries_none(marked: Path) -> None:
    rate, channels, sample_format, samples = read_wav(marked)
    for n in range(len(samples) // channels):
        samples[n * channels + 1] //= 2
    marked.unlink()
    write_wav(marked, list(samples), channels, sample_format, rate)
    assert recognised(marked) is None


def reason(result: object) -> str:
    assert isinstance(result, Failure), result
    return result.reason


@pytest.mark.parametrize("member", [-1, 511])
def test_mark_refuses_a_member_outside_the_family(tmp_path: Path, member: int) -> None:
    source = tmp_path / "in.wav"
    write_wav(source, [0] * 10, 1, "s16_le", 16000)
    assert reason(marker.mark(source, tmp_path / "out.wav", member)) == "member_range"
    assert not (tmp_path / "out.wav").exists()


def test_mark_refuses_a_rate_below_8000_hz(tmp_path: Path) -> None:
    source = tmp_path / "in.wav"
    write_wav(source, [0] * 10, 1, "s16_le", 7999)
    assert reason(marker.mark(source, tmp_path / "out.wav", 0)) == "rate_unsupported"
    assert not (tmp_path / "out.wav").exists()


def test_mark_refuses_an_existing_out(tmp_path: Path) -> None:
    source = tmp_path / "in.wav"
    write_wav(source, [0] * 10, 1, "s16_le", 16000)
    out = tmp_path / "out.wav"
    out.write_bytes(b"kept")
    assert reason(marker.mark(source, out, 0)) == "out_exists"
    assert out.read_bytes() == b"kept"


def test_mark_refuses_a_missing_source(tmp_path: Path) -> None:
    assert reason(marker.mark(tmp_path / "no.wav", tmp_path / "out.wav", 0)) == "unreadable"


@pytest.mark.parametrize("width", [1, 3])
def test_mark_refuses_8_and_24_bit_sources(tmp_path: Path, width: int) -> None:
    source = tmp_path / "in.wav"
    with wave.open(str(source), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(width)
        w.setframerate(16000)
        w.writeframes(bytes(width * 10))
    assert reason(marker.mark(source, tmp_path / "out.wav", 0)) == "source_format"
    assert not (tmp_path / "out.wav").exists()


def test_mark_refuses_a_file_that_is_not_a_wav(tmp_path: Path) -> None:
    source = tmp_path / "in.wav"
    source.write_bytes(b"not a wav at all")
    assert reason(marker.mark(source, tmp_path / "out.wav", 0)) == "source_format"


def test_mark_refuses_a_truncated_wav(tmp_path: Path) -> None:
    source = tmp_path / "in.wav"
    write_wav(source, [1] * 100, 1, "s16_le", 16000)
    source.write_bytes(source.read_bytes()[:-10])
    assert reason(marker.mark(source, tmp_path / "out.wav", 0)) == "source_format"
