"""The entry point, `//brenn_lab:lab`, started as a program against the
stand-in recorder: what it prints on stdout and the exit status it gives.
"""

import hashlib
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
PROBLEM_KEYS = {"where", "reason", "detail", "role", "command", "key", "data", "log"}

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
        "usability": {
            "answer": "findings",
            "complete": False,
            "assessment": str(out / "assessment.json"),
        },
    }
    assessed = json.loads((out / "assessment.json").read_text())
    assert (assessed["answer"], assessed["complete"]) == ("findings", False)
    # The stand-in recorder's file is not a frame log, so the pinned reader
    # cannot read it.
    assert [f["finding"] for f in assessed["findings"]] == ["capture_unreadable"]
    assert assessed["reader"]["pinned"] is True
    assert (out / "alignment.json").is_file()
    assert not (out / ".alignment.tmp").exists()
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
    # Built as the root module, the runner's stamp names no other builder.
    assert manifest["builder"] is None
    assert manifest["script"]["given_as"] == str(script)


PRINTS = """\
import os
import sys

from brenn_lab import capture
from brenn_lab.script import Lab

ROLES = {"mic": capture.KIND}

print("loaded, printed")
print("loaded, printed to stderr", file=sys.stderr)
os.write(1, b"loaded, written\\n")
os.write(2, b"loaded, written to stderr\\n")


def run(lab: Lab) -> None:
    print("ran, printed")
    print("ran, printed to stderr", file=sys.stderr)
    os.write(1, b"ran, written\\n")
    os.write(2, b"ran, written to stderr\\n")
    mic = lab.role("mic")
    mic.send(capture.Start())
    mic.send(capture.Stop())
"""

PRINTED_OUT = "loaded, printed\nloaded, written\nran, printed\nran, written\n"
PRINTED_ERR = (
    "loaded, printed to stderr\nloaded, written to stderr\n"
    "ran, printed to stderr\nran, written to stderr\n"
)


def test_what_a_script_prints_is_kept_in_a_run_and_is_on_stderr_in_a_dry_run(
    tmp_path: Path,
) -> None:
    lab, script = lab_file(tmp_path), tmp_path / "script.py"
    script.write_text(PRINTS)
    out = tmp_path / "run"
    done = start(run_args(lab, script, out))
    assert done.returncode == 0, done.stderr
    assert document(done.stdout)["completed"] is True
    assert done.stderr == ""
    assert (out / "logs" / "lab.stdout.log").read_text() == PRINTED_OUT
    assert (out / "logs" / "lab.stderr.log").read_text() == PRINTED_ERR

    dry = start(dry_args(lab, script))
    assert dry.returncode == 0, dry.stderr
    assert document(dry.stdout)["ok"] is True
    assert dry.stderr == PRINTED_OUT + PRINTED_ERR

    refused = start(run_args(lab, script, out))
    assert refused.returncode == 2
    assert only_problem(document(refused.stdout))["reason"] == "out_exists"
    assert refused.stderr == "loaded, printed\nloaded, written\n" + (
        "loaded, printed to stderr\nloaded, written to stderr\n"
    )


def test_a_run_that_does_not_complete(tmp_path: Path) -> None:
    out = tmp_path / "run"
    done = start(run_args(lab_file(tmp_path, card="no_card"), script_file(tmp_path), out))
    assert done.returncode == 1, done.stderr
    assert done.stderr == (
        "lab run: the run did not complete (outcome failed): see "
        f"{out / 'results.json'} and the logs in {out / 'logs'}\n"
    )
    pointer = document(done.stdout)
    assert (pointer["outcome"], pointer["completed"]) == ("failed", False)
    assert (out / "results.json").is_file()
    assert pointer["usability"]["answer"] == "findings"
    assessed = json.loads((out / "assessment.json").read_text())
    assert assessed["findings"][0]["finding"] == "run_not_completed"


# The script replaces the capture role's release with one that stops the
# capture and then raises, so the stop's reply never reaches the run.
RELEASE_RAISES = """\
from brenn_lab.capture.role import CaptureRole

released = CaptureRole._release


def release(self, dry):
    released(self, dry)
    raise RuntimeError("the release went wrong")


CaptureRole._release = release
lab.role("mic").send(capture.Start())
lab.wait(0.2)
"""


