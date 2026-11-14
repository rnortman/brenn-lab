"""The sink binding's own arithmetic and choices, with no client and no server:
selection, the volume scale both ways, the found value, channel naming, the
sinks refused, the stream's checks and what each refusal of the client
becomes; and, against a stub client program, a client that answers what it
cannot mean or nothing.
"""

import json
import math
import os
import shutil
import sys
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from brenn_lab.data import Failure, JsonObject
from brenn_lab.playback import pulse
from brenn_lab.playback.messages import Channel
from brenn_lab.playback.output import OUTPUTS, PLAYS_ON_AFTER_UNDERRUN, ControlName
from brenn_lab.playback.pulse import (
    MAX_MB,
    MIN_MB,
    NORM,
    VOLUME_MAX,
    PulseOutput,
    Sink,
    channel_names,
    raw_volume,
    reading_mb,
    select,
    volume_refusal,
)

SINK_LEVEL = ControlName("sink", 0)


def sink(name: str, description: str | None = None, **changes: object) -> Sink:
    fields: dict[str, object] = {
        "name": name,
        "description": description,
        "rate_hz": 48000,
        "channels": 2,
        "format": "s32le",
        "positions": ("front-left", "front-right"),
        "volumes": (NORM, NORM),
        "base_volume": NORM,
        "muted": False,
        "decibel_volume": True,
        "flat_volume": False,
        "hardware_volume": False,
    }
    fields.update(changes)
    return Sink(**fields)  # type: ignore[arg-type]


SINKS = [
    sink("alsa_output.usb-Desk-00.analog-stereo", "Desk Speaker Analog Stereo"),
    sink("alsa_output.pci-0000_00_1f.3.analog-stereo", "Built-in Audio Analog Stereo"),
    sink("bluez_output.00_00_00_00_00_00.1", None),
]


def test_a_selector_matches_a_name_or_a_description_case_insensitively() -> None:
    assert select(SINKS, "desk speaker") == SINKS[0]
    assert select(SINKS, "PCI-0000") == SINKS[1]
    assert select(SINKS, "bluez") == SINKS[2]


def test_a_selector_that_matches_nothing_lists_the_sinks() -> None:
    failure = select(SINKS, "attic")
    assert isinstance(failure, Failure)
    assert failure.reason == "no_sink"
    assert failure.data["selector"] == "attic"
    assert failure.data["sinks"] == [{"name": s.name, "description": s.description} for s in SINKS]


def test_a_selector_that_matches_several_names_them() -> None:
    failure = select(SINKS, "analog-stereo")
    assert isinstance(failure, Failure)
    assert failure.reason == "ambiguous_sink"
    assert failure.data["matched"] == [SINKS[0].name, SINKS[1].name]


def test_no_sinks_at_all() -> None:
    failure = select([], "desk")
    assert isinstance(failure, Failure)
    assert failure.reason == "no_sink"
    assert failure.data["sinks"] == []


def test_a_flat_or_non_decibel_sink_is_refused() -> None:
    assert volume_refusal(SINKS[0]) is None
    flat = volume_refusal(sink("s", flat_volume=True))
    assert isinstance(flat, Failure) and flat.reason == "sink_volume"
    linear = volume_refusal(sink("s", decibel_volume=False))
    assert isinstance(linear, Failure) and linear.reason == "sink_volume"


def exact_db(raw: int) -> float:
    return 6000 * math.log10(raw / NORM)


def test_full_volume_reads_0_mb() -> None:
    assert reading_mb(NORM) == 0
    assert raw_volume(0) == NORM


def test_a_reading_never_understates() -> None:
    for raw in [1, 2, 3, 100, 6553, 32768, 65535, 65537, 100000, 1 << 20, VOLUME_MAX]:
        mb = reading_mb(raw)
        assert mb >= exact_db(raw), raw
        assert mb - 1 < exact_db(raw), raw


