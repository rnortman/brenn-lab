"""The capture role against the stand-in recorder replaying transcripts.

Every expectation about what the recorder reported is read from the
transcript the stand-in replays, never written here.
"""

import errno
import io
import os
import signal
import subprocess
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from brenn_lab.capture import pod_capture
from brenn_lab.capture.device import CaptureDevice
from brenn_lab.capture.messages import (
    Checked,
    Failed,
    Gap,
    Late,
    Released,
    Start,
    Started,
    Stop,
    Stopped,
)
from brenn_lab.capture.role import CaptureRole
from brenn_lab.data import (
    Check,
    Command,
    Event,
    Failure,
    JsonObject,
    Release,
    Reply,
    Request,
    encode_value,
)
from brenn_lab.place import Place
from brenn_lab.testing import transcript_files
from brenn_lab.testing.transcript_files import layout_of, line

STAND_IN = os.path.abspath(os.environ["STAND_IN"])
ROLE = "mic"
MACHINE = "bench"


def reading(value: JsonObject) -> JsonObject:
    return transcript_files.reading(value, MACHINE)


class Sink:
    def __init__(self) -> None:
        self.events: list[Event] = []
        self._cond = threading.Condition()

    def __call__(self, event: Event) -> None:
        with self._cond:
            self.events.append(event)
            self._cond.notify_all()

    def wait_for(self, n: int) -> list[Event]:
        with self._cond:
            assert self._cond.wait_for(lambda: len(self.events) >= n, timeout=20), self.events
            return list(self.events)


class Bench:
    def __init__(
        self,
        tmp_path: Path,
        card: str,
        layout: tuple[str, ...] | None = None,
        program: str = STAND_IN,
        hosted: bool = True,
        sink: Callable[[Event], None] | None = None,
        note: Callable[[str], None] | None = None,
    ):
        if layout is None:
            layout = layout_of(card)
        self.run = tmp_path / "run"
        self.notes: list[str] = []
        self.sink = Sink()
        place = None
        if hosted:
            place = Place(
                data=self.run / "captures" / ROLE,
                logs=self.run / "logs" / ROLE,
                note=self.notes.append if note is None else note,
            )
        device = CaptureDevice(card=card, rate_hz=16000, sample_format="s16_le", layout=layout)
        self.role = CaptureRole(
            ROLE, MACHINE, program, device, self.sink if sink is None else sink, place
        )

    def send(self, body: Request[object], dry: bool = False) -> object:
        reply = self.role.handle(Command(role=ROLE, body=body, dry=dry))
        assert isinstance(reply, Reply)
        assert reply.role == ROLE and reply.dry == dry
        return reply.outcome


def failure(outcome: object, reason: str) -> Failure:
    assert isinstance(outcome, Failure), outcome
    assert outcome.reason == reason, outcome
    return outcome


def test_a_clean_capture(tmp_path: Path) -> None:
    bench = Bench(tmp_path, "clean")
    started_line = line("clean", "capture_started")
    stopped_line = line("clean", "capture_stopped")

    started = bench.send(Start())
    assert isinstance(started, Started)
    assert started.capture == 1
    assert started.capture_id == started_line["record_id"]
    out = bench.run / "captures" / ROLE / "0001.framelog"
    assert started.location is not None
    assert (started.location.machine, started.location.path) == (MACHINE, str(out))
    assert out.exists()
    assert started.recorder.program == "pod-capture"
    assert started.recorder.repository == "brenn-pod"
    assert encode_value(started.recorder.build) == started_line["build"]
    assert encode_value(started.device) == started_line["device"]
    assert encode_value(started.granted) == started_line["granted"]
    assert list(started.layout) == started_line["layout"]
    assert encode_value(started.start) == reading(started_line)
    for fact in (started.device_firmware, started.device_configuration):
        assert encode_value(fact) == {"reason": fact.reason, "read": False}
        assert fact.reason

    stopped = bench.send(Stop())
    assert isinstance(stopped, Stopped)
    assert stopped.capture == 1
    assert stopped.frames == stopped_line["frames"]
    assert stopped.gaps_reported == stopped_line["gaps"]
    assert encode_value(stopped.end) == reading(stopped_line)
    assert bench.sink.events == []

    logs = bench.run / "logs" / ROLE
    assert (logs / "recorder-0001.stdout.jsonl").read_bytes() == (
        transcript_files.path("clean").read_bytes()
    )
    assert (logs / "recorder-0001.stderr.log").exists()
    assert any("pid" in n for n in bench.notes)

    assert bench.send(Release()) == Released(stopped=None)
    assert failure(bench.send(Stop()), "not_capturing")


