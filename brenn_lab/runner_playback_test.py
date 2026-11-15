"""The run and the dry run of a playback role on the in-process output, beside
a capture role on the stand-in recorder.
"""

import array
import json
import os
import textwrap
import threading
import wave
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from brenn_lab import marker, runner
from brenn_lab.buildinfo import Revision
from brenn_lab.data import ROLE_ERROR, encode_object
from brenn_lab.playback import output, role
from brenn_lab.playback.output import ControlName
from brenn_lab.rundir import RunDir
from brenn_lab.testing.fake_output import CARD, LIBRARY, FakeControl, FakeOutput
from brenn_lab.testing.lab_driver import lab_toml, speaker_toml
from brenn_lab.testing.role_raised import SINK_MESSAGE, check_role_raised
from brenn_lab.testing.run_logs import check_logs, runner_log

STAND_IN = os.path.abspath(os.environ["STAND_IN"])
EXAMPLE = Path(os.path.abspath(os.environ["EXAMPLE"]))
MACHINE = "bench"
HARNESS = Revision(commit="0123456789abcdef0123456789abcdef01234567", dirty=False, source="git")
PINNED = {"pod_capture": "/nonexistent/pinned/pod-capture"}
LEVEL = ControlName("PCM", 1)
HELD = ControlName("PCM", 0)
RATE = 16000


