"""The entry point, `//brenn_lab:lab`, started as a program against the
stand-in recorder: what it prints on stdout and the exit status it gives.
"""

import json
import os
import re
import subprocess
import textwrap
import wave
from pathlib import Path
from typing import Any

import pytest

from brenn_lab import marker
from brenn_lab.testing import scenes
from brenn_lab.testing.lab_driver import document, dry_args, lab_toml, run_args
from brenn_lab.testing.scenes import Play

LAB = os.path.abspath(os.environ["LAB"])
STAND_IN = os.path.abspath(os.environ["STAND_IN"])
EXPORT_STAND_IN = os.path.abspath(os.environ["EXPORT_STAND_IN"])
PROBLEM_KEYS = {"where", "reason", "detail", "role", "command", "key", "data"}

SHORT = """\
mic = lab.role("mic")
mic.send(capture.Start())
lab.wait(0.2)
mic.send(capture.Stop())
"""


def lab_file(tmp_path: Path, card: str = "clean", extra: str = "") -> Path:
    path = tmp_path / "lab.toml"
    path.write_text(lab_toml(json.dumps(STAND_IN), card) + extra)
    return path


def script_file(tmp_path: Path, body: str = SHORT) -> Path:
    path = tmp_path / "script.py"
    path.write_text(
        "from brenn_lab import capture\n"
        "from brenn_lab.script import Lab\n\n"
        'ROLES = {"mic": capture.KIND}\n\n\n'
        "def run(lab: Lab) -> None:\n" + textwrap.indent(body, "    ")
    )
    return path


def start(args: list[str], **env: str) -> subprocess.CompletedProcess[str]:
    """Start the entry point with `args`, outside `bazel run` unless `env`
    gives its variables.
    """
    environment = {
        k: v
        for k, v in os.environ.items()
        if k not in ("BUILD_WORKSPACE_DIRECTORY", "BUILD_WORKING_DIRECTORY")
    }
    environment.update(env)
    return subprocess.run(
        [LAB, *args], env=environment, capture_output=True, text=True, timeout=120, check=False
    )


def only_problem(refusal: Any) -> Any:
    assert refusal.keys() == {"ok", "problems"} and refusal["ok"] is False
    [problem] = refusal["problems"]
    assert problem.keys() == PROBLEM_KEYS
    return problem


def test_a_dry_run_and_a_run(tmp_path: Path) -> None:
    lab, script = lab_file(tmp_path), script_file(tmp_path)
    done = start(dry_args(lab, script))
    assert done.returncode == 0, done.stderr
    report = document(done.stdout)
    assert report["ok"] is True and report["problems"] == []

    out = tmp_path / "run"
    done = start(run_args(lab, script, out))
    assert done.returncode == 0, done.stderr
    assert document(done.stdout) == {
        "run": str(out),
        "outcome": "completed",
        "completed": True,
        "results": str(out / "results.json"),
    }
    manifest = json.loads((out / "manifest.json").read_text())
    harness = manifest["harness"]
    assert harness["repository"] == "brenn-lab"
    if harness["source"] == "git":
        assert harness.keys() == {"repository", "commit", "dirty", "source"}
        assert re.fullmatch("[0-9a-f]{40}|[0-9a-f]{64}", harness["commit"])
        assert isinstance(harness["dirty"], bool)
    else:
        assert harness.keys() == {"repository", "commit", "dirty", "source", "reason"}
        assert (harness["commit"], harness["dirty"], harness["source"]) == (None, None, "unknown")
        # This build ran the status command `.bazelrc` names; an empty stamp
        # means the stamping is broken, not that the tree is not a checkout.
        assert harness["reason"] != "the build ran no workspace status command"
    assert manifest["repositories"][0] == {**harness, "from": "the harness's build stamp"}
    assert manifest["script"]["given_as"] == str(script)


def test_a_run_that_does_not_complete(tmp_path: Path) -> None:
    out = tmp_path / "run"
    done = start(run_args(lab_file(tmp_path, card="no_card"), script_file(tmp_path), out))
    assert done.returncode == 1, done.stderr
    pointer = document(done.stdout)
    assert (pointer["outcome"], pointer["completed"]) == ("failed", False)
    assert (out / "results.json").is_file()


def test_a_dry_run_with_a_problem_reports_it(tmp_path: Path) -> None:
    lab = lab_file(tmp_path, extra="surprise = 1\n")
    done = start(dry_args(lab, script_file(tmp_path)))
    assert done.returncode == 1, done.stderr
    report = document(done.stdout)
    assert report["ok"] is False
    assert [(p["where"], p["reason"]) for p in report["problems"]] == [("lab", "unknown_key")]


