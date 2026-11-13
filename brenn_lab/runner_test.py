"""The dry run and the run, against the stand-in recorder replaying transcripts.

Every expectation about what the recorder reported is read from the
transcript the stand-in replays, never written here.
"""

import json
import os
import signal
import subprocess
import sys
import textwrap
import threading
from pathlib import Path
from typing import Any

import pytest

from brenn_lab import runner
from brenn_lab.buildinfo import Revision
from brenn_lab.data import JsonObject, JsonValue
from brenn_lab.rundir import RunDir
from brenn_lab.runner import Refused
from brenn_lab.testing import transcript_files
from brenn_lab.testing.transcript_files import layout_of

STAND_IN = os.path.abspath(os.environ["STAND_IN"])
EXAMPLE = Path(os.path.abspath(os.environ["EXAMPLE"]))
MACHINE = "bench"
HARNESS = Revision(commit="0123456789abcdef0123456789abcdef01234567", dirty=False, source="git")


def line(name: str, event: str) -> Any:
    return transcript_files.line(name, event)


def reading(value: JsonObject) -> JsonObject:
    return transcript_files.reading(value, MACHINE)


def lab(
    tmp_path: Path,
    roles: dict[str, tuple[str, str]],
    layout: tuple[str, ...] | None = None,
) -> Path:
    """A lab with machines `bench` and `far`; `roles` maps a role to its card
    (a transcript name) and machine.
    """
    text = textwrap.dedent(f"""\
        schema = 1
        [machines.bench.programs]
        pod_capture = {json.dumps(STAND_IN)}
        [machines.far.programs]
        pod_capture = "/opt/pod-capture"
        """)
    for name, (card, machine) in roles.items():
        names = layout if layout is not None else layout_of(card if card != "no_card" else "clean")
        text += textwrap.dedent(f"""\
            [roles.{name}]
            kind = "capture"
            machine = "{machine}"
            [roles.{name}.device]
            card = {json.dumps(card)}
            rate_hz = 16000
            sample_format = "s16_le"
            layout = {json.dumps(names)}
            [roles.{name}.stated]
            position = "on the desk"
            """)
    path = tmp_path / "lab.toml"
    path.write_text(text)
    return path


def script(tmp_path: Path, body: str, roles: str = '{"mic": "capture"}') -> Path:
    path = tmp_path / "script.py"
    path.write_text(
        "from brenn_lab import capture\n"
        "from brenn_lab.data import Check, Release\n"
        "from brenn_lab.script import Lab, RoleFailure\n\n"
        f"ROLES = {roles}\n\n\n"
        "def run(lab: Lab) -> None:\n" + textwrap.indent(textwrap.dedent(body), "    ")
    )
    return path


START_WAIT_STOP = """\
mic = lab.role("mic")
mic.send(capture.Start())
lab.wait(0.3)
mic.send(capture.Stop())
"""


def dry(lab_path: Path, script_path: Path) -> Any:
    report = runner.dry_run(lab_path, MACHINE, script_path)
    assert isinstance(report, dict), report
    return report


def do_run(
    tmp_path: Path,
    lab_path: Path,
    script_path: Path,
    notes: Path | None = None,
    workspace: Path | None = None,
) -> tuple[Path, Any, Any]:
    out = tmp_path / "run"
    pointer = runner.run(
        lab_path, MACHINE, script_path, str(script_path), out, notes, HARNESS, workspace
    )
    assert isinstance(pointer, dict), pointer
    assert pointer["run"] == str(out)
    assert pointer["results"] == str(out / "results.json")
    results = json.loads((out / "results.json").read_text())
    assert pointer["outcome"] == results["outcome"]
    assert pointer["completed"] == results["completed"]
    assert (out / ".gitignore").read_text() == "*\n"
    assert results["failure"] is None or results["failure"].keys() == PROBLEM_KEYS
    return out, json.loads((out / "manifest.json").read_text()), results


def journal(out: Path) -> list[Any]:
    return [json.loads(t) for t in (out / "journal.jsonl").read_text().splitlines()]


PROBLEM_KEYS = {"where", "reason", "detail", "role", "command", "key", "data"}


def every_key(report: JsonObject) -> None:
    """Every problem in `report` has every key a problem has."""
    problems = report["problems"]
    assert isinstance(problems, list)
    for problem in problems:
        assert isinstance(problem, dict) and problem.keys() == PROBLEM_KEYS, problem