def test_captures_are_numbered_and_never_reuse_a_path(tmp_path: Path) -> None:
    bench = Bench(tmp_path, "clean")
    for n in (1, 2):
        started = bench.send(Start())
        assert isinstance(started, Started) and started.capture == n
        assert started.location is not None
        assert started.location.path.endswith(f"{n:04d}.framelog")
        assert isinstance(bench.send(Stop()), Stopped)


def test_check_probes_without_leaving_anything(tmp_path: Path) -> None:
    bench = Bench(tmp_path, "clean", hosted=False)
    started_line = line("clean", "capture_started")
    for dry in (False, True):
        checked = bench.send(Check(), dry=dry)
        assert isinstance(checked, Checked)
        assert encode_value(checked.device) == started_line["device"]
        assert encode_value(checked.granted) == started_line["granted"]
        assert list(checked.layout) == started_line["layout"]
    assert not bench.run.exists()


def test_dry_commands_answer_with_the_probe_and_track_order(tmp_path: Path) -> None:
    bench = Bench(tmp_path, "clean", hosted=False)
    stopped_line = line("clean", "capture_stopped")
    assert failure(bench.send(Stop(), dry=True), "not_capturing")
    started = bench.send(Start(), dry=True)
    assert isinstance(started, Started)
    assert started.capture == 0 and started.location is None
    assert failure(bench.send(Start(), dry=True), "already_capturing")
    released = bench.send(Release(), dry=True)
    assert isinstance(released, Released) and released.stopped is not None
    assert failure(bench.send(Stop(), dry=True), "not_capturing")
    assert isinstance(bench.send(Start(), dry=True), Started)
    stopped = bench.send(Stop(), dry=True)
    assert isinstance(stopped, Stopped)
    assert stopped.capture == 0
    # The probe stops at once, so it reports what the recorder said then.
    assert stopped.frames == stopped_line["frames"]
    assert bench.send(Release(), dry=True) == Released(stopped=None)
    assert not bench.run.exists()


def test_a_gap_is_an_event(tmp_path: Path) -> None:
    bench = Bench(tmp_path, "loss")
    gap_line = line("loss", "capture_gap")
    assert isinstance(bench.send(Start()), Started)
    stopped = bench.send(Stop())
    assert isinstance(stopped, Stopped)
    assert stopped.gaps_reported == line("loss", "capture_stopped")["gaps"]
    [event] = bench.sink.wait_for(1)
    assert event.role == ROLE
    assert isinstance(event.body, Gap)
    assert event.body.capture == 1
    assert event.body.cause == gap_line["cause"]
    assert event.body.at_frame == gap_line["at_frame"]
    assert event.body.frames is None and gap_line["frames"] is None
    assert event.body.detail == gap_line["detail"]
    assert encode_value(event.body.at) == reading(gap_line)


def test_a_late_block_is_an_event(tmp_path: Path) -> None:
    bench = Bench(tmp_path, "late")
    late_line = line("late", "capture_late")
    assert isinstance(bench.send(Start()), Started)
    assert isinstance(bench.send(Stop()), Stopped)
    [event] = bench.sink.wait_for(1)
    assert isinstance(event.body, Late)
    assert event.body.at_frame == late_line["at_frame"]
    assert event.body.backlog_frames == late_line["backlog_frames"]
    assert encode_value(event.body.at) == reading(late_line)


def test_no_card_fails_check_and_start_with_the_cards(tmp_path: Path) -> None:
    bench = Bench(tmp_path, "no_card", layout=("a", "b"))
    failed_line = line("no_card", "capture_failed")
    for body in (Check(), Start()):
        f = failure(bench.send(body), "no_card")
        assert f.detail == failed_line["detail"]
        assert f.data["cards"] == failed_line["cards"]
    assert failure(bench.send(Stop()), "not_capturing")


