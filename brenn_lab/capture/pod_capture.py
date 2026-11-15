"""The one place in brenn-lab that knows brenn-pod's recorder, `pod-capture`.

Its command line, its event lines, its exit statuses, its failure reasons, the
clocks its readings are on, how long to wait for it, the device's state it
states and the little of that brenn-lab reads, and the process. Nothing
else in brenn-lab names a flag, an event or a key of the recorder; what the
recorder reports reaches the capture role as the capture kind's own types.

The recorder's contract is brenn-pod's `docs/reference/pod-capture.md`. A line
outside it is not coerced: it ends the capture with `recorder_contract`.
"""

import contextlib
import dataclasses
import json
import signal
import subprocess
import threading
import types
import typing
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from brenn_lab.capture.device import CaptureDevice
from brenn_lab.capture.messages import (
    Build,
    Device,
    Failed,
    Gap,
    Late,
    Recorder,
    Started,
    Stopped,
    device_state_problem,
)
from brenn_lab.data import (
    MONOTONIC,
    REALTIME,
    ClockReading,
    ClockValue,
    DecodeError,
    Failure,
    JsonObject,
    JsonValue,
    Location,
    decode_value,
    encode_value,
)
from brenn_lab.place import create_logs, discard_logs
from brenn_lab.sound import Granted

PROGRAM = "pod-capture"
REPOSITORY = "brenn-pod"
"""The repository the recorder is built from."""

START_TIMEOUT_S = 10.0
"""How long the first line may take after the recorder is started."""

STOP_TIMEOUT_S = 15.0
"""How long the terminal line may take after the stop is sent. The recorder
bounds its own stop at 5 s and then reports `write_stalled`."""

# The recorder's failure reasons, and the capture kind's word for each. Each
# means the same thing, so the word is kept.
REASONS: dict[str, str] = {
    "destination_exists": "destination_exists",
    "no_card": "no_card",
    "ambiguous_card": "ambiguous_card",
    "open": "open",
    "params": "params",
    "destination": "destination",
    "stream_died": "stream_died",
    "destination_full": "destination_full",
    "write_failed": "write_failed",
    "write_stalled": "write_stalled",
}


@dataclass(frozen=True)
class Spec:
    """One start of the recorder: which program, what to demand of which card,
    the stream's source name, the file to write, and the two files to copy
    the recorder's stdout and stderr to (nowhere when None).
    """

    program: str
    machine: str
    device: CaptureDevice
    pod_id: str
    out: Path
    logs: tuple[Path, Path] | None


# The event lines, as far as brenn-lab reads them. Keys beyond these are
# ignored, but inside a field typed `JsonObject`, which is carried whole; a
# field with a default of None may be absent and reads as null; a missing key
# otherwise, or a value of another type, is a contract break.


@dataclass(frozen=True)
class _StartedLine:
    record_id: str
    path: str
    device: Device
    granted: Granted
    layout: tuple[str, ...]
    build: Build
    device_state: JsonObject
    frame: int
    mono_us: int
    wall_us: int


@dataclass(frozen=True)
class _GapLine:
    record_id: str
    cause: str
    at_frame: int
    frames: int | None
    mono_us: int
    wall_us: int
    detail: str


@dataclass(frozen=True)
class _LateLine:
    record_id: str
    at_frame: int
    backlog_frames: int
    mono_us: int
    wall_us: int


@dataclass(frozen=True)
class _StoppedLine:
    record_id: str
    frames: int
    gaps: int
    mono_us: int
    wall_us: int


@dataclass(frozen=True)
class _Card:
    index: int
    name: str
    long_name: str


@dataclass(frozen=True)
class _FailedLine:
    record_id: str | None
    reason: str
    detail: str
    at_frame: int | None
    mono_us: int
    wall_us: int
    cards: tuple[_Card, ...] | None = None


type _Line = _StartedLine | _GapLine | _LateLine | _StoppedLine | _FailedLine

_LINES: dict[str, type] = {
    "capture_started": _StartedLine,
    "capture_gap": _GapLine,
    "capture_late": _LateLine,
    "capture_stopped": _StoppedLine,
    "capture_failed": _FailedLine,
}


