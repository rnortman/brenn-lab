"""The Role interface, and the table of role kinds."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from brenn_lab import capture
from brenn_lab.capture.device import CaptureDevice, read_device
from brenn_lab.capture.role import CaptureRole
from brenn_lab.config_table import Table
from brenn_lab.data import Command, Event, Reply
from brenn_lab.place import Place

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
    """A role kind: the programs a machine hosting one must name in its
    `programs` table, the reader of its `device` parameters, which returns
    them or None with every problem recorded in the table it is given, and the
    factory of its hosted roles.
    """

    name: str
    programs: frozenset[str]
    read_device: Callable[[Table], object | None]
    make: Factory


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


KINDS: dict[str, Kind] = {
    capture.KIND: Kind(
        name=capture.KIND,
        programs=frozenset({"pod_capture"}),
        read_device=read_device,
        make=_make_capture,
    ),
}