def test_a_run_whose_role_raises_at_release_has_a_result(tmp_path: Path) -> None:
    out = tmp_path / "run"
    done = start(run_args(lab_file(tmp_path), script_file(tmp_path, RELEASE_RAISES), out))
    assert done.returncode == 1, done.stderr
    assert done.stderr == (
        "lab run: the run did not complete (outcome failed): see "
        f"{out / 'results.json'} and the logs in {out / 'logs'}\n"
    )
    pointer = document(done.stdout)
    assert (pointer["outcome"], pointer["completed"]) == ("failed", False)
    assert pointer["results"] == str(out / "results.json")
    results = json.loads((out / "results.json").read_text())
    failure = results["failure"]
    assert (failure["reason"], failure["role"], failure["command"]) == (
        "role_raised",
        "mic",
        "release",
    )
    assert failure["log"] == {"machine": "bench", "path": "logs/runner.log", "in_run": True}
    [capture] = results["captures"]
    assert (capture["ended"], capture["failure"]["reason"]) == ("failed", "end_not_reported")
    runner_log = (out / "logs" / "runner.log").read_text()
    assert "role mic raised on release:" in runner_log
    assert "the release went wrong" in runner_log
    assert pointer["usability"]["assessment"] == str(out / "assessment.json")
    assessed = json.loads((out / "assessment.json").read_text())
    assert assessed["findings"][0]["finding"] == "run_not_completed"


def test_a_run_assessed_with_another_reader(tmp_path: Path) -> None:
    out = tmp_path / "run"
    args = run_args(lab_file(tmp_path), script_file(tmp_path), out)
    done = start([*args, "--frame-log", EXPORT_STAND_IN])
    assert done.returncode == 0, done.stderr
    usability = document(done.stdout)["usability"]
    assert (usability["answer"], usability["complete"]) == ("findings", False)
    assessed = json.loads((out / "assessment.json").read_text())
    assert [f["finding"] for f in assessed["findings"]] == ["capture_unreadable"]
    assert assessed["reader"] == {"program": "frame-log", "pinned": False, "path": EXPORT_STAND_IN}


@pytest.mark.parametrize("given", ["absent", "not_executable", "directory"])
def test_a_run_whose_frame_log_is_not_an_executable_file_is_refused(
    tmp_path: Path, given: str
) -> None:
    reader = tmp_path / "frame-log"
    if given == "not_executable":
        reader.write_text("#!/bin/sh\n")
    elif given == "directory":
        reader.mkdir()
    out = tmp_path / "run"
    args = run_args(lab_file(tmp_path), script_file(tmp_path), out)
    done = start([*args, "--frame-log", str(reader)])
    assert done.returncode == 2, done.stderr
    problem = only_problem(document(done.stdout))
    assert (problem["where"], problem["reason"]) == ("arguments", "unreadable")
    assert str(reader) in problem["detail"]
    assert not out.exists()


def test_an_interrupted_run_is_not_assessed(tmp_path: Path) -> None:
    script = script_file(
        tmp_path,
        SHORT + "import os, signal\nos.kill(os.getpid(), signal.SIGTERM)\nlab.wait(30.0)\n",
    )
    out = tmp_path / "run"
    done = start(run_args(lab_file(tmp_path), script, out))
    assert done.returncode == 1, done.stderr
    pointer = document(done.stdout)
    assert pointer["outcome"] == "interrupted"
    usability = pointer["usability"]
    assert usability.keys() == {"answer", "reason", "detail"}
    assert (usability["answer"], usability["reason"]) == ("not_assessed", "interrupted")
    assert not (out / "assessment.json").exists()
    assert not (out / "alignment.json").exists()


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
        ["assess"],
        ["assess", "--run", "r", "--frame", "f"],
        ["run", "--lab", "a", "--machine", "m", "--script", "s", "--out", "o", "--frame", "f"],
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
        "assess_without_run",
        "assess_abbreviated_flag",
        "run_abbreviated_frame_log",
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
    assert got.keys() == {"marked", "source", "marker", "marker_channels"}
    assert got["marker_channels"] == [0, 1]
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


def test_mark_on_stated_channels(tmp_path: Path) -> None:
    source, out = source_wav(tmp_path / "in.wav", channels=6), tmp_path / "out.wav"
    args = ["--source", str(source), "--out", str(out), "--member", "7"]
    done = start(["mark", *args, "--channels", "4", "2"])
    assert done.returncode == 0, done.stderr
    got = document(done.stdout)
    assert got["marker_channels"] == [2, 4]
    assert got["source"]["channels"] == 6
    with wave.open(str(out), "rb") as w:
        assert w.getnchannels() == 6


