import pytest

from brenn_lab.capture.device import CaptureDevice
from brenn_lab.config_table import ConfigProblem
from brenn_lab.labconfig import Installed, LabConfig, Machine, Pinned, RoleConfig, parse

GOOD = """
schema = 1

[machines.bench.programs]
pod_capture = "/opt/pod/bin/pod-capture"

[machines.robot]

[roles.mic]
kind = "capture"
machine = "bench"

[roles.mic.device]
card = "Flex XVF3800 L16K6Ch"
rate_hz = 16000
sample_format = "s16_le"
layout = ["conference_beam", "asr_beam"]

[roles.mic.stated]
image = "six-channel USB audio image"
position = { room = "lab", metres = 1.5 }
"""


def _problems(text: str) -> list[ConfigProblem]:
    result = parse(text.encode())
    assert isinstance(result, list), result
    return result


def _reasons(text: str) -> set[tuple[str, str]]:
    return {(p.key, p.reason) for p in _problems(text)}


def test_a_good_configuration() -> None:
    assert parse(GOOD.encode()) == LabConfig(
        machines={
            "bench": Machine("bench", {"pod_capture": Installed("/opt/pod/bin/pod-capture")}),
            "robot": Machine("robot", {}),
        },
        roles={
            "mic": RoleConfig(
                name="mic",
                kind="capture",
                machine="bench",
                device={
                    "card": "Flex XVF3800 L16K6Ch",
                    "rate_hz": 16000,
                    "sample_format": "s16_le",
                    "layout": ["conference_beam", "asr_beam"],
                },
                parameters=CaptureDevice(
                    card="Flex XVF3800 L16K6Ch",
                    rate_hz=16000,
                    sample_format="s16_le",
                    layout=("conference_beam", "asr_beam"),
                ),
                stated={
                    "image": "six-channel USB audio image",
                    "position": {"room": "lab", "metres": 1.5},
                },
                sample_clock=None,
            )
        },
    )


def test_a_sample_clock_is_stated_by_name() -> None:
    text = GOOD.replace('machine = "bench"\n', 'machine = "bench"\nsample_clock = "flex"\n')
    result = parse(text.encode())
    assert isinstance(result, LabConfig)
    assert result.roles["mic"].sample_clock == "flex"


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ('"Flex"', "bad_value"),
        ('""', "bad_value"),
        ('"a' + "b" * 32 + '"', "bad_value"),
        ("1", "wrong_type"),
        ("true", "wrong_type"),
    ],
)
def test_a_malformed_sample_clock(value: str, reason: str) -> None:
    text = GOOD.replace('machine = "bench"\n', f'machine = "bench"\nsample_clock = {value}\n')
    assert _reasons(text) == {("roles.mic.sample_clock", reason)}


def test_stated_is_optional() -> None:
    text = GOOD.split("[roles.mic.stated]")[0]
    result = parse(text.encode())
    assert isinstance(result, LabConfig)
    assert result.roles["mic"].stated is None


def test_not_toml() -> None:
    assert _reasons("schema = ") == {("", "not_toml")}
    assert [p.reason for p in _problems_bytes(b"\xff")] == ["not_toml"]


def _problems_bytes(data: bytes) -> list[ConfigProblem]:
    result = parse(data)
    assert isinstance(result, list)
    return result


def test_unsupported_schema() -> None:
    assert _reasons(GOOD.replace("schema = 1", "schema = 2")) == {("schema", "unsupported_schema")}


def test_missing_top_level_keys() -> None:
    assert _reasons("") == {
        ("schema", "missing_key"),
        ("machines", "missing_key"),
        ("roles", "missing_key"),
    }


def test_every_problem_is_returned() -> None:
    text = (
        GOOD.replace('kind = "capture"', 'kind = "capture"\ncolour = "red"')
        .replace("rate_hz = 16000", 'rate_hz = "16000"')
        .replace('"/opt/pod/bin/pod-capture"', '"bin/pod-capture"')
        .replace("[machines.robot]", "[machines.robot]\nos = 1")
        .replace("schema = 1", "schema = 1\nversion = 3")
    )
    assert _reasons(text) == {
        ("roles.mic.colour", "unknown_key"),
        ("roles.mic.device.rate_hz", "wrong_type"),
        ("machines.bench.programs.pod_capture", "relative_path"),
        ("machines.robot.os", "unknown_key"),
        ("version", "unknown_key"),
    }


def test_a_problem_in_a_machine_does_not_hide_the_roles_on_it() -> None:
    text = GOOD.replace('"/opt/pod/bin/pod-capture"', '"pod-capture"').replace(
        "rate_hz = 16000", "rate_hz = 0"
    )
    assert _reasons(text) == {
        ("machines.bench.programs.pod_capture", "relative_path"),
        ("roles.mic.device.rate_hz", "bad_value"),
    }


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (('kind = "capture"', 'kind = "motion"'), {("roles.mic.kind", "unknown_kind")}),
        (('kind = "capture"', "kind = 1"), {("roles.mic.kind", "wrong_type")}),
        (('kind = "capture"', ""), {("roles.mic.kind", "missing_key")}),
        (('machine = "bench"', 'machine = "attic"'), {("roles.mic.machine", "unknown_machine")}),
        (('machine = "bench"', 'machine = "robot"'), {("roles.mic.machine", "missing_program")}),
        # A machine with a problem is not also reported as missing a program.
        (("pod_capture =", "recorder ="), {("machines.bench.programs.recorder", "unknown_key")}),
        (
            ("pod_capture = ", "pod_capture = 7 #"),
            {("machines.bench.programs.pod_capture", "wrong_type")},
        ),
    ],
)
def test_role_and_machine_rules(change: tuple[str, str], expected: set[tuple[str, str]]) -> None:
    assert _reasons(GOOD.replace(*change)) == expected


