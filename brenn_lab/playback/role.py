"""The playback role: one output, one play at a time, within its cap and quiet hours.

A play is judged before anything is written or set: its source, the card's
controls, the cap and quiet hours. It then writes the kept copy, opens the
stream, sets the controls, hands the first chunk over and replies; a thread of
the role's hands the rest over in chunks and, between chunks, ends the play if
it was asked to stop, if a control it holds moved or if quiet hours began.

A play with a stated start, a reading on the role's own clock, does all of
that but the first hand-over and replies; its thread waits for the start,
checking as between chunks while more than a chunk's time remains, and hands
the first chunk over when the role's monotonic clock reaches it. From its wake
to its end the thread reports nothing: its wake and first mark are carried by
its end.
"""

import datetime
import threading
import time
import wave
import zoneinfo
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Literal

from brenn_lab.data import (
    ROLE_ERROR,
    Check,
    ClockReading,
    Command,
    Event,
    Failure,
    JsonValue,
    Location,
    Release,
    Reply,
    encode_value,
    now,
)
from brenn_lab.place import Place, report_started, sink_raised_unwrapped
from brenn_lab.playback import limits, source
from brenn_lab.playback.device import PlaybackDevice
from brenn_lab.playback.messages import (
    Channel,
    Checked,
    CheckedControl,
    Clock,
    ClockRead,
    Control,
    Ended,
    Failed,
    Mark,
    Play,
    QuietSpan,
    Released,
    Started,
    Stop,
    Stopped,
    Underrun,
    VolumeRange,
)
from brenn_lab.playback.messages import Output as OutputRecord
from brenn_lab.playback.output import (
    Attached,
    ControlName,
    Element,
    Keeping,
    Output,
    Stream,
    WriteError,
)
from brenn_lab.playback.source import Judged

CHUNK_SECONDS = 0.1
PERIOD_SECONDS = 0.02
BUFFER_SECONDS = 0.5
MAX_RECOVERIES = 8
"""Consecutive recoveries without a successful write after which a play ends."""
PLAY_OUT_GRACE_SECONDS = 2.0
"""How long past the time its first reading of the delay gave a play's
buffer may take to play out before the play ends."""
LATE_SECONDS = 0.02
"""How far past its stated start a play's thread may wake and still play;
later than this the play fails `start_passed` with no frame handed."""


@dataclass(frozen=True)
class _Named:
    """A control the configuration names: the part it plays and, for a held
    control, the volume it is held at.
    """

    control: ControlName
    part: Literal["level", "held"]
    volume_mb: int | None


@dataclass(frozen=True)
class _Judged:
    """A play that passed its source and its limits: nothing written or set yet."""

    judged: Judged
    attached: Attached
    elements: dict[ControlName, Element]


@dataclass
class _Playing:
    """A play the role's thread is handing over."""

    n: int
    copy: Location
    stream: Stream
    reader: wave.Wave_read
    rate_hz: int
    # The local time of the last judgement of quiet hours, made just before the open.
    local_time: str
    # The stated start; None for a play that stated none.
    start: ClockReading | None = None
    # The controls as the first move read them; None until it has read them.
    found: tuple[Control, ...] | None = None
    set_to: tuple[Control, ...] = ()
    frames_handed: int = 0
    underruns: int = 0
    last: Mark | None = None
    # A timed play's reading when its wait ended, and its mark after the first write.
    woke: ClockReading | None = None
    first: Mark | None = None
    stop: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    stopped: Stopped | None = None
    thread: threading.Thread | None = None
    # What `_finish` raised in the thread when it could report no end.
    error: BaseException | None = None


class _Cut(Exception):
    """The play ends before its source was played out. `failure` is None when
    it was asked to stop.
    """

    def __init__(self, failure: Failure | None) -> None:
        super().__init__(None if failure is None else failure.detail)
        self.failure = failure


