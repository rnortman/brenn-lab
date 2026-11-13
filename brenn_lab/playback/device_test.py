import tomllib

import pytest

from brenn_lab.config_table import ConfigProblem, Table
from brenn_lab.playback.device import HeldControl, PlaybackDevice, read_device
from brenn_lab.playback.limits import QuietMinutes
from brenn_lab.playback.output import ControlName

PATH = "roles.speaker.device"
GOOD = """
output = "alsa"
card = "Flex XVF3800"
source_dir = "/data/lab/sources"
cap_mb = -2400
quiet_hours = [["22:00", "07:00"], ["12:00", "12:30"]]

[level]
element = "PCM"
index = 1

[[held]]
element = "PCM"
index = 0
volume_mb = 0

[[held]]
element = "Speaker"
index = 0
"""


def read(text: str) -> tuple[PlaybackDevice | None, list[ConfigProblem]]:
    problems: list[ConfigProblem] = []
    return read_device(Table(tomllib.loads(text), PATH, problems)), problems


def problems_of(text: str) -> set[tuple[str, str]]:
    device, problems = read(text)
    assert device is None
    assert problems
    return {(p.key.removeprefix(PATH + "."), p.reason) for p in problems}


def test_a_full_device() -> None:
    device, problems = read(GOOD)
    assert problems == []
    assert device == PlaybackDevice(
        output="alsa",
        card="Flex XVF3800",
        source_dir="/data/lab/sources",
        cap_mb=-2400,
        quiet_hours=(QuietMinutes(1320, 420), QuietMinutes(720, 750)),
        level=ControlName("PCM", 1),
        held=(
            HeldControl(ControlName("PCM", 0), 0),
            HeldControl(ControlName("Speaker", 0), None),
        ),
    )


def test_no_held_controls_and_no_quiet_hours() -> None:
    text = GOOD.split("[[held]]")[0].replace(
        'quiet_hours = [["22:00", "07:00"], ["12:00", "12:30"]]', "quiet_hours = []"
    )
    device, problems = read(text)
    assert problems == []
    assert device is not None
    assert device.held == ()
    assert device.quiet_hours == ()


def without(key: str) -> str:
    return "\n".join(line for line in GOOD.splitlines() if not line.startswith(f"{key} ="))


@pytest.mark.parametrize("key", ["output", "card", "source_dir", "cap_mb", "quiet_hours"])
def test_a_required_key_is_missing(key: str) -> None:
    assert problems_of(without(key)) == {(key, "missing_key")}


def test_no_level_control_is_missing() -> None:
    text = GOOD.replace('[level]\nelement = "PCM"\nindex = 1\n', "")
    assert problems_of(text) == {("level", "missing_key")}


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        ('output = "alsa"', 'output = "pulse"', ("output", "bad_value")),
        ('card = "Flex XVF3800"', 'card = ""', ("card", "bad_value")),
        ('source_dir = "/data/lab/sources"', 'source_dir = "sources"', ("source_dir", "bad_value")),
        ("cap_mb = -2400", "cap_mb = -24.0", ("cap_mb", "wrong_type")),
        ("cap_mb = -2400", 'cap_mb = "-2400"', ("cap_mb", "wrong_type")),
        ("cap_mb = -2400", "cap_mb = true", ("cap_mb", "wrong_type")),
        ('["12:00", "12:30"]', '["12:00", "12:00"]', ("quiet_hours[1]", "bad_value")),
        ('["12:00", "12:30"]', '["12:00", "24:00"]', ("quiet_hours[1]", "bad_value")),
        ('["12:00", "12:30"]', '["12:00", 1230]', ("quiet_hours[1]", "bad_value")),
        ('["12:00", "12:30"]', "[12:00:00, 12:30:00]", ("quiet_hours[1]", "bad_value")),
        ('["12:00", "12:30"]', '["12:00"]', ("quiet_hours[1]", "wrong_type")),
        ('["12:00", "12:30"]', '["12:00", "12:30", "13:00"]', ("quiet_hours[1]", "wrong_type")),
        (
            'quiet_hours = [["22:00", "07:00"], ["12:00", "12:30"]]',
            'quiet_hours = "none"',
            ("quiet_hours", "wrong_type"),
        ),
        ('element = "PCM"\nindex = 1', 'element = ""\nindex = 1', ("level.element", "bad_value")),
        ('element = "PCM"\nindex = 1', 'element = "PCM"\nindex = -1', ("level.index", "bad_value")),
        ('element = "PCM"\nindex = 1', 'element = "PCM"', ("level.index", "missing_key")),
        (
            'element = "PCM"\nindex = 1',
            'element = "PCM"\nindex = 1\nvolume_mb = 0',
            ("level.volume_mb", "unknown_key"),
        ),
        ("volume_mb = 0", "volume_mb = 0.5", ("held[0].volume_mb", "wrong_type")),
        ("volume_mb = 0", "volume_mb = 0\nswitch = true", ("held[0].switch", "unknown_key")),
        (
            'element = "Speaker"\nindex = 0',
            'element = "PCM"\nindex = 1',
            ("held", "bad_value"),
        ),
        ('[[held]]\nelement = "Speaker"', '[[held]]\nelement = "PCM"', ("held", "bad_value")),
        ("cap_mb = -2400", "cap_mb = -2400\nvolume_mb = 0", ("volume_mb", "unknown_key")),
    ],
)
def test_a_bad_value(old: str, new: str, expected: tuple[str, str]) -> None:
    assert old in GOOD
    text = GOOD.replace(old, new, 1)
    assert expected in problems_of(text)


def test_held_must_be_tables() -> None:
    text = GOOD.split("[[held]]")[0].replace("cap_mb = -2400", 'cap_mb = -2400\nheld = ["PCM"]')
    assert problems_of(text) == {("held", "wrong_type")}


def test_every_problem_is_reported() -> None:
    text = (
        GOOD.replace('output = "alsa"', 'output = "x"')
        .replace("cap_mb = -2400\n", "")
        .replace('["12:00", "12:30"]', '["1", "2"]')
    )
    assert problems_of(text) == {
        ("output", "bad_value"),
        ("cap_mb", "missing_key"),
        ("quiet_hours[1]", "bad_value"),
    }