def reasons(report: JsonObject) -> list[tuple[JsonValue, JsonValue, JsonValue]]:
    every_key(report)
    problems = report["problems"]
    assert isinstance(problems, list)
    return [(p["where"], p["role"], p["reason"]) for p in problems if isinstance(p, dict)]


def test_a_whole_experiment(tmp_path: Path) -> None:
    lab_path = lab(tmp_path, {"mic": ("clean", MACHINE)})
    started_line = line("clean", "capture_started")
    stopped_line = line("clean", "capture_stopped")

    report = dry(lab_path, EXAMPLE)
    assert report["ok"] is True and report["problems"] == []
    assert report["trace"] == {"complete": True, "commands": 2, "stopped_at": None}
    check = report["roles"]["mic"]["check"]
    assert check["ok"] is True
    assert check["result"]["device"] == started_line["device"]
    assert check["result"]["granted"] == started_line["granted"]

    notes = tmp_path / "room.md"
    notes.write_text("door shut\n")
    out, manifest, results = do_run(tmp_path, lab_path, EXAMPLE, notes)

    assert manifest["layout"] == 1
    assert manifest["harness"] == HARNESS.encode()
    assert manifest["runner"] == {"machine": MACHINE}
    assert (out / manifest["script"]["file"]).read_bytes() == EXAMPLE.read_bytes()
    assert (out / manifest["lab"]["file"]).read_bytes() == lab_path.read_bytes()
    assert (out / manifest["notes"]["file"]).read_bytes() == notes.read_bytes()
    assert manifest["script"]["roles"] == {"mic": "capture"}
    assert manifest["script"]["given_as"] == str(EXAMPLE)
    repositories = manifest["repositories"]
    assert repositories[0] == {**HARNESS.encode(), "from": "the harness's build stamp"}
    assert repositories[1]["repository"] == "brenn-pod"
    assert {k: repositories[1][k] for k in ("commit", "dirty", "source")} == started_line["build"]
    assert len(repositories) == 2

    role = manifest["roles"]["mic"]
    assert role["stated_by_configuration"] == {"position": "on the desk"}
    assert role["configured"]["card"] == "clean"
    [capture] = role["captures"]
    assert capture["capture"] == 1
    assert capture["capture_id"] == started_line["record_id"]
    assert capture["file"] == {
        "machine": MACHINE,
        "path": "captures/mic/0001.framelog",
        "in_run": True,
    }
    assert (out / capture["file"]["path"]).is_file()
    assert capture["recorder"] == {
        "program": "pod-capture",
        "repository": "brenn-pod",
        "build": started_line["build"],
    }
    for key in ("device", "granted", "layout"):
        assert capture[key] == started_line[key]
    assert capture["start"] == reading(started_line)
    for key in ("device_firmware", "device_configuration"):
        assert capture[key]["read"] is False and capture[key]["reason"]

    assert results["outcome"] == "completed" and results["completed"] is True
    assert results["failure"] is None
    assert results["recorded"] == {
        "captures_started": 1,
        "captures_stopped": 1,
        "frames": stopped_line["frames"],
    }
    [result] = results["captures"]
    assert result["file"] == capture["file"]
    assert result["ended"] == "stopped" and result["stopped_by"] == "script"
    assert result["frames"] == stopped_line["frames"]
    assert result["gaps_reported"] == stopped_line["gaps"]
    assert result["loss_free"] is True
    assert result["end"] == reading(stopped_line)
    assert results["roles"]["mic"] == {"kind": "capture", "commands": 3, "failures": []}
    assert results["usability"]["assessed"] is False

    entries = journal(out)
    assert [(e.get("command"), "ok" in e, e.get("by")) for e in entries] == [
        ("capture.start", False, "script"),
        ("capture.start", True, "script"),
        ("capture.stop", False, "script"),
        ("capture.stop", True, "script"),
        ("release", False, "runner"),
        ("release", True, "runner"),
    ]
    assert entries[-1]["result"] == {"stopped": None}
    assert [e["seq"] for e in entries] == list(range(1, 7))
    assert (out / "logs" / "mic" / "recorder-0001.stdout.jsonl").read_bytes() == (
        transcript_files.path("clean").read_bytes()
    )
    assert "pid" in (out / "logs" / "runner.log").read_text()


def test_a_configuration_reports_every_problem(tmp_path: Path) -> None:
    lab_path = lab(tmp_path, {"mic": ("clean", MACHINE)})
    text = lab_path.read_text().replace("schema = 1\n", "schema = 1\ncolour = 1\n")
    text = text.replace("rate_hz = 16000", 'rate_hz = "fast"')
    lab_path.write_text(text)
    report = dry(lab_path, EXAMPLE)
    assert report["ok"] is False
    assert sorted((str(w), str(r)) for w, _, r in reasons(report)) == [
        ("lab", "unknown_key"),
        ("lab", "wrong_type"),
    ]