class PlaybackRole:
    """A playback role hosted on the runner's machine.

    `place` is None where nothing may be left behind (a dry run); such a role
    answers `check` and dry commands only. `clock` gives the role's clock
    readings and `local_now` the machine's local civil time, an aware datetime;
    by default the clock read in the zone `machine_zone` gives when the role is
    made.
    """

    def __init__(
        self,
        name: str,
        machine: str,
        device: PlaybackDevice,
        output: Output,
        sink: Callable[[Event], None],
        place: Place | None,
        clock: Callable[[], ClockReading] | None = None,
        local_now: Callable[[], datetime.datetime] | None = None,
    ):
        self._name = name
        self._machine = machine
        self._device = device
        self._output = output
        self._sink = sink
        self._place = place
        self._clock = clock if clock is not None else lambda: now(machine)
        self._local_now = (
            local_now if local_now is not None else partial(datetime.datetime.now, machine_zone())
        )
        self._lock = threading.Lock()
        self._count = 0
        self._playing: _Playing | None = None
        # A dry play has been made since the last dry stop or release.
        self._dry_played = False
        self._named = (
            _Named(device.level, "level", None),
            *(_Named(h.control, "held", h.volume_mb) for h in device.held),
        )

    def handle(self, command: Command) -> Reply:
        outcome = sink_raised_unwrapped(partial(self._locked, command))
        return Reply(role=self._name, command=command.name, dry=command.dry, outcome=outcome)

    def _locked(self, command: Command) -> object:
        with self._lock:
            try:
                return self._handle(command.body, command.dry, command.name)
            except OSError as e:
                return Failure("role_io", f"{command.name} on role {self._name!r}: {e}")

    def _handle(self, body: object, dry: bool, name: str) -> object:
        match body:
            case Check():
                return self._check(self._keeping(dry))
            case Clock():
                return ClockRead(self._clock())
            case Play():
                return self._dry_play(body) if dry else self._play(body, self._keeping(dry))
            case Stop():
                return self._dry_stop() if dry else self._stop()
            case Release():
                return self._release(dry)
            case _:
                return Failure(
                    "unknown_command",
                    f"a playback role has no command {name!r}",
                    {"command": name},
                )

    def _live(self) -> _Playing | None:
        if self._playing is not None and not self._playing.done.is_set():
            return self._playing
        return None

    def _report(self, body: object) -> None:
        self._sink(Event(role=self._name, body=body))

    def _keeping(self, dry: bool) -> Keeping | None:
        """How the output keeps what a program it starts writes: in the
        role's log directory, each start reported as `process.started`;
        nothing, for a dry command or a role with no place.
        """
        if dry or self._place is None:
            return None

        def started(process: str, pid: int, stdout_log: Path, stderr_log: Path) -> None:
            report_started(
                self._sink, self._name, self._machine, process, pid, stdout_log, stderr_log
            )

        return Keeping(self._place.logs, started)

    # Judging a play: nothing written, set or opened.

    def _verify(self, elements: tuple[Element, ...]) -> dict[ControlName, Element] | Failure:
        """The card's controls by name, when they are exactly the ones named."""
        by_name = {e.control: e for e in elements}
        named = {n.control for n in self._named}
        missing = [n.control for n in self._named if n.control not in by_name]
        if missing:
            return Failure(
                "no_control",
                f"the card has no control {', '.join(_label(c) for c in missing)}",
                {"controls": encode_value(missing)},
            )
        unheld = [e.control for e in elements if e.control not in named]
        if unheld:
            return Failure(
                "unheld_control",
                "the configuration does not hold the card's playback control "
                + ", ".join(_label(c) for c in unheld),
                {"controls": encode_value(unheld)},
            )
        for n in self._named:
            element = by_name[n.control]
            if n.part == "level" and element.range is None:
                return _volume_range(n.control, "the level control has no volume", None)
            if n.part == "held":
                failure = _held_volume(n, element)
                if failure is not None:
                    return failure
        return by_name

    def _read_all(self) -> tuple[Control, ...] | Failure:
        read = self._output.read([n.control for n in self._named])
        if isinstance(read, Failure):
            return read
        return tuple(_control(n, channels) for n, channels in zip(self._named, read, strict=True))

    def _judge(self, play: Play, keeping: Keeping | None) -> _Judged | Failure:
        judged = source.judge(self._device.source_dir, play.source)
        if isinstance(judged, Failure):
            return judged
        attached = self._output.attach(keeping)
        if isinstance(attached, Failure):
            return attached
        try:
            outcome = self._limits(judged, attached, play.volume_mb, play.start)
        except BaseException:
            self._output.detach()
            raise
        if isinstance(outcome, Failure):
            self._output.detach()
        return outcome

    def _limits(
        self, judged: Judged, attached: Attached, volume_mb: int, start: ClockReading | None
    ) -> _Judged | Failure:
        elements = self._output.elements()
        if isinstance(elements, Failure):
            return elements
        by_name = self._verify(elements)
        if isinstance(by_name, Failure):
            return by_name
        level_range = by_name[self._device.level].range
        assert level_range is not None
        if not level_range.min_mb <= volume_mb <= level_range.max_mb:
            return _volume_range(self._device.level, f"{volume_mb} mB is out of range", level_range)
        failure = limits.over_cap(volume_mb, self._held_mb(), self._device.cap_mb)
        if failure is not None:
            return failure
        local_time = self._quiet_hours(judged, start)
        if isinstance(local_time, Failure):
            return local_time
        return _Judged(judged, attached, by_name)

    def _quiet_hours(self, judged: Judged, start: ClockReading | None) -> str | Failure:
        """`quiet_hours` if any instant from now to the play's end is in a
        quiet span; else the local time it was judged at. The play's end is
        the source's length after now, or after `start` when one is stated
        and still ahead of the role's clock.
        """
        s = judged.source
        hold = 0.0
        if start is not None:
            hold = max(0, start.monotonic.us - self._clock().monotonic.us) / 1_000_000
        local = self._local_now()
        failure = limits.quiet_hours(self._device.quiet_hours, local, hold + s.frames / s.rate_hz)
        return failure if failure is not None else limits.local_time(local)

    def _own_clock(self, start: ClockReading | None) -> Failure | None:
        """`start_clock` unless `start` is null or a reading on this role's
        machine and on the monotonic clock of its own readings.
        """
        if start is None:
            return None
        clock = self._clock().monotonic.clock
        if start.machine == self._machine and start.monotonic.clock == clock:
            return None
        return Failure(
            "start_clock",
            f"the start is a reading of {start.monotonic.clock} on {start.machine!r}; "
            f"this role plays by {clock} on {self._machine!r}",
            {
                "machine": start.machine,
                "clock": start.monotonic.clock,
                "role_machine": self._machine,
            },
        )

    def _held_mb(self) -> list[int]:
        """The configured volume of each held control that has one."""
        return [n.volume_mb for n in self._named if n.part == "held" and n.volume_mb is not None]

    def _open(self, judged: Judged) -> Stream | Failure:
        s = judged.source
        stream = self._output.open(
            s.rate_hz,
            s.channels,
            s.sample_format,
            round(s.rate_hz * PERIOD_SECONDS),
            round(s.rate_hz * BUFFER_SECONDS),
        )
        if isinstance(stream, Failure):
            return stream
        g = stream.granted
        if (g.rate_hz, g.channels, g.sample_format) != (s.rate_hz, s.channels, s.sample_format):
            stream.close()
            return Failure(
                "params",
                f"asked for {s.rate_hz} Hz, {s.channels} channels, {s.sample_format}; "
                f"granted {g.rate_hz} Hz, {g.channels} channels, {g.sample_format}",
                {"rate_hz": g.rate_hz, "channels": g.channels, "sample_format": g.sample_format},
            )
        return stream

    def _move(
        self, targets: tuple[tuple[Channel, ...], ...]
    ) -> tuple[tuple[Control, ...] | None, Failure | None]:
        """Move every control the role holds to its target, one per control in
        the order they are named; a target's null volume or switch is left as
        it is. Lowers before it raises: reads every channel, sets down every
        volume above its target and turns off every switch whose target is
        off, and only if the read and every lowering succeeded sets every
        channel to its target. So no write takes a channel above both where
        it was read and its target. Every write of a pass is attempted.
        Returns the controls as read, None if the read failed, and the first
        failure met.
        """
        read = self._read_all()
        if isinstance(read, Failure):
            return None, read
        return read, self._write(read, targets)

    def _write(
        self, read: tuple[Control, ...], targets: tuple[tuple[Channel, ...], ...]
    ) -> Failure | None:
        """`_move`'s writes, from the controls as `read`: the first failure met."""
        failure: Failure | None = None
        for control, target in zip(read, targets, strict=True):
            lowering = _lowering(control.channels, target)
            if lowering is not None:
                refused = self._output.set(ControlName(control.element, control.index), lowering)
                if failure is None:
                    failure = refused
        if failure is not None:
            return failure
        for n, target in zip(self._named, targets, strict=True):
            refused = self._output.set(n.control, target)
            if failure is None:
                failure = refused
        return failure

    def _targets(self, j: _Judged, volume_mb: int) -> tuple[tuple[Channel, ...], ...]:
        """What a play sets: the level control to `volume_mb`, each held
        control to its configured volume, every switch on, on every channel.
        """
        return tuple(
            tuple(
                Channel(
                    c,
                    volume_mb if n.part == "level" else n.volume_mb,
                    True if j.elements[n.control].switch else None,
                )
                for c in j.elements[n.control].channels
            )
            for n in self._named
        )

    def _read_back(self, volume_mb: int) -> tuple[Control, ...] | Failure:
        """Read every control back and hold the values against what was asked
        and against the cap.
        """
        controls = self._read_all()
        if isinstance(controls, Failure):
            return controls
        for n, control in zip(self._named, controls, strict=True):
            asked = volume_mb if n.part == "level" else n.volume_mb
            for c in control.channels:
                too_high = asked is not None and (c.volume_mb is None or c.volume_mb > asked)
                if too_high or c.switch is False:
                    return Failure(
                        "controls",
                        f"{_label(n.control)} channel {c.channel} reads back as "
                        f"{c.volume_mb} mB, switch {c.switch}; asked {asked} mB, switch on",
                        {"controls": encode_value(controls)},
                    )
        # No channel reads back above what was asked, and what was asked was
        # held against the cap before anything was set, so this cannot fail.
        level_volume, held = _volumes(controls)
        assert limits.over_cap(level_volume, held, self._device.cap_mb) is None
        return controls

    def _restore(self, found: tuple[Control, ...]) -> bool:
        """Move every control back to what was found, lowering first; whether
        the move succeeded and every control reads back as found. Not tried
        again.
        """
        read, failure = self._move(tuple(c.channels for c in found))
        if read is None or failure is not None:
            return False
        after = self._output.read([ControlName(c.element, c.index) for c in found])
        return after == tuple(c.channels for c in found)

    def _check(self, keeping: Keeping | None) -> Checked | Failure:
        if self._live() is not None:
            return Failure("already_playing", "a play is running; its controls are the role's")
        attached = self._output.attach(keeping)
        if isinstance(attached, Failure):
            return attached
        try:
            return self._checked(attached)
        finally:
            self._output.detach()

    def _checked(self, attached: Attached) -> Checked | Failure:
        elements = self._output.elements()
        if isinstance(elements, Failure):
            return elements
        by_name = self._verify(elements)
        if isinstance(by_name, Failure):
            return by_name
        found = self._read_all()
        if isinstance(found, Failure):
            return found
        local = self._local_now()
        return Checked(
            output=OutputRecord(self._output.kind, attached.library),
            device=attached.device,
            controls=tuple(
                CheckedControl(c.element, c.index, c.part, by_name[n.control].range, c.channels)
                for n, c in zip(self._named, found, strict=True)
            ),
            cap_mb=self._device.cap_mb,
            quiet_hours=tuple(
                QuietSpan(limits.format_clock(s.start), limits.format_clock(s.end))
                for s in self._device.quiet_hours
            ),
            local_time=limits.local_time(local),
            quiet_now=limits.quiet_span_during(self._device.quiet_hours, local, 0) is not None,
        )

    def _started(
        self,
        n: int,
        j: _Judged,
        copy: Location | None,
        stream: Stream,
        controls: tuple[Control, ...],
        found: tuple[Control, ...],
        level_mb: int,
        local_time: str,
        asked_start: ClockReading | None,
        first: Mark | None,
    ) -> Started:
        return Started(
            play=n,
            source=j.judged.source,
            copy=copy,
            output=OutputRecord(self._output.kind, j.attached.library),
            device=j.attached.device,
            granted=stream.granted,
            controls=controls,
            found=found,
            level_mb=level_mb,
            cap_mb=self._device.cap_mb,
            local_time=local_time,
            asked_start=asked_start,
            first=first,
        )

    def _dry_play(self, play: Play) -> Started | Failure:
        """Never `already_playing`: a trace passes no time, so whether an
        earlier play has played out cannot be known. Reports the controls as
        read before the open, and the level of the asked and configured
        volumes. A stated start is held to the role's clock and quiet hours
        judged up to its end; whether it has passed is not judged, and
        nothing is held for it.
        """
        failure = self._own_clock(play.start)
        if failure is not None:
            return failure
        j = self._judge(play, None)
        if isinstance(j, Failure):
            return j
        try:
            found = self._read_all()
            if isinstance(found, Failure):
                return found
            local_time = self._quiet_hours(j.judged, play.start)
            if isinstance(local_time, Failure):
                return local_time
            # TODO(output-open-not-listened-to): whether this open, with no
            # frame written, is silent on a real output has not been heard.
            stream = self._open(j.judged)
            if isinstance(stream, Failure):
                return stream
            stream.close()
        finally:
            self._output.detach()
        self._dry_played = True
        level = limits.level_mb(play.volume_mb, self._held_mb())
        return self._started(0, j, None, stream, found, found, level, local_time, play.start, None)

    def _settle(self) -> None:
        """Forget a play whose thread has ended, once the thread has. An error
        that ended the thread before the play's end was reported is raised
        here.
        """
        p = self._playing
        if p is not None and p.done.is_set():
            # The thread sets `done` before it raises on; joined, whatever it
            # raised has reached the thread hook.
            if p.thread is not None:
                p.thread.join()
            self._playing = None
            if p.error is not None:
                raise RuntimeError("the play's thread failed before reporting its end") from (
                    p.error
                )

    def _play(self, play: Play, keeping: Keeping | None) -> Started | Failure:
        if self._live() is not None:
            return Failure("already_playing", "a play is running")
        self._settle()
        if self._place is None:
            raise RuntimeError(f"role {self._name!r} has no place to keep a copy")
        failure = self._own_clock(play.start)
        if failure is not None:
            return failure
        j = self._judge(play, keeping)
        if isinstance(j, Failure):
            return j
        return self._begin(play, j, self._place)

    def _begin(self, play: Play, j: _Judged, place: Place) -> Started | Failure:
        """Keep the copy, open, set the controls, hand the first chunk over
        unless the play states a start, and start the thread that hands over
        the rest. Every way out before the stream is open detaches; every way
        out after it, short of the thread taking the play over, goes through
        `_finish`. What ending such a play raised is raised on, unless
        something is already being raised, which then carries it as a note.
        """
        try:
            p = self._prepare(j, place, play.start)
        except BaseException:
            self._output.detach()
            raise
        if isinstance(p, Failure):
            self._output.detach()
            return p
        thread = threading.Thread(
            target=self._run, args=(p,), name=f"playback-{self._name}-{p.n}", daemon=True
        )
        try:
            started = self._first(p, j, play.volume_mb)
        except BaseException as e:
            _carrying(e, self._finish(p, None))
            raise
        if isinstance(started, Failure):
            kept = self._finish(p, None)
            if kept:
                raised = _carrying(kept[0], kept)
                raised.add_note(f"the play did not begin: {started.reason}: {started.detail}")
                raise raised
            return started
        p.thread = thread
        try:
            thread.start()
        except BaseException as e:
            p.thread = None
            _carrying(e, self._finish(p, None))
            raise
        self._playing = p
        return started

    def _prepare(self, j: _Judged, place: Place, start: ClockReading | None) -> _Playing | Failure:
        """Keep the copy and open the stream. Quiet hours are judged again once
        the copy is written, since writing and syncing it takes unbounded
        time; a play refused then leaves no copy and uses no number.
        """
        n = self._count + 1
        place.data.mkdir(parents=True, exist_ok=True)
        copy = place.data / f"{n:04d}.wav"
        failure = source.write_copy(j.judged, copy)
        if failure is not None:
            return failure
        local_time = self._quiet_hours(j.judged, start)
        if isinstance(local_time, Failure):
            copy.unlink()
            return local_time
        self._count = n
        try:
            reader = wave.open(str(copy), "rb")
        except (OSError, EOFError, wave.Error) as e:
            return Failure("role_io", f"the kept copy {copy} could not be read: {e}")
        try:
            stream = self._open(j.judged)
        except BaseException:
            reader.close()
            raise
        if isinstance(stream, Failure):
            reader.close()
            return stream
        s = j.judged.source
        return _Playing(
            n, Location(self._machine, str(copy)), stream, reader, s.rate_hz, local_time, start
        )

    def _first(self, p: _Playing, j: _Judged, volume_mb: int) -> Started | Failure:
        """Set the controls; then hand the first chunk over, or, for a play
        with a stated start, refuse it with `start_passed` if the role's
        clock has reached the start.
        """
        controls = self._set_controls(p, j, volume_mb)
        if isinstance(controls, Failure):
            return controls
        assert p.found is not None
        first: Mark | None = None
        if p.start is None:
            try:
                self._hand_over(p, p.reader.readframes(_chunk_frames(p.rate_hz)))
            except _Cut as cut:
                assert cut.failure is not None
                return cut.failure
            first = p.last
            assert first is not None
        else:
            late_us = self._clock().monotonic.us - p.start.monotonic.us
            if late_us >= 0:
                return _start_passed(late_us, "when its output was ready")
        level = limits.level_mb(*_volumes(controls))
        return self._started(
            p.n, j, p.copy, p.stream, controls, p.found, level, p.local_time, p.start, first
        )

    def _set_controls(
        self, p: _Playing, j: _Judged, volume_mb: int
    ) -> tuple[Control, ...] | Failure:
        """Set the controls, what the move read being what was found, and read
        them back. What was found is kept before anything is written, so the
        end of a play whose move raised restores it.
        """
        found = self._read_all()
        if isinstance(found, Failure):
            return found
        p.found = found
        failure = self._write(found, self._targets(j, volume_mb))
        if failure is not None:
            return failure
        controls = self._read_back(volume_mb)
        if isinstance(controls, Failure):
            return controls
        p.set_to = controls
        return controls

    def _stop(self) -> Stopped | Failure:
        p = self._live()
        if p is None:
            self._settle()
            return Failure("not_playing", "no play is running")
        assert p.thread is not None
        p.stop.set()
        p.thread.join()
        self._settle()
        if p.stopped is None:
            return Failure("not_playing", "the play ended before it was stopped")
        return p.stopped

    def _dry_stop(self) -> Stopped | Failure:
        if not self._dry_played:
            return Failure("not_playing", "no play was made since the last stop")
        self._dry_played = False
        return Stopped(
            play=0,
            frames_handed=0,
            unplayed_frames=0,
            underruns=0,
            last=None,
            end=self._clock(),
            restored=True,
            woke=None,
            first=None,
        )

    def _release(self, dry: bool) -> Released | Failure:
        """A dry release stops nothing: a trace cannot know whether a dry play
        would still be running.
        """
        self._dry_played = False
        if dry:
            return Released(stopped=None)
        if self._live() is None:
            self._settle()
            return Released(stopped=None)
        stopped = self._stop()
        return Released(stopped=None if isinstance(stopped, Failure) else stopped)

    # The play's thread.

    def _mark(self, p: _Playing) -> Mark:
        # A mark ties the copy to the machine's clock to within milliseconds;
        # nothing here ties a frame of the copy to a frame of a capture.
        # TODO(own-device-playback-sample-relationship)
        delay = p.stream.delay()
        return Mark(frames_handed=p.frames_handed, delay_frames=delay, at=self._clock())

    def _hand_over(self, p: _Playing, data: bytes) -> None:
        """Hand `data` to the device whole, recovering from underruns; `_Cut`
        with `stream_died` on any other write error, a failed recovery, or
        `MAX_RECOVERIES` without a successful write.
        """
        frame_bytes = p.reader.getnchannels() * p.reader.getsampwidth()
        view = memoryview(data)
        recoveries = 0
        while view:
            written = p.stream.write(bytes(view))
            if isinstance(written, WriteError):
                # Only an underrun is recovered and reported: a recovery from
                # anything else (a suspend, say) may discard the buffer unseen.
                if not written.underrun:
                    raise _Cut(Failure("stream_died", written.detail))
                if recoveries == MAX_RECOVERIES:
                    raise _Cut(
                        Failure(
                            "stream_died",
                            f"{MAX_RECOVERIES} recoveries without a successful write: "
                            + written.detail,
                        )
                    )
                recoveries += 1
                failure = p.stream.recover(written)
                if failure is not None:
                    raise _Cut(Failure("stream_died", failure.detail, failure.data))
                p.underruns += 1
                self._report(Underrun(p.n, p.frames_handed, self._clock(), written.detail))
                continue
            if written <= 0:
                raise _Cut(Failure("stream_died", "the device took no frames from a write"))
            recoveries = 0
            p.frames_handed += written
            view = view[written * frame_bytes :]
        p.last = self._mark(p)

    def _between(self, p: _Playing) -> None:
        """`_Cut` when the play must end: asked to stop, a control moved or
        quiet hours begun.
        """
        if p.stop.is_set():
            raise _Cut(None)
        names = [ControlName(c.element, c.index) for c in p.set_to]
        read = self._output.read(names)
        if isinstance(read, Failure):
            raise _Cut(
                Failure(
                    "controls_moved",
                    f"the controls the play holds could not be read: {read.detail}",
                    read.data,
                )
            )
        for control, name, channels in zip(p.set_to, names, read, strict=True):
            if channels != control.channels:
                raise _Cut(
                    Failure(
                        "controls_moved",
                        f"{_label(name)} was set to {_encode_channels(control.channels)} "
                        f"and reads {channels}",
                        {"control": _label(name)},
                    )
                )
        failure = limits.quiet_hours(self._device.quiet_hours, self._local_now(), 0)
        if failure is not None:
            raise _Cut(failure)

    def _run(self, p: _Playing) -> None:
        """Hand the rest of the copy over and play the buffer out. Every way
        out of the thread goes through `_finish`; an exception nothing here
        names ends the play with `role_error` and is raised on, as is the
        first one ending the play raised; whichever is raised carries every
        other that ending the play raised as a note.
        """
        failure: Failure | None = None
        ended = False
        error: BaseException | None = None
        # The outer handler takes whatever the inner ones raise, and raises
        # nothing itself, so every way out reaches `_finish`.
        try:
            try:
                self._play_out(p)
                drained = p.stream.drain()
                if drained is None:
                    ended = True
                else:
                    failure = Failure("stream_died", drained.detail, drained.data)
            except _Cut as cut:
                failure = cut.failure
            except OSError as e:
                failure = Failure("role_io", f"the kept copy could not be read: {e}")
        except BaseException as e:
            failure = Failure(ROLE_ERROR, f"the play's thread raised {_repr(e)}")
            error = e
        try:
            kept = self._finish(p, failure, ended)
        except BaseException as e:
            # The role's clock or the event's sink raised, so no end reaches
            # the play's record, which the run gives `end_not_reported`. `e`
            # is the sink's exception or the first that ending the play
            # raised, and carries every other, the clock's included, and how
            # the controls were left, as notes. The output was let go before.
            p.error = e
            if error is not None:
                raise e from error
            raise
        finally:
            p.done.set()
        if error is not None:
            raise _carrying(error, kept)
        if kept:
            raise _carrying(kept[0], kept)

    def _hold(self, p: _Playing, start: ClockReading) -> None:
        """Wait until the role's monotonic clock reaches `start`, then keep
        the reading as `p.woke`. While more than a chunk's time remains,
        check as between chunks, then wait on the stop event for a chunk's
        time or what remains after the check, whichever is less; inside the
        last chunk's time make no check and wait on it for what remains,
        again while the clock still reads before the start. What remains is
        always the start less a reading taken after any check. `_Cut` when
        asked to stop, as `_between` cuts, or with `start_passed` when the
        wake is more than `LATE_SECONDS` late.
        """
        # TODO(output-open-not-listened-to): the output is held open with no
        # frame written until the start; whether that is silent on a real
        # output has not been heard.
        chunk_us = round(CHUNK_SECONDS * 1_000_000)
        while True:
            remaining_us = start.monotonic.us - self._clock().monotonic.us
            if remaining_us > chunk_us:
                self._between(p)
                remaining_us = min(chunk_us, start.monotonic.us - self._clock().monotonic.us)
            if remaining_us <= 0:
                break
            if p.stop.wait(remaining_us / 1_000_000):
                raise _Cut(None)
        p.woke = self._clock()
        late_us = p.woke.monotonic.us - start.monotonic.us
        if late_us > LATE_SECONDS * 1_000_000:
            raise _Cut(_start_passed(late_us, "when the play's thread woke"))

    def _play_out(self, p: _Playing) -> None:
        """Wait for a stated start and hand the first chunk over at it, its
        mark kept as the first even when the chunk is cut part-way; hand
        the rest over, then wait for the output's delay to reach 0.
        The first delay above 0 sets a deadline, that delay at the play's
        rate plus `PLAY_OUT_GRACE_SECONDS`, which nothing moves; a delay
        still above 0 at the deadline ends the play with `stream_died`.
        """
        chunk = _chunk_frames(p.rate_hz)
        if p.start is not None:
            self._hold(p, p.start)
            try:
                self._hand_over(p, p.reader.readframes(chunk))
            except _Cut:
                # Frames the device took before the chunk was cut still get a
                # first mark, so a play with a null mark handed no frame.
                if p.frames_handed > 0:
                    p.last = p.first = self._mark(p)
                raise
            p.first = p.last
        while data := p.reader.readframes(chunk):
            self._between(p)
            self._hand_over(p, data)
        deadline: float | None = None
        started = time.monotonic()
        while True:
            self._between(p)
            delay = p.stream.delay()
            if delay is None or delay <= 0:
                return
            at = time.monotonic()
            if deadline is None:
                deadline = at + delay / p.rate_hz + PLAY_OUT_GRACE_SECONDS
            elif at >= deadline:
                raise _Cut(
                    Failure(
                        "stream_died",
                        f"the output did not play its buffer out: {delay} frames left "
                        f"after {at - started:.1f} s",
                        {"delay_frames": delay},
                    )
                )
            p.stop.wait(min(CHUNK_SECONDS, delay / p.rate_hz, max(0.0, deadline - at)))

    def _finish(
        self, p: _Playing, failure: Failure | None, ended: bool = False
    ) -> list[BaseException]:
        """Close the stream, restore the controls, let the output go, and
        report how the play ended: an `Ended` or `Failed` event, or the
        `Stopped` a stop gets. Each step is made whatever an earlier one
        raised. A play whose ending raised is reported `Failed`: with the
        failure that ended it, the exceptions added to its detail, or else
        `role_error`. Returns every exception a step raised, in order, once
        the end is reported; a play the thread has not taken over reports
        nothing. Raises when no end can be reported, the clock or the event's
        sink having raised: the first exception a step raised, or the sink's,
        carrying every other as a note.
        """
        kept: list[BaseException] = []
        unplayed = 0 if ended else _attempt(kept, p.stream.delay)
        if not ended:
            _attempt(kept, p.stream.drop)
        _attempt(kept, p.stream.close)
        _attempt(kept, p.reader.close)
        end = _attempt(kept, self._clock)
        # A play whose first read failed or raised set nothing, so has nothing
        # to restore.
        found = p.found
        restored = found is None or _attempt(kept, lambda: self._restore(found)) is True
        _attempt(kept, self._output.detach)
        if p.thread is None:
            return kept
        if end is None:
            raise _restoration(_carrying(kept[0], kept), found is not None, restored)
        last = p.last
        if kept:
            failure = _ended_by(failure, kept)
        try:
            if failure is not None:
                self._report(
                    Failed(
                        p.n,
                        failure,
                        p.frames_handed,
                        unplayed,
                        p.underruns,
                        last,
                        end,
                        restored,
                        p.woke,
                        p.first,
                    )
                )
            elif ended:
                assert unplayed is not None and last is not None
                self._report(
                    Ended(
                        p.n,
                        p.frames_handed,
                        unplayed,
                        p.underruns,
                        last,
                        end,
                        restored,
                        p.woke,
                        p.first,
                    )
                )
            else:
                p.stopped = Stopped(
                    p.n,
                    p.frames_handed,
                    unplayed,
                    p.underruns,
                    last,
                    end,
                    restored,
                    p.woke,
                    p.first,
                )
        except BaseException as e:
            _restoration(_carrying(e, kept), found is not None, restored)
            raise
        return kept


