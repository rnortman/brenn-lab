"""The `lab` process's own output in a run and a dry run, in this process:
descriptors 1 and 2 kept in the run's logs from before the script is loaded,
written to stderr when no run directory was made, the tracebacks of threads
noted in `logs/runner.log`, and the one line on stderr after a run.
"""

import errno
import io
import json
import os
import sys
import threading
from collections.abc import Buffer
from pathlib import Path
from typing import Any

import pytest

from brenn_lab import runner, streams
from brenn_lab.buildinfo import Revision
from brenn_lab.rundir import RunDir
from brenn_lab.runner import Refused
from brenn_lab.testing.lab_driver import lab_toml
from brenn_lab.testing.run_logs import runner_log

STAND_IN = os.path.abspath(os.environ["STAND_IN"])
MACHINE = "bench"
HARNESS = Revision(commit="0123456789abcdef0123456789abcdef01234567", dirty=False, source="git")
PINNED = {"pod_capture": "/nonexistent/pinned/pod-capture"}

SAYS = """\
import os
import threading

from brenn_lab import capture
from brenn_lab.script import Lab

ROLES = {"mic": capture.KIND}

os.write(1, b"loaded out\\n")
os.write(2, b"loaded err\\n")


def run(lab: Lab) -> None:
    os.write(1, b"ran out\\n")
    os.write(2, b"ran err\\n")
    mic = lab.role("mic")
    mic.send(capture.Start())
    mic.send(capture.Stop())
"""


def lab(tmp_path: Path) -> Path:
    path = tmp_path / "lab.toml"
    path.write_text(lab_toml(json.dumps(STAND_IN), "clean"))
    return path


def script(tmp_path: Path, tail: str = "") -> Path:
    path = tmp_path / "script.py"
    path.write_text(SAYS + tail)
    return path


def start(tmp_path: Path, script_path: Path, out: Path | None = None) -> Any:
    out = tmp_path / "run" if out is None else out
    return runner.run(
        lab(tmp_path), MACHINE, script_path, str(script_path), out, None, HARNESS, None, PINNED
    )


def results(out: Path) -> Any:
    return json.loads((out / "results.json").read_text())


def kept(out: Path, stream: str) -> str:
    return (out / "logs" / f"lab.{stream}.log").read_text()


