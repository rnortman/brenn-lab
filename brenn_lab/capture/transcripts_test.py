"""The transcripts the stand-in replays, held to the recorder's contract and
to each other: an authored line has the shape of a recorded line of the same
event wherever one was recorded.
"""

import json
import re
import tomllib
from collections.abc import Callable
from pathlib import Path

import pytest

from brenn_lab.capture.messages import Build
from brenn_lab.capture.pod_capture import ContractError, _StartedLine, parse_line
from brenn_lab.data import JsonValue
from brenn_lab.testing.transcript_files import DIR as TRANSCRIPTS
from brenn_lab.testing.transcript_files import objects, shape

RECORDED = sorted((TRANSCRIPTS / "recorded").glob("*.jsonl"))
AUTHORED = sorted((TRANSCRIPTS / "authored").glob("*.jsonl"))

CARD_REASONS = {"no_card", "ambiguous_card"}

# What the recorder's contract lets differ between two lines of one event, by
# dotted key: `record_id` and `at_frame` are null before a log exists, a gap's
# `frames` is null when it was not measured, `build.dirty` is null when the
# build declared its commit rather than reading it from a checkout, and a
# card's `usb_id` and `usb`, and a USB device's `serial`, are null for a card
# or a device that has none.
EITHER = {
    ("capture_failed", "record_id"): {"str", "null"},
    ("capture_failed", "at_frame"): {"int", "null"},
    ("capture_gap", "frames"): {"int", "null"},
    ("capture_started", "build.dirty"): {"bool", "null"},
    ("capture_started", "device.usb_id"): {"str", "null"},
    ("capture_started", "device.usb"): {"object", "null"},
    ("capture_started", "device.usb.serial"): {"str", "null"},
}

# The placeholders the README's hand replacements put in `device.usb`.
USB_PLACEHOLDERS = {"port": "1-1", "bus": 1, "address": 2}
SERIAL_PLACEHOLDER = "EXAMPLE-SERIAL"


def recorded_by_event() -> dict[str, list[dict[str, JsonValue]]]:
    out: dict[str, list[dict[str, JsonValue]]] = {}
    for path in RECORDED:
        for value in objects(path):
            out.setdefault(str(value["event"]), []).append(value)
    return out


def test_there_are_transcripts_of_each_kind() -> None:
    assert RECORDED and AUTHORED


@pytest.mark.parametrize("path", RECORDED, ids=lambda p: p.name)
def test_every_recorded_line_is_accepted(path: Path) -> None:
    raw = path.read_bytes().splitlines()
    assert raw
    for line in raw:
        parse_line(line)


def _recorded(event: str) -> dict[str, JsonValue]:
    """The first recorded line of `event`, as a fresh object to change."""
    value = recorded_by_event()[event][0]
    loaded = json.loads(json.dumps(value))
    assert isinstance(loaded, dict)
    return loaded


def _bytes(value: object) -> bytes:
    return json.dumps(value).encode()


def _without(event: str, key: str) -> bytes:
    value = _recorded(event)
    del value[key]
    return _bytes(value)


def _with(event: str, keys: str, new: object) -> bytes:
    """A recorded line of `event` with the value at `keys` (dotted) replaced."""
    value = _recorded(event)
    *outer, last = keys.split(".")
    target: object = value
    for key in outer:
        assert isinstance(target, dict)
        target = target[key]
    assert isinstance(target, dict)
    target[last] = new
    return _bytes(value)


# Each refusal is a recorded line with one thing changed.
REFUSED: dict[str, tuple[Callable[[], bytes], str]] = {
    "no_ts_ms": (lambda: _without("capture_started", "ts_ms"), "ts_ms"),
    "bool_ts_ms": (lambda: _with("capture_started", "ts_ms", True), "ts_ms"),
    "nan": (lambda: _with("capture_stopped", "mono_us", float("nan")), "NaN"),
    "infinity": (lambda: _with("capture_stopped", "wall_us", float("inf")), "Infinity"),
    "not_an_object": (lambda: _bytes([_recorded("capture_stopped")]), "not a JSON object"),
    "missing_field": (lambda: _without("capture_started", "record_id"), "record_id"),
    "wrong_type": (lambda: _with("capture_stopped", "frames", "many"), "frames"),
    "nested_wrong_type": (lambda: _with("capture_started", "granted.channels", "6"), "channels"),
    "no_device_state": (lambda: _without("capture_started", "device_state"), "device_state"),
    "device_state_not_an_object": (
        lambda: _with("capture_started", "device_state", "read"),
        "device_state",
    ),
    "unknown_outcome": (
        lambda: _with("capture_started", "device_state.outcome", "partly"),
        "outcome",
    ),
    "unavailable_without_why": (
        lambda: _with(
            "capture_started",
            "device_state",
            {"outcome": "unavailable", "detail": "no node", "kind": "xvf3800"},
        ),
        "why",
    ),
    "not_attempted_detail_not_a_string": (
        lambda: _with(
            "capture_started",
            "device_state",
            {"outcome": "not_attempted", "why": "not_usb", "detail": None},
        ),
        "detail",
    ),
    "no_device_usb": (lambda: _without_device_usb(), "usb"),
    "device_usb_a_string": (lambda: _with("capture_started", "device.usb", "3-2.2"), "usb"),
}


def _without_device_usb() -> bytes:
    value = _recorded("capture_started")
    device = value["device"]
    assert isinstance(device, dict)
    del device["usb"]
    return _bytes(value)


