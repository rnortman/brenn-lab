"""The anchoring scenario, `examples/anchoring_scenario.py`, dry-run and run:
`mic` on the stand-in recorder, `speaker` on the in-process output in place of
ALSA, and `talker` on a sink of the stand-in sound server through the real
sink client, playing the marked speech clip.
"""

import json
import os
import wave
from collections.abc import Iterator, Mapping
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from brenn_lab import marker, runner
from brenn_lab.assess import report
from brenn_lab.buildinfo import Revision
from brenn_lab.playback import output
from brenn_lab.playback.output import ControlName
from brenn_lab.testing import bench
from brenn_lab.testing.fake_output import FakeControl, FakeOutput
from brenn_lab.testing.lab_driver import MACHINE, lab_toml, speaker_toml, talker_toml
from brenn_lab.testing.pulse_stand_in import StandInServer, sink
from brenn_lab.testing.sink_client import client_program

STAND_IN = os.path.abspath(os.environ["STAND_IN"])
EXPORT_STAND_IN = os.path.abspath(os.environ["EXPORT_STAND_IN"])
EXAMPLE = Path(os.path.abspath(os.environ["EXAMPLE"]))
LEVEL_STEP = Path(os.path.abspath(os.environ["LEVEL_STEP"]))
SPEECH = Path(os.path.abspath(os.environ["SPEECH"]))
HARNESS = Revision(commit="0123456789abcdef0123456789abcdef01234567", dirty=False, source="git")
PINNED = {"pod_capture": "/nonexistent/pinned/pod-capture"}
SINK = "alsa_output.usb-Desk-00.analog-stereo"
TALKER_MEMBER = 3
SPEAKER_MEMBER = 4
RAW = 50000
SPEAKER_CAP_MB = -1800
TALKER_CAP_MB = -2400


@pytest.fixture
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[StandInServer]:
    """A stand-in sound server with one sink, `Desk Speaker`, where the sink
    binding looks for one.
    """
    served = StandInServer(
        [sink(SINK, "Desk Speaker Analog Stereo", volumes=[RAW, RAW], latency_usec=20000)],
        speed=1.0,
    )
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(served.runtime))
    monkeypatch.setenv("HOME", str(tmp_path))
    try:
        yield served
    finally:
        served.close()


@pytest.fixture
def speakers(monkeypatch: pytest.MonkeyPatch) -> list[FakeOutput]:
    """Every output a card role is made with, in the order made: an in-process
    one in place of the ALSA module.
    """
    made: list[FakeOutput] = []

    def make(card: str, programs: Mapping[str, str]) -> FakeOutput:
        assert card == "Fake"
        fake = FakeOutput(
            [FakeControl(ControlName("PCM", 1)), FakeControl(ControlName("PCM", 0))], speed=4.0
        )
        made.append(fake)
        return fake

    monkeypatch.setitem(output.OUTPUTS, "alsa", make)
    return made


