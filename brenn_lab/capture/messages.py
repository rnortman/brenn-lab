"""The capture kind's commands, results and events.

These are the harness's own types. What the recorder prints is turned into
them in one place, the recorder adapter; nothing here names a recorder's flag,
event or key but two members, `device.usb` and `device_state`. They are the
recorder's own statement about the device, carried whole under the recorder's
names; their meaning is brenn-pod's (`docs/reference/frame-log.md`,
`device_state`).
"""

from dataclasses import dataclass

from brenn_lab.data import (
    ClockReading,
    Failure,
    JsonObject,
    Location,
    Request,
    command,
    event,
    result,
)
from brenn_lab.sound import CardIdentity, Granted

DEVICE_STATE_OUTCOMES = frozenset({"not_attempted", "unavailable", "read"})
"""The outcomes of a device's state; the only words of it brenn-lab reads."""


def device_state_problem(state: JsonObject) -> str | None:
    """What makes `state` other than a device's state as brenn-lab reads one:
    an `outcome` of `DEVICE_STATE_OUTCOMES`, with string `why` and `detail`
    when the state was not read. None when there is nothing.
    """
    outcome = state.get("outcome")
    if not isinstance(outcome, str) or outcome not in DEVICE_STATE_OUTCOMES:
        return f"device_state.outcome {outcome!r} is not one of {sorted(DEVICE_STATE_OUTCOMES)}"
    if outcome != "read":
        for key in ("why", "detail"):
            if not isinstance(state.get(key), str):
                return f"device_state.{key} of a state {outcome} is not a string"
    return None


@dataclass(frozen=True)
class Build:
    """A program's build as it reports it. `dirty` is null when the build was
    declared rather than read from a checkout.
    """

    commit: str
    dirty: bool | None
    source: str


@dataclass(frozen=True)
class Recorder:
    """The program that recorded, the repository it is built from, and its build."""

    program: str
    repository: str
    build: Build


@dataclass(frozen=True)
class Device(CardIdentity):
    """The card as ALSA names it, and `usb`, the recorder's statement of the
    USB device behind it, an object or null, carried as given.
    """

    usb: JsonObject | None


@result("capture.started")
@dataclass(frozen=True)
class Started:
    """A capture is running. `location` is null in a dry reply; `capture` is 0
    there, and counts a role's captures in a run from 1 otherwise.
    `device_state` is the recorder's statement of the device's own state.
    """

    capture: int
    capture_id: str
    location: Location | None
    recorder: Recorder
    device: Device
    granted: Granted
    layout: tuple[str, ...]
    start: ClockReading
    device_state: JsonObject


@result("capture.stopped")
@dataclass(frozen=True)
class Stopped:
    """A capture stopped cleanly, with the recorder's own frame and gap counts."""

    capture: int
    frames: int
    gaps_reported: int
    end: ClockReading


@result("capture.checked")
@dataclass(frozen=True)
class Checked:
    """What a probe of the device learned."""

    recorder: Recorder
    device: Device
    granted: Granted
    layout: tuple[str, ...]
    device_state: JsonObject


@result("capture.released")
@dataclass(frozen=True)
class Released:
    """The capture `release` stopped, if one was running."""

    stopped: Stopped | None


@command("capture.start")
@dataclass(frozen=True)
class Start(Request[Started]):
    """Start a capture with the configuration's device parameters."""


@command("capture.stop")
@dataclass(frozen=True)
class Stop(Request[Stopped]):
    """Stop the running capture."""


@event("capture.gap")
@dataclass(frozen=True)
class Gap:
    """Audio is missing at `at_frame`. `frames` is null for a loss of unknown length."""

    capture: int
    cause: str
    at_frame: int
    frames: int | None
    at: ClockReading
    detail: str


@event("capture.late")
@dataclass(frozen=True)
class Late:
    """The reader fell a period or more behind at `at_frame`; nothing was lost."""

    capture: int
    at_frame: int
    backlog_frames: int
    at: ClockReading


@event("capture.failed")
@dataclass(frozen=True)
class Failed:
    """The capture is over and did not stop when asked. `stopped` is the
    recorder's report when it stopped cleanly with nobody asking
    (`stopped_unasked`), null otherwise.
    """

    capture: int
    failure: Failure
    stopped: Stopped | None