THREE_ROLES = '{"absent": "capture", "far": "capture", "nocard": "capture"}'


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("pass\n", id="no_commands"),
        pytest.param(
            """\
            for name in ("absent", "far", "nocard"):
                for command in (Check(), capture.Start(), capture.Stop()):
                    try:
                        lab.role(name).send(command)
                    except RoleFailure:
                        pass
            """,
            id="every_command",
        ),
        # Each role's problem is met first by another command, in the other
        # order: what is listed must not depend on which is found first.
        pytest.param(
            """\
            for name in ("nocard", "far", "absent"):
                for command in (capture.Stop(), capture.Start()):
                    try:
                        lab.role(name).send(command)
                    except RoleFailure:
                        pass
            """,
            id="stop_first_reverse_order",
        ),
    ],
)
def test_faulty_roles_are_each_listed_once(tmp_path: Path, body: str) -> None:
    lab_path = lab(tmp_path, {"far": ("clean", "far"), "nocard": ("no_card", MACHINE)})
    report = dry(lab_path, script(tmp_path, body, THREE_ROLES))
    assert report["ok"] is False
    assert sorted(reasons(report), key=str) == sorted(
        [
            ("role", "absent", "not_configured"),
            ("role", "far", "unreachable"),
            ("role", "nocard", "no_card"),
        ],
        key=str,
    )
    assert report["trace"]["complete"] is True


# An exception class of a script's own whose `str` raises.
UNPRINTABLE = (
    "class Unprintable(Exception):\n    def __str__(self):\n        return self.missing\n\n"
)


def test_a_script_error_ends_the_trace(tmp_path: Path) -> None:
    lab_path = lab(tmp_path, {"mic": ("clean", MACHINE)})
    report = dry(lab_path, script(tmp_path, 'lab.role("mic").send(Check())\nmistake\n'))
    assert reasons(report) == [("trace", None, "script_error")]
    trace = report["trace"]
    assert isinstance(trace, dict)
    assert trace["complete"] is False and trace["commands"] == 1
    assert "NameError" in str(trace["stopped_at"]) and "script.py:" in str(trace["stopped_at"])


def test_an_exception_of_any_type_ends_the_trace(tmp_path: Path) -> None:
    body = (
        'lab.role("mic").send(Check())\n\n'
        "class Own(BaseException):\n    pass\n\n"
        "raise Own('mine')\n"
    )
    report = dry(lab(tmp_path, {"mic": ("clean", MACHINE)}), script(tmp_path, body))
    assert reasons(report) == [("trace", None, "script_error")]
    trace = report["trace"]
    assert isinstance(trace, dict)
    assert trace["complete"] is False and trace["commands"] == 1
    assert "Own: mine" in str(trace["stopped_at"])


def test_an_unprintable_exception_ends_the_trace(tmp_path: Path) -> None:
    body = 'lab.role("mic").send(Check())\n\n' + UNPRINTABLE + "raise Unprintable()\n"
    report = dry(lab(tmp_path, {"mic": ("clean", MACHINE)}), script(tmp_path, body))
    assert reasons(report) == [("trace", None, "script_error")]
    assert report["problems"][0]["detail"] == "Unprintable: <unprintable Unprintable>"
    assert "Unprintable: <unprintable Unprintable> at script.py:" in str(
        report["trace"]["stopped_at"]
    )


def test_a_capture_left_running(tmp_path: Path) -> None:
    lab_path = lab(tmp_path, {"mic": ("clean", MACHINE)})
    report = dry(lab_path, script(tmp_path, 'lab.role("mic").send(capture.Start())\n'))
    assert reasons(report) == [("role", "mic", "left_running")]


def test_an_uncaught_failure_is_listed_once(tmp_path: Path) -> None:
    lab_path = lab(tmp_path, {"mic": ("no_card", MACHINE)})
    report = dry(lab_path, EXAMPLE)
    assert reasons(report) == [("role", "mic", "no_card")]
    assert report["trace"]["complete"] is False


def test_an_undeclared_role(tmp_path: Path) -> None:
    lab_path = lab(tmp_path, {"mic": ("clean", MACHINE)})
    body = 'lab.role("mic").send(Check())\nlab.role("speaker")\n'
    report = dry(lab_path, script(tmp_path, body))
    assert [r for _, _, r in reasons(report)] == ["script_error"]
    assert "UndeclaredRole" in str(report["problems"])