def test_zero_reads_min_mb_one_below_the_reading_of_one() -> None:
    assert reading_mb(0) == MIN_MB
    assert MIN_MB == reading_mb(1) - 1
    assert raw_volume(MIN_MB) == 0
    assert raw_volume(MIN_MB - 100) == 0


def test_max_mb_is_the_reading_of_the_largest_volume() -> None:
    assert MAX_MB == reading_mb(VOLUME_MAX)
    assert raw_volume(MAX_MB) == VOLUME_MAX
    assert raw_volume(MAX_MB + 100) == VOLUME_MAX


@pytest.mark.parametrize("mb", [MIN_MB + 1, -6000, -1801, -1800, -1, 0, 1, 600, MAX_MB - 1])
def test_a_set_is_the_largest_volume_at_or_under_the_level(mb: int) -> None:
    raw = raw_volume(mb)
    assert reading_mb(raw) <= mb
    assert raw == VOLUME_MAX or reading_mb(raw + 1) > mb


def test_many_raw_volumes_share_one_reading_near_0_db() -> None:
    sharing = [r for r in range(NORM - 40, NORM + 1) if reading_mb(r) == 0]
    assert len(sharing) > 10
    assert raw_volume(0) == max(sharing)


def test_channel_names_follow_positions_and_number_repeats() -> None:
    assert channel_names(["front-left", "front-right"]) == ("front-left", "front-right")
    assert channel_names(["mono"]) == ("mono",)
    assert channel_names(["aux0", "aux0", "front-left", "aux0"]) == (
        "aux0",
        "aux0#2",
        "front-left",
        "aux0#3",
    )


def test_the_base_volume_reads_as_a_sink_volume() -> None:
    assert reading_mb(NORM) == 0
    base = raw_volume(-1200)
    assert reading_mb(base) == -1200


def test_the_pulse_kind_is_made_without_running_the_client() -> None:
    output = OUTPUTS["pulse"]("Desk", {"pulse_client": "/nonexistent/pulse-client"})
    assert isinstance(output, PulseOutput)
    assert output.kind == "pulse"
    assert output.client_pid is None


def test_a_sink_is_not_a_kind_that_plays_on_after_an_underrun() -> None:
    assert PLAYS_ON_AFTER_UNDERRUN <= OUTPUTS.keys()
    assert "pulse" not in PLAYS_ON_AFTER_UNDERRUN


class FakeClient:
    """Answers the binding's requests from a sink whose volumes tests move,
    recording every request.
    """

    def __init__(self, volumes: tuple[int, ...]) -> None:
        self.sink = sink("desk", "Desk", volumes=volumes)
        self.requests: list[JsonObject] = []
        self.pid = 1
        self.stream: JsonObject = {"sink": "desk", "volumes": [NORM, NORM], "muted": False}
        self.grant: JsonObject = {
            "rate_hz": 48000,
            "channels": 2,
            "sample_format": "s16_le",
            "period_frames": 480,
            "buffer_frames": 9600,
        }
        self.refusals: dict[str, tuple[str, str]] = {}
        """A refusal, error and detail, for every request of an op."""

    def request(self, request: JsonObject, data: bytes = b"", timeout_s: float = 0) -> JsonObject:
        self.requests.append(request)
        op = request["op"]
        assert isinstance(op, str)
        if op in self.refusals:
            raise pulse._Refused(*self.refusals[op])
        if op == "stream":
            return {"ok": True, **self.stream}
        if op == "open":
            return {"ok": True, "sink": "desk", **self.grant}
        if op == "sink":
            s = self.sink
            return {
                "ok": True,
                "sink": {
                    "name": s.name,
                    "description": s.description,
                    "rate_hz": s.rate_hz,
                    "channels": s.channels,
                    "format": s.format,
                    "positions": list(s.positions),
                    "volumes": list(s.volumes),
                    "base_volume": s.base_volume,
                    "muted": s.muted,
                    "decibel_volume": True,
                    "flat_volume": False,
                    "hardware_volume": False,
                },
            }
        if request["op"] == "set_sink":
            volumes = request.get("volumes", list(self.sink.volumes))
            muted = request.get("muted", self.sink.muted)
            assert isinstance(volumes, list) and isinstance(muted, bool)
            self.sink = sink("desk", "Desk", volumes=tuple(volumes), muted=muted)
            return {"ok": True}
        raise AssertionError(request)

    def stderr(self) -> str:
        return ""

    def close(self) -> None:
        pass


