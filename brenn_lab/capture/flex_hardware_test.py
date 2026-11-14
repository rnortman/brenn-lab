"""The capture role on a reSpeaker Flex XVF3800 through the pinned recorder.

Each test writes a lab configuration that gives machine `bench` the pinned
recorder and one capture role on the Flex's six-channel image, starts the
runner as a program with `examples/ten_seconds.py`, and reads back what the
run left: stdout and exit statuses, `manifest.json`, `results.json`, the
recorder's stdout, and the capture through the pinned reader. Nothing is
stubbed. The live event lines are held last to the recorded transcript of the
same case, and saved as a test output for the next retake of that transcript.
The clean run is then aligned with `lab align`, and the product search is run
over its ambient capture, where it must find no marker.
"""

import json
import os
import re
import shutil
import signal
import subprocess
import time
from functools import partial
from pathlib import Path
from typing import Any

from brenn_lab import marker
from brenn_lab.align import search
from brenn_lab.align.capture import read as read_capture
from brenn_lab.data import Failure, JsonObject
from brenn_lab.testing import transcript_files
from brenn_lab.testing.lab_driver import document, dry_args, lab_toml, run_args
from brenn_lab.testing.transcript_files import layout_of, objects, shape

LAB = os.path.abspath(os.environ["LAB"])
EXAMPLE = Path(os.path.abspath(os.environ["EXAMPLE"]))
FRAME_LOG = os.path.abspath(os.environ["FRAME_LOG"])
CARD = "Flex XVF3800 L16K6Ch"
RATE_HZ = 16000
CHANNELS = 6
BYTES_PER_SAMPLE = 2
PERIOD_FRAMES = 320
BUFFER_FRAMES = 5120
USB_ID = "2886:0022"
LAYOUT = list(layout_of("clean"))
SCRIPT_SECONDS = 10.0  # the example's wait between start and stop
STOP_SLACK_S = 1.0
TIMEOUT_S = 120
PID_LINE = re.compile(r"^mic: capture 1: pod-capture pid (\d+)$", re.MULTILINE)


def lab_file(tmp: Path, card: str = CARD) -> Path:
    path = tmp / "lab.toml"
    path.write_text(lab_toml("{ pinned = true }", card))
    return path


def workdir(case: str) -> Path:
    path = Path(os.environ["TEST_TMPDIR"]) / case
    path.mkdir()
    return path


def start(args: list[str]) -> subprocess.CompletedProcess[str]:
    done = subprocess.run(
        [LAB, *args], capture_output=True, text=True, timeout=TIMEOUT_S, check=False
    )
    print(done.stderr, end="")
    return done


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def recorder_stdout(out: Path, case: str) -> Path:
    """The run's recorder stdout, copied unchanged to the test's outputs as
    `<case>.jsonl`, the file a retake of the recorded transcript starts from.
    """
    path = out / "logs" / "mic" / "recorder-0001.stdout.jsonl"
    saved = os.environ.get("TEST_UNDECLARED_OUTPUTS_DIR")
    if saved is not None and path.is_file():
        shutil.copyfile(path, Path(saved) / f"{case}.jsonl")
    return path