def test_a_script_that_does_not_load(tmp_path: Path) -> None:
    lab_path = lab(tmp_path, {"mic": ("clean", MACHINE)})
    bad = tmp_path / "bad.py"
    for text, reason in [
        ("import nonexistent_module\n", "not_loadable"),
        ("import sys\nsys.exit(0)\n", "not_loadable"),
        ("class Own(BaseException):\n    pass\n\nraise Own('mine')\n", "not_loadable"),
        (UNPRINTABLE + "raise Unprintable()\n", "not_loadable"),
        ("ROLES = 3\ndef run(lab): pass\n", "bad_roles"),
        ("ROLES = {}\n", "bad_run"),
    ]:
        bad.write_text(text)
        report = dry(lab_path, bad)
        assert reasons(report) == [("script", None, reason)]

    bad.write_text("raise KeyboardInterrupt\n")
    with pytest.raises(KeyboardInterrupt):
        runner.dry_run(lab_path, MACHINE, bad)


def test_a_check_while_capturing_is_refused_in_both(tmp_path: Path) -> None:
    lab_path = lab(tmp_path, {"mic": ("clean", MACHINE)})
    body = 'mic = lab.role("mic")\nmic.send(capture.Start())\nmic.send(Check())\n'
    path = script(tmp_path, body)
    report = dry(lab_path, path)
    assert reasons(report) == [
        ("role", "mic", "already_capturing"),
        ("role", "mic", "left_running"),
    ]
    assert report["trace"]["complete"] is False
    _, _, results = do_run(tmp_path, lab_path, path)
    assert results["failure"]["reason"] == "already_capturing"


def test_a_script_cannot_send_release(tmp_path: Path) -> None:
    lab_path = lab(tmp_path, {"mic": ("clean", MACHINE)})
    body = 'mic = lab.role("mic")\nmic.send(capture.Start())\nmic.send(Release())\n'
    path = script(tmp_path, body)
    report = dry(lab_path, path)
    assert reasons(report) == [("trace", None, "script_error"), ("role", "mic", "left_running")]
    assert "RunnerCommand" in str(report["problems"])
    out, _, results = do_run(tmp_path, lab_path, path)
    failure = results["failure"]
    assert (failure["where"], failure["reason"]) == ("script", "script_error")
    assert "RunnerCommand" in failure["detail"]
    [capture] = results["captures"]
    assert capture["stopped_by"] == "runner"
    assert [e["by"] for e in journal(out) if e.get("command") == "release"] == ["runner"] * 2


def test_a_relative_out_is_taken_from_the_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lab_path = lab(tmp_path, {"mic": ("clean", MACHINE)})
    monkeypatch.chdir(tmp_path)
    pointer = runner.run(lab_path, MACHINE, EXAMPLE, str(EXAMPLE), Path("run"), None, HARNESS, None)
    assert isinstance(pointer, dict) and pointer["run"] == str(tmp_path / "run")
    results = json.loads((tmp_path / "run" / "results.json").read_text())
    assert results["captures"][0]["file"]["in_run"] is True


def test_a_dry_run_stopped_by_sigterm_kills_its_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    procs = recorders(monkeypatch)
    lab_path = lab(tmp_path, {"mic": ("silent", MACHINE)}, layout=layout_of("clean"))
    before = signal.getsignal(signal.SIGTERM)
    timer = threading.Timer(0.5, os.kill, (os.getpid(), signal.SIGTERM))
    timer.start()
    try:
        with pytest.raises(KeyboardInterrupt):
            runner.dry_run(lab_path, MACHINE, EXAMPLE)
    finally:
        timer.cancel()
    assert procs and all(p.returncode is not None for p in procs)
    assert signal.getsignal(signal.SIGTERM) is before


def test_a_capture_with_a_gap(tmp_path: Path) -> None:
    gap_line = line("loss", "capture_gap")
    stopped_line = line("loss", "capture_stopped")
    _, _, results = do_run(
        tmp_path, lab(tmp_path, {"mic": ("loss", MACHINE)}), script(tmp_path, START_WAIT_STOP)
    )
    assert results["outcome"] == "completed"
    [capture] = results["captures"]
    assert capture["gaps"] == [
        {
            "cause": gap_line["cause"],
            "at_frame": gap_line["at_frame"],
            "frames": gap_line["frames"],
            "at": reading(gap_line),
            "detail": gap_line["detail"],
        }
    ]
    assert capture["gaps_reported"] == stopped_line["gaps"]
    assert capture["loss_free"] is False