@pytest.mark.parametrize(
    ("channels", "why"),
    [(["6"], "channel_range"), (["-1"], "channel_range"), (["1", "1"], "channel_repeated")],
)
def test_mark_refuses_channels_it_cannot_write(
    tmp_path: Path, channels: list[str], why: str
) -> None:
    source, out = source_wav(tmp_path / "in.wav", channels=6), tmp_path / "out.wav"
    args = ["--source", str(source), "--out", str(out), "--member", "7"]
    done = start(["mark", *args, "--channels", *channels])
    assert done.returncode == 2
    problem = only_problem(document(done.stdout))
    assert (problem["where"], problem["reason"]) == ("mark", why)
    assert not out.exists()


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
        ("layout_3", "layout"),
        ("layout_4", "layout"),
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
    elif setup.startswith("layout_"):
        layout = int(setup.removeprefix("layout_"))
        (run / "manifest.json").write_text(json.dumps({"layout": layout, "roles": {}}))
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


def assess_args(run: Path) -> list[str]:
    return ["assess", "--run", str(run), "--frame-log", EXPORT_STAND_IN]


@pytest.mark.parametrize(
    ("plays", "status", "answer", "counts"),
    [
        ([Play("speaker", 1, 3, at=5000.0)], 0, "clean", (0, 0)),
        ([Play("speaker", 1, 1, at=500000.0)], 1, "findings", (1, 0)),
        ([Play("speaker", 1, 3, at=5000.0, record=False)], 1, "incomplete", (0, 1)),
    ],
    ids=["clean", "findings", "incomplete"],
)
def test_assess_a_run(
    tmp_path: Path, plays: list[Play], status: int, answer: str, counts: tuple[int, int]
) -> None:
    built = scenes.build(tmp_path / "run", plays)
    done = start(assess_args(built.run))
    assert done.returncode == status, done.stderr
    got = document(done.stdout)
    assert got == {
        "run": str(built.run),
        "assessment": str(built.run / "assessment.json"),
        "alignment": str(built.run / "alignment.json"),
        "answer": answer,
        "complete": counts[1] == 0,
        "findings": counts[0],
        "not_assessed": counts[1],
    }
    written = json.loads((built.run / "assessment.json").read_text())
    assert written["answer"] == answer
    assert written["reader"] == {"program": "frame-log", "pinned": False, "path": EXPORT_STAND_IN}
    assert written["harness"]["repository"] == "brenn-lab"
    assert (built.run / "alignment.json").is_file()


def test_assess_twice_names_the_report_replaced(tmp_path: Path) -> None:
    built = scenes.build(tmp_path / "run", [Play("speaker", 1, 3, at=5000.0)])
    assert start(assess_args(built.run)).returncode == 0
    first = (built.run / "assessment.json").read_bytes()
    done = start(assess_args(built.run))
    assert done.returncode == 0, done.stderr
    replaces = json.loads((built.run / "assessment.json").read_text())["replaces"]
    assert replaces["sha256"] == hashlib.sha256(first).hexdigest()


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        ("no_manifest", "unreadable"),
        ("results_without_restored", "unreadable"),
        ("layout_2", "layout"),
        ("layout_3", "layout"),
        ("layout_4", "layout"),
        ("read_only", "not_writable"),
    ],
)
def test_assess_refusals_write_nothing(tmp_path: Path, setup: str, reason: str) -> None:
    run = tmp_path / "run"
    if setup in ("read_only", "results_without_restored"):
        scenes.build(run, [Play("speaker", 1, 3, at=5000.0)])
    else:
        run.mkdir()
    if setup.startswith("layout_"):
        layout = int(setup.removeprefix("layout_"))
        (run / "manifest.json").write_text(json.dumps({"layout": layout, "roles": {}}))
    elif setup == "results_without_restored":
        results = json.loads((run / "results.json").read_text())
        del results["sources"][0]["restored"]
        (run / "results.json").write_text(json.dumps(results))
    if setup == "read_only":
        run.chmod(0o555)
        assert not os.access(run, os.W_OK), "the test's process can write a 0o555 directory"
    try:
        done = start(assess_args(run))
    finally:
        if setup == "read_only":
            run.chmod(0o755)
    assert done.returncode == 2, done.stderr
    assert "Traceback" not in done.stderr
    problem = only_problem(document(done.stdout))
    assert (problem["where"], problem["reason"]) == ("run", reason)
    assert not (run / "assessment.json").exists()
    assert not (run / "alignment.json").exists()