def attached(volumes: tuple[int, ...]) -> tuple[PulseOutput, FakeClient]:
    output = PulseOutput("desk", "/nonexistent/pulse-client")
    client = FakeClient(volumes)
    output._client = client  # type: ignore[assignment]
    output._sink_name = "desk"
    return output, client


def read_sink(output: PulseOutput) -> tuple[Channel, ...]:
    read = output.read([SINK_LEVEL])
    assert not isinstance(read, Failure), read
    [channels] = read
    return channels


def levels(volume_mb: int) -> tuple[Channel, ...]:
    return (Channel("front-left", volume_mb, True), Channel("front-right", volume_mb, True))


def set_volumes(client: FakeClient) -> list[object]:
    return [r["volumes"] for r in client.requests if r["op"] == "set_sink"]


# Two raw volumes near 0 dB, neither the largest of its reading.
FOUND = (NORM - 30, NORM - 25)


def test_the_found_reading_restores_the_found_raw_volume() -> None:
    assert all(raw_volume(reading_mb(v)) != v for v in FOUND)
    output, client = attached(FOUND)
    found = read_sink(output)
    assert [c.volume_mb for c in found] == [reading_mb(v) for v in FOUND]
    assert output.set(SINK_LEVEL, levels(-1800)) is None
    # Other reads intervene: the play's own read-back and its watch.
    output.read([SINK_LEVEL])
    output.read([SINK_LEVEL])
    assert output.set(SINK_LEVEL, found) is None
    assert set_volumes(client) == [[raw_volume(-1800)] * 2, list(FOUND)]
    assert client.sink.volumes == FOUND


def test_a_level_equal_to_the_found_reading_leaves_the_raw_volume() -> None:
    output, client = attached((NORM - 30, NORM - 30))
    output.read([SINK_LEVEL])
    assert output.set(SINK_LEVEL, levels(reading_mb(NORM - 30))) is None
    assert client.sink.volumes == (NORM - 30, NORM - 30)


def test_the_found_value_is_the_first_read_not_the_last() -> None:
    output, client = attached(FOUND)
    found = read_sink(output)
    client.sink = sink("desk", "Desk", volumes=(NORM - 2, NORM - 2))
    output.read([SINK_LEVEL])
    assert output.set(SINK_LEVEL, found) is None
    assert client.sink.volumes == FOUND


def test_the_found_value_is_forgotten_at_detach() -> None:
    output, client = attached(FOUND)
    found = read_sink(output)
    output.detach()
    output._client = client  # type: ignore[assignment]
    client.sink = sink("desk", "Desk", volumes=(NORM, NORM))
    output.read([SINK_LEVEL])
    assert output.set(SINK_LEVEL, found) is None
    assert client.sink.volumes == tuple(raw_volume(c.volume_mb or 0) for c in found)
    assert client.sink.volumes != FOUND


def test_a_muted_sink_reads_its_switch_off_and_a_set_unmutes_it() -> None:
    output, client = attached((NORM, NORM))
    client.sink = sink("desk", "Desk", muted=True)
    read = read_sink(output)
    assert {c.switch for c in read} == {False}
    assert output.set(SINK_LEVEL, levels(-600)) is None
    assert client.requests[-1]["muted"] is False


