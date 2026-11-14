"""The playback kind's commands, results and events.

Every level is an integer number of millibels, hundredths of a decibel.
"""

from dataclasses import dataclass
from typing import Literal

from brenn_lab.data import ClockReading, Failure, Location, Request, command, event, result
from brenn_lab.marker import Carried
from brenn_lab.sound import CardIdentity, Granted


@dataclass(frozen=True)
class Source:
    """A source file as a play named it, the hash of its bytes, and what it
    holds. `peak_mb` is the largest sample magnitude relative to full scale,
    rounded up; null for a source of silence. `marker` is the marker its
    frames carry, recognised from the samples; null when they carry none.
    """

    name: str
    sha256: str
    frames: int
    rate_hz: int
    channels: int
    sample_format: str
    peak_mb: int | None
    marker: Carried | None


@dataclass(frozen=True)
class Library:
    """The code through which the role reaches its device, a library it
    loaded or a program it runs, as that code names and versions itself.
    """

    name: str
    version: str


@dataclass(frozen=True)
class SinkIdentity:
    """A sound server's sink as the server names it, and the server.

    `description` is null for a sink the server describes not at all.
    `server_sample_format` is the server's own string for the sink's format,
    unparsed. `hardware_volume` says the server reports the sink's volume as
    the device's own. `base_volume_mb` is the server's base volume for the
    sink, read as a sink volume is; 0 when it is the sink's full volume.
    """

    selector: str
    sink_name: str
    description: str | None
    server_name: str
    server_version: str
    rate_hz: int
    channels: int
    server_sample_format: str
    hardware_volume: bool
    base_volume_mb: int


@dataclass(frozen=True)
class Output:
    """The kind of output the role plays through, and the library it plays with."""

    kind: str
    library: Library


@dataclass(frozen=True)
class Channel:
    """One playback channel of a control, by the output's name for it. A field
    is null where the control has no volume or no switch.
    """

    channel: str
    volume_mb: int | None
    switch: bool | None


@dataclass(frozen=True)
class Control:
    """A control the role holds, on every playback channel it has. `part` says
    whether it is the control a play sets (`level`) or one held at the
    configuration's value (`held`).
    """

    element: str
    index: int
    part: Literal["level", "held"]
    channels: tuple[Channel, ...]


@dataclass(frozen=True)
class VolumeRange:
    """The volumes a control can be set to."""

    min_mb: int
    max_mb: int


@dataclass(frozen=True)
class CheckedControl:
    """A control as `check` found it. `range` is null for a control with no volume."""

    element: str
    index: int
    part: Literal["level", "held"]
    range: VolumeRange | None
    channels: tuple[Channel, ...]


@dataclass(frozen=True)
class Mark:
    """A frame count tied to a clock reading and to the device's own account.

    `delay_frames` is the device's delay read when the mark was taken, null if
    the read failed; `at` is read directly after it. By the device's account,
    frame `frames_handed - delay_frames` of the copy is at its output at `at`.
    """

    frames_handed: int
    delay_frames: int | None
    at: ClockReading


@dataclass(frozen=True)
class QuietSpan:
    """A span of local civil time, `"HH:MM"` to `"HH:MM"`, in which nothing plays.
    A span whose end is not after its start wraps midnight.
    """

    start: str
    end: str


@result("playback.started")
@dataclass(frozen=True)
class Started:
    """A play is running, or, with a stated start, readied. In a dry reply
    `play` is 0, `copy` and `first` are null and `controls` are as found.

    `controls` are read back after the role set them, `found` before it set
    anything. `level_mb` is the level at which a full-scale sample is played:
    the controls' volumes read back, in a dry reply the asked and configured
    ones. The play's own peak level is `level_mb + source.peak_mb`.
    `local_time` is the machine's local civil time, ISO 8601 with its UTC
    offset, at which quiet hours were judged. `asked_start` is the play's
    stated start, null for a play that stated none. `first` is the mark after
    the first write of an untimed play; null for a play with a stated start,
    whose reply comes before any frame is handed and whose first mark is in
    its end.
    """

    play: int
    source: Source
    copy: Location | None
    output: Output
    device: CardIdentity | SinkIdentity
    granted: Granted
    controls: tuple[Control, ...]
    found: tuple[Control, ...]
    level_mb: int
    cap_mb: int
    local_time: str
    asked_start: ClockReading | None
    first: Mark | None