def test_dry_start_and_stop_answer_with_the_probes_failure(tmp_path: Path) -> None:
    bench = Bench(tmp_path, "no_card", layout=("a", "b"), hosted=False)
    failure(bench.send(Stop(), dry=True), "no_card")
    failure(bench.send(Start(), dry=True), "no_card")
    failure(bench.send(Stop(), dry=True), "no_card")


def _failed(bench: Bench) -> Failed:
    [event] = bench.sink.wait_for(1)
    assert isinstance(event.body, Failed)
    assert event.body.capture == 1
    return event.body


def _failed_event(bench: Bench) -> Failure:
    failed = _failed(bench)
    assert failed.stopped is None
    return failed.failure


def test_a_capture_that_dies_after_it_started(tmp_path: Path) -> None:
    bench = Bench(tmp_path, "stream_died")
    failed_line = line("stream_died", "capture_failed")
    assert isinstance(bench.send(Start()), Started)
    f = _failed_event(bench)
    assert f.reason == "stream_died" and f.detail == failed_line["detail"]
    assert f.data["at_frame"] == failed_line["at_frame"]
    assert bench.send(Stop()) == f


def test_write_stalled_at_the_stop(tmp_path: Path) -> None:
    bench = Bench(tmp_path, "write_stalled")
    assert isinstance(bench.send(Start()), Started)
    f = failure(bench.send(Stop()), "write_stalled")
    assert f.data["log_complete"] == "not_established"
    assert bench.sink.events == []


def test_a_recorder_that_exits_mid_capture(tmp_path: Path) -> None:
    bench = Bench(tmp_path, "exits_mid_capture")
    assert isinstance(bench.send(Start()), Started)
    f = _failed_event(bench)
    assert f.reason == "recorder_exited" and f.data["exit_status"] == 1
    assert bench.send(Stop()) == f


def test_a_recorder_that_exits_at_the_stop(tmp_path: Path) -> None:
    bench = Bench(tmp_path, "exits_at_stop")
    assert isinstance(bench.send(Start()), Started)
    f = failure(bench.send(Stop()), "recorder_exited")
    assert f.data["exit_status"] == 1
    assert bench.sink.events == []


def test_a_recorder_that_stops_unasked(tmp_path: Path) -> None:
    bench = Bench(tmp_path, "stops_unasked")
    stopped_line = line("stops_unasked", "capture_stopped")
    assert isinstance(bench.send(Start()), Started)
    failed = _failed(bench)
    f = failed.failure
    assert f.reason == "stopped_unasked"
    assert failed.stopped is not None
    assert failed.stopped.capture == 1
    assert failed.stopped.frames == stopped_line["frames"]
    assert failed.stopped.gaps_reported == stopped_line["gaps"]
    assert encode_value(failed.stopped.end) == reading(stopped_line)
    assert bench.send(Stop()) == f
    assert bench.send(Release()) == Released(stopped=None)


@pytest.mark.parametrize(
    ("name", "why"),
    [
        ("not_json", "not a JSON line"),
        ("unknown_event", "unknown event 'capture_paused'"),
        ("after_terminal", "a line after the terminal line"),
        ("second_started", "a second capture_started"),
        ("exit_after_stopped", "exit status 3 after capture_stopped"),
        ("exit_after_failed", "exit status 0 after capture_failed"),
        ("unknown_reason", "unknown failure reason 'power_lost'"),
    ],
)
def test_a_line_outside_the_contract(tmp_path: Path, name: str, why: str) -> None:
    bench = Bench(tmp_path, name, layout=layout_of("clean"))
    assert isinstance(bench.send(Start()), Started)
    f = _failed_event(bench)
    assert f.reason == "recorder_contract"
    assert why in f.detail, f
    assert bench.send(Stop()) == f


@pytest.mark.parametrize(
    ("name", "why"),
    [
        ("gap_first", "the first line is neither capture_started nor capture_failed"),
        ("channels_mismatch", "8 channels granted for 6 sent"),
    ],
)
def test_a_start_outside_the_contract(tmp_path: Path, name: str, why: str) -> None:
    bench = Bench(tmp_path, name, layout=layout_of("clean"))
    f = failure(bench.send(Start()), "recorder_contract")
    assert why in f.detail, f
    assert isinstance(f.data["line"], str) and f.data["line"]
    assert bench.sink.events == []


