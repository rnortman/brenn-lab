"""The run and the dry run of a playback role on the in-process output, beside
a capture role on the stand-in recorder.
"""

import array
import json
import os
import textwrap
import wave
from pathlib import Path
from typing import Any

import pytest

from brenn_lab import runner
from brenn_lab.buildinfo import Revision
from brenn_lab.data import encode_object
from brenn_lab.playback import output
from brenn_lab.playback.output import ControlName
from brenn_lab.testing.fake_output import CARD, LIBRARY, FakeControl, FakeOutput
from brenn_lab.testing.lab_driver import lab_toml, speaker_toml

STAND_IN = os.path.abspath(os.environ["STAND_IN"])
EXAMPLE = Path(os.path.abspath(os.environ["EXAMPLE"]))
MACHINE = "bench"
HARNESS = Revision(commit="0123456789abcdef0123456789abcdef01234567", dirty=False, source="git")
PINNED = {"pod_capture": "/nonexistent/pinned/pod-capture"}
LEVEL = ControlName("PCM", 1)
HELD = ControlName("PCM", 0)
RATE = 16000


@pytest.fixture
def outputs(monkeypatch: pytest.MonkeyPatch) -> list[FakeOutput]:
    """Every output a playback role is made with, in the order made: an
    in-process one in place of the ALSA module.
    """
    made: list[FakeOutput] = []

    def make(card: str) -> FakeOutput:
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
    assert record["last"]["frames_handed"] == RATE // 2
    assert record["restored"] is True and record["failure"] is None
    assert results["played"] == {
        "plays_started": 1,
        "plays_completed": 1,
        "frames_handed": RATE // 2,
    }
    assert results["recorded"]["captures_started"] == 1
    assert results["roles"]["speaker"] == {"kind": "playback", "commands": 2, "failures": []}

    assert names(out) == [
        ("command", "capture.start"),
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

    def moved_at_third_write(card: str) -> output.Output:
        made = real(card)
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
