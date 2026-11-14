import array
import datetime
import threading
import time
import wave
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from brenn_lab import marker
from brenn_lab.data import (
    MONOTONIC,
    REALTIME,
    Check,
    ClockReading,
    ClockValue,
    Command,
    Event,
    Failure,
    Release,
    Reply,
    now,
)
from brenn_lab.place import Place
from brenn_lab.playback import role as role_module
from brenn_lab.playback import source
from brenn_lab.playback.device import HeldControl, PlaybackDevice
from brenn_lab.playback.limits import QuietMinutes
from brenn_lab.playback.messages import (
    Channel,
    Checked,
    Clock,
    ClockRead,
    Control,
    Ended,
    Failed,
    Play,
    Released,
    Started,
    Stop,
    Stopped,
    Underrun,
    VolumeRange,
)
from brenn_lab.playback.output import Attached, ControlName
from brenn_lab.playback.role import PlaybackRole
from brenn_lab.testing.fake_output import CARD, LIBRARY, Armable, FakeControl, FakeOutput

LEVEL = ControlName("PCM", 1)
HELD = ControlName("PCM", 0)
RATE = 16000
ZONE = datetime.timezone(datetime.timedelta(hours=-5))
NOON = datetime.datetime(2026, 11, 13, 12, 0, tzinfo=ZONE)