def _restoration(error: BaseException, had: bool, restored: bool) -> BaseException:
    """`error`, raised when a play's end cannot be reported, with a note
    saying how the play left its controls, which nothing else carries.
    """
    if not had:
        error.add_note("the play had read no controls, so had none to put back")
    elif restored:
        error.add_note("the play's controls were put back as found")
    else:
        error.add_note("the play's controls were not all put back as found")
    return error


LOCALTIME = Path("/etc/localtime")


def machine_zone(path: Path = LOCALTIME) -> datetime.tzinfo:
    """The machine's zone setting: the zone file at `path`, or UTC when there
    is none, as the C library takes it. The process's `TZ` is not read, so no
    environment variable moves quiet hours.
    """
    try:
        with path.open("rb") as file:
            return zoneinfo.ZoneInfo.from_file(file, key=str(path))
    except FileNotFoundError:
        return datetime.UTC


def _attempt[T](kept: list[BaseException], step: Callable[[], T]) -> T | None:
    """`step`'s result; None, with the exception added to `kept`, when it raised."""
    try:
        return step()
    except BaseException as e:
        kept.append(e)
        return None


def _carrying(error: BaseException, kept: list[BaseException]) -> BaseException:
    """`error`, with a note naming each exception in `kept` other than it."""
    for e in kept:
        if e is not error:
            error.add_note(f"ending the play also raised {_repr(e)}")
    return error


