"""The Role interface, and the table of role kinds."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from brenn_lab import capture, playback
from brenn_lab.account import Account, Pointer
from brenn_lab.capture import account as capture_account
from brenn_lab.capture.device import CaptureDevice, read_device
from brenn_lab.capture.role import CaptureRole
from brenn_lab.config_table import Table
from brenn_lab.data import Command, Event, JsonObject, Reply
from brenn_lab.place import Place
from brenn_lab.playback import account as playback_account
from brenn_lab.playback import device as playback_device
from brenn_lab.playback.device import PlaybackDevice
from brenn_lab.playback.output import OUTPUTS
from brenn_lab.playback.role import PlaybackRole

type EventSink = Callable[[Event], None]


class Role(Protocol):
    """A device role. It answers each command with exactly one reply, in order,
    and reports events through the sink it was given at construction.
    """

    def handle(self, command: Command) -> Reply: ...


type Factory = Callable[[str, str, Mapping[str, str], object, EventSink, Place | None], Role]
"""Makes a role hosted on the runner's machine from its name, its machine's
name, the programs that machine names, its device parameters as the kind's
`read_device` returned them, its event sink, and where it writes in a run
(None in a dry run).
"""


@dataclass(frozen=True)
class Kind:
    """A role kind: the programs a machine hosting one may name in its
    `programs` table, the reader of its `device` parameters, which returns
    them or None with every problem recorded in the table it is given, and the
    factory of its hosted roles. `needs` gives the programs a role with the
    parameters read must find on its machine; `clock_refusal` why a role with
    them can state no `sample_clock`, or None.

    What a run records of the kind: `section` names the kind's directory
    under the run, the key of a role's entries in the manifest's role section
    and the key of its records in the results; `totals_key` and `totals` the
    results' totals over every record of the kind; `account` makes the account
    of one role of the script, hosted or not, given the run's pointer.
    `left_running` reads the role's answer to `release`: what it says was
    still running, as prose (`"capture 2"`), or None.
    """

    name: str
    programs: frozenset[str]
    read_device: Callable[[Table], object | None]
    needs: Callable[[object], frozenset[str]]
    clock_refusal: Callable[[object], str | None]
    make: Factory
    section: str
    totals_key: str
    totals: Callable[[list[JsonObject]], JsonObject]
    account: Callable[[str, Pointer], Account]
    left_running: Callable[[object], str | None]


def _make_capture(
    name: str,
    machine: str,
    programs: Mapping[str, str],
    parameters: object,
    sink: EventSink,
    place: Place | None,
) -> Role:
    assert isinstance(parameters, CaptureDevice)
    return CaptureRole(name, machine, programs["pod_capture"], parameters, sink, place)


def _make_playback(
    name: str,
    machine: str,
    programs: Mapping[str, str],
    parameters: object,
    sink: EventSink,
    place: Place | None,
) -> Role:
    assert isinstance(parameters, PlaybackDevice)
    output = OUTPUTS[parameters.output](parameters.selector, programs)
    return PlaybackRole(name, machine, parameters, output, sink, place)


def _playback_needs(parameters: object) -> frozenset[str]:
    assert isinstance(parameters, PlaybackDevice)
    return frozenset({"pulse_client"}) if parameters.output == "pulse" else frozenset()


def _playback_clock_refusal(parameters: object) -> str | None:
    assert isinstance(parameters, PlaybackDevice)
    if parameters.output == "pulse":
        return (
            "a role on a sound server's sink samples on no device's clock: the server "
            "resamples and remixes its frames on the way to the device"
        )
    return None


KINDS: dict[str, Kind] = {
    capture.KIND: Kind(
        name=capture.KIND,
        programs=frozenset({"pod_capture"}),
        read_device=read_device,
        needs=lambda parameters: frozenset({"pod_capture"}),
        clock_refusal=lambda parameters: None,
        make=_make_capture,
        section="captures",
        totals_key="recorded",
        totals=capture_account.totals,
        account=capture_account.CaptureAccount,
        left_running=capture_account.left_running,
    ),
    playback.KIND: Kind(
        name=playback.KIND,
        programs=frozenset({"pulse_client"}),
        read_device=playback_device.read_device,
        needs=_playback_needs,
        clock_refusal=_playback_clock_refusal,
        make=_make_playback,
        section="sources",
        totals_key="played",
        totals=playback_account.totals,
        account=playback_account.PlaybackAccount,
        left_running=playback_account.left_running,
    ),
}