PATH_FORM = 'pod_capture = "/opt/pod/bin/pod-capture"'


def test_a_pinned_program() -> None:
    result = parse(GOOD.replace(PATH_FORM, "pod_capture = { pinned = true }").encode())
    assert isinstance(result, LabConfig)
    assert result.machines["bench"].programs == {"pod_capture": Pinned()}


def test_a_pinned_program_on_a_machine_the_runner_is_not_on() -> None:
    # Which machine the runner is on is not the configuration's to know.
    text = GOOD.replace("[machines.robot]", "[machines.robot.programs]\npod_capture.pinned = true")
    result = parse(text.encode())
    assert isinstance(result, LabConfig)
    assert result.machines["robot"].programs == {"pod_capture": Pinned()}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("{ pinned = false }", {("machines.bench.programs.pod_capture.pinned", "bad_value")}),
        ("{ pinned = 1 }", {("machines.bench.programs.pod_capture.pinned", "wrong_type")}),
        ("{}", {("machines.bench.programs.pod_capture.pinned", "missing_key")}),
        (
            "{ pinned = true, path = 1 }",
            {("machines.bench.programs.pod_capture.path", "unknown_key")},
        ),
        ("7", {("machines.bench.programs.pod_capture", "wrong_type")}),
        ("[]", {("machines.bench.programs.pod_capture", "wrong_type")}),
        ('"pod-capture"', {("machines.bench.programs.pod_capture", "relative_path")}),
    ],
)
def test_a_program_is_a_path_or_pinned(value: str, expected: set[tuple[str, str]]) -> None:
    assert _reasons(GOOD.replace(PATH_FORM, f"pod_capture = {value}")) == expected


def test_a_machine_naming_neither_form_has_no_program() -> None:
    assert _reasons(GOOD.replace(PATH_FORM, "")) == {("roles.mic.machine", "missing_program")}


@pytest.mark.parametrize("name", ["Mic", "m-c", "1mic", "_mic", "a" + "b" * 32])
def test_a_bad_role_name(name: str) -> None:
    assert _reasons(GOOD.replace("roles.mic", f"roles.{name}")) == {(f"roles.{name}", "bad_name")}


@pytest.mark.parametrize("name", ["m", "mic_2", "a" + "b" * 31])
def test_a_good_role_name(name: str) -> None:
    result = parse(GOOD.replace("roles.mic", f"roles.{name}").encode())
    assert isinstance(result, LabConfig)
    assert list(result.roles) == [name]


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (('card = "Flex XVF3800 L16K6Ch"', 'card = ""'), {("card", "bad_value")}),
        (('card = "Flex XVF3800 L16K6Ch"', ""), {("card", "missing_key")}),
        (("rate_hz = 16000", "rate_hz = -1"), {("rate_hz", "bad_value")}),
        (("rate_hz = 16000", "rate_hz = 16000.0"), {("rate_hz", "wrong_type")}),
        (("rate_hz = 16000", "rate_hz = true"), {("rate_hz", "wrong_type")}),
        (('sample_format = "s16_le"', "sample_format = 16"), {("sample_format", "wrong_type")}),
        (('layout = ["conference_beam", "asr_beam"]', "layout = []"), {("layout", "bad_value")}),
        (
            ('layout = ["conference_beam", "asr_beam"]', 'layout = ["a", ""]'),
            {("layout", "bad_value")},
        ),
        (
            ('layout = ["conference_beam", "asr_beam"]', 'layout = ["a", "a"]'),
            {("layout", "bad_value")},
        ),
        (
            ('layout = ["conference_beam", "asr_beam"]', 'layout = ["a", 1]'),
            {("layout", "wrong_type")},
        ),
        (
            ('layout = ["conference_beam", "asr_beam"]', 'layout = "a,b"'),
            {("layout", "wrong_type")},
        ),
        (("rate_hz = 16000", "rate_hz = 16000\nchannels = 2"), {("channels", "unknown_key")}),
    ],
)
def test_capture_device_rules(change: tuple[str, str], expected: set[tuple[str, str]]) -> None:
    assert _reasons(GOOD.replace(*change)) == {("roles.mic.device." + k, r) for k, r in expected}


def test_the_device_table_is_required() -> None:
    text = GOOD.replace("[roles.mic.device]", "[roles.mic.unused]")
    assert _reasons(text) == {
        ("roles.mic.device", "missing_key"),
        ("roles.mic.unused", "unknown_key"),
    }


def test_a_value_with_no_json_form_is_a_problem() -> None:
    text = GOOD.replace('image = "six-channel USB audio image"', "image = 2026-11-12")
    assert _reasons(text) == {("roles.mic.stated.image", "wrong_type")}
    text = GOOD.replace('sample_format = "s16_le"', 'sample_format = "s16_le"\nwhen = 10:00:00')
    assert _reasons(text) == {("roles.mic.device.when", "wrong_type")}
    text = GOOD.replace("metres = 1.5", "metres = nan")
    assert _reasons(text) == {("roles.mic.stated.position.metres", "wrong_type")}


def test_a_lab_may_hold_several_machines_and_roles() -> None:
    roles = GOOD[GOOD.index("[roles.mic]") :]
    text = GOOD + roles.replace("roles.mic", "roles.desk")
    result = parse(text.encode())
    assert isinstance(result, LabConfig)
    assert sorted(result.machines) == ["bench", "robot"]
    assert sorted(result.roles) == ["desk", "mic"]