def test_a_late_block_is_recorded(tmp_path: Path) -> None:
    late_line = line("late", "capture_late")
    _, _, results = do_run(
        tmp_path, lab(tmp_path, {"mic": ("late", MACHINE)}), script(tmp_path, START_WAIT_STOP)
    )
    [capture] = results["captures"]
    assert capture["late"] == [
        {
            "at_frame": late_line["at_frame"],
            "backlog_frames": late_line["backlog_frames"],
            "at": reading(late_line),
        }
    ]
    assert capture["loss_free"] is True


def test_a_start_on_a_missing_card(tmp_path: Path) -> None:
    failed_line = line("no_card", "capture_failed")
    out, manifest, results = do_run(tmp_path, lab(tmp_path, {"mic": ("no_card", MACHINE)}), EXAMPLE)
    assert results["outcome"] == "failed" and results["completed"] is False
    assert results["recorded"]["captures_started"] == 0
    assert results["captures"] == []
    failure = results["failure"]
    assert (failure["where"], failure["role"], failure["reason"]) == ("role", "mic", "no_card")
    assert failure["data"]["cards"] == failed_line["cards"]
    assert [r["repository"] for r in manifest["repositories"]] == ["brenn-lab"]
    assert manifest["roles"]["mic"]["captures"] == []


@pytest.mark.parametrize(
    ("card", "reason", "by"),
    [
        ("stream_died", "stream_died", "event"),
        ("exits_mid_capture", "recorder_exited", "event"),
        ("exits_at_stop", "recorder_exited", "command"),
        ("write_stalled", "write_stalled", "command"),
        ("stops_unasked", "stopped_unasked", "event"),
        ("not_json", "recorder_contract", "event"),
        ("unknown_event", "recorder_contract", "event"),
    ],
)
def test_a_capture_that_fails(tmp_path: Path, card: str, reason: str, by: str) -> None:
    out, _, results = do_run(
        tmp_path, lab(tmp_path, {"mic": (card, MACHINE)}), script(tmp_path, START_WAIT_STOP)
    )
    assert results["outcome"] == "failed" and results["completed"] is False
    failure = results["failure"]
    assert (failure["where"], failure["role"], failure["reason"]) == ("role", "mic", reason)
    [capture] = results["captures"]
    assert capture["ended"] == "failed" and capture["loss_free"] is False
    assert capture["failure"]["reason"] == reason
    first = results["roles"]["mic"]["failures"][0]
    assert first["reason"] == reason
    if by == "event":
        assert first["event"] == "capture.failed"
        # The script's later stop is answered with the failure the capture failed with.
        stop_reply = [e for e in journal(out) if e.get("command") == "capture.stop" and "ok" in e]
        assert stop_reply[0]["failure"]["reason"] == reason
    else:
        assert first["command"] == "capture.stop"
    if card == "write_stalled":
        assert capture["failure"]["data"]["log_complete"] == "not_established"
    if card == "exits_mid_capture":
        assert capture["failure"]["data"]["exit_status"] == 1
    if card == "stops_unasked":
        stopped_line = line(card, "capture_stopped")
        assert capture["frames"] == stopped_line["frames"]
        assert capture["gaps_reported"] == stopped_line["gaps"]