@pytest.mark.parametrize("case", REFUSED, ids=str)
def test_parse_line_refuses(case: str) -> None:
    make, why = REFUSED[case]
    with pytest.raises(ContractError, match=why):
        parse_line(make())


def test_parse_line_ignores_keys_beyond_the_contract() -> None:
    value = _recorded("capture_started")
    original = parse_line(_bytes(value))
    value["future_key"] = 1
    device = value["device"]
    assert isinstance(device, dict)
    device["future_key"] = "x"
    assert parse_line(_bytes(value)) == original


def test_parse_line_carries_the_device_statement_whole() -> None:
    value = _recorded("capture_started")
    state = value["device_state"]
    device = value["device"]
    assert isinstance(state, dict) and isinstance(device, dict)
    usb = device["usb"]
    assert isinstance(usb, dict)
    state["future_key"] = {"nested": [1, None]}
    usb["future_key"] = "x"
    parsed = parse_line(_bytes(value))
    assert isinstance(parsed, _StartedLine)
    assert parsed.device_state == state
    assert parsed.device.usb == usb


def test_parse_line_reads_absent_cards_as_null() -> None:
    absent = parse_line(_without("capture_failed", "cards"))
    assert absent == parse_line(_with("capture_failed", "cards", None))
    assert absent != parse_line(_bytes(_recorded("capture_failed")))


def _alike(event: str, key: str, a: JsonValue, b: JsonValue) -> bool:
    """Whether `a` and `b`, the values at dotted `key` of two lines of `event`,
    have the same shape or two shapes the contract lets differ there.
    """
    if isinstance(a, dict) and isinstance(b, dict):
        if (event, key) == ("capture_started", "device_state") and a.get("outcome") != b.get(
            "outcome"
        ):
            # A union tagged on `outcome`: states of two outcomes are not
            # compared, and one no recorded line has is held to brenn-pod's
            # references alone.
            return True
        return a.keys() == b.keys() and all(_alike(event, f"{key}.{k}", a[k], b[k]) for k in a)
    either = EITHER.get((event, key))
    return shape(a) == shape(b) or bool(either and {_kind(a), _kind(b)} <= either)


def _kind(value: JsonValue) -> str:
    """The JSON type of `value` as `EITHER` names it."""
    return "object" if isinstance(value, dict) else str(shape(value))


def test_parse_line_takes_a_declared_and_a_checkout_build() -> None:
    recorded = _recorded("capture_started")
    build = recorded["build"]
    assert isinstance(build, dict)
    commit = str(build["commit"])
    for dirty, source in ((None, "declared"), (False, "git")):
        given = {"commit": commit, "dirty": dirty, "source": source}
        parsed = parse_line(_with("capture_started", "build", given))
        assert isinstance(parsed, _StartedLine), source
        assert parsed.build == Build(commit, dirty, source)


def _comparable(line: dict[str, JsonValue], against: dict[str, JsonValue]) -> bool:
    event = str(line["event"])
    keys = set(line) - {"cards"}
    if keys != set(against) - {"cards"}:
        return False
    if not all(_alike(event, key, line[key], against[key]) for key in keys):
        return False
    if "cards" in line and "cards" in against:
        return shape(line["cards"]) == shape(against["cards"])
    return True


@pytest.mark.parametrize("path", AUTHORED, ids=lambda p: p.name)
def test_authored_lines_have_the_shape_of_recorded_ones(path: Path) -> None:
    recorded = recorded_by_event()
    for value in objects(path):
        event = str(value.get("event"))
        if event not in recorded:
            continue
        assert any(_comparable(value, r) for r in recorded[event]), value


@pytest.mark.parametrize("path", RECORDED + AUTHORED, ids=lambda p: p.name)
def test_cards_only_for_the_card_reasons(path: Path) -> None:
    for value in objects(path):
        if value.get("event") == "capture_failed":
            assert ("cards" in value) == (value["reason"] in CARD_REASONS), value


def test_the_readme_names_the_recorders_commit() -> None:
    readme = (TRANSCRIPTS / "README.md").read_text()
    match = re.search(r"brenn-pod commit: `([0-9a-f]{40})`", readme)
    assert match is not None
    commits = {
        value["build"]["commit"]  # type: ignore[call-overload,index]
        for path in RECORDED + AUTHORED
        for value in objects(path)
        if value["event"] == "capture_started"
    }
    assert commits == {match.group(1)}


@pytest.mark.parametrize("path", RECORDED + AUTHORED, ids=lambda p: p.name)
def test_no_usb_location_or_serial_but_the_placeholders(path: Path) -> None:
    for value in objects(path):
        device = value.get("device")
        usb = device.get("usb") if isinstance(device, dict) else None
        if not isinstance(usb, dict):
            continue
        assert usb["serial"] in (None, SERIAL_PLACEHOLDER), path.name
        assert {k: usb[k] for k in USB_PLACEHOLDERS} == USB_PLACEHOLDERS, path.name


def test_replay_files() -> None:
    assert not list((TRANSCRIPTS / "recorded").glob("*.replay.toml"))
    replays = sorted((TRANSCRIPTS / "authored").glob("*.replay.toml"))
    assert replays
    for path in replays:
        assert path.with_name(path.name.removesuffix(".replay.toml") + ".jsonl").is_file()
        replay = tomllib.loads(path.read_text())
        assert replay.keys() <= {"held", "unasked_exit"}, path.name
