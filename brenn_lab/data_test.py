import dataclasses
from pathlib import Path
from typing import Any, Literal

import pytest

from brenn_lab.capture import messages as m
from brenn_lab.data import (
    COMMANDS,
    EVENTS,
    MONOTONIC,
    REALTIME,
    RESULTS,
    Check,
    ClockReading,
    ClockValue,
    Command,
    DecodeError,
    Event,
    Failure,
    JsonObject,
    JsonValue,
    Location,
    ProcessStarted,
    Release,
    Reply,
    Request,
    StreamLog,
    decode,
    dumps,
    encode,
    failure_of,
    loads,
)
from brenn_lab.data import command as register_command
from brenn_lab.data import event as register_event
from brenn_lab.sound import Granted

AT = ClockReading(
    machine="bench",
    monotonic=ClockValue(MONOTONIC, 81234567),
    wall=ClockValue(REALTIME, 1794000000000000),
)
BUILD = m.Build(commit="0" * 40, dirty=None, source="declared")
RECORDER = m.Recorder(program="pod-capture", repository="brenn-pod", build=BUILD)
CARD = m.Device(
    selector="Flex",
    card_index=2,
    card_name="reSpeaker Flex",
    card_long_name="Seeed reSpeaker Flex at usb-1",
    pcm="hw:2,0",
    usb_id=None,
    usb={"port": "1-1", "bus": 1, "address": 2, "serial": None, "bcd_device": "0100"},
)
STATE: JsonObject = {
    "outcome": "read",
    "kind": "xvf3800",
    "registers": {"version": {"bytes": "010000", "value": [1, 0, 0]}, "gain": {"value": 1.5}},
}
GRANTED = Granted(
    rate_hz=16000, channels=2, sample_format="s16_le", period_frames=320, buffer_frames=5120
)
STOPPED = m.Stopped(capture=1, frames=159680, gaps_reported=0, end=AT)
FAILURE = Failure("no_card", "no card matches", {"cards": [{"name": "x", "index": 0}]})


@register_command("test.shapes")
@dataclasses.dataclass(frozen=True)
class Shapes(Request[object]):
    """A command registered by this test alone, with a field of each shape
    the decoder knows.
    """

    count: int
    name: str
    flag: bool
    maybe: int | None
    mode: Literal["a", "b"]
    names: tuple[str, ...]
    table: dict[str, int]
    at: ClockReading
    free: JsonValue
    state: JsonObject


SHAPES = Shapes(
    count=3,
    name="x",
    flag=True,
    maybe=None,
    mode="a",
    names=("l", "r"),
    table={"k": 1},
    at=AT,
    free=[1, "two", None],
    state={"outcome": "read"},
)

COMMAND_BODIES = [Check(), Release(), m.Start(), m.Stop(), SHAPES]
RESULT_BODIES: list[object] = [
    m.Started(
        capture=1,
        capture_id="ab" * 16,
        location=Location(machine="bench", path="/runs/r/captures/mic/0001.framelog"),
        recorder=RECORDER,
        device=CARD,
        granted=GRANTED,
        layout=("left", "right"),
        start=AT,
        device_state=STATE,
    ),
    STOPPED,
    m.Checked(
        recorder=RECORDER,
        device=CARD,
        granted=GRANTED,
        layout=("left", "right"),
        device_state={"outcome": "not_attempted", "why": "not_usb", "detail": "not on USB"},
    ),
    m.Released(stopped=STOPPED),
    m.Released(stopped=None),
]
EVENT_BODIES: list[object] = [
    m.Gap(capture=1, cause="xrun", at_frame=80000, frames=None, at=AT, detail="overrun"),
    m.Late(capture=1, at_frame=80000, backlog_frames=640, at=AT),
    m.Failed(capture=1, failure=FAILURE, stopped=None),
    m.Failed(capture=1, failure=Failure("stopped_unasked", "stopped"), stopped=STOPPED),
    ProcessStarted(
        process="pod-capture",
        pid=4242,
        logs=(
            StreamLog("stdout", Location("bench", "/runs/r/logs/mic/recorder-0001.stdout.jsonl")),
            StreamLog("stderr", Location("bench", "/runs/r/logs/mic/recorder-0001.stderr.log")),
        ),
    ),
]