def test_a_dry_run_whose_script_does_not_exist_is_refused(tmp_path: Path) -> None:
    done = start(dry_args(lab_file(tmp_path), tmp_path / "missing.py"))
    assert done.returncode == 2
    problem = only_problem(document(done.stdout))
    assert (problem["where"], problem["reason"]) == ("arguments", "unreadable")


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["frobnicate"],
        ["dry-run", "--lab", "lab.toml"],
        ["run", "--lab", "a", "--machine", "m", "--script", "s"],
        ["dry-run", "--lab", "a", "--machine", "m", "--script", "s", "--out", "o"],
        ["dry-run", "--lab", "a", "--machine", "m", "--script", "s", "-h"],
        ["--help"],
        ["run", "--lab", "a", "--machine", "m", "--script", "s", "--ou", "o"],
        ["mark", "--source", "a.wav", "--out", "b.wav"],
        ["mark", "--source", "a.wav", "--out", "b.wav", "--member", "one"],
        ["align"],
        ["align", "--run", "r", "--frame", "f"],
        ["align", "--run", "r", "--member", "1"],
    ],
    ids=[
        "nothing",
        "unknown_command",
        "missing_flags",
        "run_without_out",
        "dry_run_with_out",
        "help",
        "top_level_help",
        "abbreviated_flag",
        "mark_without_member",
        "mark_member_not_an_integer",
        "align_without_run",
        "align_abbreviated_flag",
        "align_with_a_mark_flag",
    ],
)
def test_a_command_line_the_parser_refuses(tmp_path: Path, args: list[str]) -> None:
    done = start(args)
    assert done.returncode == 2
    problem = only_problem(document(done.stdout))
    assert (problem["where"], problem["reason"]) == ("arguments", "usage")
    assert problem["detail"]
    assert "usage:" in done.stderr


def test_an_interrupt_in_the_trace_gives_status_3(tmp_path: Path) -> None:
    script = script_file(
        tmp_path, 'lab.role("mic").send(capture.Start())\nraise KeyboardInterrupt\n'
    )
    done = start(dry_args(lab_file(tmp_path), script))
    assert done.returncode == 3
    assert done.stdout == ""
    assert "Traceback" in done.stderr and "KeyboardInterrupt" in done.stderr


def test_relative_paths_under_bazel_run(tmp_path: Path) -> None:
    lab_file(tmp_path)
    script_file(tmp_path)
    (tmp_path / "notes.md").write_text("why this run\n")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    done = start(
        [*run_args(Path("lab.toml"), Path("script.py"), Path("run")), "--notes", "notes.md"],
        BUILD_WORKING_DIRECTORY=str(tmp_path),
        BUILD_WORKSPACE_DIRECTORY=str(workspace),
    )
    assert done.returncode == 0, done.stderr
    assert document(done.stdout)["run"] == str(tmp_path / "run")
    manifest = json.loads((tmp_path / "run" / "manifest.json").read_text())
    assert manifest["script"]["given_as"] == "script.py"
    assert (tmp_path / "run" / "notes" / "notes.md").read_text() == "why this run\n"


def test_a_run_whose_runner_raises_gives_status_3(tmp_path: Path) -> None:
    """The script makes the results file's place a directory, so the runner's
    write of it raises.
    """
    out = tmp_path / "run"
    script = script_file(tmp_path, SHORT + f"import os\nos.mkdir({str(out / 'results.json')!r})\n")
    done = start(run_args(lab_file(tmp_path), script, out))
    assert done.returncode == 3
    assert done.stdout == ""
    assert "Traceback" in done.stderr
    assert not (out / "results.json").is_file()


def test_a_run_inside_the_workspace_is_refused(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    out = workspace / "run"
    done = start(
        run_args(lab_file(tmp_path), script_file(tmp_path), out),
        BUILD_WORKSPACE_DIRECTORY=str(workspace),
    )
    assert done.returncode == 2
    problem = only_problem(document(done.stdout))
    assert (problem["where"], problem["reason"]) == ("out", "out_in_workspace")
    assert not out.exists()


def source_wav(path: Path, frames: int = 4000, rate_hz: int = 16000, channels: int = 2) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate_hz)
        w.writeframes(bytes(2 * channels * frames))
    return path


def test_mark_writes_a_marked_source(tmp_path: Path) -> None:
    source, out = source_wav(tmp_path / "in.wav"), tmp_path / "out.wav"
    done = start(["mark", "--source", str(source), "--out", str(out), "--member", "7"])
    assert done.returncode == 0, done.stderr
    got = document(done.stdout)
    assert got.keys() == {"marked", "source", "marker"}
    assert got["marked"] == str(out)
    assert got["source"] == {
        "frames": 4000,
        "rate_hz": 16000,
        "channels": 2,
        "sample_format": "s16_le",
    }
    start_code, end_code = marker.codes(7)
    carried = got["marker"]
    assert (carried["definition"], carried["member"]) == (marker.DEFINITION, 7)
    assert (carried["start_code"], carried["end_code"]) == (start_code, end_code)
    with wave.open(str(out), "rb") as w:
        total = w.getnframes()
        assert (w.getnchannels(), w.getframerate(), w.getsampwidth()) == (2, 16000, 2)
    spans = marker.layout(16000, total)
    assert spans is not None
    assert carried["start"] == {"frame": spans[0].frame, "frames": spans[0].frames}
    assert carried["end"] == {"frame": spans[1].frame, "frames": spans[1].frames}