@pytest.mark.parametrize(
    "call",
    [
        lambda o: o.read([ControlName("PCM", 0)]),
        lambda o: o.set(ControlName("PCM", 0), levels(0)),
    ],
)
def test_a_sink_has_one_control(call: Callable[[PulseOutput], object]) -> None:
    output, _ = attached((NORM, NORM))
    failure = call(output)
    assert isinstance(failure, Failure) and failure.reason == "no_control"


def test_a_set_must_name_every_channel_of_the_sink() -> None:
    output, client = attached((NORM, NORM))
    failure = output.set(SINK_LEVEL, levels(0)[:1])
    assert isinstance(failure, Failure) and failure.reason == "mixer"
    assert set_volumes(client) == []


def test_a_set_with_no_volume_changes_only_the_switch() -> None:
    output, client = attached(FOUND)
    off = (Channel("front-left", None, False), Channel("front-right", None, False))
    assert output.set(SINK_LEVEL, off) is None
    assert client.requests[-1] == {"op": "set_sink", "name": "desk", "muted": True}
    assert client.sink.volumes == FOUND and client.sink.muted


def test_a_channel_with_no_volume_keeps_its_raw_volume() -> None:
    output, client = attached(FOUND)
    mixed = (Channel("front-left", -1800, None), Channel("front-right", None, None))
    assert output.set(SINK_LEVEL, mixed) is None
    assert client.requests[-1] == {
        "op": "set_sink",
        "name": "desk",
        "volumes": [raw_volume(-1800), FOUND[1]],
    }


def test_a_set_takes_one_switch_for_every_channel() -> None:
    output, client = attached((NORM, NORM))
    split = (Channel("front-left", 0, True), Channel("front-right", 0, False))
    failure = output.set(SINK_LEVEL, split)
    assert isinstance(failure, Failure) and failure.reason == "mixer"
    assert set_volumes(client) == []


def test_the_element_is_the_sink_over_the_whole_range() -> None:
    output, _ = attached((NORM, NORM))
    elements = output.elements()
    assert not isinstance(elements, Failure)
    [element] = elements
    assert element.control == SINK_LEVEL
    assert element.channels == ("front-left", "front-right")
    assert element.range == pulse.RANGE
    assert element.switch


def test_no_server_without_a_runtime_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    failure = PulseOutput("desk", "/nonexistent/pulse-client").attach()
    assert isinstance(failure, Failure) and failure.reason == "no_server"


def test_a_client_that_cannot_be_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/nonexistent")
    failure = PulseOutput("desk", "/nonexistent/pulse-client").attach()
    assert isinstance(failure, Failure) and failure.reason == "client_missing"


def streaming(volumes: tuple[int, ...] = (NORM, NORM)) -> tuple[PulseOutput, FakeClient]:
    output, client = attached(volumes)
    stream = output.open(48000, 2, "s16_le", 480, 9600)
    assert not isinstance(stream, Failure), stream
    return output, client


@pytest.mark.parametrize(
    ("stream", "data"),
    [
        (
            {"sink": "other", "volumes": [NORM, NORM], "muted": False},
            {"sink": "other", "volumes": [NORM, NORM], "muted": False},
        ),
        (
            {"sink": "desk", "volumes": [NORM, NORM], "muted": True},
            {"sink": "desk", "volumes": [NORM, NORM], "muted": True},
        ),
        (
            {"sink": "desk", "volumes": [NORM, NORM - 1], "muted": False},
            {"sink": "desk", "volumes": [NORM, NORM - 1], "muted": False},
        ),
    ],
)
def test_a_stream_off_the_sink_muted_or_not_at_0_db_is_stream_moved(
    stream: JsonObject, data: JsonObject
) -> None:
    output, client = streaming()
    client.stream = stream
    failure = output.read([SINK_LEVEL])
    assert isinstance(failure, Failure) and failure.reason == "stream_moved", failure
    assert failure.data == data


