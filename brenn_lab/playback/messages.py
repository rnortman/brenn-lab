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
    """A library the role loaded, with the version it reports."""

    name: str
    version: str


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
    """A play is running. In a dry reply `play` is 0, `copy` and `first` are
    null and `controls` are as found.

    `controls` are read back after the role set them, `found` before it set
    anything. `level_mb` is the level at which a full-scale sample is played:
    the controls' volumes read back, in a dry reply the asked and configured
    ones. The play's own peak level is `level_mb + source.peak_mb`.
    `local_time` is the machine's local civil time, ISO 8601 with its UTC
    offset, at which quiet hours were judged.
    """

    play: int
    source: Source
    copy: Location | None
    output: Output
    device: CardIdentity
    granted: Granted
    controls: tuple[Control, ...]
    found: tuple[Control, ...]
    level_mb: int
    cap_mb: int
    local_time: str
    first: Mark | None


@result("playback.stopped")
@dataclass(frozen=True)
class Stopped:
    """A play was cut when asked.

    `unplayed_frames` is the device's delay read just before the cut, the
    handed frames that were discarded, null when it could not be read.
    `underruns` counts the play's underruns. `last` is the mark after the last
    write, null in a dry reply; `end` is read once the stream is closed.
    `restored` says whether every control was put back as found.
    """

    play: int
    frames_handed: int
    unplayed_frames: int | None
    underruns: int
    last: Mark | None
    end: ClockReading
    restored: bool


@result("playback.checked")
@dataclass(frozen=True)
class Checked:
    """The card, its controls as found, and the limits in force. Nothing set, no stream opened."""

    output: Output
    device: CardIdentity
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
    control at `volume_mb`. Replies once the first frames are handed to the
    device; the play goes on without blocking the script.
    """

    source: str
    volume_mb: int


@command("playback.stop")
@dataclass(frozen=True)
class Stop(Request[Stopped]):
    """Cut the running play, discarding what the device still holds."""


@event("playback.ended")
@dataclass(frozen=True)
class Ended:
    """The source was handed over whole and the device played its buffer out."""

    play: int
    frames_handed: int
    unplayed_frames: int
    underruns: int
    last: Mark
    end: ClockReading
    restored: bool


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
    quiet hours began, or the recoveries ran out.
    """

    play: int
    failure: Failure
    frames_handed: int
    unplayed_frames: int | None
    underruns: int
    last: Mark
    end: ClockReading
    restored: bool
