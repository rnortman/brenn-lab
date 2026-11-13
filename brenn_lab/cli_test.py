"""The entry point, `//brenn_lab:lab`, started as a program against the
stand-in recorder: what it prints on stdout and the exit status it gives.
"""

import json
import os
import re
import subprocess
import textwrap
from pathlib import Path
from typing import Any

import pytest

from brenn_lab.testing.transcript_files import layout_of

LAB = os.path.abspath(os.environ["LAB"])
STAND_IN = os.path.abspath(os.environ["STAND_IN"])
MACHINE = "bench"
PROBLEM_KEYS = {"where", "reason", "detail", "role", "command", "key", "data"}

SHORT = """\
mic = lab.role("mic")
mic.send(capture.Start())
lab.wait(0.2)
mic.send(capture.Stop())
"""


def lab_file(tmp_path: Path, card: str = "clean", extra: str = "") -> Path:
    path = tmp_path / "lab.toml"
    path.write_text(
        textwrap.dedent(f"""\
            schema = 1
            [machines.bench.programs]
            pod_capture = {json.dumps(STAND_IN)}
            [roles.mic]
            kind = "capture"
            machine = "bench"
            [roles.mic.device]
            card = {json.dumps(card)}
            rate_hz = 16000
            sample_format = "s16_le"
            layout = {json.dumps(layout_of("clean"))}
            """)
        + extra
    )
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


def document(done: subprocess.CompletedProcess[str]) -> Any:
    """The one JSON document on stdout, which holds nothing else."""
    assert done.stdout.endswith("\n") and done.stdout.count("\n") == 1, done.stdout
    return json.loads(done.stdout)


def dry_args(lab: Path, script: Path) -> list[str]:
    return ["dry-run", "--lab", str(lab), "--machine", MACHINE, "--script", str(script)]


def run_args(lab: Path, script: Path, out: Path) -> list[str]:
    return [
        "run",
        "--lab",
        str(lab),
        "--machine",
        MACHINE,
        "--script",
        str(script),
        "--out",
        str(out),
    ]


def only_problem(refusal: Any) -> Any:
    assert refusal.keys() == {"ok", "problems"} and refusal["ok"] is False
    [problem] = refusal["problems"]
    assert problem.keys() == PROBLEM_KEYS
    return problem


def test_a_dry_run_and_a_run(tmp_path: Path) -> None:
    lab, script = lab_file(tmp_path), script_file(tmp_path)
    done = start(dry_args(lab, script))
    assert done.returncode == 0, done.stderr
    report = document(done)
    assert report["ok"] is True and report["problems"] == []

    out = tmp_path / "run"
    done = start(run_args(lab, script, out))
    assert done.returncode == 0, done.stderr
    assert document(done) == {
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
    pointer = document(done)
    assert (pointer["outcome"], pointer["completed"]) == ("failed", False)
    assert (out / "results.json").is_file()


def test_a_dry_run_with_a_problem_reports_it(tmp_path: Path) -> None:
    lab = lab_file(tmp_path, extra="surprise = 1\n")
    done = start(dry_args(lab, script_file(tmp_path)))
    assert done.returncode == 1, done.stderr
    report = document(done)
    assert report["ok"] is False
    assert [(p["where"], p["reason"]) for p in report["problems"]] == [("lab", "unknown_key")]


def test_a_dry_run_whose_script_does_not_exist_is_refused(tmp_path: Path) -> None:
    done = start(dry_args(lab_file(tmp_path), tmp_path / "missing.py"))
    assert done.returncode == 2
    problem = only_problem(document(done))
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
    ],
)
def test_a_command_line_the_parser_refuses(tmp_path: Path, args: list[str]) -> None:
    done = start(args)
    assert done.returncode == 2
    problem = only_problem(document(done))
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
    assert document(done)["run"] == str(tmp_path / "run")
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
    problem = only_problem(document(done))
    assert (problem["where"], problem["reason"]) == ("out", "out_in_workspace")
    assert not out.exists()
