"""The sink output on the host's sound server, read only.

Through `PlaybackRole.handle(Check)` and `PulseOutput`, against the server at
`$XDG_RUNTIME_DIR/pulse/native`: the client asks for sinks and for one sink by
name, and nothing else. No stream is made, no volume or mute is set and no
frame is written; each output here fails the test if the role tries.
"""

import os
from collections.abc import Sequence
from pathlib import Path

from brenn_lab.data import Check, Command, Event, Failure
from brenn_lab.playback.device import SINK_LEVEL, PlaybackDevice
from brenn_lab.playback.messages import Channel, Checked, SinkIdentity
from brenn_lab.playback.output import ControlName
from brenn_lab.playback.pulse import MAX_MB, MIN_MB, PulseOutput, PulseStream
from brenn_lab.playback.role import PlaybackRole
from brenn_lab.testing.sink_client import client_program, mapped_sound_libraries

NOTHING = "no sink is called this 7f3a9c"


class Guarded(PulseOutput):
    """A sink output that fails a test that sets a control or opens a stream."""

    def __init__(self, selector: str) -> None:
        super().__init__(selector, client_program())

    def set(self, control: ControlName, channels: tuple[Channel, ...]) -> Failure | None:
        raise AssertionError("check set a control")

    def open(
        self,
        rate_hz: int,
        channels: int,
        sample_format: str,
        period_frames: int,
        buffer_frames: int,
    ) -> PulseStream | Failure:
        raise AssertionError("check opened a stream")


def check(selector: str) -> Checked | Failure:
    device = PlaybackDevice(
        output="pulse",
        selector=selector,
        source_dir="/",
        cap_mb=-2400,
        quiet_hours=(),
        level=SINK_LEVEL,
        held=(),
    )
    events: list[Event] = []
    role = PlaybackRole("talker", "bench", device, Guarded(selector), events.append, None)
    outcome = role.handle(Command("talker", Check())).outcome
    assert events == []
    assert isinstance(outcome, Checked | Failure), outcome
    return outcome


def listed() -> list[str]:
    """The names of the server's sinks, from the refusal of a selector that
    matches none.
    """
    refused = check(NOTHING)
    assert isinstance(refused, Failure), refused
    assert refused.reason == "no_sink", refused
    assert refused.data["selector"] == NOTHING
    sinks = refused.data["sinks"]
    assert isinstance(sinks, list)
    names = []
    for s in sinks:
        assert isinstance(s, dict) and isinstance(s["name"], str), s
        names.append(s["name"])
    return names


def selectable(names: Sequence[str]) -> list[str]:
    """The sinks a selector of their whole name picks out alone."""
    return [n for n in names if not any(n.lower() in m.lower() for m in names if m != n)]


def test_the_server_is_reached_and_an_unknown_selector_names_the_sinks() -> None:
    assert os.environ.get("XDG_RUNTIME_DIR"), "the lane passes XDG_RUNTIME_DIR"
    names = listed()
    print("sinks:", names)
    assert names, "the server has no sink"


def test_check_on_each_sink_answers_its_identity_and_sets_nothing() -> None:
    checked = 0
    for name in selectable(listed()):
        first = check(name)
        if isinstance(first, Failure):
            # A flat or non-decibel sink is refused before any stream exists.
            assert first.reason == "sink_volume", first
            print(f"{name}: refused, {first.detail}")
            continue
        checked += 1
        d = first.device
        assert isinstance(d, SinkIdentity), d
        print(
            f"{d.sink_name}: base_volume_mb {d.base_volume_mb}, "
            f"server_sample_format {d.server_sample_format}, "
            f"rate_hz {d.rate_hz}, channels {d.channels}, "
            f"hardware_volume {d.hardware_volume}"
        )
        assert d.sink_name == name and d.selector == name
        assert d.rate_hz > 0 and d.channels > 0
        assert MIN_MB <= d.base_volume_mb <= MAX_MB
        assert first.output.kind == "pulse"
        assert first.output.library.name == "pulseaudio-rs"
        [control] = first.controls
        assert (control.element, control.index, control.part) == ("sink", 0, "level")
        assert control.range is not None
        assert (control.range.min_mb, control.range.max_mb) == (MIN_MB, MAX_MB)
        assert len(control.channels) == d.channels
        for channel in control.channels:
            assert channel.volume_mb is not None and MIN_MB <= channel.volume_mb <= MAX_MB
            assert isinstance(channel.switch, bool)
        assert check(name) == first
    assert checked, "no sink could be checked"


def test_two_reads_agree_and_the_client_maps_no_host_sound_library() -> None:
    [name, *_] = [n for n in selectable(listed()) if isinstance(check(n), Checked)]
    output = Guarded(name)
    attached = output.attach()
    assert not isinstance(attached, Failure), attached
    try:
        first = output.read((SINK_LEVEL,))
        assert not isinstance(first, Failure), first
        assert output.read((SINK_LEVEL,)) == first
        pid = output.client_pid
        assert pid is not None
        maps = Path(f"/proc/{pid}/maps").read_text().splitlines()
    finally:
        output.detach()
    assert maps
    assert mapped_sound_libraries(maps) == []
