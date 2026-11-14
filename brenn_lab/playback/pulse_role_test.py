"""The playback role on a sound-server sink: `PlaybackRole` on `PulseOutput`,
the real sink client and the stand-in server.
"""

import array
import dataclasses
import os
import shutil
import signal
import socket
import tempfile
import threading
import time
import wave
from collections.abc import Iterator
from pathlib import Path

import pytest

from brenn_lab.data import (
    MONOTONIC,
    REALTIME,
    ClockReading,
    ClockValue,
    Command,
    Event,
    Failure,
    JsonObject,
    JsonValue,
    Reply,
    now,
)
from brenn_lab.data import Check as CheckCommand
from brenn_lab.place import Place
from brenn_lab.playback import role as role_module
from brenn_lab.playback.device import SINK_LEVEL, PlaybackDevice
from brenn_lab.playback.messages import (
    Channel,
    Checked,
    Ended,
    Failed,
    Library,
    Play,
    SinkIdentity,
    Started,
    Stop,
    Stopped,
    Underrun,
)
from brenn_lab.playback.output import ControlName
from brenn_lab.playback.pulse import (
    MAX_MB,
    MIN_MB,
    NORM,
    REPLY_TIMEOUT_S,
    PulseOutput,
    PulseStream,
    Sink,
    raw_volume,
    reading_mb,
)
from brenn_lab.testing.pulse_stand_in import StandInServer, sink
from brenn_lab.testing.sink_client import client_program, mapped_sound_libraries

RATE = 16000
FRAME_BYTES = 4
SPEAKER = "alsa_output.usb-Desk-00.analog-stereo"
OTHER = "alsa_output.pci-0000_00_1f.3.analog-stereo"
FOUND_RAW = 60000
"""A raw volume that is not the largest of its reading."""
LATENCY_USEC = 20000