@result("playback.clock_read")
@dataclass(frozen=True)
class ClockRead:
    """The role's clock reading, taken as the last thing before the reply."""

    at: ClockReading


@result("playback.stopped")
@dataclass(frozen=True)
class Stopped:
    """A play was cut when asked.

    `unplayed_frames` is the device's delay read just before the cut, the
    handed frames that were discarded, null when it could not be read.
    `underruns` counts the play's underruns. `last` is the mark after the last
    write, null in a dry reply and for a play that handed no frame; `end` is
    read once the stream is closed. `restored` says whether every control was
    put back as found. For a play with a stated start, `woke` is the reading
    when its wait ended at the start, before the first write, and `first` the
    mark after its first write; both are null for an untimed play, whose first
    mark is in its reply, and where the play never got that far.
    """

    play: int
    frames_handed: int
    unplayed_frames: int | None
    underruns: int
    last: Mark | None
    end: ClockReading
    restored: bool
    woke: ClockReading | None
    first: Mark | None


@result("playback.checked")
@dataclass(frozen=True)
class Checked:
    """The device, its controls as found, and the limits in force. Nothing
    set, no stream opened.
    """

    output: Output
    device: CardIdentity | SinkIdentity
    controls: tuple[CheckedControl, ...]
    cap_mb: int
    quiet_hours: tuple[QuietSpan, ...]
    local_time: str
    quiet_now: bool


@result("playback.released")
@dataclass(frozen=True)
class Released:
    """The play `release` cut, if one was running."""

    stopped: Stopped | None


@command("playback.play")
@dataclass(frozen=True)
class Play(Request[Started]):
    """Play `source`, a path under the role's source directory, with the level
    control at `volume_mb`. The play goes on without blocking the script.

    With `start` null, replies once the first frames are handed to the
    device. With `start` set, a reading on the role's own machine and
    monotonic clock, the role readies the play (source, copy, output,
    controls) and replies; the first frames are handed over when its
    monotonic clock reaches `start`. Only the monotonic value places the
    play; the wall value is carried for the record.
    """

    source: str
    volume_mb: int
    start: ClockReading | None = None


@command("playback.clock")
@dataclass(frozen=True)
class Clock(Request[ClockRead]):
    """Read the role's clock. Touches no output; answered while a play runs,
    and the same whether dry or not.
    """


@command("playback.stop")
@dataclass(frozen=True)
class Stop(Request[Stopped]):
    """Cut the running play, discarding what the device still holds."""


@event("playback.ended")
@dataclass(frozen=True)
class Ended:
    """The source was handed over whole and the device played its buffer out.
    `woke` and `first` are as in `Stopped`.
    """

    play: int
    frames_handed: int
    unplayed_frames: int
    underruns: int
    last: Mark
    end: ClockReading
    restored: bool
    woke: ClockReading | None
    first: Mark | None


@event("playback.underrun")
@dataclass(frozen=True)
class Underrun:
    """The device ran dry before frame `at_frame` of the copy was handed. The
    stream was recovered and the play goes on; sound was interrupted there for
    an unknown length.
    """

    play: int
    at_frame: int
    at: ClockReading
    detail: str


@event("playback.failed")
@dataclass(frozen=True)
class Failed:
    """The play ended and nobody asked: the stream died, a held control moved,
    quiet hours began, the recoveries ran out, or a play with a stated start
    woke too late for it. `last` is null when no frame was handed; `woke` and
    `first` are as in `Stopped`.
    """

    play: int
    failure: Failure
    frames_handed: int
    unplayed_frames: int | None
    underruns: int
    last: Mark | None
    end: ClockReading
    restored: bool
    woke: ClockReading | None
    first: Mark | None