def test_a_refused_command_line_is_recorder_usage(tmp_path: Path) -> None:
    card = "names_no_transcript"
    layout = ("a", "b")
    expected = subprocess.run(
        [STAND_IN, "--card", card, "--rate", "16000", "--format", "s16_le", "--layout", "a,b"]
        + ["--pod-id", ROLE, "--out", str(tmp_path / "x.framelog")],
        capture_output=True,
        check=False,
    )
    assert expected.returncode == 2 and expected.stdout == b""
    stderr = expected.stderr.decode()
    bench = Bench(tmp_path, card, layout=layout)
    for body in (Check(), Start()):
        f = failure(bench.send(body), "recorder_usage")
        assert f.data["stderr"] == stderr
        assert f.detail == stderr.splitlines()[0]
    assert not (tmp_path / "x.framelog").exists()


def test_a_comma_in_a_channel_name_is_refused_before_anything_starts(tmp_path: Path) -> None:
    # A program that does not exist: had anything been started, the reason
    # would be recorder_missing.
    bench = Bench(tmp_path, "clean", layout=("left,right",), program="/nonexistent/pod-capture")
    for body in (Check(), Start()):
        f = failure(bench.send(body), "recorder_usage")
        assert "left,right" in f.detail
    assert bench.notes == []


def test_a_missing_recorder(tmp_path: Path) -> None:
    bench = Bench(tmp_path, "clean", program=str(tmp_path / "pod-capture"))
    for body in (Check(), Start()):
        failure(bench.send(body), "recorder_missing")


def test_a_layout_other_than_the_one_sent_is_a_contract_break(tmp_path: Path) -> None:
    other = tuple(f"{name}_x" for name in layout_of("clean"))
    bench = Bench(tmp_path, "clean", layout=other)
    for body in (Check(), Start()):
        f = failure(bench.send(body), "recorder_contract")
        assert "layout" in f.detail
    assert bench.sink.events == []


def test_a_dry_check_while_dry_capturing(tmp_path: Path) -> None:
    bench = Bench(tmp_path, "clean", hosted=False)
    assert isinstance(bench.send(Start(), dry=True), Started)
    failure(bench.send(Check(), dry=True), "already_capturing")
    assert isinstance(bench.send(Stop(), dry=True), Stopped)
    assert isinstance(bench.send(Check(), dry=True), Checked)