def write_clip(path: Path, seconds: float) -> bytes:
    """A two-channel 16-bit clip at RATE whose samples step through +-8192."""
    frames = round(RATE * seconds)
    samples = array.array("h", [(8192 if (i // 2) % 2 else -8192) for i in range(2 * frames)])
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(samples.tobytes())
    return samples.tobytes()


def sinks(**speaker: JsonValue) -> list[JsonObject]:
    fields: dict[str, JsonValue] = {
        "volumes": [FOUND_RAW, FOUND_RAW],
        "muted": True,
        "base_volume": 40000,
        "hardware_volume": True,
        "latency_usec": LATENCY_USEC,
    }
    fields.update(speaker)
    return [
        sink(SPEAKER, "Desk Speaker Analog Stereo", **fields),
        sink(OTHER, "Built-in Audio Analog Stereo"),
    ]


class Recording(PulseOutput):
    """A `PulseOutput` that keeps every set it made, with the stand-in's
    state of the sink after it, and the stand-in's streams after each open.
    """

    def __init__(self, selector: str, server: StandInServer) -> None:
        super().__init__(selector, client_program())
        self.server = server
        self.sets: list[tuple[tuple[Channel, ...], JsonObject]] = []
        self.opened: list[list[JsonObject]] = []

    def set(self, control: ControlName, channels: tuple[Channel, ...]) -> Failure | None:
        refused = super().set(control, channels)
        self.sets.append((channels, self.server.sink_state(SPEAKER)))
        return refused

    def open(
        self,
        rate_hz: int,
        channels: int,
        sample_format: str,
        period_frames: int,
        buffer_frames: int,
    ) -> PulseStream | Failure:
        stream = super().open(rate_hz, channels, sample_format, period_frames, buffer_frames)
        self.opened.append(self.server.streams())
        return stream


class Bench:
    """A role on a sink of a stand-in server, its sources, run directory and
    events.
    """

    def __init__(
        self,
        tmp: Path,
        monkeypatch: pytest.MonkeyPatch,
        speed: float = 4.0,
        selector: str = "desk speaker",
        cap_mb: int = -1800,
        described: list[JsonObject] | None = None,
    ) -> None:
        self.server = StandInServer(sinks() if described is None else described, speed)
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(self.server.runtime))
        monkeypatch.setenv("HOME", str(tmp))
        self.sources = tmp / "sources"
        self.sources.mkdir(exist_ok=True)
        self.data = tmp / "run" / "talker"
        self.output = Recording(selector, self.server)
        self.events: list[Event] = []
        self.ended = threading.Event()
        # The role's clock reads this, while it is set, and the machine's clock otherwise.
        self.at: ClockReading | None = None
        device = PlaybackDevice(
            output="pulse",
            selector=selector,
            source_dir=str(self.sources),
            cap_mb=cap_mb,
            quiet_hours=(),
            level=SINK_LEVEL,
            held=(),
        )
        self.role = role_module.PlaybackRole(
            "talker",
            "bench",
            device,
            self.output,
            self._sink,
            Place(self.data, tmp / "logs" / "talker", lambda _: None),
            clock=lambda: self.at if self.at is not None else now("bench"),
        )
        self.dry_role = role_module.PlaybackRole(
            "talker", "bench", device, self.output, self._sink, None
        )

    def _sink(self, event: Event) -> None:
        self.events.append(event)
        if isinstance(event.body, Ended | Failed):
            self.ended.set()

    def clip(self, seconds: float, name: str = "a.wav") -> bytes:
        return write_clip(self.sources / name, seconds)

    def send(self, body: object, dry: bool = False) -> object:
        role = self.dry_role if dry else self.role
        reply = role.handle(Command(role="talker", body=body, dry=dry))  # type: ignore[arg-type]
        assert isinstance(reply, Reply)
        return reply.outcome

    def play(
        self, volume_mb: int = -1800, dry: bool = False, start: ClockReading | None = None
    ) -> object:
        return self.send(Play(source="a.wav", volume_mb=volume_mb, start=start), dry)

    def wait_end(self) -> Ended | Failed:
        assert self.ended.wait(20), self.events
        body = self.events[-1].body
        assert isinstance(body, Ended | Failed)
        return body

    def wait_taken(self, frames: int) -> None:
        """Until the stand-in's one stream has taken `frames`."""
        deadline = time.monotonic() + 10
        while True:
            streams = self.server.streams()
            if streams and streams[0]["taken_frames"] == frames:
                return
            assert time.monotonic() < deadline, streams
            time.sleep(0.01)

    def found_again(self) -> None:
        """The sink is as it was found, raw volume for raw volume."""
        assert self.server.sink_state(SPEAKER) == {
            "name": SPEAKER,
            "volumes": [FOUND_RAW, FOUND_RAW],
            "muted": True,
        }

    def nothing_done(self) -> None:
        """Nothing set, opened, written or reported, and nothing left attached."""
        self.found_again()
        assert self.output.sets == []
        assert self.server.state()["streams_made"] == 0
        assert self.server.frames() == b""
        assert self.events == []
        assert self.output.client_pid is None


@pytest.fixture
def bench(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Bench]:
    b = Bench(tmp_path, monkeypatch)
    yield b
    b.server.close()


def reason(outcome: object) -> str:
    assert isinstance(outcome, Failure), outcome
    return outcome.reason


def started(outcome: object) -> Started:
    assert isinstance(outcome, Started), outcome
    return outcome


def channels(volume_mb: int, switch: bool) -> tuple[Channel, ...]:
    return (Channel("front-left", volume_mb, switch), Channel("front-right", volume_mb, switch))


FOUND_MB = reading_mb(FOUND_RAW)


def test_the_found_volume_is_not_the_largest_of_its_reading() -> None:
    assert raw_volume(FOUND_MB) != FOUND_RAW


def test_check_answers_the_sink_and_sets_nothing(bench: Bench) -> None:
    checked = bench.send(CheckCommand())
    assert isinstance(checked, Checked), checked
    assert checked.output.kind == "pulse"
    assert checked.output.library == Library("pulseaudio-rs", "0.3.2")
    device = checked.device
    assert isinstance(device, SinkIdentity)
    assert device == SinkIdentity(
        selector="desk speaker",
        sink_name=SPEAKER,
        description="Desk Speaker Analog Stereo",
        server_name=device.server_name,
        server_version=device.server_version,
        rate_hz=48000,
        channels=2,
        server_sample_format="s32le",
        hardware_volume=True,
        base_volume_mb=reading_mb(40000),
    )
    assert device.server_name and device.server_version
    [control] = checked.controls
    assert (control.element, control.index, control.part) == ("sink", 0, "level")
    assert control.range is not None and (control.range.min_mb, control.range.max_mb) == (
        MIN_MB,
        MAX_MB,
    )
    assert control.channels == channels(FOUND_MB, False)
    bench.nothing_done()


def test_a_play_to_its_end(bench: Bench) -> None:
    frames = bench.clip(0.35)
    s = started(bench.play())
    assert s.output.kind == "pulse" and isinstance(s.device, SinkIdentity)
    assert (s.granted.rate_hz, s.granted.channels, s.granted.sample_format) == (16000, 2, "s16_le")
    assert s.found[0].channels == channels(FOUND_MB, False)
    assert s.controls[0].channels == channels(-1800, True)
    assert (s.level_mb, s.cap_mb) == (-1800, -1800)
    assert s.first is not None and s.first.frames_handed == 1600
    # The buffer plus the sink's latency, by the server's account.
    assert s.first.delay_frames is not None and s.first.delay_frames >= 1600 + 320

    ended = bench.wait_end()
    assert isinstance(ended, Ended), ended
    assert (ended.frames_handed, ended.unplayed_frames, ended.underruns) == (5600, 0, 0)
    assert ended.restored
    assert ended.last.frames_handed == 5600 and ended.last.delay_frames is not None
    assert bench.server.frames() == frames
    assert s.copy is not None
    with wave.open(s.copy.path, "rb") as copy:
        assert copy.readframes(copy.getnframes()) == frames
    assert bench.server.streams() == []
    assert bench.server.state()["streams_made"] == 1
    bench.found_again()
    assert bench.output.client_pid is None
    assert bench.events == [Event("talker", ended)]


def test_the_stream_is_made_corked_at_0_db_and_unmuted_on_the_sink(bench: Bench) -> None:
    bench.clip(0.35)
    started(bench.play())
    bench.wait_end()
    [[stream]] = bench.output.opened
    assert stream["sink"] == SPEAKER
    assert stream["volumes"] == [NORM, NORM] and stream["muted"] is False
    assert stream["corked"] is True and stream["taken_frames"] == 0


def test_the_sink_is_lowered_before_it_is_unmuted_and_restored_to_the_raw_volume_found(
    bench: Bench,
) -> None:
    bench.clip(0.35)
    started(bench.play())
    assert isinstance(bench.wait_end(), Ended)
    level = raw_volume(-1800)
    assert bench.output.sets == [
        # Lowering: the volume down, the switch untouched, the sink still muted.
        (
            (Channel("front-left", -1800, None), Channel("front-right", -1800, None)),
            {"name": SPEAKER, "volumes": [level, level], "muted": True},
        ),
        # Raising: the volume as asked and the sink unmuted.
        (channels(-1800, True), {"name": SPEAKER, "volumes": [level, level], "muted": False}),
        # Restoring: muted first, then the volume found, raw volume for raw volume.
        (
            (Channel("front-left", None, False), Channel("front-right", None, False)),
            {"name": SPEAKER, "volumes": [level, level], "muted": True},
        ),
        (
            channels(FOUND_MB, False),
            {"name": SPEAKER, "volumes": [FOUND_RAW, FOUND_RAW], "muted": True},
        ),
    ]


def test_a_play_at_the_found_reading_leaves_the_raw_volume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = Bench(tmp_path, monkeypatch, cap_mb=0)
    try:
        bench.clip(0.2)
        s = started(bench.play(volume_mb=FOUND_MB))
        assert s.controls[0].channels == channels(FOUND_MB, True)
        assert isinstance(bench.wait_end(), Ended)
        # Unmuted for the play, muted again after: the raw volume never moves.
        volumes = [state["volumes"] for _, state in bench.output.sets]
        assert volumes == [[FOUND_RAW, FOUND_RAW]] * 3
        bench.found_again()
    finally:
        bench.server.close()


@pytest.mark.parametrize(
    ("volume_mb", "expected"), [(-1700, "over_cap"), (MAX_MB + 1, "volume_range")]
)
def test_limits_refuse_before_anything_is_set(bench: Bench, volume_mb: int, expected: str) -> None:
    bench.clip(0.2)
    assert reason(bench.play(volume_mb=volume_mb)) == expected
    bench.nothing_done()


def test_a_server_that_ignores_the_streams_volume_fails_the_play_before_a_frame(
    bench: Bench,
) -> None:
    bench.server.instruct("ignore_initial_volume", volume=NORM // 2)
    bench.clip(0.35)
    outcome = bench.play()
    assert reason(outcome) == "stream_moved"
    assert isinstance(outcome, Failure) and outcome.data is not None
    assert outcome.data["volumes"] == [NORM // 2, NORM // 2]
    assert bench.server.frames() == b""
    assert bench.server.streams() == []
    bench.found_again()
    assert bench.events == []
    assert bench.output.client_pid is None


def moved(bench: Bench, op: str, **fields: JsonValue) -> Failed:
    """A long play on a real-time stand-in, ended by an instruction sent once
    it has started.
    """
    bench.clip(5.0)
    started(bench.play())
    bench.server.instruct(op, **fields)
    failed = bench.wait_end()
    assert isinstance(failed, Failed), failed
    assert bench.server.streams() == []
    return failed


@pytest.mark.parametrize(
    ("op", "fields"),
    [
        ("sink_volume", {"sink": SPEAKER, "volumes": [NORM, NORM]}),
        ("sink_mute", {"sink": SPEAKER, "muted": True}),
        ("stream_volume", {"volumes": [NORM // 2, NORM // 2]}),
    ],
)
def test_a_moved_control_ends_the_play(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, op: str, fields: dict[str, JsonValue]
) -> None:
    bench = Bench(tmp_path, monkeypatch, speed=1.0)
    try:
        failed = moved(bench, op, **fields)
        assert failed.failure.reason == "controls_moved"
        assert 0 < failed.frames_handed < 80000
        assert failed.restored
        bench.found_again()
    finally:
        bench.server.close()


def test_a_stream_moved_to_another_sink_ends_the_play(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = Bench(tmp_path, monkeypatch, speed=1.0)
    try:
        failed = moved(bench, "move_stream", sink=OTHER)
        assert failed.failure.reason in {"controls_moved", "stream_died"}
        assert failed.restored
        bench.found_again()
    finally:
        bench.server.close()


def test_a_sink_that_vanishes_mid_play_ends_it_unrestored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = Bench(tmp_path, monkeypatch, speed=1.0)
    try:
        failed = moved(bench, "remove_sink", sink=SPEAKER)
        assert failed.failure.reason in {"controls_moved", "stream_died"}
        assert not failed.restored
        assert bench.output.client_pid is None
    finally:
        bench.server.close()


def test_a_killed_client_ends_the_play_and_the_role_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = Bench(tmp_path, monkeypatch, speed=1.0)
    try:
        bench.clip(5.0)
        started(bench.play())
        pid = bench.output.client_pid
        assert pid is not None
        os.kill(pid, signal.SIGKILL)
        failed = bench.wait_end()
        assert isinstance(failed, Failed), failed
        assert failed.failure.reason in {"controls_moved", "stream_died"}
        assert "client" in failed.failure.detail
        assert not failed.restored
        # The sink stays where the play set it: at or under the cap.
        level = raw_volume(-1800)
        assert bench.server.sink_state(SPEAKER)["volumes"] == [level, level]
        assert isinstance(bench.send(CheckCommand()), Checked)
    finally:
        bench.server.close()


def test_an_underrun_is_reported_and_the_play_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = Bench(tmp_path, monkeypatch, speed=1.0)
    try:
        frames = bench.clip(1.5)
        started(bench.play())
        bench.server.instruct("underflow")
        ended = bench.wait_end()
        assert isinstance(ended, Ended), ended
        assert ended.underruns == 1 and ended.frames_handed == 24000
        underrun = bench.events[0].body
        assert isinstance(underrun, Underrun)
        assert underrun.play == 1 and 0 < underrun.at_frame < 24000
        assert bench.server.frames() == frames
        assert ended.restored
    finally:
        bench.server.close()


def test_stop_cuts_the_play_with_its_unplayed_frames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = Bench(tmp_path, monkeypatch, speed=1.0)
    try:
        bench.clip(5.0)
        started(bench.play())
        stopped = bench.send(Stop())
        assert isinstance(stopped, Stopped), stopped
        assert 0 < stopped.frames_handed < 80000
        assert stopped.unplayed_frames is not None and stopped.unplayed_frames > 0
        assert stopped.last is not None and stopped.last.delay_frames is not None
        assert stopped.restored
        assert bench.server.streams() == []
        bench.found_again()
        assert bench.events == []
    finally:
        bench.server.close()


SET = ClockReading(
    machine="bench",
    monotonic=ClockValue(MONOTONIC, 81_000_000),
    wall=ClockValue(REALTIME, 1_794_000_000_000_000),
)
"""The role's set clock: it reads this until a test sets it again."""
START = SET.after(5.0)


def test_a_timed_play_holds_a_stream_that_takes_nothing_until_its_start(bench: Bench) -> None:
    frames = bench.clip(0.35)
    bench.at = SET
    s = started(bench.play(start=START))
    assert (s.asked_start, s.first) == (START, None)
    [stream] = bench.server.streams()
    assert stream["sink"] == SPEAKER
    assert stream["corked"] is True and stream["taken_frames"] == 0
    assert bench.server.frames() == b"" and bench.events == []
    bench.at = START
    ended = bench.wait_end()
    assert isinstance(ended, Ended), ended
    assert (ended.frames_handed, ended.unplayed_frames, ended.underruns) == (5600, 0, 0)
    assert ended.restored and ended.woke == START
    assert ended.first is not None and ended.first.at == START
    assert ended.first.frames_handed == 1600 and ended.first.delay_frames is not None
    assert bench.server.frames() == frames
    assert bench.server.streams() == []
    bench.found_again()
    assert bench.output.client_pid is None


def test_a_stop_during_the_hold_leaves_no_stream(bench: Bench) -> None:
    bench.clip(0.35)
    bench.at = SET
    started(bench.play(start=START))
    stopped = bench.send(Stop())
    assert isinstance(stopped, Stopped), stopped
    assert (stopped.frames_handed, stopped.last, stopped.first, stopped.woke) == (
        0,
        None,
        None,
        None,
    )
    assert stopped.restored
    assert bench.server.streams() == []
    assert bench.server.frames() == b"" and bench.events == []
    bench.found_again()
    assert bench.output.client_pid is None


def test_a_dry_play_makes_one_corked_stream_and_writes_and_sets_nothing(bench: Bench) -> None:
    bench.clip(0.35)
    s = started(bench.play(dry=True))
    assert s.play == 0 and s.copy is None and s.first is None
    assert isinstance(s.device, SinkIdentity)
    assert s.found[0].channels == channels(FOUND_MB, False)
    [[stream]] = bench.output.opened
    assert stream["corked"] is True and stream["taken_frames"] == 0
    assert bench.server.state()["streams_made"] == 1
    assert bench.server.streams() == []
    assert bench.output.sets == []
    assert bench.server.frames() == b""
    bench.found_again()
    assert isinstance(bench.send(Stop(), dry=True), Stopped)


def check_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selector: str, described: list[JsonObject]
) -> Failure:
    bench = Bench(tmp_path, monkeypatch, selector=selector, described=described)
    try:
        outcome = bench.send(CheckCommand())
        assert isinstance(outcome, Failure), outcome
        assert bench.server.state()["streams_made"] == 0
        assert bench.output.client_pid is None
        return outcome
    finally:
        bench.server.close()


def test_no_sink_names_the_sinks_there(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    failure = check_failure(tmp_path, monkeypatch, "hall", sinks())
    assert failure.reason == "no_sink"
    assert failure.data is not None and failure.data["selector"] == "hall"
    assert failure.data["sinks"] == [
        {"name": SPEAKER, "description": "Desk Speaker Analog Stereo"},
        {"name": OTHER, "description": "Built-in Audio Analog Stereo"},
    ]


def test_ambiguous_sink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    failure = check_failure(tmp_path, monkeypatch, "analog stereo", sinks())
    assert failure.reason == "ambiguous_sink"
    assert failure.data is not None and failure.data["matched"] == [SPEAKER, OTHER]


@pytest.mark.parametrize("flags", [{"flat_volume": True}, {"decibel_volume": False}])
def test_a_sink_whose_volume_cannot_be_held_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flags: dict[str, JsonValue]
) -> None:
    failure = check_failure(tmp_path, monkeypatch, "desk speaker", sinks(**flags))
    assert failure.reason == "sink_volume"


def test_no_server_at_the_socket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bench = Bench(tmp_path, monkeypatch)
    try:
        empty = tmp_path / "empty"
        (empty / "pulse").mkdir(parents=True)
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(empty))
        assert reason(bench.send(CheckCommand())) == "no_server"
        assert bench.output.client_pid is None
    finally:
        bench.server.close()


def test_a_server_that_answers_nothing_is_no_server_naming_the_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = Path(tempfile.mkdtemp(prefix="pulse-silent-", dir="/tmp"))
    try:
        (runtime / "pulse").mkdir()
        path = runtime / "pulse" / "native"
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
        monkeypatch.setenv("HOME", str(runtime))
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(str(path))
            listener.listen(1)
            started_at = time.monotonic()
            failure = PulseOutput("desk", client_program()).attach()
            took = time.monotonic() - started_at
        assert isinstance(failure, Failure) and failure.reason == "no_server", failure
        assert str(path) in failure.detail
        assert took < REPLY_TIMEOUT_S
    finally:
        shutil.rmtree(runtime)


def test_a_sink_back_with_other_channels_ends_the_play_and_the_role_goes_on(
    bench: Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = bench.output._read_sink
    reshaped = threading.Event()

    def read_sink() -> Sink | Failure:
        sink = real()
        if reshaped.is_set() and isinstance(sink, Sink):
            return dataclasses.replace(
                sink, channels=1, positions=("mono",), volumes=sink.volumes[:1]
            )
        return sink

    monkeypatch.setattr(bench.output, "_read_sink", read_sink)
    bench.clip(5.0)
    started(bench.play())
    reshaped.set()
    failed = bench.wait_end()
    assert isinstance(failed, Failed), failed
    assert failed.failure.reason == "controls_moved"
    assert "['mono']" in failed.failure.detail
    assert not failed.restored
    assert bench.output.client_pid is None
    reshaped.clear()
    assert isinstance(bench.send(CheckCommand()), Checked)


def stalled_play(bench: Bench) -> Started:
    """A play shorter than the granted buffer on a stand-in that plays
    nothing, once the stand-in has taken all of it.
    """
    bench.server.instruct("stall", stalled=True)
    bench.clip(0.3)
    s = started(bench.play())
    bench.wait_taken(4800)
    return s


def test_a_sink_that_goes_silent_ends_the_play_by_itself(
    bench: Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(role_module, "PLAY_OUT_GRACE_SECONDS", 0.3)
    stalled_play(bench)
    failed = bench.wait_end()
    assert isinstance(failed, Failed), failed
    assert failed.failure.reason == "stream_died"
    assert "did not play its buffer out" in failed.failure.detail
    assert failed.unplayed_frames is not None and failed.unplayed_frames >= 4800
    assert failed.restored
    bench.found_again()
    assert bench.server.streams() == []


def test_a_suspension_announced_while_the_buffer_plays_out_is_controls_moved(
    bench: Bench,
) -> None:
    stalled_play(bench)
    bench.server.instruct("suspend_stream")
    failed = bench.wait_end()
    assert isinstance(failed, Failed), failed
    assert failed.failure.reason == "controls_moved"
    assert "suspend" in failed.failure.detail
    assert failed.restored
    bench.found_again()


def test_an_underflow_once_every_frame_is_handed_is_not_reported(bench: Bench) -> None:
    s = stalled_play(bench)
    bench.server.instruct("underflow")
    bench.server.instruct("stall", stalled=False)
    ended = bench.wait_end()
    assert isinstance(ended, Ended), ended
    assert ended.underruns == 0 and ended.frames_handed == 4800
    assert not [e for e in bench.events if isinstance(e.body, Underrun)]
    assert s.source.frames == 4800
    bench.found_again()


def test_neither_the_role_nor_the_client_mapped_a_host_sound_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = Bench(tmp_path, monkeypatch, speed=1.0)
    try:
        bench.clip(5.0)
        started(bench.play())
        pid = bench.output.client_pid
        assert pid is not None
        client_maps = Path(f"/proc/{pid}/maps").read_text().splitlines()
        assert isinstance(bench.send(Stop()), Stopped)
    finally:
        bench.server.close()
    assert client_maps
    assert mapped_sound_libraries(client_maps) == []
    assert mapped_sound_libraries(Path("/proc/self/maps").read_text().splitlines()) == []