def messages() -> list[Command | Reply | Event]:
    out: list[Command | Reply | Event] = []
    for body in COMMAND_BODIES:
        dry = Command(role="mic", body=body, dry=True)
        out.append(Command(role="mic", body=body, dry=False))
        out.append(dry)
        out.append(Reply(role="mic", command=dry.name, dry=True, outcome=FAILURE))
    for result in RESULT_BODIES:
        out.append(Reply(role="mic", command="capture.start", dry=False, outcome=result))
    for event in EVENT_BODIES:
        out.append(Event(role="mic", body=event))
    return out


def test_every_registered_type_is_exercised() -> None:
    assert {type(b) for b in COMMAND_BODIES} == set(COMMANDS.values())
    assert {type(b) for b in RESULT_BODIES} == set(RESULTS.values())
    assert {type(b) for b in EVENT_BODIES} == set(EVENTS.values())


@pytest.mark.parametrize("message", messages(), ids=repr)
def test_round_trip_through_json_text(message: Command | Reply | Event) -> None:
    assert loads(dumps(message)) == message


def test_json_forms() -> None:
    assert encode(Command(role="mic", body=m.Start())) == {
        "role": "mic",
        "command": "capture.start",
        "dry": False,
        "args": {},
    }
    assert encode(Reply(role="mic", command="capture.stop", dry=True, outcome=FAILURE)) == {
        "role": "mic",
        "command": "capture.stop",
        "dry": True,
        "ok": False,
        "failure": {"reason": "no_card", "detail": "no card matches", "data": FAILURE.data},
    }
    reply = encode(Reply(role="mic", command="capture.stop", dry=False, outcome=STOPPED))
    assert reply["ok"] is True
    assert reply["result_type"] == "capture.stopped"
    started = encode(
        Reply(role="mic", command="capture.start", dry=False, outcome=RESULT_BODIES[0])
    )
    result = started["result"]
    assert isinstance(result, dict)
    assert result["device_state"] == STATE
    assert isinstance(result["device"], dict)
    assert result["device"]["usb"] == CARD.usb
    assert result["start"] == {
        "machine": "bench",
        "monotonic": {"clock": "CLOCK_MONOTONIC", "us": 81234567},
        "wall": {"clock": "CLOCK_REALTIME", "us": 1794000000000000},
    }


def _gap() -> tuple[JsonObject, JsonObject]:
    """A gap event's encoding, and the object of its fields within it."""
    value = encode(Event(role="mic", body=EVENT_BODIES[0]))
    data = value["data"]
    assert isinstance(data, dict)
    return value, data


@pytest.mark.parametrize(
    "text",
    [
        '{"role": "mic", "command": "capture.record", "dry": false, "args": {}}',
        '{"role": "mic", "event": "capture.unknown", "data": {}}',
        '{"role": "mic", "command": "capture.start", "dry": false}',
        '{"role": "mic", "command": "capture.start", "dry": false, "args": {}, "x": 1}',
        '{"role": "mic", "command": "capture.start", "dry": 0, "args": {}}',
        '{"role": "mic", "command": "capture.start", "dry": false, "args": {"rate": 1}}',
        '{"role": "mic", "command": "capture.start", "dry": false, "ok": "yes"}',
        '{"role": "mic", "command": "capture.start", "dry": false, "ok": true,'
        ' "result_type": "capture.nothing", "result": {}}',
        '{"role": "mic", "command": "check", "dry": false, "ok": false,'
        ' "failure": {"reason": "no_card", "detail": "x"}}',
        "[1, 2]",
        "not json",
        '{"role": "mic", "event": "capture.gap", "data": NaN}',
    ],
)
def test_rejects(text: str) -> None:
    with pytest.raises(DecodeError):
        loads(text)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("capture", True),
        ("capture", "1"),
        ("frames", 1.5),
        ("at", None),
        ("detail", None),
    ],
)
def test_rejects_a_field_of_the_wrong_type(field: str, value: JsonValue) -> None:
    gap, data = _gap()
    data[field] = value
    with pytest.raises(DecodeError):
        decode(gap)