def test_a_refused_command_line(tmp_path: Path) -> None:
    card = "names_no_transcript"
    layout = ("a", "b")
    expected = subprocess.run(
        [STAND_IN, "--card", card, "--rate", "16000", "--format", "s16_le"]
        + ["--layout", "a,b", "--pod-id", "mic", "--out", str(tmp_path / "x.framelog")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert expected.returncode == 2
    lab_path = lab(tmp_path, {"mic": (card, MACHINE)}, layout=layout)
    report = dry(lab_path, EXAMPLE)
    problems = report["problems"]
    assert isinstance(problems, list) and len(problems) == 1
    [problem] = problems
    assert isinstance(problem, dict) and problem["reason"] == "recorder_usage"
    assert problem["detail"] == expected.stderr.splitlines()[0]
    data = problem["data"]
    assert isinstance(data, dict) and data["stderr"] == expected.stderr
    _, _, results = do_run(tmp_path, lab_path, EXAMPLE)
    assert results["failure"]["reason"] == "recorder_usage"
    assert results["recorded"]["captures_started"] == 0


def test_a_comma_in_a_channel_name(tmp_path: Path) -> None:
    lab_path = lab(tmp_path, {"mic": ("clean", MACHINE)}, layout=("left,right",))
    report = dry(lab_path, EXAMPLE)
    assert reasons(report) == [("role", "mic", "recorder_usage")]
    _, _, results = do_run(tmp_path, lab_path, EXAMPLE)
    assert results["failure"]["reason"] == "recorder_usage"


def test_a_layout_other_than_the_one_sent(tmp_path: Path) -> None:
    other = tuple(f"{name}_x" for name in layout_of("clean"))
    lab_path = lab(tmp_path, {"mic": ("clean", MACHINE)}, layout=other)
    assert reasons(dry(lab_path, EXAMPLE)) == [("role", "mic", "recorder_contract")]


def test_a_caught_failure_then_a_script_error(tmp_path: Path) -> None:
    body = """\
    try:
        lab.role("mic").send(capture.Start())
    except RoleFailure:
        raise RuntimeError("giving up")
    """
    _, _, results = do_run(
        tmp_path, lab(tmp_path, {"mic": ("no_card", MACHINE)}), script(tmp_path, body)
    )
    assert results["failure"]["where"] == "role"
    assert results["failure"]["reason"] == "no_card"


def test_a_script_error_releases_the_capture(tmp_path: Path) -> None:
    body = 'lab.role("mic").send(capture.Start())\nraise RuntimeError("oops")\n'
    out, _, results = do_run(
        tmp_path, lab(tmp_path, {"mic": ("clean", MACHINE)}), script(tmp_path, body)
    )
    failure = results["failure"]
    assert (failure["where"], failure["reason"]) == ("script", "script_error")
    assert "RuntimeError: oops" in failure["detail"]
    [capture] = results["captures"]
    assert capture["ended"] == "stopped" and capture["stopped_by"] == "runner"
    assert "Traceback" in (out / "logs" / "runner.log").read_text()


def recorders(monkeypatch: pytest.MonkeyPatch) -> list[subprocess.Popen[bytes]]:
    """Every process the roles start from here on."""
    procs: list[subprocess.Popen[bytes]] = []
    real = subprocess.Popen

    class Recording(real):  # type: ignore[valid-type,misc]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            procs.append(self)

    monkeypatch.setattr(subprocess, "Popen", Recording)
    return procs


START_THEN_RAISE = 'lab.role("mic").send(capture.Start())\n'

RAISES = {
    "keyboard_interrupt": ("KeyboardInterrupt", "raise KeyboardInterrupt\n"),
    "base_exception": (
        "Own",
        "class Own(BaseException):\n    pass\n\nraise Own('mine')\n",
    ),
    "interrupted": (
        "Interrupted",
        "from brenn_lab.runner import Interrupted\n\nraise Interrupted\n",
    ),
    "forged_role_failure": (
        "RoleFailure",
        "from brenn_lab.data import Failure\n\n"
        "raise RoleFailure('mic', 'capture.start', Failure('made_up', 'by the script'))\n",
    ),
    "unprintable": ("Unprintable", UNPRINTABLE + "raise Unprintable()\n"),
}


@pytest.mark.parametrize("raised", sorted(RAISES))
def test_a_run_releases_its_roles_whatever_leaves_the_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raised: str
) -> None:
    name, raise_it = RAISES[raised]
    procs = recorders(monkeypatch)
    out, _, results = do_run(
        tmp_path,
        lab(tmp_path, {"mic": ("clean", MACHINE)}),
        script(tmp_path, START_THEN_RAISE + raise_it),
    )
    assert results["outcome"] == "failed" and results["completed"] is False
    failure = results["failure"]
    assert (failure["where"], failure["reason"]) == ("script", "script_error")
    assert failure["detail"].startswith(f"{name}:")
    [capture] = results["captures"]
    assert capture["ended"] == "stopped" and capture["stopped_by"] == "runner"
    entries = journal(out)
    assert [(e["command"], e["by"], "ok" in e) for e in entries[-2:]] == [
        ("release", "runner", False),
        ("release", "runner", True),
    ]
    assert "Traceback" in (out / "logs" / "runner.log").read_text()
    assert procs and all(p.poll() is not None for p in procs)


@pytest.mark.parametrize("refused", ["note", "journal"])
def test_a_release_does_not_wait_on_the_runners_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, refused: str
) -> None:
    """From the script's end on, the run directory refuses the runner's note
    of the exception, or every journal line.
    """
    procs = recorders(monkeypatch)
    ended = threading.Event()
    real_script_error = runner._Ledger.script_error
    real_write = getattr(RunDir, refused)

    def script_error(self: runner._Ledger, e: BaseException) -> bool:
        ended.set()
        return real_script_error(self, e)

    def write(self: RunDir, value: Any) -> None:
        if ended.is_set():
            raise OSError(28, "No space left on device")
        real_write(self, value)

    monkeypatch.setattr(runner._Ledger, "script_error", script_error)
    monkeypatch.setattr(RunDir, refused, write)
    out = tmp_path / "run"
    with pytest.raises(ExceptionGroup) as raised:
        runner.run(
            lab(tmp_path, {"mic": ("clean", MACHINE)}),
            MACHINE,
            script(tmp_path, START_THEN_RAISE + 'raise RuntimeError("oops")\n'),
            "script.py",
            out,
            None,
            HARNESS,
            None,
        )
    assert raised.value.exceptions and all(isinstance(e, OSError) for e in raised.value.exceptions)
    assert procs and all(p.poll() is not None for p in procs)
    assert not (out / "results.json").exists()
    assert (out / "manifest.json").is_file()