def test_a_stream_the_client_refuses_to_read_is_stream_moved() -> None:
    output, client = streaming()
    client.refusals["stream"] = ("stream", "the server moved the stream to other")
    failure = output.read([SINK_LEVEL])
    assert failure == Failure(
        "stream_moved", "the server moved the stream to other", {"sink": "desk"}
    )


@pytest.mark.parametrize(
    "call",
    [
        lambda o: o.read([SINK_LEVEL]),
        lambda o: o.set(SINK_LEVEL, levels(0)),
        lambda o: o.open(48000, 2, "s16_le", 480, 9600),
    ],
)
def test_a_sink_the_client_finds_no_more_is_no_sink(call: Callable[[PulseOutput], object]) -> None:
    output, client = attached((NORM, NORM))
    read_sink(output)
    client.refusals["sink"] = ("no_sink", "no sink is named desk")
    client.refusals["open"] = ("no_sink", "no sink is named desk")
    failure = call(output)
    assert failure == Failure("no_sink", "no sink is named desk", {"sink": "desk"})


def test_a_set_the_server_refuses_is_mixer() -> None:
    output, client = attached((NORM, NORM))
    client.refusals["set_sink"] = ("server", "the server refused: access denied")
    failure = output.set(SINK_LEVEL, levels(-600))
    assert isinstance(failure, Failure) and failure.reason == "mixer"
    assert "access denied" in failure.detail


def test_a_sink_back_with_other_channels_is_mixer_and_sets_nothing() -> None:
    output, client = attached((NORM, NORM))
    found = read_sink(output)
    client.sink = sink("desk", "Desk", channels=1, positions=("mono",), volumes=(NORM,))
    failure = output.read([SINK_LEVEL])
    assert isinstance(failure, Failure) and failure.reason == "mixer", failure
    assert "['front-left', 'front-right']" in failure.detail and "['mono']" in failure.detail
    assert isinstance(output.set(SINK_LEVEL, found), Failure)
    assert set_volumes(client) == []


@pytest.mark.parametrize(
    ("error", "reason", "data"),
    [
        ("params", "params", {"rate_hz": 48000, "channels": 2, "sample_format": "s16_le"}),
        ("stream", "open", {"rate_hz": 48000, "channels": 2, "sample_format": "s16_le"}),
        ("server", "open", {"rate_hz": 48000, "channels": 2, "sample_format": "s16_le"}),
    ],
)
def test_an_open_the_client_refuses(error: str, reason: str, data: JsonObject) -> None:
    output, client = attached((NORM, NORM))
    client.refusals["open"] = (error, "refused")
    failure = output.open(48000, 2, "s16_le", 480, 9600)
    assert isinstance(failure, Failure) and failure.reason == reason
    assert failure.data == data
    client.refusals.clear()
    assert not isinstance(output.open(48000, 2, "s16_le", 480, 9600), Failure)


def test_a_grant_missing_a_key_is_client() -> None:
    output, client = attached((NORM, NORM))
    del client.grant["buffer_frames"]
    failure = output.open(48000, 2, "s16_le", 480, 9600)
    assert isinstance(failure, Failure) and failure.reason == "client"
    assert "buffer_frames" in failure.detail


STUB = """\
import json, os, sys, time
lines = json.loads(os.environ["STUB_LINES"])
for i, line in enumerate(lines):
    if i:
        sys.stdin.readline()
    sys.stdout.write(line + "\\n")
    sys.stdout.flush()
while sys.stdin.readline():
    pass
"""
"""A client that answers its start and each request after with the next of
`STUB_LINES`, then nothing until its stdin closes.
"""