@pytest.fixture(autouse=True)
def late_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A role hosted by the runner has the machine's clock, so a timed play's
    thread can wake late on a busy machine; no test here judges how late.
    """
    monkeypatch.setattr(role, "LATE_SECONDS", 3600.0)


@pytest.fixture
def outputs(monkeypatch: pytest.MonkeyPatch) -> list[FakeOutput]:
    """Every output a playback role is made with, in the order made: an
    in-process one in place of the ALSA module.
    """
    made: list[FakeOutput] = []

    def make(card: str, programs: Mapping[str, str]) -> FakeOutput:
        assert card == "Fake"
        fake = FakeOutput([FakeControl(LEVEL), FakeControl(HELD)], speed=4.0)
        made.append(fake)
        return fake

    monkeypatch.setitem(output.OUTPUTS, "alsa", make)
    return made


def clip(sources: Path, name: str, seconds: float) -> bytes:
    """A two-channel 16-bit clip at RATE, peak half of full scale."""
    sources.mkdir(exist_ok=True)
    frames = round(RATE * seconds)
    samples = array.array("h", [(16384 if (i // 2) % 2 else -16384) for i in range(2 * frames)])
    with wave.open(str(sources / name), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(samples.tobytes())
    return samples.tobytes()


def lab(tmp_path: Path) -> Path:
    path = tmp_path / "lab.toml"
    path.write_text(
        lab_toml(json.dumps(STAND_IN), "clean", sample_clock="flex")
        + "\n"
        + speaker_toml("Fake", tmp_path / "sources", -1800)
    )
    return path


def script(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "script.py"
    path.write_text(
        "from brenn_lab import playback\n"
        "from brenn_lab.script import Lab\n\n"
        'ROLES = {"speaker": playback.KIND}\n\n\n'
        "def run(lab: Lab) -> None:\n"
        '    speaker = lab.role("speaker")\n' + textwrap.indent(textwrap.dedent(body), "    ")
    )
    return path


def dry(lab_path: Path, script_path: Path) -> Any:
    report = runner.dry_run(lab_path, MACHINE, script_path, PINNED)
    assert isinstance(report, dict), report
    return report


def do_run(tmp_path: Path, lab_path: Path, script_path: Path) -> tuple[Path, Any, Any]:
    out = tmp_path / "run"
    pointer = runner.run(
        lab_path, MACHINE, script_path, str(script_path), out, None, HARNESS, None, PINNED
    )
    assert isinstance(pointer, dict), pointer
    manifest = json.loads((out / "manifest.json").read_text())
    results = json.loads((out / "results.json").read_text())
    assert pointer["outcome"] == results["outcome"]
    assert pointer["completed"] == results["completed"]
    check_logs(out, sink_client=False)
    return out, manifest, results


def names(out: Path) -> list[tuple[str, str]]:
    """The journal as (kind of line, name) pairs."""
    lines = [json.loads(t) for t in (out / "journal.jsonl").read_text().splitlines()]
    pairs = []
    for line in lines:
        if "event" in line:
            pairs.append(("event", line["event"]))
        elif "ok" in line:
            pairs.append(("reply", line["command"]))
        else:
            pairs.append(("command", line["command"]))
    return pairs


def test_play_and_record(tmp_path: Path, outputs: list[FakeOutput]) -> None:
    frames = clip(tmp_path / "sources", "clip.wav", 0.5)
    lab_path = lab(tmp_path)

    report = dry(lab_path, EXAMPLE)
    assert report["problems"] == [] and report["ok"] is True
    assert report["trace"] == {"complete": True, "commands": 3, "stopped_at": None}
    checked = report["roles"]["speaker"]["check"]
    assert checked["ok"] is True
    assert checked["result"]["device"] == encode_object(CARD)
    [dry_output] = outputs
    assert dry_output.opens == 1 and dry_output.writes == 0 and dry_output.sets == []

    out, manifest, results = do_run(tmp_path, lab_path, EXAMPLE)
    assert results["outcome"] == "completed", results["failure"]

    speaker = manifest["roles"]["speaker"]
    assert speaker["kind"] == "playback"
    assert speaker["sample_clock"] == "flex"
    assert manifest["roles"]["mic"]["sample_clock"] == "flex"
    assert "captures" not in speaker
    [entry] = speaker["sources"]
    file = {"machine": MACHINE, "path": "sources/speaker/0001.wav", "in_run": True}
    assert entry["file"] == file
    assert "copy" not in entry
    assert entry["play"] == 1
    assert entry["source"]["name"] == "clip.wav"
    assert entry["source"]["frames"] == RATE // 2
    assert entry["output"] == {"kind": "alsa", "library": encode_object(LIBRARY)}
    assert entry["level_mb"] == -3000 and entry["cap_mb"] == -1800
    with wave.open(str(out / "sources" / "speaker" / "0001.wav"), "rb") as kept:
        assert kept.readframes(kept.getnframes()) == frames
    # Nothing but the harness's stamp and the recorder's: no other code played.
    assert [r["repository"] for r in manifest["repositories"][1:]] == ["brenn-pod"]

    [record] = results["sources"]
    assert {k: record[k] for k in ("role", "play", "file", "ended", "stopped_by")} == {
        "role": "speaker",
        "play": 1,
        "file": file,
        "ended": "completed",
        "stopped_by": None,
    }
    assert record["source"] == {"name": "clip.wav", "sha256": entry["source"]["sha256"]}
    assert record["frames"] == record["frames_handed"] == RATE // 2
    assert record["unplayed_frames"] == 0
    assert record["underruns"] == [] and record["as_sent"] is True
    assert record["first"] == entry["first"]
    assert entry["asked_start"] is None
    assert record["asked_start"] is None and record["woke"] is None
    assert record["started_after_us"] is None
    assert record["last"]["frames_handed"] == RATE // 2
    assert record["restored"] is True and record["failure"] is None
    assert entry["source"]["marker"] is None and record["marker"] is None
    assert results["played"] == {
        "plays_started": 1,
        "plays_completed": 1,
        "plays_marked": 0,
        "frames_handed": RATE // 2,
    }
    assert results["recorded"]["captures_started"] == 1
    assert results["roles"]["speaker"] == {"kind": "playback", "commands": 2, "failures": []}

    assert names(out) == [
        ("command", "capture.start"),
        ("event", "process.started"),
        ("reply", "capture.start"),
        ("command", "playback.play"),
        ("reply", "playback.play"),
        ("event", "playback.ended"),
        ("command", "capture.stop"),
        ("reply", "capture.stop"),
        ("command", "release"),
        ("reply", "release"),
        ("command", "release"),
        ("reply", "release"),
    ]
    live = outputs[-1]
    assert bytes(live.streams[0].frames) == frames
    assert not live.attached


@pytest.mark.parametrize("raised_on", ["caught_stop", "stop", "release"])
def test_a_role_that_raises_on_a_scripts_command_fails_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outputs: list[FakeOutput], raised_on: str
) -> None:
    """The play's end cannot be reported; the script's stop, which the
    script may catch, or the runner's release, held until then, raises the
    play thread's error. A stop that reached a live play would get its
    `Stopped` and no end would be reported.
    """
    held = "release" if raised_on == "release" else "playback.stop"
    real_event = runner._Ledger.event
    real_command = runner._Ledger.command
    refused = threading.Event()

    def event(self: runner._Ledger, event: Any) -> None:
        if event.name == "playback.ended":
            refused.set()
            raise RuntimeError(SINK_MESSAGE)
        real_event(self, event)

    def command(self: runner._Ledger, command: Any, by: str) -> None:
        if command.name == held:
            assert refused.wait(20)
        real_command(self, command, by)

    monkeypatch.setattr(runner._Ledger, "event", event)
    monkeypatch.setattr(runner._Ledger, "command", command)
    clip(tmp_path / "sources", "clip.wav", 0.2)
    body = 'speaker.send(playback.Play(source="clip.wav", volume_mb=-3000))\n'
    stop = "speaker.send(playback.Stop())\n"
    if raised_on == "caught_stop":
        body += "try:\n    " + stop + "except Exception:\n    pass\n"
    elif raised_on == "stop":
        body += stop
    out, _, results = do_run(tmp_path, lab(tmp_path), script(tmp_path, body))
    check_role_raised(out, results, "speaker", {held})
    [record] = results["sources"]
    assert (record["ended"], record["restored"], record["stopped_by"]) == ("failed", None, None)
    assert record["failure"]["reason"] == "end_not_reported"
    [raise_note] = [e for e in runner_log(out) if e["text"].startswith("role speaker raised")]
    assert "the play's controls were put back as found" in raise_note["text"]
    if raised_on == "release":
        assert [p for p in names(out) if p[0] != "event"][-3:] == [
            ("command", "playback.play"),
            ("reply", "playback.play"),
            ("command", "release"),
        ]
        assert not outputs[-1].attached
        return
    # The play's thread raised the sink's error on its way out, and its
    # traceback is in the runner's log by the time the run has ended.
    [thread] = [e for e in runner_log(out) if e["text"].startswith("thread ")]
    assert thread["text"].startswith("thread playback-speaker-1 raised:\nTraceback")
    assert SINK_MESSAGE in thread["text"]
    # The play was answered; the stop, held until the end was refused, raised.
    assert [p for p in names(out) if p[0] != "event" and p[1].startswith("playback.")] == [
        ("command", "playback.play"),
        ("reply", "playback.play"),
        ("command", "playback.stop"),
    ]


BADLY_TYPED = {
    "volume": ('playback.Play(source="clip.wav", volume_mb="-3000")', "playback.play.volume_mb"),
    "source": ("playback.Play(source=None, volume_mb=-3000)", "playback.play.source"),
}


@pytest.mark.parametrize("bad", sorted(BADLY_TYPED))
@pytest.mark.parametrize("used_first", [False, True])
def test_a_badly_typed_command_is_the_scripts_error(
    tmp_path: Path, outputs: list[FakeOutput], bad: str, used_first: bool
) -> None:
    """The script's `send` raises `TypeError` and nothing reaches the role or
    the journal; a role the script had already used is still released.
    """
    sent, field = BADLY_TYPED[bad]
    body = "speaker.send(playback.Clock())\n" if used_first else ""
    body += (
        f"try:\n    speaker.send({sent})\n"
        "except TypeError as e:\n"
        f"    assert {field + ': '!r} in str(e), str(e)\n"
        "    raise\n"
    )
    out, _, results = do_run(tmp_path, lab(tmp_path), script(tmp_path, body))
    assert results["outcome"] == "failed"
    failure = results["failure"]
    assert (failure["where"], failure["reason"]) == ("script", "script_error")
    assert failure["detail"].startswith("TypeError: command playback.play.")
    assert results["roles"]["speaker"]["failures"] == []
    if used_first:
        assert names(out) == [
            ("command", "playback.clock"),
            ("reply", "playback.clock"),
            ("command", "release"),
            ("reply", "release"),
        ]
        assert results["roles"]["speaker"]["commands"] == 2
    else:
        assert names(out) == []
        assert results["roles"]["speaker"]["commands"] == 0
    assert outputs == [] or not any(o.attached for o in outputs)
    assert "TypeError: command playback.play." in runner_log(out)[-1]["text"]


TIMED = """\
now = speaker.send(playback.Clock()).at
speaker.send(playback.Play(source="clip.wav", volume_mb=-3000, start=now.after(2.0)))
lab.wait(3.0)
"""


def test_a_timed_play_s_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outputs: list[FakeOutput]
) -> None:
    clip(tmp_path / "sources", "clip.wav", 0.2)
    written: list[Any] = []
    real = RunDir.write_manifest

    def write_manifest(self: RunDir, manifest: Any) -> None:
        speaker = manifest["roles"]["speaker"]
        written.append(json.loads(json.dumps(speaker.get("sources", []))))
        real(self, manifest)

    monkeypatch.setattr(RunDir, "write_manifest", write_manifest)
    out, manifest, results = do_run(tmp_path, lab(tmp_path), script(tmp_path, TIMED))
    assert results["outcome"] == "completed", results["failure"]

    [entry] = manifest["roles"]["speaker"]["sources"]
    assert entry["first"] is None
    asked = entry["asked_start"]
    assert asked is not None and asked["machine"] == MACHINE
    # Once with no play, once at the play's reply; its end rewrites nothing.
    assert written == [[], [entry]]

    [record] = results["sources"]
    assert record["ended"] == "completed"
    assert record["asked_start"] == asked
    ended = [
        json.loads(t)
        for t in (out / "journal.jsonl").read_text().splitlines()
        if json.loads(t).get("event") == "playback.ended"
    ]
    [end] = [e["data"] for e in ended]
    assert record["woke"] == end["woke"] is not None
    assert record["first"] == end["first"] is not None
    assert record["first"]["at"]["monotonic"]["us"] >= asked["monotonic"]["us"]
    assert record["woke"]["monotonic"]["us"] >= asked["monotonic"]["us"]
    assert record["started_after_us"] == (
        record["first"]["at"]["monotonic"]["us"] - asked["monotonic"]["us"]
    )
    assert record["started_after_us"] >= 0
    assert names(out) == [
        ("command", "playback.clock"),
        ("reply", "playback.clock"),
        ("command", "playback.play"),
        ("reply", "playback.play"),
        ("event", "playback.ended"),
        ("command", "release"),
        ("reply", "release"),
    ]


HELD_AN_HOUR = """\
now = speaker.send(playback.Clock()).at
speaker.send(playback.Play(source="clip.wav", volume_mb=-3000, start=now.after(3600.0)))
lab.wait(0.2)
speaker.send(playback.Stop())
"""


def test_a_timed_play_stopped_in_its_hold(tmp_path: Path, outputs: list[FakeOutput]) -> None:
    clip(tmp_path / "sources", "clip.wav", 0.2)
    _, manifest, results = do_run(tmp_path, lab(tmp_path), script(tmp_path, HELD_AN_HOUR))
    assert results["outcome"] == "completed", results["failure"]
    [entry] = manifest["roles"]["speaker"]["sources"]
    assert entry["first"] is None and entry["asked_start"] is not None
    [record] = results["sources"]
    assert record["ended"] == "stopped" and record["stopped_by"] == "script"
    assert record["frames_handed"] == 0
    assert record["first"] is None and record["last"] is None and record["woke"] is None
    assert record["started_after_us"] is None
    assert record["asked_start"] == entry["asked_start"]
    assert outputs[-1].writes == 0


MARKED = """\
started = speaker.send(playback.Play(source="marked.wav", volume_mb=-3000))
assert started.source.marker is not None and started.source.marker.member == 12
lab.wait(1.5)
speaker.send(playback.Play(source="clip.wav", volume_mb=-3000))
lab.wait(0.5)
"""


def test_a_marked_play_and_an_unmarked_one(tmp_path: Path, outputs: list[FakeOutput]) -> None:
    clip(tmp_path / "sources", "clip.wav", 0.1)
    marked = marker.mark(tmp_path / "sources" / "clip.wav", tmp_path / "sources" / "marked.wav", 12)
    assert isinstance(marked, marker.Marked)
    carried = marked.marker
    lab_path, script_path = lab(tmp_path), script(tmp_path, MARKED)

    report = dry(lab_path, script_path)
    assert report["problems"] == [], report

    _, manifest, results = do_run(tmp_path, lab_path, script_path)
    assert results["outcome"] == "completed", results["failure"]
    marked, unmarked = manifest["roles"]["speaker"]["sources"]
    assert marked["source"]["marker"] == encode_object(carried)
    assert unmarked["source"]["marker"] is None
    first, second = results["sources"]
    assert first["marker"] == encode_object(carried) and second["marker"] is None
    assert results["played"]["plays_marked"] == 1
    assert results["played"]["plays_started"] == 2
    assert results["usability"]["assessed"] is False
    assert results["usability"]["see"] == "assessment.json"


LEFT = """\
speaker.send(playback.Play(source="clip.wav", volume_mb=-3000))
"""


def test_a_play_left_running(tmp_path: Path, outputs: list[FakeOutput]) -> None:
    clip(tmp_path / "sources", "clip.wav", 20.0)
    lab_path, script_path = lab(tmp_path), script(tmp_path, LEFT)

    report = dry(lab_path, script_path)
    assert report["problems"] == []

    _, manifest, results = do_run(tmp_path, lab_path, script_path)
    assert results["outcome"] == "failed"
    assert results["failure"] == encode_object(
        runner.Problem(
            "runner", "left_running", "play 1 was running when the script ended", "speaker"
        )
    )
    [record] = results["sources"]
    assert record["ended"] == "stopped" and record["stopped_by"] == "runner"
    assert 0 < record["frames_handed"] < record["frames"]
    assert record["as_sent"] is False and record["restored"] is True
    assert results["played"]["plays_completed"] == 0
    assert len(manifest["roles"]["speaker"]["sources"]) == 1


STOPPED = """\
speaker.send(playback.Play(source="clip.wav", volume_mb=-3000))
lab.wait(0.2)
speaker.send(playback.Stop())
"""


def test_a_play_the_script_stops(tmp_path: Path, outputs: list[FakeOutput]) -> None:
    clip(tmp_path / "sources", "clip.wav", 20.0)
    _, _, results = do_run(tmp_path, lab(tmp_path), script(tmp_path, STOPPED))
    assert results["outcome"] == "completed", results["failure"]
    [record] = results["sources"]
    assert record["ended"] == "stopped" and record["stopped_by"] == "script"
    assert isinstance(record["unplayed_frames"], int)
    assert record["as_sent"] is False


MOVED = """\
speaker.send(playback.Play(source="clip.wav", volume_mb=-3000))
lab.wait(3.0)
"""


def test_a_play_that_fails(tmp_path: Path, outputs: list[FakeOutput]) -> None:
    clip(tmp_path / "sources", "clip.wav", 2.0)
    lab_path, script_path = lab(tmp_path), script(tmp_path, MOVED)

    real = output.OUTPUTS["alsa"]

    def moved_at_third_write(card: str, programs: Mapping[str, str]) -> output.Output:
        made = real(card, programs)
        assert isinstance(made, FakeOutput)
        made.on_write = lambda n: made.move(LEVEL, 0, volume_mb=-1000) if n == 3 else None
        return made

    output.OUTPUTS["alsa"] = moved_at_third_write
    out, _, results = do_run(tmp_path, lab_path, script_path)

    assert results["outcome"] == "failed"
    failure = results["failure"]
    assert (failure["where"], failure["role"], failure["reason"]) == (
        "role",
        "speaker",
        "controls_moved",
    )
    [failed] = results["roles"]["speaker"]["failures"]
    assert failed["event"] == "playback.failed"
    [record] = results["sources"]
    assert record["ended"] == "failed" and record["stopped_by"] is None
    assert record["failure"]["reason"] == "controls_moved"
    assert record["restored"] is True
    assert ("event", "playback.failed") in names(out)
    # An ALSA output starts no process, so no log stands behind the failure.
    assert failure["log"] is None and failed["log"] is None


def test_a_play_whose_thread_raises_points_at_the_runner_log(
    tmp_path: Path, outputs: list[FakeOutput]
) -> None:
    clip(tmp_path / "sources", "clip.wav", 2.0)
    lab_path = lab(tmp_path)
    script_path = script(
        tmp_path, 'speaker.send(playback.Play(source="clip.wav", volume_mb=-3000))\nlab.wait(1.0)\n'
    )

    real = output.OUTPUTS["alsa"]

    def raises_at_third_write(card: str, programs: Mapping[str, str]) -> output.Output:
        made = real(card, programs)
        assert isinstance(made, FakeOutput)

        def on_write(n: int) -> None:
            if n == 3:
                raise RuntimeError("the write broke")

        made.on_write = on_write
        return made

    output.OUTPUTS["alsa"] = raises_at_third_write
    out, _, results = do_run(tmp_path, lab_path, script_path)

    assert results["outcome"] == "failed"
    failure = results["failure"]
    assert (failure["role"], failure["reason"]) == ("speaker", ROLE_ERROR)
    runner_log_file = {"machine": MACHINE, "path": "logs/runner.log", "in_run": True}
    assert failure["log"] == runner_log_file
    [failed] = results["roles"]["speaker"]["failures"]
    assert (failed["event"], failed["log"]) == ("playback.failed", runner_log_file)
    [thread] = [e for e in runner_log(out) if e["text"].startswith("thread ")]
    assert "RuntimeError: the write broke" in thread["text"]


def test_a_dry_run_of_a_play_refused_over_the_cap(
    tmp_path: Path, outputs: list[FakeOutput]
) -> None:
    clip(tmp_path / "sources", "clip.wav", 0.5)
    body = 'speaker.send(playback.Play(source="clip.wav", volume_mb=-1700))\n'
    report = dry(lab(tmp_path), script(tmp_path, body))
    [problem] = report["problems"]
    assert (problem["where"], problem["role"], problem["reason"]) == ("role", "speaker", "over_cap")
    [dry_output] = outputs
    assert dry_output.opens == 0 and dry_output.sets == []