def _repr(e: BaseException) -> str:
    """`e`'s `repr`, or its type's name when that `repr` raised."""
    try:
        return repr(e)
    except BaseException:
        return f"<{type(e).__qualname__} whose repr raised>"


def _ended_by(failure: Failure | None, kept: list[BaseException]) -> Failure:
    """The failure of a play whose ending raised the exceptions in `kept`:
    the one that had ended it, with the exceptions in its detail, or else
    `role_error`.
    """
    raised = f"ending the play raised {', '.join(_repr(e) for e in kept)}"
    if failure is None:
        return Failure(ROLE_ERROR, raised)
    return Failure(failure.reason, f"{failure.detail}; {raised}", failure.data)


def _start_passed(late_us: int, when: str) -> Failure:
    return Failure(
        "start_passed",
        f"the play's start had passed by {late_us} us {when}; no frame was handed over",
        {"late_us": late_us},
    )


def _chunk_frames(rate_hz: int) -> int:
    return max(1, round(rate_hz * CHUNK_SECONDS))


def _control(n: _Named, channels: tuple[Channel, ...]) -> Control:
    return Control(n.control.element, n.control.index, n.part, channels)


def _lowering(now: tuple[Channel, ...], target: tuple[Channel, ...]) -> tuple[Channel, ...] | None:
    """The write that lowers a control from `now` toward `target` and raises
    nothing: each channel's volume where it is above its target, its switch
    off where it is on and its target is off, null otherwise. None when
    nothing is to be lowered.
    """
    targets = {t.channel: t for t in target}
    lowering = []
    for c in now:
        t = targets[c.channel]
        volume = (
            t.volume_mb
            if t.volume_mb is not None and c.volume_mb is not None and c.volume_mb > t.volume_mb
            else None
        )
        switch = False if c.switch is True and t.switch is False else None
        lowering.append(Channel(c.channel, volume, switch))
    if all(c.volume_mb is None and c.switch is None for c in lowering):
        return None
    return tuple(lowering)


