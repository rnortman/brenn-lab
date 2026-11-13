"""The ALSA module on the host's libasound, silently.

The stream path runs on ALSA's `null` PCM, which discards what it is given and
has no device behind it: so nothing here hands a frame to a real output. The
playback role's `check` runs on the attached reSpeaker Flex XVF3800, which
resolves the card and reads its mixer, sets nothing and opens no stream.
"""

import errno
import re

import pytest

from brenn_lab.data import Check, Command, Event, Failure
from brenn_lab.playback import alsa
from brenn_lab.playback.device import HeldControl, PlaybackDevice
from brenn_lab.playback.messages import Channel, Checked
from brenn_lab.playback.output import ControlName
from brenn_lab.playback.role import PlaybackRole

CARD = "Flex XVF3800 L16K6Ch"
USB_ID = "2886:0022"
LEVEL = ControlName("PCM", 1)
HELD = ControlName("PCM", 0)
WIDTHS = {"s16_le": 2, "s32_le": 4}


def loaded() -> alsa.Lib:
    lib = alsa.library()
    assert not isinstance(lib, Failure), lib
    return lib


def test_the_library_loads_and_reports_its_version() -> None:
    record = alsa.library_record(loaded())
    assert record.name == "libasound.so.2"
    assert re.fullmatch(r"1\.\d+\.\d+(\.\d+)?", record.version), record.version


@pytest.mark.parametrize(
    ("rate_hz", "channels", "sample_format"),
    [(16000, 2, "s16_le"), (48000, 1, "s32_le")],
)
def test_a_clip_is_handed_whole_to_null(rate_hz: int, channels: int, sample_format: str) -> None:
    lib = loaded()
    stream = alsa.open_stream(
        lib, "null", rate_hz, channels, sample_format, rate_hz // 50, rate_hz // 2
    )
    assert isinstance(stream, alsa.AlsaStream), stream
    try:
        g = stream.granted
        assert (g.rate_hz, g.channels, g.sample_format) == (rate_hz, channels, sample_format)
        assert 0 < g.period_frames <= g.buffer_frames
        frame_bytes = channels * WIDTHS[sample_format]
        chunk = rate_hz // 10
        clip = bytes(range(256)) * (rate_hz * frame_bytes // 256)
        total = len(clip) // frame_bytes
        handed = 0
        while handed < total:
            written = stream.write(clip[handed * frame_bytes : (handed + chunk) * frame_bytes])
            assert isinstance(written, int), written
            assert written > 0
            handed += written
        assert handed == total
        delay = stream.delay()
        assert isinstance(delay, int) and delay >= 0, delay
        underrun = alsa.AlsaWriteError(True, "underrun", -errno.EPIPE)
        assert stream.recover(underrun) is None
        assert stream.write(clip[: chunk * frame_bytes]) == chunk
        assert stream.drain() is None
    finally:
        stream.close()


class Guarded(alsa.AlsaOutput):
    """The Flex's output, which fails a test that sets a control or opens a stream."""

    def __init__(self) -> None:
        super().__init__(CARD)
        self.touched: list[str] = []

    def set(self, control: ControlName, channels: tuple[Channel, ...]) -> Failure | None:
        self.touched.append(f"set {control}")
        raise AssertionError("check set a control")

    def open(
        self,
        rate_hz: int,
        channels: int,
        sample_format: str,
        period_frames: int,
        buffer_frames: int,
    ) -> alsa.AlsaStream | Failure:
        self.touched.append("open")
        raise AssertionError("check opened a stream")


def mixer_now() -> dict[ControlName, tuple[Channel, ...]]:
    output = alsa.AlsaOutput(CARD)
    attached = output.attach()
    assert not isinstance(attached, Failure), attached
    try:
        read = output.read((LEVEL, HELD))
        assert not isinstance(read, Failure), read
        return dict(zip((LEVEL, HELD), read, strict=True))
    finally:
        output.detach()


def test_check_on_the_flex_names_it_reads_its_controls_and_touches_nothing() -> None:
    device = PlaybackDevice(
        output="alsa",
        card=CARD,
        source_dir="/",
        cap_mb=-2400,
        quiet_hours=(),
        level=LEVEL,
        held=(HeldControl(HELD, 0),),
    )
    output = Guarded()
    events: list[Event] = []
    role = PlaybackRole("speaker", "bench", device, output, events.append, None)
    before = mixer_now()
    reply = role.handle(Command("speaker", Check()))
    checked = reply.outcome
    assert isinstance(checked, Checked), checked
    assert mixer_now() == before
    assert output.touched == [] and events == []

    d = checked.device
    assert d.usb_id == USB_ID
    assert CARD.lower() in d.card_name.lower() or CARD.lower() in d.card_long_name.lower()
    assert d.pcm == f"hw:{d.card_index},0"
    assert checked.output.kind == "alsa"
    assert checked.output.library.name == "libasound.so.2"

    assert [(c.element, c.index, c.part) for c in checked.controls] == [
        ("PCM", 1, "level"),
        ("PCM", 0, "held"),
    ]
    for c in checked.controls:
        assert c.range is not None and c.range.min_mb < c.range.max_mb, c
        assert c.channels == before[ControlName(c.element, c.index)]
        assert c.channels, c
        for channel in c.channels:
            assert channel.volume_mb is not None and channel.switch is not None, c
    print(checked)
