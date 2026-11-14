import dataclasses

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
    Release,
    Reply,
    decode,
    dumps,
    encode,
    failure_of,
    loads,
)
from brenn_lab.data import event as register_event
from brenn_lab.sound import CardIdentity, Granted

AT = ClockReading(
    machine="bench",
    monotonic=ClockValue(MONOTONIC, 81234567),
    wall=ClockValue(REALTIME, 1794000000000000),
)
BUILD = m.Build(commit="0" * 40, dirty=None, source="declared")
RECORDER = m.Recorder(program="pod-capture", repository="brenn-pod", build=BUILD)
CARD = CardIdentity(
    selector="Flex",
    card_index=2,
    card_name="reSpeaker Flex",
    card_long_name="Seeed reSpeaker Flex at usb-1",
    pcm="hw:2,0",
    usb_id=None,
)
GRANTED = Granted(
    rate_hz=16000, channels=2, sample_format="s16_le", period_frames=320, buffer_frames=5120
)
STOPPED = m.Stopped(capture=1, frames=159680, gaps_reported=0, end=AT)
FAILURE = Failure("no_card", "no card matches", {"cards": [{"name": "x", "index": 0}]})

COMMAND_BODIES = [Check(), Release(), m.Start(), m.Stop()]
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
        device_firmware=m.NotRead("not read"),
        device_configuration=m.NotRead("not read"),
    ),
    STOPPED,
    m.Checked(recorder=RECORDER, device=CARD, granted=GRANTED, layout=("left", "right")),
    m.Released(stopped=STOPPED),
    m.Released(stopped=None),
]
EVENT_BODIES: list[object] = [
    m.Gap(capture=1, cause="xrun", at_frame=80000, frames=None, at=AT, detail="overrun"),
    m.Late(capture=1, at_frame=80000, backlog_frames=640, at=AT),
    m.Failed(capture=1, failure=FAILURE, stopped=None),
    m.Failed(capture=1, failure=Failure("stopped_unasked", "stopped"), stopped=STOPPED),
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
    assert result["device_firmware"] == {"reason": "not read", "read": False}
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


def test_not_read_is_never_read() -> None:
    started = encode(
        Reply(role="mic", command="capture.start", dry=False, outcome=RESULT_BODIES[0])
    )
    result = started["result"]
    assert isinstance(result, dict)
    result["device_firmware"] = {"reason": "x", "read": True}
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