class Spawned:
    """Every recorder process the adapter spawns while installed."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.procs: list[subprocess.Popen[bytes]] = []
        real = subprocess.Popen
        procs = self.procs

        class Recording(real):  # type: ignore[valid-type,misc]
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, **kwargs)
                procs.append(self)

        monkeypatch.setattr(subprocess, "Popen", Recording)

    def gone(self) -> bool:
        return bool(self.procs) and all(p.returncode is not None for p in self.procs)


def test_a_recorder_that_says_nothing_is_killed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pod_capture, "START_TIMEOUT_S", 0.5)
    spawned = Spawned(monkeypatch)
    bench = Bench(tmp_path, "silent", layout=layout_of("clean"))
    for body in (Check(), Start()):
        f = failure(bench.send(body), "recorder_timeout")
        assert "0.5 s" in f.detail
    assert len(spawned.procs) == 2 and spawned.gone()
    assert spawned.procs[1].returncode == -signal.SIGKILL


def test_a_recorder_that_does_not_stop_is_killed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pod_capture, "STOP_TIMEOUT_S", 0.5)
    spawned = Spawned(monkeypatch)
    bench = Bench(tmp_path, "clean")
    assert isinstance(bench.send(Start()), Started)
    [proc] = spawned.procs
    # A stopped process leaves SIGTERM pending, as a recorder that ignores
    # its stop would; only SIGKILL ends it.
    os.kill(proc.pid, signal.SIGSTOP)
    f = failure(bench.send(Stop()), "recorder_timeout")
    assert "0.5 s" in f.detail
    assert proc.returncode == -signal.SIGKILL
    assert bench.sink.events == []


def test_an_interrupted_probe_kills_its_recorder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawned = Spawned(monkeypatch)
    bench = Bench(tmp_path, "silent", layout=layout_of("clean"), hosted=False)
    timer = threading.Timer(0.5, os.kill, (os.getpid(), signal.SIGINT))
    timer.start()
    try:
        with pytest.raises(KeyboardInterrupt):
            bench.send(Check())
    finally:
        timer.cancel()
    assert spawned.gone()


def test_a_probe_interrupted_while_stopping_kills_its_recorder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawned = Spawned(monkeypatch)
    real_start = pod_capture.start
    timers: list[threading.Timer] = []

    def start_then_hang(*args: Any, **kwargs: Any) -> pod_capture.Running | Failure:
        running = real_start(*args, **kwargs)
        # A SIGSTOPped recorder cannot act on the probe's SIGTERM, so the
        # probe waits in stop() and the interrupt lands there.
        os.kill(spawned.procs[-1].pid, signal.SIGSTOP)
        timer = threading.Timer(0.5, os.kill, (os.getpid(), signal.SIGINT))
        timers.append(timer)
        timer.start()
        return running

    monkeypatch.setattr(pod_capture, "start", start_then_hang)
    bench = Bench(tmp_path, "clean", hosted=False)
    try:
        with pytest.raises(KeyboardInterrupt):
            bench.send(Check())
    finally:
        for timer in timers:
            timer.cancel()
    assert spawned.gone()
    assert spawned.procs[-1].returncode == -signal.SIGKILL


def test_a_directory_the_role_cannot_make_is_role_io(tmp_path: Path) -> None:
    bench = Bench(tmp_path, "clean")
    bench.run.write_text("a file where the run's directory would be\n")
    f = failure(bench.send(Start()), "role_io")
    assert f.data["path"] == str(bench.run / "captures" / ROLE)
    assert isinstance(f.data["errno"], int)


def test_a_probe_with_no_scratch_directory_is_role_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "missing"))
    bench = Bench(tmp_path, "clean", hosted=False)
    for dry in (False, True):
        failure(bench.send(Check(), dry=dry), "role_io")


def test_a_pid_that_cannot_be_noted_is_role_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def full(text: str) -> None:
        raise OSError(errno.ENOSPC, "No space left on device", "runner.log")

    spawned = Spawned(monkeypatch)
    bench = Bench(tmp_path, "clean", note=full)
    f = failure(bench.send(Start()), "role_io")
    assert f.data["path"] == "runner.log"
    assert spawned.gone()
    assert bench.sink.events == []


class _FullLog(io.BytesIO):
    def write(self, data: Any) -> int:
        raise OSError(errno.ENOSPC, "No space left on device")


@pytest.mark.parametrize("which", ["stdout", "stderr"])
def test_a_log_that_cannot_be_written_fails_the_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, which: str
) -> None:
    real = pod_capture._create

    def create(path: Path | None) -> Any:
        if path is not None and which in path.name:
            return _FullLog()
        return real(path)

    monkeypatch.setattr(pod_capture, "_create", create)
    # The no_card transcript prints its line at once, and the stand-in's
    # refusal of an unknown card writes stderr: each log gets a copy.
    card = "no_card" if which == "stdout" else "names_no_transcript"
    bench = Bench(tmp_path, card, layout=("a", "b"))
    f = failure(bench.send(Start()), "role_io")
    assert which in f.detail


def test_a_reader_that_fails_does_not_hang_the_role(tmp_path: Path) -> None:
    def broken(event: Event) -> None:
        raise RuntimeError("the sink failed")

    bench = Bench(tmp_path, "loss", sink=broken)
    # The gap line may be read before or after the start is answered, so
    # either the start or the stop raises the reader's error.
    with pytest.raises(RuntimeError, match="reader failed") as raised:
        bench.send(Start())
        bench.send(Stop())
    assert isinstance(raised.value.__cause__, RuntimeError)
    assert bench.send(Release()) == Released(stopped=None)


def test_one_capture_at_a_time(tmp_path: Path) -> None:
    bench = Bench(tmp_path, "clean")
    assert isinstance(bench.send(Start()), Started)
    failure(bench.send(Start()), "already_capturing")
    failure(bench.send(Check()), "already_capturing")
    released = bench.send(Release())
    assert isinstance(released, Released)
    assert released.stopped is not None and released.stopped.capture == 1
    assert bench.send(Release()) == Released(stopped=None)