def sources(tmp_path: Path) -> tuple[Path, marker.Marked, marker.Marked]:
    """The two marked sources: `talker.wav`, the speech clip, and `own.wav`,
    half a second of a stepped stereo tone.
    """
    folder = tmp_path / "sources"
    folder.mkdir()
    talker = marker.mark(SPEECH, folder / "talker.wav", TALKER_MEMBER)
    tone = tmp_path / "tone.wav"
    frames = 8000
    with wave.open(str(tone), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(
            b"".join(
                (8192 if (i // 4) % 2 else -8192).to_bytes(2, "little", signed=True)
                for i in range(2 * frames)
            )
        )
    own = marker.mark(tone, folder / "own.wav", SPEAKER_MEMBER)
    assert isinstance(talker, marker.Marked) and isinstance(own, marker.Marked)
    return folder, talker, own


def lab(tmp_path: Path, folder: Path) -> Path:
    path = tmp_path / "lab.toml"
    path.write_text(
        lab_toml(
            json.dumps(STAND_IN),
            "clean",
            sample_clock="flex",
            pulse_client=json.dumps(client_program()),
        )
        + "\n"
        + speaker_toml("Fake", folder, SPEAKER_CAP_MB)
        + "\n"
        + talker_toml("desk speaker", folder, TALKER_CAP_MB)
    )
    return path


def frames_of(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        return w.readframes(w.getnframes())


def us(mark: Any) -> int:
    reading = mark["at"]
    assert reading["machine"] == MACHINE
    value: int = reading["monotonic"]["us"]
    return value


def test_the_example_dry_runs_with_no_problem(
    tmp_path: Path, server: StandInServer, speakers: list[FakeOutput]
) -> None:
    folder, _, _ = sources(tmp_path)
    dry = runner.dry_run(lab(tmp_path, folder), MACHINE, EXAMPLE, PINNED)
    assert isinstance(dry, dict), dry
    report_: Any = dry
    assert report_["problems"] == [] and report_["ok"] is True, report_["problems"]
    assert report_["trace"] == {"complete": True, "commands": 6, "stopped_at": None}
    talker = report_["roles"]["talker"]["check"]
    assert talker["ok"] is True
    assert talker["result"]["device"]["sink_name"] == SINK
    state = server.state()
    assert state["streams_made"] == 1 and state["streams"] == []
    assert server.frames() == b""
    assert server.sink_state(SINK)["volumes"] == [RAW, RAW]
    [dry_speaker] = speakers
    assert dry_speaker.opens == 1 and dry_speaker.writes == 0 and dry_speaker.sets == []


def test_the_example_runs_both_plays_overlapping(
    tmp_path: Path, server: StandInServer, speakers: list[FakeOutput]
) -> None:
    folder, talker_marked, own_marked = sources(tmp_path)
    out = tmp_path / "run"
    pointer = runner.run(
        lab(tmp_path, folder), MACHINE, EXAMPLE, str(EXAMPLE), out, None, HARNESS, None, PINNED
    )
    assert isinstance(pointer, dict), pointer
    manifest = json.loads((out / "manifest.json").read_text())
    results = json.loads((out / "results.json").read_text())
    assert results["outcome"] == "completed", results["failure"]

    roles = manifest["roles"]
    assert roles["talker"]["sample_clock"] is None
    assert roles["speaker"]["sample_clock"] == "flex"
    [talker] = roles["talker"]["sources"]
    [speaker] = roles["speaker"]["sources"]
    assert talker["output"]["kind"] == "pulse"
    assert talker["device"]["sink_name"] == SINK
    assert speaker["output"]["kind"] == "alsa"
    assert talker["source"]["marker"]["member"] == TALKER_MEMBER
    assert speaker["source"]["marker"]["member"] == SPEAKER_MEMBER

    for role, name in (("talker", "talker.wav"), ("speaker", "own.wav")):
        assert frames_of(out / "sources" / role / "0001.wav") == frames_of(folder / name)
    assert server.frames() == frames_of(folder / "talker.wav")
    assert server.sink_state(SINK)["volumes"] == [RAW, RAW]

    records = {r["role"]: r for r in results["sources"]}
    assert records.keys() == {"talker", "speaker"}
    for record, marked in ((records["talker"], talker_marked), (records["speaker"], own_marked)):
        assert record["ended"] == "completed" and record["restored"] is True
        assert record["frames_handed"] == record["frames"]
        assert record["marker"]["member"] == marked.marker.member
        assert record["first"]["delay_frames"] is not None
        assert record["last"]["frames_handed"] == record["frames"]
    assert records["talker"]["first"] == talker["first"]
    assert records["speaker"]["first"] == speaker["first"]
    assert [(c["role"], c["command"]) for c in journaled(out, ("talker", "speaker"))] == [
        ("talker", "check"),
        ("speaker", "check"),
        ("talker", "playback.play"),
        ("speaker", "playback.play"),
    ]
    for entry, cap in ((talker, TALKER_CAP_MB), (speaker, SPEAKER_CAP_MB)):
        assert entry["cap_mb"] == cap and entry["level_mb"] == cap, entry
    assert results["played"]["plays_marked"] == 2

    talker_first, talker_last = us(records["talker"]["first"]), us(records["talker"]["last"])
    speaker_first = us(records["speaker"]["first"])
    assert talker_first < speaker_first < talker_last

    assessed = report.assess(out, EXPORT_STAND_IN, False, HARNESS.encode())
    assert isinstance(assessed, report.Assessed), assessed
    written = json.loads((out / report.REPORT).read_text())
    # The stand-in recorder's file is not a frame log, so no capture is read.
    assert "capture_unreadable" in [f["finding"] for f in written["findings"]]
    assert written["answer"] != "clean"


# `examples/level_step.py`, its `speaker` on the in-process output or on the
# stand-in sound server's sink, under the lab text the bench test writes.
STEP_CAP_MB = -2400
STEP_MEMBER = 4


def agreed_now() -> bench.Window:
    """A window from two hours before now to two hours after."""
    now = bench.local_now()
    text = f"{now - timedelta(hours=2):%H:%M}-{now + timedelta(hours=2):%H:%M}"
    agreed = bench.window(text, now)
    assert agreed.inside
    return agreed


def step_lab(tmp_path: Path, on: str) -> tuple[Path, Path, marker.Marked]:
    """The step's lab file, with `speaker` on `on` (`card` or `sink`), and its
    sources: a one-second primer tone and the marked half-second tone.
    """
    folder, _, own = sources(tmp_path)
    (folder / "own.wav").rename(folder / "marked.wav")
    bench.write_tone(folder / "primer.wav", 1.0)
    output: bench.FlexOut | bench.SinkOut = (
        bench.FlexOut("Fake", STEP_CAP_MB)
        if on == "card"
        else bench.SinkOut("speaker", "desk speaker", STEP_CAP_MB)
    )
    path = tmp_path / "lab.toml"
    path.write_text(
        bench.lab_text(
            agreed_now(),
            "clean",
            folder,
            [output],
            pod_capture=json.dumps(STAND_IN),
            pulse_client=json.dumps(client_program()),
        )
    )
    return path, folder, own


def journaled(out: Path, roles: tuple[str, ...] = ("speaker",)) -> list[Any]:
    """The script's commands to `roles`, in order."""
    lines = [json.loads(line) for line in (out / "journal.jsonl").read_text().splitlines()]
    return [
        line
        for line in lines
        if line.get("role") in roles and "args" in line and line.get("by") == "script"
    ]


@pytest.mark.parametrize("on", ["card", "sink"])
def test_level_step_dry_runs_with_no_problem(
    tmp_path: Path, server: StandInServer, speakers: list[FakeOutput], on: str
) -> None:
    path, _, _ = step_lab(tmp_path, on)
    dry = runner.dry_run(path, MACHINE, LEVEL_STEP, PINNED)
    assert isinstance(dry, dict), dry
    report_: Any = dry
    assert report_["problems"] == [] and report_["ok"] is True, report_["problems"]
    assert report_["trace"] == {"complete": True, "commands": 5, "stopped_at": None}
    assert report_["roles"]["speaker"]["check"]["result"]["cap_mb"] == STEP_CAP_MB
    if on == "sink":
        assert server.state()["streams_made"] == 2 and server.frames() == b""
        assert server.sink_state(SINK)["volumes"] == [RAW, RAW]
    else:
        [dry_speaker] = speakers
        assert dry_speaker.writes == 0 and dry_speaker.sets == []


@pytest.mark.parametrize("on", ["card", "sink"])
def test_level_step_plays_the_primer_then_the_marked_source_at_the_cap(
    tmp_path: Path, server: StandInServer, speakers: list[FakeOutput], on: str
) -> None:
    path, folder, marked = step_lab(tmp_path, on)
    out = tmp_path / "run"
    pointer = runner.run(
        path, MACHINE, LEVEL_STEP, str(LEVEL_STEP), out, None, HARNESS, None, PINNED
    )
    assert isinstance(pointer, dict), pointer
    manifest = json.loads((out / "manifest.json").read_text())
    results = json.loads((out / "results.json").read_text())
    assert results["outcome"] == "completed", results["failure"]

    commands = journaled(out)
    assert [c["command"] for c in commands] == ["check", "playback.play", "playback.play"]
    assert [c["args"] for c in commands[1:]] == [
        {"source": "primer.wav", "volume_mb": STEP_CAP_MB},
        {"source": "marked.wav", "volume_mb": STEP_CAP_MB},
    ]
    entries = manifest["roles"]["speaker"]["sources"]
    assert [e["source"]["name"] for e in entries] == ["primer.wav", "marked.wav"]
    assert entries[0]["source"]["marker"] is None
    assert entries[1]["source"]["marker"]["member"] == marked.marker.member == STEP_MEMBER
    for entry in entries:
        assert entry["cap_mb"] == STEP_CAP_MB
        assert entry["level_mb"] <= STEP_CAP_MB
        if on == "card":
            assert entry["level_mb"] == STEP_CAP_MB
    records = results["sources"]
    assert [r["play"] for r in records] == [1, 2]
    for record in records:
        assert record["ended"] == "completed" and record["restored"] is True, record
    assert results["played"]["plays_marked"] == 1
    if on == "sink":
        assert entries[0]["device"]["sink_name"] == SINK
        played = frames_of(folder / "primer.wav") + frames_of(folder / "marked.wav")
        assert server.frames() == played
        assert server.state()["streams_made"] == 2
        # What the marked play found is where the primer left the sink, and
        # the stand-in's raw volumes are where they started after the last.
        assert entries[1]["found"] == entries[0]["found"]
        assert server.sink_state(SINK)["volumes"] == [RAW, RAW]
