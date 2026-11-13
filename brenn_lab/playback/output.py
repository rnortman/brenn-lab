"""The interface between the playback role and an output: the output, which
resolves a card and holds its mixer, and the stream it opens; and the table of
output kinds a configuration may name.

Every level is an integer number of millibels.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from brenn_lab.data import Failure
from brenn_lab.playback.messages import Channel, Library, VolumeRange
from brenn_lab.sound import CardIdentity, Granted


@dataclass(frozen=True)
class ControlName:
    """A simple mixer element of the card, by name and index."""

    element: str
    index: int


@dataclass(frozen=True)
class Attached:
    """The card an output resolved, and the library it plays with."""

    device: CardIdentity
    library: Library


@dataclass(frozen=True)
class Element:
    """A mixer control of the card with a playback volume or a playback
    switch: its playback channels by the output's names for them, its volume
    range (None for a control with no volume) and whether it has a switch.
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
        """The frames handed and not yet at the output, by the device's own
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
    """One role's output. `attach` resolves the card and opens its mixer;
    every other call but `detach` needs it attached.
    """

    @property
    def kind(self) -> str: ...

    def attach(self) -> Attached | Failure: ...

    def detach(self) -> None: ...

    def elements(self) -> tuple[Element, ...] | Failure:
        """Every control of the card with a playback volume or switch."""
        ...

    def read(self, controls: Sequence[ControlName]) -> tuple[tuple[Channel, ...], ...] | Failure:
        """Each of `controls`' values on each of its playback channels, in
        order, as the mixer has them now: changes made by other programs are
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
        """Open the card's stream demanding the rate, channels and format
        exactly, and the period and buffer at the nearest the device has.
        """
        ...


def _alsa(card: str) -> Output:
    # Imported here: the ALSA module imports this module's types.
    from brenn_lab.playback import alsa

    return alsa.AlsaOutput(card)


# TODO(playback-through-sound-server-sink): an output behind a sound server is
# another entry here.
OUTPUTS: dict[str, Callable[[str], Output]] = {"alsa": _alsa}
"""Each output kind a configuration may name, and how an output of that kind
is made from the configuration's `card`.
"""
