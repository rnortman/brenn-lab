"""The capture role: one device, recorded to one file per capture.

The role holds the state (idle or capturing, and a dry state of its own) and
nothing about the recorder's command line or lines; those are the adapter's.
"""

import dataclasses
import shutil
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from brenn_lab.capture import pod_capture
from brenn_lab.capture.device import CaptureDevice
from brenn_lab.capture.messages import Checked, Released, Start, Started, Stop, Stopped
from brenn_lab.data import Check, Command, Event, Failure, Release, Reply
from brenn_lab.place import Place, log_paths, report_started, sink_raised_unwrapped


def _unreported(pid: int) -> None:
    """A probe that keeps nothing reports no start."""


@dataclass(frozen=True)
class _Probe:
    """A probe's start and stop, as capture 0, with no location: a dry reply's."""

    started: Started
    stopped: Stopped


class CaptureRole:
    """A capture role hosted on the runner's machine.

    `place` is None where nothing may be left behind (a dry run); such a role
    answers `check` and dry commands only.
    """

    def __init__(
        self,
        name: str,
        machine: str,
        program: str,
        device: CaptureDevice,
        sink: Callable[[Event], None],
        place: Place | None,
    ):
        self._name = name
        self._machine = machine
        self._program = program
        self._device = device
        self._sink = sink
        self._place = place
        self._lock = threading.Lock()
        self._count = 0
        self._probes = 0
        self._running: pod_capture.Running | None = None
        self._probe: _Probe | Failure | None = None
        self._dry_capturing = False

    def handle(self, command: Command) -> Reply:
        outcome = sink_raised_unwrapped(partial(self._locked, command))
        return Reply(role=self._name, command=command.name, dry=command.dry, outcome=outcome)

    def _locked(self, command: Command) -> object:
        with self._lock:
            try:
                return self._handle(command.body, command.dry, command.name)
            except OSError as e:
                # The role's own files: its directories in the run, its copies
                # of the recorder's output, the probe's scratch directory.
                return pod_capture.io_failure(e, f"{command.name} on role {self._name!r}")

    def _handle(self, body: object, dry: bool, name: str) -> object:
        match body:
            case Check():
                return self._check(dry)
            case Start():
                return self._dry_start() if dry else self._start()
            case Stop():
                return self._dry_stop() if dry else self._stop()
            case Release():
                return self._release(dry)
            case _:
                return Failure(
                    "unknown_command",
                    f"a capture role has no command {name!r}",
                    {"command": name},
                )

    def _live(self) -> pod_capture.Running | None:
        """The capture whose recorder is still running, if any."""
        if self._running is not None and not self._running.ended:
            return self._running
        return None

    def _report(self, body: object) -> None:
        self._sink(Event(role=self._name, body=body))

    def _spec(self, out: Path, logs: tuple[Path, Path] | None) -> pod_capture.Spec:
        return pod_capture.Spec(
            program=self._program,
            machine=self._machine,
            device=self._device,
            pod_id=self._name,
            out=out,
            logs=logs,
        )

    def _spawned(self, logs: tuple[Path, Path]) -> Callable[[int], None]:
        """What reports a recorder spawned with `logs` as `process.started`."""
        return lambda pid: report_started(
            self._sink, self._name, self._machine, pod_capture.PROGRAM, pid, *logs
        )

    def _check(self, dry: bool) -> Checked | Failure:
        if self._live() is not None or (dry and self._dry_capturing):
            return Failure(
                "already_capturing", "the device is being recorded; a probe would not open it"
            )
        probe = self._run_probe(None if dry else self._place)
        if isinstance(probe, Failure):
            return probe
        s = probe.started
        return Checked(
            recorder=s.recorder,
            device=s.device,
            granted=s.granted,
            layout=s.layout,
            device_state=s.device_state,
        )

    def _run_probe(self, place: Place | None) -> _Probe | Failure:
        """Start a capture into a scratch directory, stop it as soon as it has
        started, and delete the directory. With a `place`, the recorder's
        output is kept as the role's next probe's logs and its start is
        reported; with none, nothing is kept.
        """
        scratch = Path(tempfile.mkdtemp(prefix="brenn-lab-probe-"))
        try:
            logs = None
            spawned: Callable[[int], None] = _unreported
            if place is not None:
                self._probes += 1
                logs = log_paths(place.logs, f"probe-{self._probes:04d}")
                spawned = self._spawned(logs)
            spec = self._spec(scratch / "probe.framelog", logs)
            running = pod_capture.start(spec, 0, lambda _: None, spawned)
            if isinstance(running, Failure):
                probe: _Probe | Failure = running
            else:
                try:
                    stopped = running.stop()
                except BaseException:
                    running.abort()
                    raise
                probe = (
                    stopped
                    if isinstance(stopped, Failure)
                    else _Probe(dataclasses.replace(running.started, location=None), stopped)
                )
        finally:
            shutil.rmtree(scratch)
        self._probe = probe
        return probe

    def _settle(self) -> None:
        """Forget a capture whose recorder has exited. An error that broke its
        reader is raised here: no stop has raised it, and the capture has
        reported no end.
        """
        running = self._running
        if running is not None and running.ended:
            self._running = None
            running.raise_error()

    def _start(self) -> Started | Failure:
        if self._live() is not None:
            return Failure("already_capturing", "a capture is running")
        self._settle()
        if self._place is None:
            raise RuntimeError(f"role {self._name!r} has no place to write a capture")
        self._count += 1
        n = self._count
        self._place.data.mkdir(parents=True, exist_ok=True)
        logs = log_paths(self._place.logs, f"recorder-{n:04d}")
        out = self._place.data / f"{n:04d}.framelog"
        running = pod_capture.start(self._spec(out, logs), n, self._report, self._spawned(logs))
        if isinstance(running, Failure):
            return running
        self._running = running
        return running.started

    def _stop(self) -> Stopped | Failure:
        running = self._running
        if running is None:
            return Failure("not_capturing", "no capture is running")
        self._running = None
        return running.stop()

    def _dry_probe(self) -> _Probe | Failure:
        if self._probe is None:
            return self._run_probe(None)
        return self._probe

    def _dry_start(self) -> Started | Failure:
        if self._dry_capturing:
            return Failure("already_capturing", "a capture is running")
        probe = self._dry_probe()
        if isinstance(probe, Failure):
            return probe
        self._dry_capturing = True
        return probe.started

    def _dry_stop(self) -> Stopped | Failure:
        probe = self._dry_probe()
        if isinstance(probe, Failure):
            return probe
        if not self._dry_capturing:
            return Failure("not_capturing", "no capture is running")
        self._dry_capturing = False
        return probe.stopped

    def _release(self, dry: bool) -> Released | Failure:
        if dry:
            was = self._dry_capturing
            self._dry_capturing = False
            if not was:
                return Released(stopped=None)
            probe = self._dry_probe()
            if isinstance(probe, Failure):
                return probe
            return Released(stopped=probe.stopped)
        self._dry_capturing = False
        if self._live() is None:
            self._settle()
            return Released(stopped=None)
        stopped = self._stop()
        if isinstance(stopped, Failure):
            return stopped
        return Released(stopped=stopped)