def test_a_release_does_not_wait_on_recording_the_scripts_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    procs = recorders(monkeypatch)

    def script_error(self: runner._Ledger, e: BaseException) -> bool:
        raise RuntimeError("the ledger failed")

    monkeypatch.setattr(runner._Ledger, "script_error", script_error)
    out = tmp_path / "run"
    with pytest.raises(ExceptionGroup) as raised:
        runner.run(
            lab(tmp_path, {"mic": ("clean", MACHINE)}),
            MACHINE,
            script(tmp_path, START_THEN_RAISE + 'raise ValueError("oops")\n'),
            "script.py",
            out,
            None,
            HARNESS,
            None,
        )
    assert [str(e) for e in raised.value.exceptions] == ["the ledger failed"]
    assert procs and all(p.poll() is not None for p in procs)
    assert journal(out)[-1]["command"] == "release"
    assert not (out / "results.json").exists()


def test_a_capture_left_running_is_released(tmp_path: Path) -> None:
    body = 'lab.role("mic").send(capture.Start())\n'
    out, _, results = do_run(
        tmp_path, lab(tmp_path, {"mic": ("clean", MACHINE)}), script(tmp_path, body)
    )
    failure = results["failure"]
    assert (failure["where"], failure["role"], failure["reason"]) == (
        "runner",
        "mic",
        "left_running",
    )
    [capture] = results["captures"]
    assert capture["stopped_by"] == "runner"
    assert capture["frames"] == line("clean", "capture_stopped")["frames"]
    released = journal(out)[-1]
    assert released["by"] == "runner" and released["result"]["stopped"]["capture"] == 1


def created(tmp_path: Path) -> set[str]:
    return {p.name for p in tmp_path.iterdir()}


def refused(
    tmp_path: Path, lab_path: Path, script_path: Path, out: Path, workspace: Path | None = None
) -> list[tuple[JsonValue, JsonValue]]:
    before = created(tmp_path)
    result = runner.run(
        lab_path, MACHINE, script_path, str(script_path), out, None, HARNESS, workspace
    )
    assert isinstance(result, Refused), result
    assert created(tmp_path) == before
    report = result.report()
    assert report["ok"] is False
    every_key(report)
    return [(p.where, p.reason) for p in result.problems]