def test_rejects_a_missing_field() -> None:
    gap, data = _gap()
    del data["cause"]
    with pytest.raises(DecodeError):
        decode(gap)


def test_rejects_an_unknown_field() -> None:
    gap, data = _gap()
    data["extra"] = 1
    with pytest.raises(DecodeError):
        decode(gap)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("device_state", "read"),
        ("device_state", None),
        ("device", {**dataclasses.asdict(CARD), "usb": "1-1"}),
        ("device", {k: v for k, v in dataclasses.asdict(CARD).items() if k != "usb"}),
    ],
)
def test_the_device_statement_decodes_strictly(key: str, value: JsonValue) -> None:
    started = encode(
        Reply(role="mic", command="capture.start", dry=False, outcome=RESULT_BODIES[0])
    )
    result = started["result"]
    assert isinstance(result, dict)
    result[key] = value
    with pytest.raises(DecodeError):
        decode(started)


def test_unregistered_bodies_are_refused() -> None:
    @dataclasses.dataclass(frozen=True)
    class Stray:
        pass

    with pytest.raises(TypeError):
        Event(role="mic", body=Stray())
    with pytest.raises(TypeError):
        Reply(role="mic", command="capture.start", dry=False, outcome=Stray())
    with pytest.raises(TypeError):
        Reply(role="mic", command="capture.record", dry=False, outcome=STOPPED)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("count", "3"),
        ("count", 3.0),
        ("count", True),
        ("name", None),
        ("name", Path("x")),
        ("flag", 1),
        ("maybe", "1"),
        ("mode", "c"),
        ("names", ["l", "r"]),
        ("names", ("l", 2)),
        ("table", {"k": "1"}),
        ("table", {1: 1}),
        ("at", {"machine": "bench"}),
        ("free", ("a",)),
        ("free", float("nan")),
        ("free", {"k": object()}),
        ("state", []),
    ],
)
def test_a_command_whose_body_does_not_survive_a_wire_is_refused(field: str, value: Any) -> None:
    changed: dict[str, Any] = {field: value}
    body = dataclasses.replace(SHAPES, **changed)
    with pytest.raises(TypeError, match=f"command test.shapes.{field}: "):
        Command(role="mic", body=body)


def test_every_registered_command_passes_the_wire_check() -> None:
    for body in COMMAND_BODIES:
        assert Command(role="mic", body=body).body is body


def test_a_failure_event_carries_its_failure_in_failure() -> None:
    failed = m.Failed(capture=1, failure=FAILURE, stopped=None)
    assert failure_of(failed) is FAILURE
    assert failure_of(m.Late(capture=1, at_frame=0, backlog_frames=320, at=AT)) is None


@pytest.mark.parametrize("annotation", [str, Failure | None, JsonObject])
def test_an_event_whose_failure_is_not_a_failure_is_refused(annotation: object) -> None:
    @dataclasses.dataclass(frozen=True)
    class Odd:
        failure: annotation  # type: ignore[valid-type]

    with pytest.raises(TypeError, match="failure"):
        register_event("test.odd_failure")(Odd)
    assert "test.odd_failure" not in EVENTS


def test_after_moves_both_values_and_keeps_the_names() -> None:
    assert AT.after(1.5) == ClockReading(
        machine="bench",
        monotonic=ClockValue(MONOTONIC, 81234567 + 1_500_000),
        wall=ClockValue(REALTIME, 1794000000000000 + 1_500_000),
    )
    assert AT.after(0.0000004) == AT
    assert AT.after(0) == AT


@pytest.mark.parametrize("seconds", [-0.000001, -1.0, float("nan")])
def test_after_refuses_a_negative(seconds: float) -> None:
    with pytest.raises(ValueError, match="0 or more"):
        AT.after(seconds)