def test_mark_relative_paths_under_bazel_run(tmp_path: Path) -> None:
    source_wav(tmp_path / "in.wav")
    done = start(
        ["mark", "--source", "in.wav", "--out", "out.wav", "--member", "0"],
        BUILD_WORKING_DIRECTORY=str(tmp_path),
    )
    assert done.returncode == 0, done.stderr
    assert document(done.stdout)["marked"] == str(tmp_path / "out.wav")
    assert (tmp_path / "out.wav").is_file()


@pytest.mark.parametrize(
    ("setup", "member", "reason"),
    [
        ("good", "511", "member_range"),
        ("good", "-1", "member_range"),
        ("missing", "0", "unreadable"),
        ("not_wav", "0", "source_format"),
        ("slow", "0", "rate_unsupported"),
        ("out_exists", "0", "out_exists"),
    ],
)
def test_mark_refusals_write_nothing(tmp_path: Path, setup: str, member: str, reason: str) -> None:
    source, out = tmp_path / "in.wav", tmp_path / "out.wav"
    if setup in ("good", "out_exists"):
        source_wav(source)
    elif setup == "not_wav":
        source.write_text("not a wav\n")
    elif setup == "slow":
        source_wav(source, rate_hz=4000)
    if setup == "out_exists":
        out.write_text("kept\n")
    done = start(["mark", "--source", str(source), "--out", str(out), "--member", member])
    assert done.returncode == 2, done.stderr
    problem = only_problem(document(done.stdout))
    assert (problem["where"], problem["reason"]) == ("mark", reason)
    assert problem["detail"]
    if setup == "out_exists":
        assert out.read_text() == "kept\n"
    else:
        assert not out.exists()


def align_args(run: Path) -> list[str]:
    return ["align", "--run", str(run), "--frame-log", EXPORT_STAND_IN]


def test_align_a_run_whose_marked_play_is_found(tmp_path: Path) -> None:
    built = scenes.build(tmp_path / "run", [Play("speaker", 1, 3, at=5000.0)])
    done = start(align_args(built.run))
    assert done.returncode == 0, done.stderr
    got = document(done.stdout)
    assert got.keys() == {"run", "report", "ok", "summary"}
    assert got["run"] == str(built.run)
    assert got["report"] == str(built.run / "alignment.json")
    assert got["ok"] is True
    written = json.loads((built.run / "alignment.json").read_text())
    assert got["summary"] == written["summary"]
    assert (got["summary"]["marked"], got["summary"]["located"]) == (1, 1)
    assert written["reader"] == {"program": "frame-log", "pinned": False, "path": EXPORT_STAND_IN}
    harness = written["harness"]
    assert harness["repository"] == "brenn-lab"
    assert harness["source"] in ("git", "unknown")


def test_align_a_run_with_no_marked_play(tmp_path: Path) -> None:
    built = scenes.build(tmp_path / "run", [Play("speaker", 1, None, at=5000.0)])
    done = start(align_args(built.run))
    assert done.returncode == 0, done.stderr
    got = document(done.stdout)
    assert got["ok"] is True
    assert (got["summary"]["marked"], got["summary"]["captures"]) == (0, 1)


def test_align_a_run_whose_marker_is_not_found(tmp_path: Path) -> None:
    built = scenes.build(tmp_path / "run", [Play("speaker", 1, 1, at=500000.0)])
    done = start(align_args(built.run))
    assert done.returncode == 1, done.stderr
    got = document(done.stdout)
    assert got["ok"] is False
    assert got["summary"]["located"] == 0
    assert (built.run / "alignment.json").is_file()


def test_align_relative_run_under_bazel_run(tmp_path: Path) -> None:
    scenes.build(tmp_path / "run", [Play("speaker", 1, 3, at=5000.0)])
    done = start(
        ["align", "--run", "run", "--frame-log", EXPORT_STAND_IN],
        BUILD_WORKING_DIRECTORY=str(tmp_path),
    )
    assert done.returncode == 0, done.stderr
    assert document(done.stdout)["report"] == str(tmp_path / "run" / "alignment.json")


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        ("no_directory", "unreadable"),
        ("no_manifest", "unreadable"),
        ("not_json", "unreadable"),
        ("layout_2", "layout"),
        ("read_only", "not_writable"),
    ],
)
def test_align_refusals_write_no_report(tmp_path: Path, setup: str, reason: str) -> None:
    run = tmp_path / "run"
    if setup == "read_only":
        scenes.build(run, [Play("speaker", 1, 3, at=5000.0)])
    elif setup != "no_directory":
        run.mkdir()
    if setup == "not_json":
        (run / "manifest.json").write_text("{")
    elif setup == "layout_2":
        (run / "manifest.json").write_text(json.dumps({"layout": 2, "roles": {}}))
    if setup == "read_only":
        run.chmod(0o555)
        assert not os.access(run, os.W_OK), "the test's process can write a 0o555 directory"
    try:
        done = start(align_args(run))
    finally:
        if setup == "read_only":
            run.chmod(0o755)
    assert done.returncode == 2, done.stderr
    assert "Traceback" not in done.stderr
    problem = only_problem(document(done.stdout))
    assert (problem["where"], problem["reason"]) == ("run", reason)
    assert not (run / "alignment.json").exists()