FIRST = json.dumps(
    {
        "ok": True,
        "library": {"name": "stub", "version": "0"},
        "server": {"name": "s", "version": "0"},
    }
)
SINK_LINE = {
    "name": "desk",
    "description": "Desk",
    "rate_hz": 48000,
    "channels": 2,
    "format": "s32le",
    "positions": ["front-left", "front-right"],
    "volumes": [NORM, NORM],
    "base_volume": NORM,
    "muted": False,
    "decibel_volume": True,
    "flat_volume": False,
    "hardware_volume": False,
}


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, list[pulse._Client]]]:
    """A stub client program, and every `_Client` the binding makes."""
    directory = Path(tempfile.mkdtemp(prefix="pulse-stub-", dir="/tmp"))
    try:
        interpreter = directory / "py"
        interpreter.symlink_to(os.path.realpath(sys.executable))
        program = directory / "client"
        program.write_text(f"#!{interpreter} -I\n{STUB}")
        program.chmod(0o755)
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(directory))
        made: list[pulse._Client] = []

        class Kept(pulse._Client):
            def __init__(self, program: str, socket: Path) -> None:
                super().__init__(program, socket)
                made.append(self)

        monkeypatch.setattr(pulse, "_Client", Kept)
        yield str(program), made
    finally:
        shutil.rmtree(directory)


@pytest.mark.parametrize(
    ("lines", "says"),
    [
        (["not json"], "unreadable"),
        ([json.dumps({"library": {}})], "unreadable"),
        (
            [FIRST, json.dumps({"ok": True, "sinks": [{**SINK_LINE, "volumes": [NORM]}]})],
            "disagree",
        ),
    ],
)
def test_a_client_that_answers_what_it_cannot_mean_is_client_and_is_reaped(
    stub: tuple[str, list[pulse._Client]],
    monkeypatch: pytest.MonkeyPatch,
    lines: list[str],
    says: str,
) -> None:
    program, made = stub
    monkeypatch.setenv("STUB_LINES", json.dumps(lines))
    failure = PulseOutput("desk", program).attach()
    assert isinstance(failure, Failure) and failure.reason == "client", failure
    assert says in failure.detail
    [client] = made
    assert client._process.poll() is not None


def test_a_client_that_answers_nothing_is_broken_and_killed(
    stub: tuple[str, list[pulse._Client]], monkeypatch: pytest.MonkeyPatch
) -> None:
    program, _ = stub
    monkeypatch.setenv("STUB_LINES", "[]")
    client = pulse._Client(program, Path("/nonexistent"))
    with pytest.raises(pulse._Broken, match="answered nothing in 0.2 s"):
        client.answer(timeout_s=0.2)
    client.close()
    assert client._process.returncode is not None
    with pytest.raises(pulse._Broken):
        client.request({"op": "sinks"})


def test_a_stub_client_that_answers_well_attaches(
    stub: tuple[str, list[pulse._Client]], monkeypatch: pytest.MonkeyPatch
) -> None:
    program, made = stub
    monkeypatch.setenv(
        "STUB_LINES", json.dumps([FIRST, json.dumps({"ok": True, "sinks": [SINK_LINE]})])
    )
    output = PulseOutput("desk", program)
    attached = output.attach()
    assert not isinstance(attached, Failure), attached
    output.detach()
    [client] = made
    assert client._process.returncode == 0


def test_a_detach_whose_close_raises_leaves_the_output_attachable(
    stub: tuple[str, list[pulse._Client]], monkeypatch: pytest.MonkeyPatch
) -> None:
    program, made = stub
    monkeypatch.setenv(
        "STUB_LINES", json.dumps([FIRST, json.dumps({"ok": True, "sinks": [SINK_LINE]})])
    )
    output = PulseOutput("desk", program)
    assert not isinstance(output.attach(), Failure)
    [first] = made
    close = first.close

    def broken() -> None:
        raise RuntimeError("close broke")

    monkeypatch.setattr(first, "close", broken)
    with pytest.raises(RuntimeError, match="close broke"):
        output.detach()
    assert output.client_pid is None
    assert not isinstance(output.attach(), Failure)
    assert len(made) == 2 and output.client_pid == made[1].pid
    output.detach()
    assert made[1]._process.returncode == 0
    close()