class ContractError(ValueError):
    """A line or an exit status that is not what the recorder's contract says."""


def parse_line(raw: bytes) -> _Line:
    """The event line `raw`, held to the recorder's contract."""
    try:
        value = json.loads(raw.decode("utf-8"), parse_constant=_refuse_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ContractError(f"not a JSON line: {e}") from e
    if not isinstance(value, dict):
        raise ContractError("not a JSON object")
    ts_ms = value.get("ts_ms")
    if not isinstance(ts_ms, int) or isinstance(ts_ms, bool):
        raise ContractError("ts_ms: expected an integer")
    name = value.get("event")
    cls = _LINES.get(name) if isinstance(name, str) else None
    if cls is None:
        raise ContractError(f"unknown event {name!r}")
    try:
        line = decode_value(cls, _documented(cls, value), "line")
    except DecodeError as e:
        raise ContractError(f"{name}: {e}") from e
    if isinstance(line, _StartedLine):
        problem = device_state_problem(line.device_state)
        if problem is not None:
            raise ContractError(f"{name}: {problem}")
    return typing.cast(_Line, line)


def _refuse_constant(name: str) -> JsonValue:
    raise ContractError(f"not a JSON line: {name}")


def _documented(tp: object, value: JsonValue) -> JsonValue:
    """`value` with every object restricted to the fields of the dataclass it is
    read as, so that keys beyond the documented ones are ignored. A field whose
    default is None reads as null when absent. A field typed `JsonObject` is
    carried whole: the object is not walked and nothing in it is cut. The
    strict decoding that follows judges everything else.

    Only the shapes the line types use are walked; a line type with any other
    shape is refused here, so it cannot slip past the walk unnoticed.
    """
    if tp is JsonObject:
        return value
    if isinstance(tp, type) and dataclasses.is_dataclass(tp):
        if not isinstance(value, dict):
            return value
        hints = typing.get_type_hints(tp)
        out: dict[str, JsonValue] = {}
        for f in dataclasses.fields(tp):
            if f.name in value:
                out[f.name] = _documented(hints[f.name], value[f.name])
            elif f.default is None:
                out[f.name] = None
        return out
    if tp in (int, str, bool, type(None)):
        return value
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if origin is types.UnionType:
        rest = [a for a in args if a is not type(None)]
        if len(rest) != 1:
            raise TypeError(f"{tp!r}: a line type's union is one type or null")
        return value if value is None else _documented(rest[0], value)
    if origin is tuple and len(args) == 2 and args[1] is Ellipsis:
        return [_documented(args[0], v) for v in value] if isinstance(value, list) else value
    raise TypeError(f"{tp!r} is not a shape a line type uses")


def _reading(machine: str, mono_us: int, wall_us: int) -> ClockReading:
    # The lines carry `mono_us` and `wall_us` and no clock id. The recorder's
    # contract names them: CLOCK_MONOTONIC and CLOCK_REALTIME, in microseconds,
    # on the recorder's machine (brenn-pod's docs/reference/frame-log.md).
    return ClockReading(
        machine=machine,
        monotonic=ClockValue(MONOTONIC, mono_us),
        wall=ClockValue(REALTIME, wall_us),
    )


def argv(spec: Spec) -> list[str] | Failure:
    """The recorder's command line for `spec`, or `recorder_usage` when the
    device parameters cannot be said on it.
    """
    for name in spec.device.layout:
        if "," in name:
            return Failure(
                "recorder_usage",
                f"channel name {name!r} contains a comma, which the recorder's --layout splits on",
                {"channel": name},
            )
    return [
        spec.program,
        "--card",
        spec.device.card,
        "--rate",
        str(spec.device.rate_hz),
        "--format",
        spec.device.sample_format,
        "--layout",
        ",".join(spec.device.layout),
        "--pod-id",
        spec.pod_id,
        "--out",
        str(spec.out),
    ]


def start(
    spec: Spec,
    capture: int,
    report: Callable[[object], None],
    spawned: Callable[[int], None],
) -> Running | Failure:
    """Start capture number `capture`. Returns the running capture once the
    recorder says it started, or the failure. `report` receives the capture's
    `Gap`, `Late` and `Failed` events, from a thread of the adapter's, and is
    never called while the adapter holds a lock; `spawned` receives the
    recorder's pid once it is spawned, before any of its output is read.

    The log files exist only for a recorder that was spawned: a spawn that
    fails removes them. An `OSError` creating a log file is raised, and so is
    whatever `spawned` raises and anything that interrupts the wait for the
    first line; a recorder already spawned is killed, and has exited, before
    it is.
    """
    args = argv(spec)
    if isinstance(args, Failure):
        return args
    logs = None if spec.logs is None else create_logs(spec.logs)
    try:
        proc = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as e:
        if spec.logs is not None and logs is not None:
            discard_logs(spec.logs, logs)
        return Failure(
            "recorder_missing",
            f"cannot start {spec.program}: {e.strerror}",
            {"program": spec.program},
        )
    stdout_log, stderr_log = (None, None) if logs is None else logs
    running = Running(proc, spec, capture, report, stdout_log, stderr_log)
    try:
        try:
            spawned(proc.pid)
        finally:
            # Read whatever `spawned` did, so that an abort below is seen
            # through to the recorder's exit.
            running.read()
        return running.await_start()
    except BaseException:
        running.abort()
        raise


def io_failure(e: OSError, doing: str) -> Failure:
    """`role_io`: the role could not write a file of its own while `doing`."""
    return Failure(
        "role_io",
        f"{doing}: {e.strerror or e}",
        {"path": None if e.filename is None else str(e.filename), "errno": e.errno},
    )


class Running:
    """One recorder process, from its start to its exit. Its output is read
    from `read` on.
    """

    def __init__(
        self,
        proc: subprocess.Popen[bytes],
        spec: Spec,
        capture: int,
        report: Callable[[object], None],
        stdout_log: IO[bytes] | None,
        stderr_log: IO[bytes] | None,
    ):
        self._proc = proc
        self._machine = spec.machine
        self._layout = spec.device.layout
        self._location = Location(machine=spec.machine, path=str(spec.out))
        self._capture = capture
        self._report = report
        self._stdout_log = stdout_log
        self._stderr_log = stderr_log
        self._lock = threading.Lock()
        self._decided = threading.Event()
        self._done = threading.Event()
        self._started: Started | None = None
        self._terminal: _StoppedLine | _FailedLine | None = None
        self._override: Failure | None = None
        self._outcome: Stopped | Failure | None = None
        self._error: BaseException | None = None
        self._stop_asked = False
        self._stderr = bytearray()
        self._stderr_thread = threading.Thread(target=self._copy_stderr, daemon=True)
        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)

    def read(self) -> None:
        """Start reading the recorder's output."""
        self._stderr_thread.start()
        self._stdout_thread.start()

    @property
    def started(self) -> Started:
        """What the recorder reported when the capture started, located where
        the capture is written.
        """
        assert self._started is not None
        return self._started

    @property
    def ended(self) -> bool:
        """True once the recorder has exited and its outcome is known."""
        return self._done.is_set()

    def await_start(self) -> Running | Failure:
        if not self._decided.wait(START_TIMEOUT_S):
            self._end_with(
                Failure(
                    "recorder_timeout", f"no line from the recorder within {START_TIMEOUT_S:g} s"
                ),
                signal.SIGKILL,
            )
        # The reader thread decides the start whatever happens to it, so these
        # waits end.
        self._decided.wait()
        self.raise_error()
        if self._started is not None:
            return self
        self._done.wait()
        self.raise_error()
        assert isinstance(self._outcome, Failure)
        return self._outcome

    def stop(self) -> Stopped | Failure:
        """Stop the capture and return how it ended. A capture that already
        ended unasked returns the failure its `Failed` event carried.
        """
        with self._lock:
            already = self._outcome
            if already is None:
                self._stop_asked = True
        if already is None:
            self._signal(signal.SIGTERM)
            if not self._done.wait(STOP_TIMEOUT_S):
                self._end_with(
                    Failure(
                        "recorder_timeout",
                        f"no terminal line within {STOP_TIMEOUT_S:g} s of the stop",
                    ),
                    signal.SIGKILL,
                )
        self._done.wait()
        self.raise_error()
        assert self._outcome is not None
        return self._outcome if already is None else _unasked(already)

    def abort(self) -> None:
        """Kill the recorder and wait for it to exit, for a caller that cannot
        go on with the capture. Its end is reported to nobody.
        """
        with self._lock:
            self._stop_asked = True
        self._signal(signal.SIGKILL)
        self._done.wait()

    def raise_error(self) -> None:
        """Raise the error that broke the recorder's reader, if one did."""
        if self._error is not None:
            raise RuntimeError("the recorder's reader failed") from self._error

    def _signal(self, sig: int) -> None:
        try:
            self._proc.send_signal(sig)
        except ProcessLookupError:
            pass

    def _end_with(self, failure: Failure, sig: int | None = None) -> None:
        """End the capture with `failure`, whatever the recorder says after it.
        The recorder is sent `sig`, or by default SIGTERM once it has started
        and SIGKILL before then, when it may not yet have its handler.
        """
        with self._lock:
            if self._override is None:
                self._override = failure
            begun = self._started is not None
        if sig is None:
            sig = signal.SIGTERM if begun else signal.SIGKILL
        self._signal(sig)

    def _copy(self, log: IO[bytes] | None, data: bytes, what: str) -> IO[bytes] | None:
        """Copy `data` to `log`; the log to copy to next, None once a copy
        failed. A failed copy ends the capture with `role_io`: a capture whose
        recorder output was not kept is not a capture the run can explain.
        """
        if log is None:
            return None
        try:
            log.write(data)
            log.flush()
        except OSError as e:
            self._end_with(io_failure(e, f"copying the recorder's {what}"))
            return None
        return log

    def _copy_stderr(self) -> None:
        stderr = self._proc.stderr
        assert stderr is not None
        with _closing(self._stderr_log) as log:
            for chunk in stderr:
                self._stderr += chunk
                log = self._copy(log, chunk, "stderr")
        stderr.close()

    def _read_stdout(self) -> None:
        stdout = self._proc.stdout
        assert stdout is not None
        try:
            with _closing(self._stdout_log) as log:
                for raw in stdout:
                    log = self._copy(log, raw, "stdout")
                    self._take(raw)
        except BaseException as e:
            # Whatever failed here, the outcome is still decided: the
            # recorder is killed, its output drained, and the error is raised
            # to whoever waits on the capture.
            self._error = e
            self._signal(signal.SIGKILL)
            for _ in stdout:
                pass
        finally:
            stdout.close()
            self._stderr_thread.join()
            self._finish(self._proc.wait())

    def _take(self, raw: bytes) -> None:
        if self._override is not None:
            return
        try:
            line = parse_line(raw.rstrip(b"\n"))
        except ContractError as e:
            self._break(str(e), raw)
            return
        if self._terminal is not None:
            self._break("a line after the terminal line", raw)
            return
        match line:
            case _StartedLine():
                if self._started is not None:
                    self._break("a second capture_started", raw)
                    return
                problem = self._held_to_spec(line)
                if problem is not None:
                    self._break(problem, raw)
                    return
                with self._lock:
                    self._started = self._started_from(line)
                self._decided.set()
            case _FailedLine():
                self._terminal = line
            case _ if self._started is None:
                self._break("the first line is neither capture_started nor capture_failed", raw)
            case _StoppedLine():
                self._terminal = line
            case _GapLine():
                self._report(
                    Gap(
                        capture=self._capture,
                        cause=line.cause,
                        at_frame=line.at_frame,
                        frames=line.frames,
                        at=_reading(self._machine, line.mono_us, line.wall_us),
                        detail=line.detail,
                    )
                )
            case _LateLine():
                self._report(
                    Late(
                        capture=self._capture,
                        at_frame=line.at_frame,
                        backlog_frames=line.backlog_frames,
                        at=_reading(self._machine, line.mono_us, line.wall_us),
                    )
                )

    def _held_to_spec(self, line: _StartedLine) -> str | None:
        sent = self._layout
        if line.layout != sent:
            return f"layout {list(line.layout)} is not the layout sent, {list(sent)}"
        if line.granted.channels != len(sent):
            return f"{line.granted.channels} channels granted for {len(sent)} sent"
        return None

    def _started_from(self, line: _StartedLine) -> Started:
        return Started(
            capture=self._capture,
            capture_id=line.record_id,
            location=self._location,
            recorder=Recorder(program=PROGRAM, repository=REPOSITORY, build=line.build),
            device=line.device,
            granted=line.granted,
            layout=line.layout,
            start=_reading(self._machine, line.mono_us, line.wall_us),
            device_state=line.device_state,
        )

    def _break(self, why: str, raw: bytes) -> None:
        self._end_with(
            Failure(
                "recorder_contract",
                f"the recorder's output broke its contract: {why}",
                {"line": raw.decode("utf-8", errors="replace").rstrip("\n")},
            )
        )

    def _finish(self, status: int) -> None:
        outcome = self._outcome_of(status)
        with self._lock:
            self._outcome = outcome
            asked = self._stop_asked
            begun = self._started is not None
        try:
            # An end nobody asked for is reported before the capture reads as
            # ended, so that no one can find it ended and not yet reported.
            # With the reader's error held, no end reaches the capture's
            # record, which the run gives `end_not_reported`.
            if begun and not asked and self._error is None:
                self._report(
                    Failed(
                        capture=self._capture,
                        failure=_unasked(outcome),
                        stopped=outcome if isinstance(outcome, Stopped) else None,
                    )
                )
        except BaseException as e:
            self._error = e
        finally:
            self._decided.set()
            self._done.set()

    def _outcome_of(self, status: int) -> Stopped | Failure:
        if self._override is not None:
            return self._override
        exited: dict[str, JsonValue] = {"exit_status": status}
        match self._terminal:
            case _StoppedLine() as line:
                if status != 0:
                    return _contract(f"exit status {status} after capture_stopped", exited)
                return Stopped(
                    capture=self._capture,
                    frames=line.frames,
                    gaps_reported=line.gaps,
                    end=_reading(self._machine, line.mono_us, line.wall_us),
                )
            case _FailedLine() as line:
                if status != 1:
                    return _contract(f"exit status {status} after capture_failed", exited)
                return self._failure_from(line)
            case None if self._started is None and status == 2:
                text = self._stderr.decode("utf-8", errors="replace")
                first = (
                    text.splitlines()[0]
                    if text.strip()
                    else "the recorder refused its command line"
                )
                return Failure("recorder_usage", first, {"stderr": text, "exit_status": status})
            case None:
                return Failure(
                    "recorder_exited",
                    f"the recorder ended with no terminal line, exit status {status}",
                    exited,
                )

    def _failure_from(self, line: _FailedLine) -> Failure:
        reason = REASONS.get(line.reason)
        if reason is None:
            return _contract(f"unknown failure reason {line.reason!r}", {"reason": line.reason})
        data: dict[str, JsonValue] = {
            "at_frame": line.at_frame,
            "at": encode_value(_reading(self._machine, line.mono_us, line.wall_us)),
        }
        if line.cards is not None:
            data["cards"] = encode_value(line.cards)
        if reason == "write_stalled":
            # The line says only that the writer did not report in time;
            # whether the log ends is the reader tool's verdict, not given here.
            data["log_complete"] = "not_established"
        return Failure(reason, line.detail, data)


def _unasked(outcome: Stopped | Failure) -> Failure:
    """How a capture that ended with nobody asking reads as a failure."""
    if isinstance(outcome, Failure):
        return outcome
    return Failure("stopped_unasked", "the recorder stopped cleanly without being asked")


def _contract(why: str, data: dict[str, JsonValue]) -> Failure:
    return Failure("recorder_contract", f"the recorder broke its contract: {why}", data)


@contextlib.contextmanager
def _closing(f: IO[bytes] | None) -> Iterator[IO[bytes] | None]:
    try:
        yield f
    finally:
        if f is not None:
            # Every copy is flushed as it is made, so only a log whose copy
            # failed, which has already ended the capture, can fail here.
            with contextlib.suppress(OSError):
                f.close()