def frame_log_summary(capture: Path) -> Any:
    done = subprocess.run(
        [FRAME_LOG, "summary", str(capture)],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return document(done.stdout)


def only_capture(manifest: Any, results: Any) -> tuple[Any, Any]:
    [started] = manifest["roles"]["mic"]["captures"]
    [ended] = results["captures"]
    assert started["capture_id"] == ended["capture_id"]
    return started, ended


def check_dry_run(lab: Path) -> Any:
    done = start(dry_args(lab, EXAMPLE))
    assert done.returncode == 0, done.stdout
    report = document(done.stdout)
    assert report["ok"] is True and report["problems"] == [], report
    check = report["roles"]["mic"]["check"]
    assert check["ok"] is True and check["result_type"] == "capture.checked", check
    return check["result"]


def check_started(started: Any, checked: Any) -> None:
    assert started["device"]["usb_id"] == USB_ID
    assert started["granted"] == {
        "rate_hz": RATE_HZ,
        "channels": CHANNELS,
        "sample_format": "s16_le",
        "period_frames": PERIOD_FRAMES,
        "buffer_frames": BUFFER_FRAMES,
    }
    assert started["layout"] == LAYOUT
    for key in ("granted", "layout"):
        assert checked[key] == started[key], key
    assert checked["device"]["usb_id"] == started["device"]["usb_id"]
    # A recorder built with its commit declared asserts no cleanliness.
    recorded = transcript_files.line("clean", "capture_started")["build"]
    assert isinstance(recorded, dict)
    commit = recorded["commit"]
    assert started["recorder"]["build"] == {"commit": commit, "dirty": None, "source": "declared"}


def check_completed(results: Any, ended: Any, loss_free: bool) -> None:
    assert (results["outcome"], results["completed"], results["failure"]) == (
        "completed",
        True,
        None,
    )
    assert ended["ended"] == "stopped" and ended["stopped_by"] == "script"
    assert ended["loss_free"] is loss_free
    assert results["recorded"] == {
        "captures_started": 1,
        "captures_stopped": 1,
        "frames": ended["frames"],
    }


def check_capture_file(out: Path, started: Any, ended: Any) -> Path:
    pointer = started["file"]
    assert pointer["in_run"] is True and pointer == ended["file"]
    capture = out / str(pointer["path"])
    assert capture.stat().st_size >= ended["frames"] * CHANNELS * BYTES_PER_SAMPLE
    return capture


def check_summary(summary: Any, started: Any, ended: Any) -> None:
    assert summary["record_id"] == started["capture_id"]
    assert summary["hello"]["sample_rate_hz"] == RATE_HZ
    assert summary["hello"]["channels"] == CHANNELS
    assert summary["capture"]["channel_names"] == LAYOUT
    assert summary["capture"]["period_frames"] == PERIOD_FRAMES
    assert summary["capture"]["started"] == {
        "mono_us": started["start"]["monotonic"]["us"],
        "wall_us": started["start"]["wall"]["us"],
    }
    assert summary["frames_recorded"] == ended["frames"]
    assert summary["frames_delivered"] == ended["frames"]
    assert summary["tail"]["verdict"] == "ended"


def same_case(live: Path, case: str) -> None:
    """The live lines against the recorded transcript `case`: the same events
    in the same order, `capture_late` left out of both; line by line the same
    shape; and in `capture_started` the same build.
    """

    def kept(lines: list[JsonObject]) -> list[JsonObject]:
        return [v for v in lines if v["event"] != "capture_late"]

    live_lines = objects(live)
    for value in live_lines:
        if value["event"] == "capture_late":
            print("capture_late in the live lines:", json.dumps(value))
    got, want = kept(live_lines), kept(objects(transcript_files.path(case)))
    assert [v["event"] for v in got] == [v["event"] for v in want]
    for a, b in zip(got, want, strict=True):
        assert shape(a) == shape(b), (a, b)
        if a["event"] == "capture_started":
            assert a["build"] == b["build"]


def test_clean() -> None:
    tmp = workdir("clean")
    lab = lab_file(tmp)
    checked = check_dry_run(lab)

    out = tmp / "run"
    done = start(run_args(lab, EXAMPLE, out))
    live = recorder_stdout(out, "clean")
    assert done.returncode == 0, done.stdout
    assert document(done.stdout)["outcome"] == "completed"
    manifest, results = read_json(out / "manifest.json"), read_json(out / "results.json")
    started, ended = only_capture(manifest, results)

    check_started(started, checked)
    assert ended["gaps_reported"] == 0 and ended["gaps"] == []
    seconds = (ended["end"]["monotonic"]["us"] - started["start"]["monotonic"]["us"]) / 1e6
    # The script waits after the start's reply and stops after the wait, and
    # the recorder reads the stop's clock after the stop, so the span is at
    # least the wait; the slack bounds the stop's latency.
    assert SCRIPT_SECONDS <= seconds <= SCRIPT_SECONDS + STOP_SLACK_S, seconds
    assert abs(ended["frames"] - RATE_HZ * seconds) <= BUFFER_FRAMES, (ended["frames"], seconds)
    check_completed(results, ended, loss_free=True)
    capture = check_capture_file(out, started, ended)

    summary = frame_log_summary(capture)
    check_summary(summary, started, ended)
    assert summary["discontinuities"] == []

    same_case(live, "clean")

    check_align(out, ended)
    check_no_marker_in_ambient(tmp, capture, started)


def check_align(out: Path, ended: Any) -> None:
    """`lab align` on the run, through the pinned reader: no plays, the one
    capture read whole.
    """
    done = start(["align", "--run", str(out)])
    assert done.returncode == 0, done.stdout
    pointer = document(done.stdout)
    assert pointer["ok"] is True
    report = read_json(out / "alignment.json")
    assert report["reader"] == {"program": "frame-log", "pinned": True, "path": None}
    assert report["summary"] == {
        "plays": 0,
        "marked": 0,
        "located": 0,
        "captures": 1,
        "marker_failures": 0,
        "read_failures": 0,
    }
    [read] = report["captures"]
    assert (read["rate_hz"], read["layout"]) == (RATE_HZ, LAYOUT)
    assert read["exported_frames"] == ended["frames"]
    assert read["failure"] is None
    assert read["tail"] == "ended"
    assert not (out / ".alignment.tmp").exists()


def check_no_marker_in_ambient(tmp: Path, capture: Path, started: Any) -> None:
    """The product search over the ambient capture, for both markers of
    members 0 to 3 on every channel: no instance. The largest score of each
    channel is printed; one at or above the threshold is an unexpected
    hardware reading for the maintainer.
    """
    exported = read_capture(
        capture, tmp / "ambient.wav", FRAME_LOG, RATE_HZ, "s16_le", tuple(started["layout"])
    )
    assert not isinstance(exported, Failure), exported
    templates = [
        search.template(marker.code(shift), RATE_HZ)
        for member in range(4)
        for shift in marker.codes(member)
    ]
    for index, name in enumerate(LAYOUT):
        found = search.search(partial(exported.blocks, index), RATE_HZ, templates)
        best = max(f.best for f in found)
        print(f"ambient {name}: largest score {best:.3f}")
        assert all(f.instances == () for f in found), (name, best)
        assert best < search.THRESHOLD
    exported.path.unlink()


def test_loss() -> None:
    tmp = workdir("loss")
    lab = lab_file(tmp)
    check_dry_run(lab)

    out = tmp / "run"
    run = subprocess.Popen(
        [LAB, *run_args(lab, EXAMPLE, out)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        pid = recorder_pid(out / "logs" / "runner.log", run)
        time.sleep(5)
        os.kill(pid, signal.SIGSTOP)
        time.sleep(1)
        os.kill(pid, signal.SIGCONT)
        stdout, stderr = run.communicate(timeout=TIMEOUT_S)
    finally:
        if run.poll() is None:
            run.kill()
            run.wait()
    print(stderr, end="")
    live = recorder_stdout(out, "loss")
    assert run.returncode == 0, stdout
    assert document(stdout)["outcome"] == "completed"
    manifest, results = read_json(out / "manifest.json"), read_json(out / "results.json")
    started, ended = only_capture(manifest, results)

    check_completed(results, ended, loss_free=False)
    assert ended["gaps_reported"] == 1
    [gap] = ended["gaps"]
    assert (gap["cause"], gap["frames"]) == ("overrun", None)
    assert 0 < gap["at_frame"] < ended["frames"]
    capture = check_capture_file(out, started, ended)

    [printed] = [v for v in objects(live) if v["event"] == "capture_gap"]
    summary = frame_log_summary(capture)
    check_summary(summary, started, ended)
    [hole] = summary["discontinuities"]
    assert (hole["kind"], hole["cause"], hole["frames"]) == ("loss", "overrun", None)
    assert (hole["at_frame"], hole["mono_us"], hole["wall_us"]) == (
        printed["at_frame"],
        printed["mono_us"],
        printed["wall_us"],
    )

    same_case(live, "loss")


def recorder_pid(log: Path, run: subprocess.Popen[str]) -> int:
    """The recorder's pid, from the runner's log line for capture 1."""
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        assert run.poll() is None, "the run ended before its recorder started"
        if log.is_file():
            found = PID_LINE.search(log.read_text())
            if found is not None:
                return int(found.group(1))
        time.sleep(0.05)
    raise AssertionError(f"no recorder pid in {log}")


def test_no_card() -> None:
    tmp = workdir("no_card")
    lab = lab_file(tmp, card="No Such Card")

    done = start(dry_args(lab, EXAMPLE))
    assert done.returncode == 1, done.stdout
    report = document(done.stdout)
    assert report["ok"] is False
    assert [(p["where"], p["reason"]) for p in report["problems"]] == [("role", "no_card")]

    out = tmp / "run"
    done = start(run_args(lab, EXAMPLE, out))
    live = recorder_stdout(out, "no_card")
    assert done.returncode == 1, done.stdout
    results = read_json(out / "results.json")
    assert (results["outcome"], results["completed"]) == ("failed", False)
    assert results["recorded"]["captures_started"] == 0
    assert (results["failure"]["where"], results["failure"]["reason"]) == ("role", "no_card")
    manifest = read_json(out / "manifest.json")
    assert [r["repository"] for r in manifest["repositories"]] == ["brenn-lab"]

    same_case(live, "no_card")
