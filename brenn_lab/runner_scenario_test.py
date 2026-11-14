"""The anchoring scenario, `examples/anchoring_scenario.py`, dry-run and run:
`mic` on the stand-in recorder, `speaker` on the in-process output in place of
ALSA, and `talker` on a sink of the stand-in sound server through the real
sink client, playing the marked speech clip.
"""

import json
import os
import wave
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from brenn_lab import marker, runner
from brenn_lab.assess import report
from brenn_lab.buildinfo import Revision
from brenn_lab.playback import output
from brenn_lab.playback.output import ControlName
from brenn_lab.testing.fake_output import FakeControl, FakeOutput
from brenn_lab.testing.lab_driver import MACHINE, lab_toml, speaker_toml, talker_toml
from brenn_lab.testing.pulse_stand_in import StandInServer, sink
from brenn_lab.testing.sink_client import client_program

STAND_IN = os.path.abspath(os.environ["STAND_IN"])
EXPORT_STAND_IN = os.path.abspath(os.environ["EXPORT_STAND_IN"])
EXAMPLE = Path(os.path.abspath(os.environ["EXAMPLE"]))
SPEECH = Path(os.path.abspath(os.environ["SPEECH"]))
HARNESS = Revision(commit="0123456789abcdef0123456789abcdef01234567", dirty=False, source="git")
PINNED = {"pod_capture": "/nonexistent/pinned/pod-capture"}
SINK = "alsa_output.usb-Desk-00.analog-stereo"
TALKER_MEMBER = 3
SPEAKER_MEMBER = 4
RAW = 50000


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
        + speaker_toml("Fake", folder, -1800)
        + "\n"
        + talker_toml("desk speaker", folder, -1800)
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
    assert report_["trace"] == {"complete": True, "commands": 4, "stopped_at": None}
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