def write_clip(path: Path, seconds: float, peak: int = 16384) -> bytes:
    """A two-channel 16-bit clip at RATE whose samples step through +-peak."""
    frames = round(RATE * seconds)
    samples = array.array("h", [(peak if (i // 2) % 2 else -peak) for i in range(2 * frames)])
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(samples.tobytes())
    with wave.open(str(path), "rb") as r:
        return r.readframes(frames)


def fake_controls(**level: object) -> list[FakeControl]:
    return [FakeControl(LEVEL, **level), FakeControl(HELD)]  # type: ignore[arg-type]


class Bench:
    """A role on a fake output, its source directory, run directory and events."""

    def __init__(
        self,
        tmp: Path,
        output: FakeOutput | None = None,
        quiet: tuple[QuietMinutes, ...] = (),
        cap_mb: int = -1800,
        held: tuple[HeldControl, ...] = (HeldControl(HELD, 0),),
        name: str = "speaker",
    ) -> None:
        self.sources = tmp / "sources"
        self.sources.mkdir(exist_ok=True)
        self.data = tmp / "sources-run" / name
        self.output = output if output is not None else FakeOutput(fake_controls(), speed=4.0)
        self.events: list[Event] = []
        self.ended = threading.Event()
        # The sink raises on a play's end instead of taking it.
        self.refuse_end = False
        # The role's clock raises instead of answering.
        self.clock_broken = False
        # The role's clock reads this, while it is set, and the machine's clock otherwise.
        self.at: ClockReading | None = None
        # The frames the output had been handed when each event was reported.
        self.written_at_report: list[int] = []
        self.local = NOON
        device = PlaybackDevice(
            output="alsa",
            selector="Fake",
            source_dir=str(self.sources),
            cap_mb=cap_mb,
            quiet_hours=quiet,
            level=LEVEL,
            held=held,
        )
        self.role = PlaybackRole(
            name,
            "bench",
            device,
            self.output,
            self._sink,
            Place(self.data, tmp / "logs" / name, lambda _: None),
            clock=self._clock,
            local_now=lambda: self.local,
        )
        self.dry_role = PlaybackRole(
            name,
            "bench",
            device,
            self.output,
            self._sink,
            None,
            clock=self._clock,
            local_now=lambda: self.local,
        )

    def _clock(self) -> ClockReading:
        if self.clock_broken:
            raise Unexpected("clock broke")
        return self.at if self.at is not None else now("bench")

    def _sink(self, event: Event) -> None:
        if self.refuse_end and isinstance(event.body, Ended | Failed):
            self.ended.set()
            raise RuntimeError("the sink failed")
        self.written_at_report.append(sum(len(s.frames) for s in self.output.streams))
        self.events.append(event)
        if isinstance(event.body, Ended | Failed):
            self.ended.set()

    def clip(self, name: str, seconds: float, peak: int = 16384) -> bytes:
        return write_clip(self.sources / name, seconds, peak)

    def send(self, body: object, dry: bool = False) -> object:
        role = self.dry_role if dry else self.role
        reply = role.handle(Command(role="speaker", body=body, dry=dry))  # type: ignore[arg-type]
        assert isinstance(reply, Reply)
        return reply.outcome

    def play(
        self,
        source: str = "a.wav",
        volume_mb: int = -1800,
        dry: bool = False,
        start: ClockReading | None = None,
    ) -> object:
        return self.send(Play(source=source, volume_mb=volume_mb, start=start), dry)

    def wait_end(self) -> Ended | Failed:
        assert self.ended.wait(10), self.events
        body = self.events[-1].body
        assert isinstance(body, Ended | Failed)
        return body

    def nothing_done(self) -> None:
        """Nothing written, set, opened or reported."""
        assert not self.data.exists() or list(self.data.iterdir()) == []
        assert self.output.opens == 0
        assert self.output.sets == []
        assert self.output.writes == 0
        assert self.events == []
        assert not self.output.attached


def reason(outcome: object) -> str:
    assert isinstance(outcome, Failure), outcome
    return outcome.reason


def started(outcome: object) -> Started:
    assert isinstance(outcome, Started), outcome
    return outcome


def channels(volume_mb: int | None, switch: bool | None = True) -> tuple[Channel, ...]:
    return (Channel("Front Left", volume_mb, switch), Channel("Front Right", volume_mb, switch))


def test_a_play_to_its_end(tmp_path: Path) -> None:
    b = Bench(tmp_path)
    frames = b.clip("a.wav", 0.35)
    s = started(b.play())
    assert s.play == 1
    assert s.source.frames == 5600 and s.source.peak_mb == -602
    assert s.source.marker is None
    assert s.copy is not None and s.copy.machine == "bench"
    assert s.output.kind == "alsa" and s.output.library == LIBRARY
    assert s.device == CARD
    assert (s.granted.rate_hz, s.granted.channels, s.granted.sample_format) == (16000, 2, "s16_le")
    assert (s.granted.period_frames, s.granted.buffer_frames) == (320, 8000)
    assert s.found == (
        Control("PCM", 1, "level", channels(0)),
        Control("PCM", 0, "held", channels(0)),
    )
    assert s.controls == (
        Control("PCM", 1, "level", channels(-1800)),
        Control("PCM", 0, "held", channels(0)),
    )
    assert (s.level_mb, s.cap_mb) == (-1800, -1800)
    assert s.local_time == "2026-11-13T12:00:00-05:00"
    assert s.first is not None and s.first.frames_handed == 1600
    assert s.first.delay_frames is not None and s.first.at.machine == "bench"

    assert s.asked_start is None

    ended = b.wait_end()
    assert isinstance(ended, Ended)
    assert (ended.play, ended.frames_handed, ended.unplayed_frames) == (1, 5600, 0)
    assert ended.underruns == 0 and ended.restored
    assert ended.last.frames_handed == 5600
    assert (ended.woke, ended.first) == (None, None)
    stream = b.output.streams[0]
    assert stream.drained and stream.closed and not stream.dropped
    assert bytes(stream.frames) == frames
    with wave.open(s.copy.path, "rb") as copy:
        assert copy.readframes(copy.getnframes()) == frames
    assert Path(s.copy.path) == b.data / "0001.wav"
    assert b.output.controls[LEVEL].values == list(channels(0))
    assert not b.output.attached
    assert b.events == [Event("speaker", ended)]


def test_stop_cuts_the_play(tmp_path: Path) -> None:
    b = Bench(tmp_path, FakeOutput(fake_controls(), speed=1.0))
    b.clip("a.wav", 5.0)
    started(b.play())
    stopped = b.send(Stop())
    assert isinstance(stopped, Stopped)
    assert stopped.play == 1 and stopped.restored
    assert 0 < stopped.frames_handed < 80000
    assert stopped.unplayed_frames is not None and stopped.unplayed_frames > 0
    assert stopped.last is not None
    assert (stopped.woke, stopped.first) == (None, None)
    stream = b.output.streams[0]
    assert stream.dropped and stream.closed and not stream.drained
    assert b.output.controls[LEVEL].values == list(channels(0))
    assert b.events == []
    assert reason(b.send(Stop())) == "not_playing"


def test_one_play_at_a_time(tmp_path: Path) -> None:
    b = Bench(tmp_path, FakeOutput(fake_controls(), speed=1.0))
    b.clip("a.wav", 5.0)
    started(b.play())
    assert reason(b.play()) == "already_playing"
    assert reason(b.send(Check())) == "already_playing"
    assert isinstance(b.send(Stop()), Stopped)
    assert started(b.play()).play == 2
    b.send(Stop())


def test_stop_with_nothing_playing(tmp_path: Path) -> None:
    b = Bench(tmp_path)
    assert reason(b.send(Stop())) == "not_playing"
    b.clip("a.wav", 0.1)
    started(b.play())
    b.wait_end()
    assert reason(b.send(Stop())) == "not_playing"


@pytest.mark.parametrize(
    ("volume_mb", "cap_mb", "quiet", "expected"),
    [
        (-1700, -1800, (), "over_cap"),
        (-7000, -1800, (), "volume_range"),
        (100, 0, (), "volume_range"),
        (-1800, -1800, (QuietMinutes(11 * 60, 13 * 60),), "quiet_hours"),
        (-1800, -1800, (QuietMinutes(12 * 60 + 1, 13 * 60),), "quiet_hours"),
    ],
)
def test_limits_refuse_before_anything_is_done(
    tmp_path: Path, volume_mb: int, cap_mb: int, quiet: tuple[QuietMinutes, ...], expected: str
) -> None:
    b = Bench(tmp_path, quiet=quiet, cap_mb=cap_mb)
    b.clip("a.wav", 90.0)
    assert reason(b.play(volume_mb=volume_mb)) == expected
    b.nothing_done()


def test_a_source_that_changes_before_its_copy_is_refused(tmp_path: Path) -> None:
    b = Bench(tmp_path)
    b.clip("a.wav", 0.2)
    attach = b.output.attach

    def attach_and_change() -> Attached | Failure:
        b.clip("a.wav", 0.2, peak=100)
        return attach()

    b.output.attach = attach_and_change  # type: ignore[method-assign]
    assert reason(b.play()) == "source_changed"
    b.nothing_done()


def test_a_grant_that_differs_is_params(tmp_path: Path) -> None:
    b = Bench(tmp_path, FakeOutput(fake_controls(), grant={"rate_hz": 48000}))
    b.clip("a.wav", 0.2)
    assert reason(b.play()) == "params"
    assert b.output.sets == [] and b.output.open_streams == 0
    assert (b.data / "0001.wav").exists()
    assert not b.output.attached


def test_a_busy_device_is_open(tmp_path: Path) -> None:
    b = Bench(tmp_path)
    b.output.busy = True
    b.clip("a.wav", 0.2)
    assert reason(b.play()) == "open"
    assert b.output.sets == [] and not b.output.attached


def test_a_control_nobody_named_is_unheld(tmp_path: Path) -> None:
    extra = FakeControl(ControlName("Speaker", 0), range=None)
    b = Bench(tmp_path, FakeOutput([*fake_controls(), extra]))
    b.clip("a.wav", 0.2)
    failure = b.play()
    assert reason(failure) == "unheld_control"
    assert isinstance(failure, Failure)
    assert failure.data["controls"] == [{"element": "Speaker", "index": 0}]
    b.nothing_done()
    assert reason(b.send(Check())) == "unheld_control"


def test_a_named_control_the_card_lacks(tmp_path: Path) -> None:
    b = Bench(tmp_path, FakeOutput([FakeControl(LEVEL)]))
    b.clip("a.wav", 0.2)
    assert reason(b.play()) == "no_control"
    b.nothing_done()


@pytest.mark.parametrize(
    ("volume_mb", "held_range", "data"),
    [
        (None, VolumeRange(-6000, 0), {"min_mb": -6000, "max_mb": 0}),
        (0, None, {}),
        (1, VolumeRange(-6000, 0), {"min_mb": -6000, "max_mb": 0}),
        (-6001, VolumeRange(-6000, 0), {"min_mb": -6000, "max_mb": 0}),
    ],
)
def test_a_held_volume_must_match_the_control(
    tmp_path: Path, volume_mb: int | None, held_range: VolumeRange | None, data: dict[str, int]
) -> None:
    output = FakeOutput([FakeControl(LEVEL), FakeControl(HELD, range=held_range)])
    b = Bench(tmp_path, output, held=(HeldControl(HELD, volume_mb),))
    b.clip("a.wav", 0.2)
    refused = b.play()
    assert reason(refused) == "volume_range"
    assert isinstance(refused, Failure) and refused.data == {"control": "'PCM' index 0"} | data
    b.nothing_done()


@pytest.mark.parametrize("volume_mb", [-6000, 0])
def test_a_held_volume_at_either_end_of_its_range_is_accepted(
    tmp_path: Path, volume_mb: int
) -> None:
    b = Bench(tmp_path, held=(HeldControl(HELD, volume_mb),))
    assert isinstance(b.send(Check()), Checked)


class Stubborn(FakeOutput):
    """A fake whose level control, the first time it is set, reads back
    `extra_mb` above what was set on its last channel, or with that channel's
    switch off.
    """

    extra_mb = 0
    switch_off = False
    perturbed = False

    def set(self, control: ControlName, chans: tuple[Channel, ...]) -> Failure | None:
        failure = super().set(control, chans)
        # The raise that ends the play's first move turns every switch on.
        if control == LEVEL and not self.perturbed and chans[-1].switch is True:
            self.perturbed = True
            last = len(chans) - 1
            volume = self.controls[LEVEL].values[last].volume_mb
            assert volume is not None
            self.move(LEVEL, last, volume_mb=volume + self.extra_mb)
            if self.switch_off:
                self.move(LEVEL, last, switch=False)
        return failure


def test_a_read_back_above_the_asked_volume_is_refused(tmp_path: Path) -> None:
    output = Stubborn(fake_controls())
    output.extra_mb = 10
    b = Bench(tmp_path, output)
    b.clip("a.wav", 0.2)
    assert reason(b.play()) == "controls"
    assert output.writes == 0 and output.open_streams == 0
    assert output.controls[LEVEL].values == list(channels(0))
    assert not output.attached


def test_a_switch_that_stays_off_is_refused(tmp_path: Path) -> None:
    output = Stubborn(fake_controls())
    output.switch_off = True
    b = Bench(tmp_path, output)
    b.clip("a.wav", 0.2)
    assert reason(b.play()) == "controls"
    assert output.writes == 0


def test_a_read_back_below_the_asked_volume_is_the_step(tmp_path: Path) -> None:
    b = Bench(tmp_path, FakeOutput(fake_controls(step_mb=700)), cap_mb=-1750)
    b.clip("a.wav", 0.2)
    s = started(b.play(volume_mb=-1750))
    assert s.controls[0] == Control("PCM", 1, "level", channels(-1800))
    assert s.level_mb == -1800
    assert isinstance(b.wait_end(), Ended)


def test_the_level_is_the_highest_channel() -> None:
    controls = (
        Control("PCM", 1, "level", (Channel("L", -3000, True), Channel("R", -1800, True))),
        Control("PCM", 0, "held", (Channel("L", -100, None), Channel("R", -200, None))),
        Control("Speaker", 0, "held", (Channel("Mono", None, True),)),
    )
    assert role_module._volumes(controls) == (-1800, [-100])


def moved(b: Bench, at_write: int, change: dict[str, object], control: ControlName) -> Failed:
    def on_write(n: int) -> None:
        if n == at_write:
            b.output.move(control, 0, **change)  # type: ignore[arg-type]

    b.output.on_write = on_write
    b.clip("a.wav", 5.0)
    started(b.play())
    ended = b.wait_end()
    assert isinstance(ended, Failed)
    return ended


@pytest.mark.parametrize(
    ("change", "control"),
    [
        ({"volume_mb": -1000}, LEVEL),
        ({"volume_mb": -2000}, LEVEL),
        ({"volume_mb": -100}, HELD),
        ({"switch": False}, LEVEL),
    ],
)
def test_a_control_moved_mid_play_ends_it(
    tmp_path: Path, change: dict[str, object], control: ControlName
) -> None:
    b = Bench(tmp_path, FakeOutput(fake_controls(), speed=1.0))
    failed = moved(b, 3, change, control)
    assert failed.failure.reason == "controls_moved"
    assert failed.frames_handed == 3 * 1600
    assert failed.restored
    assert b.output.controls[LEVEL].values == list(channels(0))
    stream = b.output.streams[0]
    assert stream.dropped and stream.closed
    assert failed.unplayed_frames is not None


def test_quiet_hours_beginning_mid_play_end_it(tmp_path: Path) -> None:
    b = Bench(
        tmp_path, FakeOutput(fake_controls(), speed=1.0), quiet=(QuietMinutes(13 * 60, 14 * 60),)
    )

    def on_write(n: int) -> None:
        if n == 2:
            b.local = NOON + datetime.timedelta(hours=1)

    b.output.on_write = on_write
    b.clip("a.wav", 5.0)
    started(b.play())
    failed = b.wait_end()
    assert isinstance(failed, Failed)
    assert failed.failure.reason == "quiet_hours"
    assert failed.frames_handed == 2 * 1600
    assert (failed.woke, failed.first) == (None, None)


def test_an_underrun_is_reported_and_the_play_goes_on(tmp_path: Path) -> None:
    b = Bench(tmp_path)
    frames = b.clip("a.wav", 0.35)

    def on_write(n: int) -> None:
        if n == 2:
            b.output.underruns = 1

    b.output.on_write = on_write
    started(b.play())
    ended = b.wait_end()
    assert isinstance(ended, Ended)
    assert ended.underruns == 1 and ended.frames_handed == 5600
    underrun = b.events[0].body
    assert isinstance(underrun, Underrun)
    assert (underrun.play, underrun.at_frame) == (1, 1600)
    assert bytes(b.output.streams[0].frames) == frames


def test_recoveries_running_out_end_the_play(tmp_path: Path) -> None:
    b = Bench(tmp_path, FakeOutput(fake_controls(), speed=1.0))

    def on_write(n: int) -> None:
        if n == 3:
            b.output.underruns = 100

    b.output.on_write = on_write
    b.clip("a.wav", 5.0)
    started(b.play())
    failed = b.wait_end()
    assert isinstance(failed, Failed)
    assert failed.failure.reason == "stream_died"
    assert b.output.recoveries == role_module.MAX_RECOVERIES
    assert failed.underruns == role_module.MAX_RECOVERIES
    assert failed.restored


def test_a_write_error_that_is_not_an_underrun_ends_the_play(tmp_path: Path) -> None:
    b = Bench(tmp_path, FakeOutput(fake_controls(), speed=1.0))

    def on_write(n: int) -> None:
        if n == 3:
            b.output.errors = 1

    b.output.on_write = on_write
    b.clip("a.wav", 5.0)
    started(b.play())
    failed = b.wait_end()
    assert isinstance(failed, Failed)
    assert failed.failure == Failure("stream_died", "write error")
    assert b.output.recoveries == 0
    assert (failed.frames_handed, failed.underruns) == (2 * 1600, 0)
    assert failed.restored
    assert b.events == [Event("speaker", failed)]


def test_a_write_error_on_the_first_write_fails_the_play(tmp_path: Path) -> None:
    b = Bench(tmp_path)
    b.output.errors = 1
    b.clip("a.wav", 0.2)
    assert reason(b.play()) == "stream_died"
    assert b.output.recoveries == 0
    assert b.output.controls[LEVEL].values == list(channels(0))
    assert b.output.open_streams == 0 and not b.output.attached
    assert b.events == []


def test_the_device_going_away_ends_the_play(tmp_path: Path) -> None:
    b = Bench(tmp_path, FakeOutput(fake_controls(), speed=1.0))

    def on_write(n: int) -> None:
        if n == 3:
            b.output.gone = True

    b.output.on_write = on_write
    b.clip("a.wav", 5.0)
    started(b.play())
    failed = b.wait_end()
    assert isinstance(failed, Failed)
    assert failed.failure.reason == "stream_died"
    assert failed.frames_handed == 2 * 1600
    assert failed.unplayed_frames is None
    assert not failed.restored


def test_controls_that_cannot_be_read_mid_play_end_it(tmp_path: Path) -> None:
    b = Bench(tmp_path, FakeOutput(fake_controls(), speed=1.0))
    unreadable = Failure("mixer", "the mixer could not be read", {"code": -5})

    def on_write(n: int) -> None:
        if n == 3:
            b.output.read_failure = unreadable

    b.output.on_write = on_write
    b.clip("a.wav", 5.0)
    started(b.play())
    failed = b.wait_end()
    assert isinstance(failed, Failed)
    assert failed.failure.reason == "controls_moved"
    assert failed.failure.data == {"code": -5}
    assert unreadable.detail in failed.failure.detail
    assert failed.frames_handed == 3 * 1600
    assert not failed.restored
    # The restoration's read failed, so it wrote nothing: the controls stay as
    # the play set them, at or under the cap.
    assert b.output.sets == [
        (LEVEL, channels(-1800, None)),
        (LEVEL, channels(-1800)),
        (HELD, channels(0)),
    ]
    assert b.output.controls[LEVEL].values == list(channels(-1800))
    stream = b.output.streams[0]
    assert stream.dropped and stream.closed
    assert not b.output.attached


def test_a_write_that_takes_nothing_ends_the_play(tmp_path: Path) -> None:
    b = Bench(tmp_path, FakeOutput(fake_controls(), speed=1.0))

    def on_write(n: int) -> None:
        if n == 3:
            b.output.stalls = 1

    b.output.on_write = on_write
    b.clip("a.wav", 5.0)
    started(b.play())
    failed = b.wait_end()
    assert isinstance(failed, Failed)
    assert failed.failure.reason == "stream_died"
    assert failed.frames_handed == 2 * 1600 and failed.restored


def test_a_write_that_takes_nothing_at_first_fails_the_play(tmp_path: Path) -> None:
    b = Bench(tmp_path)
    b.output.stalls = 1
    b.clip("a.wav", 0.2)
    assert reason(b.play()) == "stream_died"
    assert b.output.controls[LEVEL].values == list(channels(0))
    assert b.output.open_streams == 0 and not b.output.attached


def test_a_buffer_that_never_plays_out_ends_the_play(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(role_module, "PLAY_OUT_GRACE_SECONDS", 0.2)
    b = Bench(tmp_path, FakeOutput(fake_controls(), speed=1.0))
    b.output.stuck = True
    b.clip("a.wav", 0.3)
    begun = time.monotonic()
    started(b.play())
    failed = b.wait_end()
    waited = time.monotonic() - begun
    assert isinstance(failed, Failed)
    assert failed.failure.reason == "stream_died"
    assert failed.failure.data == {"delay_frames": 4800}
    assert "4800 frames left" in failed.failure.detail
    # The deadline is the first delay read at the play's rate, plus the grace.
    assert 0.5 <= waited < 5
    assert (failed.frames_handed, failed.unplayed_frames) == (4800, 4800)
    assert failed.restored
    assert b.output.controls[LEVEL].values == list(channels(0))
    stream = b.output.streams[0]
    assert stream.dropped and stream.closed and not stream.drained
    assert not b.output.attached
    assert b.events == [Event("speaker", failed)]


def test_a_stop_while_a_stuck_buffer_is_waited_on_is_stopped(tmp_path: Path) -> None:
    b = Bench(tmp_path, FakeOutput(fake_controls(), speed=1.0))
    b.output.stuck = True
    b.clip("a.wav", 0.3)
    started(b.play())
    stream = b.output.streams[0]
    deadline = time.monotonic() + 5
    while stream.handed < 4800:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    stopped = b.send(Stop())
    assert isinstance(stopped, Stopped)
    assert (stopped.frames_handed, stopped.unplayed_frames) == (4800, 4800)
    assert stopped.restored
    assert stream.dropped and stream.closed
    assert b.events == []


def test_the_machine_zone_is_not_moved_by_tz(monkeypatch: pytest.MonkeyPatch) -> None:
    offsets = []
    try:
        for tz in ("AAA-14", "BBB+12"):
            monkeypatch.setenv("TZ", tz)
            time.tzset()
            assert datetime.datetime.now().astimezone().utcoffset() == datetime.timedelta(
                hours=14 if tz == "AAA-14" else -12
            )
            offsets.append(datetime.datetime.now(role_module.machine_zone()).utcoffset())
    finally:
        monkeypatch.undo()
        time.tzset()
    assert offsets[0] == offsets[1]


def test_a_machine_with_no_zone_file_is_on_utc(tmp_path: Path) -> None:
    assert role_module.machine_zone(tmp_path / "localtime") == datetime.UTC


def test_the_role_judges_by_the_machine_zone_whatever_tz(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    b = Bench(tmp_path)
    try:
        monkeypatch.setenv("TZ", "AAA-14")
        time.tzset()
        role = PlaybackRole("speaker", "bench", b.role._device, b.output, lambda _: None, None)
        checked = role.handle(Command(role="speaker", body=Check(), dry=False)).outcome
    finally:
        monkeypatch.undo()
        time.tzset()
    assert isinstance(checked, Checked)
    offset = datetime.datetime.fromisoformat(checked.local_time).utcoffset()
    assert offset == datetime.datetime.now(role_module.machine_zone()).utcoffset()
    assert offset != datetime.timedelta(hours=14)


def test_a_kept_copy_that_cannot_be_read_opens_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    b = Bench(tmp_path)
    b.clip("a.wav", 0.2)
    write_copy = source.write_copy

    def write_then_spoil(judged: source.Judged, copy: Path) -> Failure | None:
        failure = write_copy(judged, copy)
        copy.write_bytes(b"RIFF")
        return failure

    monkeypatch.setattr(source, "write_copy", write_then_spoil)
    assert reason(b.play()) == "role_io"
    assert b.output.opens == 0 and b.output.sets == []
    assert not b.output.attached and b.events == []


def slow_copy(b: Bench, monkeypatch: pytest.MonkeyPatch, after: datetime.timedelta) -> None:
    """Writing the kept copy takes `after` by the role's clock."""
    write_copy = source.write_copy

    def write_then_move_the_clock(judged: source.Judged, copy: Path) -> Failure | None:
        failure = write_copy(judged, copy)
        b.local = NOON + after
        return failure

    monkeypatch.setattr(source, "write_copy", write_then_move_the_clock)


def test_quiet_hours_reached_while_the_copy_is_written_refuse_the_play(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    b = Bench(tmp_path, quiet=(QuietMinutes(12 * 60 + 1, 13 * 60),))
    b.clip("a.wav", 0.2)
    slow_copy(b, monkeypatch, datetime.timedelta(minutes=1))
    refused = b.play()
    assert reason(refused) == "quiet_hours"
    assert isinstance(refused, Failure)
    assert refused.data["local_time"] == "2026-11-13T12:01:00-05:00"
    b.nothing_done()
    # The refused play used no number.
    b.local = NOON + datetime.timedelta(hours=2)
    monkeypatch.undo()
    s = started(b.play())
    assert s.play == 1 and Path(s.copy.path) == b.data / "0001.wav"  # type: ignore[union-attr]
    b.wait_end()


def test_the_reply_carries_the_local_time_judged_just_before_the_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    b = Bench(tmp_path, quiet=(QuietMinutes(13 * 60, 14 * 60),))
    b.clip("a.wav", 0.2)
    slow_copy(b, monkeypatch, datetime.timedelta(minutes=1))
    assert started(b.play()).local_time == "2026-11-13T12:01:00-05:00"
    b.wait_end()


def test_a_play_whose_first_read_fails_sets_nothing_and_keeps_its_copy(tmp_path: Path) -> None:
    b = Bench(tmp_path)
    b.clip("a.wav", 0.2)
    unreadable = Failure("mixer", "the mixer could not be read", {"code": -5})
    b.output.read_failure = unreadable
    assert b.play() == unreadable
    assert b.output.sets == [] and b.output.writes == 0
    assert b.output.opens == 1 and b.output.open_streams == 0
    stream = b.output.streams[0]
    assert stream.dropped and stream.closed
    assert not b.output.attached and b.events == []
    # A play that fails at the controls leaves its copy, named nowhere, and
    # uses its number.
    assert (b.data / "0001.wav").exists()
    b.output.read_failure = None
    s = started(b.play())
    assert s.play == 2 and Path(s.copy.path) == b.data / "0002.wav"  # type: ignore[union-attr]
    assert isinstance(b.wait_end(), Ended)


def raise_at_write(b: Bench, at_write: int) -> None:
    def on_write(n: int) -> None:
        if n == at_write:
            raise RuntimeError("the output broke")

    b.output.on_write = on_write


def test_an_error_on_the_first_write_closes_and_restores(tmp_path: Path) -> None:
    b = Bench(tmp_path)
    b.clip("a.wav", 0.2)
    raise_at_write(b, 1)
    with pytest.raises(RuntimeError, match="the output broke"):
        b.play()
    assert b.output.sets != []
    assert b.output.controls[LEVEL].values == list(channels(0))
    assert b.output.open_streams == 0 and b.output.streams[0].dropped
    assert not b.output.attached and b.events == []


class Unexpected(Exception):
    """An exception no output answers with."""


def thread_raised(monkeypatch: pytest.MonkeyPatch) -> list[BaseException | None]:
    raised: list[BaseException | None] = []
    monkeypatch.setattr(threading, "excepthook", lambda args: raised.append(args.exc_value))
    return raised


def join_play(b: Bench) -> None:
    p = b.role._playing
    assert p is not None and p.thread is not None
    p.thread.join()


def test_an_error_in_the_plays_thread_ends_it_and_restores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raised = thread_raised(monkeypatch)
    b = Bench(tmp_path, FakeOutput(fake_controls(), speed=1.0))
    b.clip("a.wav", 5.0)
    raise_at_write(b, 3)
    started(b.play())
    failed = b.wait_end()
    assert isinstance(failed, Failed)
    assert failed.failure.reason == "role_error"
    assert failed.frames_handed == 2 * 1600 and failed.restored
    assert b.output.controls[LEVEL].values == list(channels(0))
    assert b.output.open_streams == 0 and not b.output.attached
    join_play(b)
    assert [str(e) for e in raised] == ["the output broke"]
    assert reason(b.send(Stop())) == "not_playing"


@pytest.mark.parametrize("command", ["release", "stop", "play"])
def test_an_end_that_could_not_be_reported_is_raised_by_the_next_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    raised = thread_raised(monkeypatch)
    b = Bench(tmp_path)
    b.clip("a.wav", 0.2)
    b.refuse_end = True
    started(b.play())
    assert b.ended.wait(10)
    join_play(b)
    assert [str(e) for e in raised] == ["the sink failed"]
    with pytest.raises(RuntimeError, match="before reporting its end") as error:
        b.send(
            {"release": Release(), "stop": Stop(), "play": Play(source="a.wav", volume_mb=-1800)}[
                command
            ]
        )
    assert str(error.value.__cause__) == "the sink failed"
    assert b.output.open_streams == 0 and not b.output.attached
    # Raised once: the next command finds no play.
    assert b.send(Release()) == Released(stopped=None)


def raised_once(raised: list[BaseException | None], call: Armable) -> None:
    [error] = raised
    assert isinstance(error, Unexpected) and str(error) == f"{call} broke"


def plays_again(b: Bench) -> None:
    """The role answers its next `check`, and its next play runs to its end."""
    assert isinstance(b.send(Check()), Checked)
    b.ended.clear()
    b.clip("b.wav", 0.2)
    started(b.play("b.wav"))
    assert isinstance(b.wait_end(), Ended)


@pytest.mark.parametrize("call", ["delay", "drop", "close", "read", "set", "detach"])
def test_a_raise_while_a_cut_play_ends_still_ends_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, call: Armable
) -> None:
    raised = thread_raised(monkeypatch)
    b = Bench(tmp_path, FakeOutput(fake_controls(), speed=1.0))
    b.clip("a.wav", 5.0)

    # The failed write cuts the play at once: the role's next call of any of
    # the six is the one in the play's end.
    def on_write(n: int) -> None:
        if n == 3:
            b.output.errors = 1
            b.output.raises[call] = Unexpected(f"{call} broke")

    b.output.on_write = on_write
    started(b.play())
    failed = b.wait_end()
    assert isinstance(failed, Failed)
    assert failed.failure.reason == "stream_died"
    assert (
        failed.failure.detail == f"write error; ending the play raised Unexpected('{call} broke')"
    )
    assert b.output.raised == [call]
    restores = call not in ("read", "set")
    assert failed.restored is restores
    if restores:
        assert b.output.controls[LEVEL].values == list(channels(0))
    else:
        assert b.output.controls[LEVEL].values == list(channels(-1800))
    assert (failed.unplayed_frames is None) is (call == "delay")
    assert not b.output.attached
    join_play(b)
    raised_once(raised, call)
    plays_again(b)


def cut_and_arm(b: Bench, *calls: Armable, clock: bool = False) -> None:
    """Fail the third write and arm `calls`, and the clock when `clock`, in
    the same `on_write`: their next occurrences are in the play's end.
    """

    def on_write(n: int) -> None:
        if n == 3:
            b.output.errors = 1
            for call in calls:
                b.output.raises[call] = Unexpected(f"{call} broke")
            b.clock_broken = clock

    b.output.on_write = on_write


def test_every_raise_while_a_play_ends_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raised = thread_raised(monkeypatch)
    b = Bench(tmp_path, FakeOutput(fake_controls(), speed=1.0))
    b.clip("a.wav", 5.0)
    cut_and_arm(b, "delay", "set")
    started(b.play())
    failed = b.wait_end()
    assert isinstance(failed, Failed)
    assert failed.failure == Failure(
        "stream_died",
        "write error; ending the play raised Unexpected('delay broke'), Unexpected('set broke')",
    )
    assert not failed.restored and failed.unplayed_frames is None
    join_play(b)
    raised_once(raised, "delay")
    assert raised[0] is not None
    assert raised[0].__notes__ == ["ending the play also raised Unexpected('set broke')"]
    plays_again(b)


def test_a_clock_that_raises_after_an_earlier_step_is_named_by_the_next_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raised = thread_raised(monkeypatch)
    b = Bench(tmp_path, FakeOutput(fake_controls(), speed=1.0))
    b.clip("a.wav", 5.0)
    cut_and_arm(b, "close", clock=True)
    started(b.play())
    p = b.role._playing
    assert p is not None
    assert p.done.wait(10)
    join_play(b)
    b.clock_broken = False
    raised_once(raised, "close")
    assert not any(isinstance(e.body, Ended | Failed) for e in b.events)
    assert b.output.controls[LEVEL].values == list(channels(0))
    assert not b.output.attached
    with pytest.raises(RuntimeError, match="before reporting its end") as error:
        b.send(Release())
    assert error.value.__cause__ is raised[0]
    assert raised[0] is not None
    assert raised[0].__notes__ == ["ending the play also raised Unexpected('clock broke')"]
    plays_again(b)


def test_a_play_that_drained_and_whose_restoration_raised_is_role_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raised = thread_raised(monkeypatch)
    b = Bench(tmp_path, FakeOutput(fake_controls(), speed=1.0))
    b.clip("a.wav", 0.5)
    started(b.play())
    # The play's thread makes no set before the restoration.
    b.output.raises["set"] = Unexpected("set broke")
    failed = b.wait_end()
    assert isinstance(failed, Failed)
    assert failed.failure == Failure("role_error", "ending the play raised Unexpected('set broke')")
    assert failed.unplayed_frames == 0 and not failed.restored
    assert b.output.streams[0].drained and not b.output.attached
    join_play(b)
    raised_once(raised, "set")
    plays_again(b)


def test_a_stop_whose_end_raised_is_not_playing_and_the_end_is_the_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raised = thread_raised(monkeypatch)
    b = Bench(tmp_path, FakeOutput(fake_controls(), speed=1.0))
    b.clip("a.wav", 5.0)
    # Only the end of a play drops.
    b.output.raises["drop"] = Unexpected("drop broke")
    started(b.play())
    assert reason(b.send(Stop())) == "not_playing"
    failed = b.wait_end()
    assert isinstance(failed, Failed)
    assert failed.failure.reason == "role_error" and failed.restored
    assert b.output.controls[LEVEL].values == list(channels(0))
    assert not b.output.attached
    raised_once(raised, "drop")
    plays_again(b)


def test_a_raise_ending_a_play_that_did_not_begin_is_raised_from_the_command(
    tmp_path: Path,
) -> None:
    output = Stubborn(fake_controls())
    output.extra_mb = 10
    b = Bench(tmp_path, output)
    b.clip("a.wav", 0.2)
    # The stream is closed, and the output detached, nowhere else on the way
    # to a refused read-back.
    output.raises["close"] = Unexpected("close broke")
    output.raises["detach"] = Unexpected("detach broke")
    with pytest.raises(Unexpected, match="close broke") as error:
        b.play()
    also, began = error.value.__notes__
    assert also == "ending the play also raised Unexpected('detach broke')"
    assert began.startswith("the play did not begin: controls: ")
    assert output.raised == ["close", "detach"]
    assert output.controls[LEVEL].values == list(channels(0))
    assert not output.attached and output.writes == 0 and b.events == []
    assert isinstance(b.send(Check()), Checked)


def test_a_raise_in_the_first_move_restores_what_it_wrote(tmp_path: Path) -> None:
    # Found below the play's level: the move raises the level, then raises
    # in the held control's write.
    b = Bench(tmp_path, FakeOutput(fake_controls(values=list(channels(-3000)))))
    b.clip("a.wav", 0.2)
    armed = False

    def arm(control: ControlName, _: tuple[Channel, ...]) -> bool:
        nonlocal armed
        if control == LEVEL and not armed:
            armed = True
            b.output.raises["set"] = Unexpected("set broke")
        return False

    b.output.refuse = arm
    with pytest.raises(Unexpected, match="set broke"):
        b.play()
    assert b.output.raised == ["set"]
    assert b.output.states[0][LEVEL] == channels(-1800)
    assert b.output.controls[LEVEL].values == list(channels(-3000))
    assert b.output.controls[HELD].values == list(channels(0))
    assert b.output.open_streams == 0 and b.output.writes == 0
    assert not b.output.attached and b.events == []
    assert isinstance(b.send(Check()), Checked)


def test_a_raise_in_the_first_write_carries_what_ending_the_play_raised(
    tmp_path: Path,
) -> None:
    b = Bench(tmp_path)
    b.clip("a.wav", 0.2)
    raise_at_write(b, 1)
    # The stream is closed nowhere before the play's end.
    b.output.raises["close"] = Unexpected("close broke")
    with pytest.raises(RuntimeError, match="the output broke") as error:
        b.play()
    assert error.value.__notes__ == ["ending the play also raised Unexpected('close broke')"]
    assert b.output.controls[LEVEL].values == list(channels(0))
    assert not b.output.attached and b.events == []
    b.output.on_write = None
    plays_again(b)


def test_a_thread_that_cannot_start_ends_the_play(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    b = Bench(tmp_path)
    b.clip("a.wav", 0.2)

    def refuse(_: threading.Thread) -> None:
        raise Unexpected("start broke")

    with monkeypatch.context() as m:
        m.setattr(threading.Thread, "start", refuse)
        with pytest.raises(Unexpected, match="start broke"):
            b.play()
    assert b.output.controls[LEVEL].values == list(channels(0))
    assert b.output.open_streams == 0 and b.output.streams[0].dropped
    assert not b.output.attached and b.events == []
    plays_again(b)


class Unprintable(Exception):
    """An exception whose `repr` raises."""

    def __repr__(self) -> str:
        raise ValueError("no repr")


class UnprintableOSError(OSError):
    """An `OSError` whose `str` raises."""

    def __str__(self) -> str:
        raise ValueError("no str")


@pytest.mark.parametrize("kind", [Unprintable, UnprintableOSError])
def test_an_exception_that_cannot_be_named_still_ends_the_play(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: type[Exception]
) -> None:
    raised = thread_raised(monkeypatch)
    b = Bench(tmp_path, FakeOutput(fake_controls(), speed=1.0))
    b.clip("a.wav", 5.0)

    def on_write(n: int) -> None:
        if n == 3:
            b.output.raises["close"] = Unprintable()
            raise kind()

    b.output.on_write = on_write
    started(b.play())
    failed = b.wait_end()
    assert isinstance(failed, Failed)
    assert failed.failure.reason == "role_error"
    assert "ending the play raised <Unprintable whose repr raised>" in failed.failure.detail
    assert failed.restored and b.output.controls[LEVEL].values == list(channels(0))
    assert not b.output.attached
    join_play(b)
    assert len(raised) == 1
    plays_again(b)


@pytest.mark.parametrize("refuse_end", [False, True])
def test_a_thread_error_carries_what_ending_the_play_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, refuse_end: bool
) -> None:
    raised = thread_raised(monkeypatch)
    b = Bench(tmp_path, FakeOutput(fake_controls(), speed=1.0))
    b.clip("a.wav", 5.0)
    b.refuse_end = refuse_end
    thread_error = Unexpected("thread broke")

    def on_write(n: int) -> None:
        if n == 3:
            b.output.raises["set"] = Unexpected("set broke")
            raise thread_error

    b.output.on_write = on_write
    started(b.play())
    assert b.ended.wait(10)
    join_play(b)
    [error] = raised
    assert error is not None
    if not refuse_end:
        failed = b.wait_end()
        assert isinstance(failed, Failed)
        assert failed.failure == Failure(
            "role_error",
            "the play's thread raised Unexpected('thread broke'); "
            "ending the play raised Unexpected('set broke')",
        )
        assert error is thread_error
        assert error.__notes__ == ["ending the play also raised Unexpected('set broke')"]
        plays_again(b)
        return
    # The sink's exception is raised on, from the thread's, carrying the set's.
    assert str(error) == "the sink failed" and error.__cause__ is thread_error
    assert error.__notes__ == ["ending the play also raised Unexpected('set broke')"]
    assert not b.output.attached
    with pytest.raises(RuntimeError, match="before reporting its end") as next_error:
        b.send(Release())
    assert next_error.value.__cause__ is error


def test_a_dry_play_and_stop(tmp_path: Path) -> None:
    b = Bench(tmp_path)
    b.clip("a.wav", 0.2)
    s = started(b.play(dry=True))
    assert (s.play, s.copy, s.first) == (0, None, None)
    assert (
        s.controls
        == s.found
        == (
            Control("PCM", 1, "level", channels(0)),
            Control("PCM", 0, "held", channels(0)),
        )
    )
    assert s.level_mb == -1800
    assert b.output.opens == 1 and b.output.open_streams == 0
    stopped = b.send(Stop(), dry=True)
    assert isinstance(stopped, Stopped) and stopped.play == 0
    assert reason(b.send(Stop(), dry=True)) == "not_playing"
    assert b.output.sets == [] and b.output.writes == 0 and b.events == []
    assert not b.data.exists()
    assert not b.output.attached


def test_a_dry_play_after_a_dry_play_is_not_refused(tmp_path: Path) -> None:
    """A script that plays, waits a play out and plays again; a trace passes
    no time, so the dry role cannot know the first play is still running.
    """
    b = Bench(tmp_path)
    b.clip("a.wav", 0.2)
    b.clip("b.wav", 0.2)
    started(b.play("a.wav", dry=True))
    started(b.play("b.wav", -3000, dry=True))
    assert isinstance(b.send(Stop(), dry=True), Stopped)
    assert b.send(Release(), dry=True) == Released(stopped=None)
    assert b.output.opens == 2 and b.output.writes == 0 and b.events == []


def test_a_dry_play_in_quiet_hours_opens_nothing(tmp_path: Path) -> None:
    b = Bench(tmp_path, quiet=(QuietMinutes(11 * 60, 13 * 60),))
    b.clip("a.wav", 0.2)
    assert reason(b.play(dry=True)) == "quiet_hours"
    b.nothing_done()


def test_a_dry_play_whose_quiet_hours_begin_before_its_open_opens_nothing(
    tmp_path: Path,
) -> None:
    b = Bench(tmp_path, quiet=(QuietMinutes(12 * 60 + 1, 13 * 60),))
    b.clip("a.wav", 0.2)
    read = b.output.read

    def read_late(controls: list[ControlName]) -> object:
        b.local = NOON + datetime.timedelta(minutes=1)
        return read(controls)

    b.output.read = read_late  # type: ignore[assignment,method-assign]
    assert reason(b.play(dry=True)) == "quiet_hours"
    b.nothing_done()


def test_a_dry_play_whose_mixer_read_fails_opens_nothing(tmp_path: Path) -> None:
    b = Bench(tmp_path)
    b.clip("a.wav", 0.2)
    unreadable = Failure("mixer", "the mixer could not be read", {"code": -5})
    b.output.read_failure = unreadable
    assert b.play(dry=True) == unreadable
    b.nothing_done()


def test_check_opens_nothing_and_sets_nothing(tmp_path: Path) -> None:
    b = Bench(tmp_path, quiet=(QuietMinutes(11 * 60, 13 * 60), QuietMinutes(22 * 60, 7 * 60)))
    checked = b.send(Check())
    assert isinstance(checked, Checked)
    assert checked.output.kind == "alsa" and checked.device == CARD
    assert [(c.element, c.index, c.part) for c in checked.controls] == [
        ("PCM", 1, "level"),
        ("PCM", 0, "held"),
    ]
    assert checked.controls[0].range == VolumeRange(-6000, 0)
    assert checked.controls[0].channels == channels(0)
    assert checked.cap_mb == -1800
    assert [(q.start, q.end) for q in checked.quiet_hours] == [
        ("11:00", "13:00"),
        ("22:00", "07:00"),
    ]
    assert checked.local_time == "2026-11-13T12:00:00-05:00"
    assert checked.quiet_now
    b.nothing_done()


def test_attach_failures_are_the_reply(tmp_path: Path) -> None:
    missing = Failure("alsa_missing", "libasound.so.2 could not be loaded")
    b = Bench(tmp_path, FakeOutput(fake_controls(), attach_failure=missing))
    b.clip("a.wav", 0.2)
    assert b.send(Check()) == missing
    assert b.play() == missing
    assert b.play(dry=True) == missing


def test_release(tmp_path: Path) -> None:
    b = Bench(tmp_path, FakeOutput(fake_controls(), speed=1.0))
    assert b.send(Release()) == Released(stopped=None)
    b.clip("a.wav", 5.0)
    started(b.play())
    released = b.send(Release())
    assert isinstance(released, Released) and released.stopped is not None
    assert released.stopped.play == 1 and released.stopped.restored
    assert b.events == []
    assert b.send(Release(), dry=True) == Released(stopped=None)
    started(b.play(dry=True))
    assert b.send(Release(), dry=True) == Released(stopped=None)
    assert reason(b.send(Stop(), dry=True)) == "not_playing"


def test_two_roles_play_at_once(tmp_path: Path) -> None:
    first = Bench(tmp_path, FakeOutput(fake_controls(), speed=1.0), name="left")
    second = Bench(tmp_path, FakeOutput(fake_controls(), speed=1.0), name="right")
    first.clip("a.wav", 1.0)
    started(first.play())
    started(second.play())
    assert not first.ended.is_set()
    assert isinstance(first.wait_end(), Ended)
    assert isinstance(second.wait_end(), Ended)


# Plays with a stated start, on a set clock: the role's clock reads `Bench.at`,
# which changes only when a test sets it, so a play's thread waits until the
# test moves the clock and wakes to the reading the test set.

SET = ClockReading(
    machine="bench",
    monotonic=ClockValue(MONOTONIC, 81_000_000),
    wall=ClockValue(REALTIME, 1_794_000_000_000_000),
)
START = SET.after(5.0)
SETTLE_SECONDS = 0.3
"""Real time a test lets pass so the play's thread sees a clock it set; the
assertion after it holds whether or not the thread looked."""


def timed(tmp_path: Path, seconds: float = 0.35, **bench: object) -> Bench:
    b = Bench(tmp_path, **bench)  # type: ignore[arg-type]
    b.clip("a.wav", seconds)
    b.at = SET
    return b


def assert_held(b: Bench) -> None:
    """The play is readied and nothing has been handed over or reported."""
    assert b.output.writes == 0 and b.events == []
    assert b.output.open_streams == 1


def test_a_timed_play_is_readied_and_replies_before_any_write(tmp_path: Path) -> None:
    b = timed(tmp_path)
    s = started(b.play(start=START))
    assert (s.play, s.asked_start, s.first) == (1, START, None)
    assert s.copy is not None and Path(s.copy.path).exists()
    assert s.controls == (
        Control("PCM", 1, "level", channels(-1800)),
        Control("PCM", 0, "held", channels(0)),
    )
    assert b.output.controls[LEVEL].values == list(channels(-1800))
    assert b.output.attached
    assert_held(b)
    assert isinstance(b.send(Stop()), Stopped)


def test_a_timed_play_hands_nothing_over_before_its_start(tmp_path: Path) -> None:
    b = timed(tmp_path)
    frames = b.clip("a.wav", 0.35)
    started(b.play(start=START))
    b.at = SET.after(4.999)
    time.sleep(SETTLE_SECONDS)
    assert_held(b)
    b.at = START
    ended = b.wait_end()
    assert isinstance(ended, Ended)
    assert (ended.frames_handed, ended.unplayed_frames, ended.restored) == (5600, 0, True)
    assert ended.woke == START
    assert ended.first is not None and ended.first.at == START
    assert ended.first.frames_handed == 1600
    assert bytes(b.output.streams[0].frames) == frames
    assert b.output.controls[LEVEL].values == list(channels(0))
    assert not b.output.attached


def test_a_timed_play_reports_nothing_at_its_start(tmp_path: Path) -> None:
    b = timed(tmp_path)
    started(b.play(start=START))
    b.at = START
    ended = b.wait_end()
    assert [type(e.body) for e in b.events] == [Ended]
    assert b.written_at_report == [5600 * 4]
    assert isinstance(ended, Ended) and ended.frames_handed == 5600


def test_a_wake_at_the_tolerance_plays(tmp_path: Path) -> None:
    b = timed(tmp_path)
    started(b.play(start=START))
    late = START.after(role_module.LATE_SECONDS)
    b.at = late
    ended = b.wait_end()
    assert isinstance(ended, Ended) and ended.frames_handed == 5600
    assert ended.woke == late
    assert ended.first is not None and ended.first.at == late


def test_a_wake_past_the_tolerance_fails_with_no_frame(tmp_path: Path) -> None:
    b = timed(tmp_path)
    started(b.play(start=START))
    late = START.after(role_module.LATE_SECONDS + 0.000001)
    b.at = late
    failed = b.wait_end()
    assert isinstance(failed, Failed)
    assert failed.failure.reason == "start_passed"
    assert failed.failure.data == {"late_us": round(role_module.LATE_SECONDS * 1e6) + 1}
    assert (failed.frames_handed, failed.last, failed.first) == (0, None, None)
    assert failed.woke == late and failed.restored
    assert b.output.writes == 0
    assert b.output.controls[LEVEL].values == list(channels(0))
    assert not b.output.attached


def test_a_first_chunk_cut_part_way_keeps_a_first_mark(tmp_path: Path) -> None:
    """The output takes 1000 of the first chunk's 1600 frames and the next
    write fails: the failure carries those frames and a mark for them.
    """
    output = FakeOutput(fake_controls(), speed=4.0, grant={"buffer_frames": 1000})

    def on_write(n: int) -> None:
        if n == 2:
            output.errors = 1

    output.on_write = on_write
    b = timed(tmp_path, output=output)
    started(b.play(start=START))
    b.at = START
    failed = b.wait_end()
    assert isinstance(failed, Failed) and failed.failure.reason == "stream_died"
    assert failed.frames_handed == 1000 and failed.woke == START
    assert failed.first is not None and failed.first == failed.last
    assert failed.first.frames_handed == 1000 and failed.first.at == START
    assert output.writes == 2


CHECK_SECONDS = 0.05
"""What one read of the controls costs on the clock of the test below."""


def test_a_wait_after_a_check_ends_at_the_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A check made with more than a chunk's time left moves the clock on, and
    the wait after it is for what remains, not a whole chunk. The role's clock
    moves only on the play's thread: by `CHECK_SECONDS` at each read of the
    controls, and by what the thread asks to wait on its stop event.
    """
    b = timed(tmp_path)
    test_thread = threading.current_thread()
    waits: list[float] = []

    def advance(seconds: float) -> None:
        if threading.current_thread() is not test_thread:
            assert b.at is not None
            b.at = b.at.after(seconds)

    class ClockedStop(threading.Event):
        def wait(self, timeout: float | None = None) -> bool:
            assert timeout is not None
            waits.append(timeout)
            advance(timeout)
            return super().wait(timeout)

    @dataclass
    class Playing(role_module._Playing):
        stop: threading.Event = field(default_factory=ClockedStop)

    monkeypatch.setattr(role_module, "_Playing", Playing)
    read = b.output.read

    def costly_read(controls: Sequence[ControlName]) -> tuple[tuple[Channel, ...], ...] | Failure:
        advance(CHECK_SECONDS)
        return read(controls)

    monkeypatch.setattr(b.output, "read", costly_read)
    start = SET.after(role_module.CHUNK_SECONDS + 0.02)
    started(b.play(start=start))
    ended = b.wait_end()
    assert waits[0] == pytest.approx(role_module.CHUNK_SECONDS + 0.02 - CHECK_SECONDS)
    assert isinstance(ended, Ended) and ended.frames_handed == 5600
    assert ended.woke == start
    assert ended.first is not None and ended.first.at == start


@pytest.mark.parametrize("late_us", [0, 1000])
def test_a_start_reached_when_the_output_is_ready_is_refused(tmp_path: Path, late_us: int) -> None:
    b = timed(tmp_path)
    start = ClockReading(
        "bench", ClockValue(MONOTONIC, SET.monotonic.us - late_us), ClockValue(REALTIME, 0)
    )
    refused = b.play(start=start)
    assert reason(refused) == "start_passed"
    assert isinstance(refused, Failure) and refused.data == {"late_us": late_us}
    assert b.output.writes == 0 and b.events == []
    assert b.output.controls[LEVEL].values == list(channels(0))
    assert b.output.open_streams == 0 and not b.output.attached
    assert (b.data / "0001.wav").exists()
    b.at = None
    assert started(b.play()).play == 2
    b.wait_end()


@pytest.mark.parametrize(
    ("machine", "clock"), [("elsewhere", MONOTONIC), ("bench", "CLOCK_BOOTTIME")]
)
@pytest.mark.parametrize("dry", [False, True])
def test_a_start_on_another_clock_is_refused_before_anything_is_done(
    tmp_path: Path, machine: str, clock: str, dry: bool
) -> None:
    b = timed(tmp_path)
    start = ClockReading(machine, ClockValue(clock, START.monotonic.us), START.wall)
    refused = b.play(start=start, dry=dry)
    assert reason(refused) == "start_clock"
    assert isinstance(refused, Failure)
    assert refused.data == {"machine": machine, "clock": clock, "role_machine": "bench"}
    b.nothing_done()
    assert b.output.attaches == 0


def test_quiet_hours_reached_by_the_held_play_refuse_it_at_the_command(tmp_path: Path) -> None:
    b = timed(tmp_path, quiet=(QuietMinutes(12 * 60 + 2, 13 * 60),))
    # The span begins 120 s after the local time; the clip is 0.35 s long.
    assert reason(b.play(start=SET.after(119.8))) == "quiet_hours"
    b.nothing_done()
    assert started(b.play(start=SET.after(119.6))).asked_start == SET.after(119.6)
    b.send(Stop())


def test_quiet_hours_begun_during_the_hold_end_the_play_unplayed(tmp_path: Path) -> None:
    b = timed(tmp_path, quiet=(QuietMinutes(13 * 60, 14 * 60),))
    started(b.play(start=START))
    b.local = NOON + datetime.timedelta(hours=1)
    failed = b.wait_end()
    assert isinstance(failed, Failed) and failed.failure.reason == "quiet_hours"
    assert (failed.frames_handed, failed.last, failed.first, failed.woke) == (0, None, None, None)
    assert failed.restored and b.output.writes == 0


def test_a_control_moved_during_the_hold_ends_the_play_unplayed(tmp_path: Path) -> None:
    b = timed(tmp_path)
    started(b.play(start=START))
    b.output.move(LEVEL, 0, volume_mb=-1000)
    failed = b.wait_end()
    assert isinstance(failed, Failed) and failed.failure.reason == "controls_moved"
    assert (failed.frames_handed, failed.last, failed.first, failed.woke) == (0, None, None, None)
    assert b.output.writes == 0


@pytest.mark.parametrize("inside_the_last_chunk", [False, True])
def test_stop_during_the_hold(tmp_path: Path, inside_the_last_chunk: bool) -> None:
    b = timed(tmp_path)
    started(b.play(start=START))
    if inside_the_last_chunk:
        b.at = SET.after(4.95)
    assert reason(b.play(start=START)) == "already_playing"
    assert reason(b.send(Check())) == "already_playing"
    stopped = b.send(Stop())
    assert isinstance(stopped, Stopped)
    assert (stopped.play, stopped.frames_handed, stopped.unplayed_frames) == (1, 0, 0)
    assert (stopped.last, stopped.first, stopped.woke) == (None, None, None)
    assert stopped.restored
    assert b.output.writes == 0 and b.events == []
    assert b.output.controls[LEVEL].values == list(channels(0))
    assert b.output.open_streams == 0 and not b.output.attached


def test_release_during_the_hold_names_the_play_it_cut(tmp_path: Path) -> None:
    b = timed(tmp_path)
    started(b.play(start=START))
    released = b.send(Release())
    assert isinstance(released, Released) and released.stopped is not None
    assert (released.stopped.play, released.stopped.frames_handed) == (1, 0)
    assert released.stopped.last is None and released.stopped.restored
    assert b.output.writes == 0 and b.events == []


def test_the_clock_is_read_live_dry_and_during_a_play(tmp_path: Path) -> None:
    b = timed(tmp_path)
    assert b.send(Clock()) == ClockRead(SET)
    assert b.send(Clock(), dry=True) == ClockRead(SET)
    assert b.output.attaches == 0 and b.output.opens == 0
    started(b.play(start=START))
    b.at = SET.after(1.0)
    assert b.send(Clock()) == ClockRead(SET.after(1.0))
    assert b.output.attaches == 1 and b.output.opens == 1
    assert_held(b)
    b.send(Stop())


def test_a_dry_timed_play_holds_nothing(tmp_path: Path) -> None:
    b = timed(tmp_path)
    start = SET.after(3600.0)
    threads = threading.active_count()
    s = started(b.play(start=start, dry=True))
    assert (s.play, s.copy, s.asked_start, s.first) == (0, None, start, None)
    assert b.output.opens == 1 and b.output.open_streams == 0
    assert not b.output.attached
    assert b.dry_role._live() is None
    assert threading.active_count() == threads
    assert b.output.sets == [] and b.output.writes == 0 and b.events == []


def test_a_dry_timed_play_judges_quiet_hours_up_to_its_end(tmp_path: Path) -> None:
    b = timed(tmp_path, quiet=(QuietMinutes(13 * 60, 14 * 60),))
    assert reason(b.play(start=SET.after(3600.0), dry=True)) == "quiet_hours"
    b.nothing_done()
    assert started(b.play(start=SET, dry=True)).asked_start == SET


@pytest.mark.parametrize("peak", [0, 1])
def test_a_quiet_source_earns_no_volume_above_the_cap(tmp_path: Path, peak: int) -> None:
    b = Bench(tmp_path)
    b.clip("a.wav", 0.2, peak=peak)
    refused = b.play(volume_mb=-1700)
    assert reason(refused) == "over_cap"
    assert isinstance(refused, Failure) and refused.data == {
        "level_control_mb": -1700,
        "held_mb": 0,
        "level_mb": -1700,
        "cap_mb": -1800,
    }
    b.nothing_done()
    s = started(b.play(volume_mb=-1800))
    assert s.source.peak_mb == (None if peak == 0 else -9030)
    assert s.level_mb == -1800
    assert isinstance(b.wait_end(), Ended)


SWITCH = ControlName("Speaker", 0)


def three_controls(
    level: tuple[Channel, ...] = channels(-3000),
    held: tuple[Channel, ...] = channels(0),
    speed: float = 4.0,
) -> FakeOutput:
    """A level control and a held volume as given, and a held switch with no
    volume found off.
    """
    return FakeOutput(
        [
            FakeControl(LEVEL, values=list(level)),
            FakeControl(HELD, values=list(held)),
            FakeControl(
                SWITCH, channels=("Mono",), range=None, values=[Channel("Mono", None, False)]
            ),
        ],
        speed=speed,
    )


THREE_HELD = (HeldControl(HELD, -600), HeldControl(SWITCH, None))


def never_louder_than_both(
    output: FakeOutput, found: dict[ControlName, tuple[Channel, ...]]
) -> None:
    """After every write the role made, the level is at or under the cap of
    -1800, or no channel is above where it was found.
    """
    assert output.states
    for state in output.states:
        volumes = {
            c: max(ch.volume_mb for ch in chans if ch.volume_mb is not None)
            for c, chans in state.items()
            if any(ch.volume_mb is not None for ch in chans)
        }
        level = sum(volumes.values())
        above = [
            (c, now)
            for c, chans in state.items()
            for now, was in zip(chans, found[c], strict=True)
            if (
                now.volume_mb is not None
                and was.volume_mb is not None
                and now.volume_mb > was.volume_mb
            )
            or (now.switch is True and was.switch is False)
        ]
        assert level <= -1800 or not above, (state, above)


def found_of(output: FakeOutput) -> dict[ControlName, tuple[Channel, ...]]:
    return {c: tuple(f.values) for c, f in output.controls.items()}


def test_the_controls_are_lowered_before_they_are_raised(tmp_path: Path) -> None:
    output = three_controls()
    found = found_of(output)
    b = Bench(tmp_path, output, held=THREE_HELD)
    b.clip("a.wav", 0.2)
    s = started(b.play(volume_mb=-1200))
    assert s.level_mb == -1800
    ended = b.wait_end()
    assert isinstance(ended, Ended) and ended.restored
    mono_on, mono_off = (Channel("Mono", None, True),), (Channel("Mono", None, False),)
    assert output.sets == [
        # The start: the held volume down, then everything to what is asked.
        (HELD, channels(-600, None)),
        (LEVEL, channels(-1200)),
        (HELD, channels(-600)),
        (SWITCH, mono_on),
        # The restoration: the level down and the switch off, then all as found.
        (LEVEL, channels(-3000, None)),
        (SWITCH, (Channel("Mono", None, False),)),
        (LEVEL, channels(-3000)),
        (HELD, channels(0)),
        (SWITCH, mono_off),
    ]
    assert output.refused == []
    never_louder_than_both(output, found)
    assert found_of(output) == found


def test_a_refused_lowering_at_the_restoration_raises_nothing(tmp_path: Path) -> None:
    output = three_controls()
    found = found_of(output)
    output.refuse = lambda c, chans: c == LEVEL and chans == channels(-3000, None)
    b = Bench(tmp_path, output, held=THREE_HELD)
    b.clip("a.wav", 0.2)
    started(b.play(volume_mb=-1200))
    ended = b.wait_end()
    assert isinstance(ended, Ended) and not ended.restored
    assert output.refused == [(LEVEL, channels(-3000, None))]
    assert output.sets[4:] == [
        (LEVEL, channels(-3000, None)),
        (SWITCH, (Channel("Mono", None, False),)),
    ]
    never_louder_than_both(output, found)
    assert output.controls[LEVEL].values == list(channels(-1200))
    assert output.controls[HELD].values == list(channels(-600))


def test_a_refused_lowering_at_the_start_refuses_the_play(tmp_path: Path) -> None:
    output = three_controls()
    found = found_of(output)
    output.refuse = lambda c, chans: c == HELD and chans == channels(-600, None)
    b = Bench(tmp_path, output, held=THREE_HELD)
    b.clip("a.wav", 0.2)
    refused = b.play(volume_mb=-1200)
    assert reason(refused) == "mixer"
    assert output.sets == [
        (HELD, channels(-600, None)),
        # The restoration: nothing is above what was found, so it only raises.
        (LEVEL, channels(-3000)),
        (HELD, channels(0)),
        (SWITCH, (Channel("Mono", None, False),)),
    ]
    never_louder_than_both(output, found)
    assert found_of(output) == found
    assert output.writes == 0 and output.open_streams == 0 and not output.attached
    assert b.events == []


def test_a_refused_raise_at_the_restoration_does_not_stop_the_others(tmp_path: Path) -> None:
    output = three_controls()
    found = found_of(output)
    output.refuse = lambda c, chans: c == HELD and chans == channels(0)
    b = Bench(tmp_path, output, held=THREE_HELD)
    b.clip("a.wav", 0.2)
    started(b.play(volume_mb=-1200))
    ended = b.wait_end()
    assert isinstance(ended, Ended) and not ended.restored
    assert output.sets[-3:] == [
        (LEVEL, channels(-3000)),
        (HELD, channels(0)),
        (SWITCH, (Channel("Mono", None, False),)),
    ]
    never_louder_than_both(output, found)
    assert output.controls[LEVEL].values == list(channels(-3000))
    assert output.controls[HELD].values == list(channels(-600))


def test_a_control_raised_mid_play_is_lowered_before_anything_is_raised(tmp_path: Path) -> None:
    output = FakeOutput(
        [FakeControl(LEVEL), FakeControl(HELD, values=list(channels(-600)))], speed=1.0
    )
    found = found_of(output)
    b = Bench(tmp_path, output, held=(HeldControl(HELD, -600),))
    failed = moved(b, 3, {"volume_mb": 0}, HELD)
    assert failed.failure.reason == "controls_moved" and failed.restored
    assert output.sets[3:] == [
        (HELD, (Channel("Front Left", -600, None), Channel("Front Right", None, None))),
        (LEVEL, channels(0)),
        (HELD, channels(-600)),
    ]
    never_louder_than_both(output, found)
    assert found_of(output) == found


def test_a_control_found_off_is_turned_off_before_its_volume_is_raised(tmp_path: Path) -> None:
    output = FakeOutput([FakeControl(LEVEL, values=list(channels(0, False))), FakeControl(HELD)])
    b = Bench(tmp_path, output)
    b.clip("a.wav", 0.2)
    started(b.play())
    ended = b.wait_end()
    assert isinstance(ended, Ended) and ended.restored
    assert output.sets[3:] == [
        (LEVEL, channels(None, False)),
        (LEVEL, channels(0, False)),
        (HELD, channels(0)),
    ]
    assert output.controls[LEVEL].values == list(channels(0, False))


def test_found_is_read_when_the_controls_are_first_moved(tmp_path: Path) -> None:
    b = Bench(tmp_path)
    b.clip("a.wav", 0.2)
    open_stream = b.output.open

    def move_then_open(*args: int | str) -> object:
        b.output.move(LEVEL, 0, volume_mb=-2500)
        return open_stream(*args)  # type: ignore[arg-type]

    b.output.open = move_then_open  # type: ignore[assignment]
    s = started(b.play())
    moved_level = (Channel("Front Left", -2500, True), Channel("Front Right", 0, True))
    assert s.found[0] == Control("PCM", 1, "level", moved_level)
    assert isinstance(b.wait_end(), Ended)
    assert b.output.controls[LEVEL].values == list(moved_level)


def test_a_marked_source_is_played_with_its_marker_recorded(tmp_path: Path) -> None:
    b = Bench(tmp_path)
    b.clip("raw.wav", 0.1)
    marked = marker.mark(b.sources / "raw.wav", b.sources / "marked.wav", 9)
    assert isinstance(marked, marker.Marked)
    carried = marked.marker
    with wave.open(str(b.sources / "marked.wav"), "rb") as r:
        frames = r.readframes(r.getnframes())

    assert started(b.play("marked.wav", dry=True)).source.marker == carried
    s = started(b.play("marked.wav"))
    assert s.source.marker == carried
    assert isinstance(b.wait_end(), Ended)
    assert s.copy is not None
    assert bytes(b.output.streams[-1].frames) == frames
    with wave.open(s.copy.path, "rb") as copy:
        assert copy.readframes(copy.getnframes()) == frames


def test_the_role_tests_mapped_no_libasound() -> None:
    """Last in the file, so every test above has run in this process."""
    assert "libasound" not in Path("/proc/self/maps").read_text()