def test_what_a_run_refuses(tmp_path: Path) -> None:
    lab_path = lab(tmp_path, {"mic": ("clean", MACHINE), "other": ("clean", MACHINE)})
    (tmp_path / "taken").mkdir()
    assert refused(tmp_path, lab_path, EXAMPLE, tmp_path / "taken") == [("out", "out_exists")]
    assert refused(tmp_path, lab_path, EXAMPLE, tmp_path / "no" / "run") == [
        ("out", "out_parent_missing")
    ]
    assert refused(tmp_path, lab_path, EXAMPLE, tmp_path / "run", workspace=tmp_path) == [
        ("out", "out_in_workspace")
    ]
    bad = tmp_path / "bad.py"
    bad.write_text("ROLES = {}\n")
    assert refused(tmp_path, lab_path, bad, tmp_path / "run") == [("script", "bad_run")]
    mismatched = script(tmp_path, "pass\n", '{"absent": "capture", "mic": "speaker"}')
    assert refused(tmp_path, lab_path, mismatched, tmp_path / "run") == [
        ("role", "not_configured"),
        ("role", "kind_mismatch"),
    ]
    missing = tmp_path / "missing"
    assert refused(tmp_path, lab_path, missing, tmp_path / "run") == [("arguments", "unreadable")]
    before = created(tmp_path)
    result = runner.run(
        lab_path, MACHINE, EXAMPLE, str(EXAMPLE), tmp_path / "run", missing, HARNESS, None
    )
    assert isinstance(result, Refused)
    assert [(p.where, p.reason) for p in result.problems] == [("arguments", "unreadable")]
    assert created(tmp_path) == before
    broken = tmp_path / "broken.toml"
    broken.write_text("schema = 1\nmachines = 3\n")
    assert refused(tmp_path, broken, EXAMPLE, tmp_path / "run")[0][0] == "lab"
    result = runner.run(
        lab_path, "elsewhere", EXAMPLE, str(EXAMPLE), tmp_path / "run", None, HARNESS, None
    )
    assert isinstance(result, Refused)
    assert [p.reason for p in result.problems] == ["unknown_machine"]


def test_a_role_on_an_unreachable_machine(tmp_path: Path) -> None:
    lab_path = lab(tmp_path, {"mic": ("clean", "far")})
    out, _, results = do_run(tmp_path, lab_path, EXAMPLE)
    failure = results["failure"]
    assert (failure["where"], failure["role"], failure["reason"]) == ("role", "mic", "unreachable")
    entries = journal(out)
    assert [(e["command"], "ok" in e) for e in entries] == [
        ("capture.start", False),
        ("capture.start", True),
    ]
    assert entries[1]["failure"]["reason"] == "unreachable"
    assert results["roles"]["mic"]["commands"] == 1


def test_a_run_interrupted_by_sigterm(tmp_path: Path) -> None:
    body = 'lab.role("mic").send(capture.Start())\nlab.wait(60.0)\n'
    lab_path = lab(tmp_path, {"mic": ("clean", MACHINE)})
    timer = threading.Timer(1.0, os.kill, (os.getpid(), signal.SIGTERM))
    timer.start()
    try:
        _, _, results = do_run(tmp_path, lab_path, script(tmp_path, body))
    finally:
        timer.cancel()
    assert results["outcome"] == "interrupted" and results["completed"] is False
    assert results["failure"]["where"] == "runner"
    assert results["failure"]["reason"] == "interrupted"
    [capture] = results["captures"]
    assert capture["ended"] == "stopped" and capture["stopped_by"] == "runner"


DRIVER = """\
import os, sys
from pathlib import Path
from brenn_lab import runner, rundir
from brenn_lab.buildinfo import Revision

die_after_create = sys.argv[5] == "die_after_create"
if die_after_create:
    def keep(self, section, name, data):
        os._exit(9)
    rundir.RunDir.keep = keep
runner.run(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), sys.argv[3], Path(sys.argv[4]),
           None, Revision(None, None, "unknown", "test"), None)
"""


def die(tmp_path: Path, lab_path: Path, script_path: Path, how: str) -> Path:
    out = tmp_path / "run"
    env = {k: v for k, v in os.environ.items() if k != "BUILD_WORKSPACE_DIRECTORY"}
    env["PYTHONPATH"] = os.pathsep.join(sys.path)
    done = subprocess.run(
        [sys.executable, "-c", DRIVER, str(lab_path), MACHINE, str(script_path), str(out), how],
        env=env,
        check=False,
        timeout=60,
    )
    assert done.returncode == 9
    return out


def test_a_runner_that_dies_at_once_leaves_its_gitignore(tmp_path: Path) -> None:
    lab_path = lab(tmp_path, {"mic": ("clean", MACHINE)})
    out = die(tmp_path, lab_path, EXAMPLE, "die_after_create")
    assert [p.name for p in out.iterdir()] == [".gitignore"]
    assert (out / ".gitignore").read_text() == "*\n"


def test_a_runner_that_dies_leaves_its_journal(tmp_path: Path) -> None:
    body = 'lab.role("mic").send(Check())\nimport os\nos._exit(9)\n'
    out = die(tmp_path, lab(tmp_path, {"mic": ("clean", MACHINE)}), script(tmp_path, body), "-")
    assert not (out / "results.json").exists()
    assert (out / ".gitignore").read_text() == "*\n"
    assert (out / "manifest.json").is_file()
    entries = journal(out)
    assert [(e["command"], "ok" in e) for e in entries] == [("check", False), ("check", True)]