def test_a_completed_run_keeps_what_the_process_wrote_and_says_nothing(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    hook = threading.excepthook
    pointer = start(tmp_path, script(tmp_path))
    assert isinstance(pointer, dict) and pointer["completed"] is True
    out = tmp_path / "run"
    assert kept(out, "stdout") == "loaded out\nran out\n"
    assert kept(out, "stderr") == "loaded err\nran err\n"
    assert capfd.readouterr() == ("", "")
    assert threading.excepthook is hook
    [started] = runner_log(out)
    assert started["text"] == "run started" and started["at"]["machine"] == MACHINE


def test_a_dry_run_writes_what_was_held_to_stderr(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    report = runner.dry_run(lab(tmp_path), MACHINE, script(tmp_path), PINNED)
    assert isinstance(report, dict) and report["ok"] is True
    assert capfd.readouterr() == ("", "loaded out\nran out\nloaded err\nran err\n")


def test_a_refused_run_writes_what_was_held_to_stderr(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    taken = tmp_path / "taken"
    taken.mkdir()
    refused = start(tmp_path, script(tmp_path), taken)
    assert isinstance(refused, Refused)
    assert [p.reason for p in refused.problems] == ["out_exists"]
    assert capfd.readouterr() == ("", "loaded out\nloaded err\n")


def test_a_run_whose_logs_cannot_be_created_puts_the_descriptors_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    real = RunDir.create

    def create(path: Path, machine: str) -> RunDir:
        made = real(path, machine)
        (path / "logs" / "lab.stdout.log").write_text("in the way\n")
        return made

    monkeypatch.setattr(RunDir, "create", staticmethod(create))
    with pytest.raises(FileExistsError):
        start(tmp_path, script(tmp_path))
    assert capfd.readouterr() == ("", "loaded out\nloaded err\n")
    os.write(1, b"after\n")
    assert capfd.readouterr().out == "after\n"


class _FullUnlessTerminal(io.RawIOBase):
    """A raw stdout that writes to descriptor 1 while it is one of the
    terminal's two files and raises ENOSPC while it is anything else.
    """

    def __init__(self) -> None:
        super().__init__()
        self._terminal = {_file(1), _file(2)}

    def writable(self) -> bool:
        return True

    def write(self, b: Buffer, /) -> int:
        if _file(1) not in self._terminal:
            raise OSError(errno.ENOSPC, "No space left on device")
        return os.write(1, b)


def _file(fd: int) -> tuple[int, int]:
    st = os.fstat(fd)
    return st.st_dev, st.st_ino


@pytest.mark.parametrize("dry", [True, False], ids=["dry_run", "run"])
def test_a_flush_that_raises_at_release_puts_the_descriptors_back(
    dry: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(io.BufferedWriter(_FullUnlessTerminal())))
    path = script(tmp_path, '    print("stuck")\n')
    with pytest.raises(OSError) as raised:
        if dry:
            runner.dry_run(lab(tmp_path), MACHINE, path, PINNED)
        else:
            start(tmp_path, path)
    assert raised.value.errno == errno.ENOSPC
    held = "loaded out\nran out\nloaded err\nran err\n" if dry else ""
    assert capfd.readouterr() == ("", held + "stuck\n")
    if not dry:
        assert kept(tmp_path / "run", "stdout") == "loaded out\nran out\n"
    os.write(1, b"after out\n")
    os.write(2, b"after err\n")
    assert capfd.readouterr() == ("after out\n", "after err\n")


def test_a_held_file_is_copied_without_moving_its_shared_offset(tmp_path: Path) -> None:
    # More than one chunk, and a write through a shared descriptor, as a
    # program started during the hold makes, after the first.
    written = b"x" * (1 << 16) + b"held\n"
    with (tmp_path / "held").open("w+b") as held:
        held.write(written)
        held.flush()
        child = os.dup(held.fileno())
        copied: list[bytes] = []

        def write(chunk: bytes) -> None:
            if not copied:
                os.write(child, b"late\n")
            copied.append(chunk)

        try:
            streams._copy(held, write)
        finally:
            os.close(child)
    assert b"".join(copied) == written + b"late\n"
    assert (tmp_path / "held").read_bytes() == written + b"late\n"


THREAD_RAISES = """\
    def boom() -> None:
        raise ValueError("from a thread")

    thread = threading.Thread(target=boom, name="boom")
    thread.start()
    thread.join()
"""


def raising_script(tmp_path: Path) -> Path:
    path = tmp_path / "script.py"
    path.write_text(SAYS + THREAD_RAISES)
    return path


def test_a_thread_that_raises_in_a_completed_run(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    hook = threading.excepthook
    pointer = start(tmp_path, raising_script(tmp_path))
    out = tmp_path / "run"
    assert pointer["completed"] is True and results(out)["outcome"] == "completed"
    started, raised = runner_log(out)
    assert started["text"] == "run started"
    assert raised["text"].startswith("thread boom raised:\nTraceback")
    assert "ValueError: from a thread" in raised["text"]
    assert raised["at"]["machine"] == MACHINE
    assert "Traceback" not in kept(out, "stderr")
    said = capfd.readouterr()
    assert said.out == ""
    assert said.err == (
        "lab run: 1 thread(s) raised during the run; 1 traceback(s) are in "
        f"{out / 'logs' / 'runner.log'}\n"
    )
    assert threading.excepthook is hook


def test_a_thread_whose_traceback_cannot_be_noted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    real = RunDir.note

    def note(self: RunDir, text: str) -> None:
        if text.startswith("thread "):
            raise OSError(28, "No space left on device")
        real(self, text)

    monkeypatch.setattr(RunDir, "note", note)
    # The hook found before; in a `lab` process it is Python's, which writes
    # the traceback to stderr, so to `lab.stderr.log`.
    handed: list[BaseException | None] = []
    monkeypatch.setattr(threading, "excepthook", lambda args: handed.append(args.exc_value))
    start(tmp_path, raising_script(tmp_path))
    out = tmp_path / "run"
    assert [e["text"] for e in runner_log(out)] == ["run started"]
    [value] = handed
    assert isinstance(value, ValueError) and str(value) == "from a thread"
    assert capfd.readouterr().err == (
        "lab run: 1 thread(s) raised during the run; 0 traceback(s) are in "
        f"{out / 'logs' / 'runner.log'}, the rest in {out / 'logs' / 'lab.stderr.log'}\n"
    )


def test_a_run_that_does_not_complete_says_where_to_look(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    pointer = start(tmp_path, script(tmp_path, '    raise RuntimeError("oops")\n'))
    out = tmp_path / "run"
    assert pointer["completed"] is False
    assert capfd.readouterr() == (
        "",
        "lab run: the run did not complete (outcome failed): see "
        f"{out / 'results.json'} and the logs in {out / 'logs'}\n",
    )
    texts = [e["text"] for e in runner_log(out)]
    assert texts[0] == "run started"
    assert texts[1].startswith("the script raised:\nTraceback")


def test_a_failed_run_in_which_a_thread_raised_says_both_in_one_line(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    start(tmp_path, script(tmp_path, THREAD_RAISES + '    raise RuntimeError("oops")\n'))
    out = tmp_path / "run"
    [said] = capfd.readouterr().err.splitlines()
    assert said == (
        "lab run: the run did not complete (outcome failed): see "
        f"{out / 'results.json'} and the logs in {out / 'logs'}; 1 thread(s) raised during "
        f"the run; 1 traceback(s) are in {out / 'logs' / 'runner.log'}"
    )