def _volumes(controls: tuple[Control, ...]) -> tuple[int, list[int]]:
    """The level control's volume and each held control's, each the highest of
    its channels'; a held control with no volume counts for nothing.
    """
    level = 0
    held = []
    for control in controls:
        volumes = [c.volume_mb for c in control.channels if c.volume_mb is not None]
        if control.part == "level":
            level = max(volumes)
        elif volumes:
            held.append(max(volumes))
    return level, held


def _held_volume(n: _Named, element: Element) -> Failure | None:
    r = element.range
    if r is None and n.volume_mb is not None:
        return _volume_range(n.control, "the control has no volume, and one is stated", None)
    if r is not None and n.volume_mb is None:
        return _volume_range(n.control, "the control has a volume, and none is stated", r)
    if r is not None and n.volume_mb is not None and not r.min_mb <= n.volume_mb <= r.max_mb:
        return _volume_range(n.control, f"{n.volume_mb} mB is out of range", r)
    return None


def _volume_range(control: ControlName, why: str, r: VolumeRange | None) -> Failure:
    data: dict[str, JsonValue] = {"control": _label(control)}
    if r is not None:
        data |= {"min_mb": r.min_mb, "max_mb": r.max_mb}
    return Failure("volume_range", f"{_label(control)}: {why}", data)


def _label(control: ControlName) -> str:
    return f"{control.element!r} index {control.index}"


def _encode_channels(channels: tuple[Channel, ...]) -> str:
    return ", ".join(f"{c.channel}: {c.volume_mb} mB switch {c.switch}" for c in channels)
