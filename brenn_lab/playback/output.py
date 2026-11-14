"""The interface between the playback role and an output: the output, which
resolves a device and holds its playback controls, and the stream it opens;
and the table of output kinds a configuration may name.

Every level is an integer number of millibels.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from brenn_lab.data import Failure
from brenn_lab.playback.messages import Channel, Library, SinkIdentity, VolumeRange
from brenn_lab.sound import CardIdentity, Granted


@dataclass(frozen=True)
class ControlName:
    """A playback control of the device, by name and index."""

    element: str
    index: int


@dataclass(frozen=True)
class Attached:
    """The device an output resolved, and the library it plays with."""

    device: CardIdentity | SinkIdentity
    library: Library


@dataclass(frozen=True)
class Element:
    """A control of the device with a playback volume or a playback switch:
    its playback channels by the output's names for them, its volume range
    (None for a control with no volume) and whether it has a switch.
    """

    control: ControlName
    channels: tuple[str, ...]
    range: VolumeRange | None
    switch: bool


@dataclass(frozen=True)
class WriteError:
    """A write the device did not take. `underrun` when it ran dry."""

    underrun: bool
    detail: str


class Stream(Protocol):
    """A stream open for playing, with the parameters it was granted."""

    @property
    def granted(self) -> Granted: ...

    def write(self, data: bytes) -> int | WriteError:
        """Hand `data`, whole frames, to the device, blocking while its buffer
        is full; the number of frames it took.
        """
        ...

    def recover(self, error: WriteError) -> Failure | None:
        """Return the stream to a state it can be written in after `error`."""
        ...

    def delay(self) -> int | None:
        """The frames handed and not yet at the output, by the output's own
        account; None if it cannot be read.
        """
        ...

    def drain(self) -> Failure | None:
        """Wait for the device to play its buffer out."""
        ...

    def drop(self) -> None:
        """Discard what the device has not played."""
        ...

    def close(self) -> None: ...


class Output(Protocol):
    """One role's output. `attach` resolves the device and reaches its
    controls; every other call but `detach` needs it attached.
    """

    @property
    def kind(self) -> str: ...

    def attach(self) -> Attached | Failure: ...

    def detach(self) -> None:
        """Let the device go. After it the output can be attached again,
        whatever `detach` raised.
        """
        ...

    def elements(self) -> tuple[Element, ...] | Failure:
        """Every control of the device with a playback volume or switch."""
        ...

    def read(self, controls: Sequence[ControlName]) -> tuple[tuple[Channel, ...], ...] | Failure:
        """Each of `controls`' values on each of its playback channels, in
        order, as the device has them now: changes made by other programs are
        taken in once, before any is read.
        """
        ...

    def set(self, control: ControlName, channels: tuple[Channel, ...]) -> Failure | None:
        """Set each named channel's volume, rounded down to the control's
        step, and its switch, where the channel gives them.
        """
        ...

    def open(
        self,
        rate_hz: int,
        channels: int,
        sample_format: str,
        period_frames: int,
        buffer_frames: int,
    ) -> Stream | Failure:
        """Open the device's stream demanding the rate, channels and format
        exactly, and the period and buffer at the nearest the device has.
        """
        ...


type Factory = Callable[[str, Mapping[str, str]], Output]
"""Makes an output from the configuration's selector of its device and the
path of each program the role's machine names.
"""


def _alsa(card: str, programs: Mapping[str, str]) -> Output:
    # Imported here: the ALSA module imports this module's types.
    from brenn_lab.playback import alsa

    return alsa.AlsaOutput(card)


def _pulse(sink: str, programs: Mapping[str, str]) -> Output:
    # Imported here: the binding imports this module's types.
    from brenn_lab.playback import pulse

    return pulse.PulseOutput(sink, programs["pulse_client"])


# TODO(quality-output-kind-facts-split-across-modules): a kind's program,
# clock refusal and underrun behaviour are answered in `roles.py` and below,
# apart from this table.
OUTPUTS: dict[str, Factory] = {"alsa": _alsa, "pulse": _pulse}
"""Each output kind a configuration may name, and how an output of that kind
is made. Importing this module loads no library.
"""

PLAYS_ON_AFTER_UNDERRUN: frozenset[str] = frozenset({"alsa"})
"""The output kinds on which an underrun is a gap and every frame handed after
it is played. On any other kind, frames handed after an underrun may never
sound: a sound server reads on past what it was given and skips what arrives
behind its read position.
"""
