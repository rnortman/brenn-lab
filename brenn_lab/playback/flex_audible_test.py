"""The playback role on a reSpeaker Flex XVF3800 while the capture role records
it: the first sound the harness makes.

Audible: it plays through the Flex's output, so it runs only in the audible
lane, inside a window the maintainer agreed. One test, two runs of the runner
as a program, through the pinned recorder and reader. Nothing is stubbed.

Run 1 starts the capture, waits a second, plays clip A at -1800 mB and clip B
at -3000 mB, each waited out, and stops the capture. Run 2 starts the capture,
asks for clip B at -1700 mB, which is over the cap and refused, waits four
seconds and stops. What is heard: two chirps and a two-second 1 kHz tone at
24 dB under the Flex's full output, then a chirp and the tone 12 dB quieter;
silence from run 2.
"""

import array
import json
import os
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Any

from brenn_lab.testing import signals
from brenn_lab.testing.lab_driver import document, dry_args, lab_toml, run_args, speaker_toml
from brenn_lab.testing.signals import CLIP_A, CLIP_B, RATE_HZ, Clip

LAB = os.path.abspath(os.environ["LAB"])
FRAME_LOG = os.path.abspath(os.environ["FRAME_LOG"])
CARD = "Flex XVF3800 L16K6Ch"
CAPTURE_CHANNELS = 6
RAW_MIC_0 = "raw_mic_0"
CAP_MB = -1800
LOUD_MB = -1800  # play 1, clip A: at the cap
QUIET_MB = -3000  # play 2, clip B
REFUSED_MB = -1700  # run 2, clip B: one decibel over the cap
TIMEOUT_S = 180
SEARCH_S = 1.0  # each side of where the clock tie puts a chirp
FOUND_WITHIN_S = 0.3
TIE_WITHIN_S = 0.15
SAME_OFFSET_FRAMES = 2
STEP_DB = 12.0  # the two settings are 12 dB apart
STEP_WITHIN_DB = 2.0
ABOVE_FLOOR_DB = 15.0

PLAY_1 = f"""\
    mic.send(capture.Start())
    lab.wait(1.0)
    a = speaker.send(playback.Play(source="{CLIP_A.name}", volume_mb={LOUD_MB}))
    lab.wait(a.source.frames / a.source.rate_hz + 1.0)
    b = speaker.send(playback.Play(source="{CLIP_B.name}", volume_mb={QUIET_MB}))
    lab.wait(b.source.frames / b.source.rate_hz + 1.0)
    mic.send(capture.Stop())
"""

REFUSED = f"""\
    mic.send(capture.Start())
    try:
        speaker.send(playback.Play(source="{CLIP_B.name}", volume_mb={REFUSED_MB}))
    except RoleFailure:
        pass
    lab.wait(4.0)
    mic.send(capture.Stop())
"""


def workdir() -> Path:
    path = Path(os.environ["TEST_TMPDIR"]) / "flex"
    path.mkdir()
    return path


def lab_file(tmp: Path, sources: Path) -> Path:
    """`mic` as in the hardware lane and `speaker` on the same card, both
    stated on one sample clock; the level control `PCM` 1, `PCM` 0 held at
    its maximum.
    """
    path = tmp / "lab.toml"
    path.write_text(
        lab_toml("{ pinned = true }", CARD, sample_clock="flex")
        + "\n"
        + speaker_toml(CARD, sources, CAP_MB)
    )
    return path


def script(tmp: Path, name: str, body: str) -> Path:
    path = tmp / name
    path.write_text(
        "from brenn_lab import capture, playback\n"
        "from brenn_lab.script import Lab, RoleFailure\n\n"
        'ROLES = {"mic": capture.KIND, "speaker": playback.KIND}\n\n\n'
        "def run(lab: Lab) -> None:\n"
        '    mic, speaker = lab.role("mic"), lab.role("speaker")\n' + body
    )
    return path


def start(args: list[str]) -> subprocess.CompletedProcess[str]:
    done = subprocess.run(
        [LAB, *args], capture_output=True, text=True, timeout=TIMEOUT_S, check=False
    )
    print(done.stderr, end="")
    return done


def keep(path: Path) -> None:
    """Copy `path`, a file or a run directory, to the test's outputs, so a
    reading can be looked at again without playing again.
    """
    saved = os.environ.get("TEST_UNDECLARED_OUTPUTS_DIR")
    if saved is None or not path.exists():
        return
    if path.is_dir():
        shutil.copytree(path, Path(saved) / path.name)
    else:
        shutil.copyfile(path, Path(saved) / path.name)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def raw_mic_0(out: Path, started: Any) -> list[float]:
    """Raw microphone 0 of the run's one capture, exported by the pinned
    reader, which must report no hole.
    """
    capture = out / str(started["file"]["path"])
    wav = out.parent / f"{out.name}.wav"
    done = subprocess.run(
        [FRAME_LOG, "export-wav", str(capture), str(wav)],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    keep(wav)
    exported = document(done.stdout)
    assert exported["holes"] == [], exported["holes"]
    with wave.open(str(wav), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (
            CAPTURE_CHANNELS,
            2,
            RATE_HZ,
        )
        data = w.readframes(w.getnframes())
    channel = list(started["layout"]).index(RAW_MIC_0)
    samples = array.array("h", data)
    return [float(v) for v in samples[channel::CAPTURE_CHANNELS]]


def tie(first: Any, capture_start: Any) -> float:
    """Where the clock tie puts copy frame 0 in the capture, in capture
    frames: the first mark's frame at the output, at the mark's reading,
    against the capture's start reading. Both readings are the machine's
    monotonic clock.
    """
    assert first["delay_frames"] is not None, first
    at_output = int(first["frames_handed"]) - int(first["delay_frames"])
    elapsed_us = int(first["at"]["monotonic"]["us"]) - int(capture_start["monotonic"]["us"])
    return elapsed_us * RATE_HZ / 1e6 - at_output


def offsets(mic: list[float], clip: Clip, predicted: float) -> list[int]:
    """The measured offset of `clip` in the capture at each of its chirps:
    the capture frame where correlation finds the chirp, less the chirp's
    frame in the clip. The search spans SEARCH_S each side of `predicted`.
    """
    chirp = signals.chirp()
    found = []
    for at in clip.chirps:
        where = predicted + signals.frame_at(at)
        lo = max(0, round(where) - signals.frame_at(SEARCH_S))
        hi = min(len(mic), round(where) + signals.frame_at(SEARCH_S) + len(chirp))
        lag = lo + signals.find(mic[lo:hi], chirp)
        found.append(lag - signals.frame_at(at))
    return found


def tone_db(mic: list[float], clip: Clip, offset: int) -> float:
    """The 1 kHz level of `clip`'s tone in the capture: the middle second of
    the tone, placed by the measured offset.
    """
    begin = offset + signals.frame_at(clip.tone[0] + 0.5)
    assert 0 <= begin and begin + RATE_HZ <= len(mic), (begin, len(mic))
    return signals.level_db(mic[begin : begin + RATE_HZ], signals.TONE_HZ)


def spans_db(mic: list[float]) -> list[float]:
    """The 1 kHz level of every whole second of the capture."""
    return [
        signals.level_db(mic[i : i + RATE_HZ], signals.TONE_HZ)
        for i in range(0, len(mic) - RATE_HZ + 1, RATE_HZ)
    ]


def check_dry_run(lab: Path, play: Path) -> None:
    """The dry run of run 1's script reports no problem, and both roles'
    checks name one card.
    """
    done = start(dry_args(lab, play))
    assert done.returncode == 0, done.stdout
    report = document(done.stdout)
    assert report["ok"] is True and report["problems"] == [], report
    checks = {r: report["roles"][r]["check"] for r in ("mic", "speaker")}
    for role, check in checks.items():
        assert check["ok"] is True, (role, check)
    mic, speaker = (checks[r]["result"]["device"] for r in ("mic", "speaker"))
    assert (speaker["card_index"], speaker["card_long_name"]) == (
        mic["card_index"],
        mic["card_long_name"],
    ), (mic, speaker)


def check_play(entry: Any, record: Any, out: Path, clip: Clip, volume_mb: int) -> None:
    """One play's manifest entry and results record."""
    assert record["ended"] == "completed" and record["stopped_by"] is None, record
    assert record["as_sent"] is True and record["underruns"] == [], record
    assert record["frames"] == clip.frames and record["frames_handed"] == clip.frames, record
    assert record["restored"] is True and record["failure"] is None, record

    assert record["file"] == entry["file"] and entry["file"]["in_run"] is True
    with wave.open(str(out / str(entry["file"]["path"])), "rb") as w:
        copied = w.readframes(w.getnframes())
    assert copied == signals.frames(clip), clip.name

    assert entry["source"]["name"] == clip.name
    assert entry["source"]["peak_mb"] == signals.PEAK_MB
    assert entry["granted"]["rate_hz"] == RATE_HZ
    assert entry["granted"]["channels"] == signals.CHANNELS
    assert entry["granted"]["sample_format"] == "s16_le"
    wanted = {"level": volume_mb, "held": 0}
    assert sorted(c["part"] for c in entry["controls"]) == ["held", "level"], entry["controls"]
    for control in entry["controls"]:
        for channel in control["channels"]:
            assert channel["volume_mb"] == wanted[control["part"]], control
            assert channel["switch"] is True, control
    assert (entry["cap_mb"], entry["level_mb"]) == (CAP_MB, volume_mb), entry


def test_play_and_record() -> None:
    tmp = workdir()
    sources = tmp / "sources"
    sources.mkdir()
    for clip in (CLIP_A, CLIP_B):
        signals.write(clip, sources)
    lab = lab_file(tmp, sources)
    play = script(tmp, "play.py", PLAY_1)
    refused = script(tmp, "refused.py", REFUSED)

    check_dry_run(lab, play)

    out = tmp / "run1"
    done = start(run_args(lab, play, out))
    keep(out)
    assert done.returncode == 0, done.stdout
    manifest, results = read_json(out / "manifest.json"), read_json(out / "results.json")
    assert (results["outcome"], results["failure"]) == ("completed", None), results
    for role in ("mic", "speaker"):
        assert manifest["roles"][role]["sample_clock"] == "flex"
    entries, records = manifest["roles"]["speaker"]["sources"], results["sources"]
    assert [r["play"] for r in records] == [1, 2], records
    plays = [(CLIP_A, LOUD_MB), (CLIP_B, QUIET_MB)]
    for entry, record, (clip, volume) in zip(entries, records, plays, strict=True):
        check_play(entry, record, out, clip, volume)
    assert results["played"] == {
        "plays_started": 2,
        "plays_completed": 2,
        "frames_handed": CLIP_A.frames + CLIP_B.frames,
    }

    [started] = manifest["roles"]["mic"]["captures"]
    [captured] = results["captures"]
    assert captured["loss_free"] is True and captured["gaps"] == [], captured
    mic = raw_mic_0(out, started)

    differences = []
    measured = []
    for entry, (clip, _) in zip(entries, plays, strict=True):
        predicted = tie(entry["first"], started["start"])
        found = offsets(mic, clip, predicted)
        print(f"{clip.name}: first mark {entry['first']}, capture start {started['start']}")
        for offset in found:
            differences.append((predicted - offset) / RATE_HZ)
            print(f"{clip.name}: chirp offset {offset} frames, clock tie {predicted:.1f}")
        measured.append(found)
    print("clock tie less measured offset, ms:", [round(d * 1000, 2) for d in differences])
    for difference in differences:
        assert abs(difference) <= FOUND_WITHIN_S, differences
    a_offsets, [b_offset] = measured
    assert abs(a_offsets[0] - a_offsets[1]) <= SAME_OFFSET_FRAMES, a_offsets
    for difference in differences:
        assert abs(difference) <= TIE_WITHIN_S, differences

    loud = tone_db(mic, CLIP_A, a_offsets[0])
    quiet = tone_db(mic, CLIP_B, b_offset)
    floor = signals.level_db(mic[:RATE_HZ], signals.TONE_HZ)
    print(f"1 kHz: play 1 {loud:.2f} dB, play 2 {quiet:.2f} dB, first second {floor:.2f} dB")
    assert abs((loud - quiet) - STEP_DB) <= STEP_WITHIN_DB, (loud, quiet)
    assert quiet - floor >= ABOVE_FLOOR_DB, (quiet, floor)

    out = tmp / "run2"
    done = start(run_args(lab, refused, out))
    keep(out)
    assert done.returncode == 1, done.stdout
    results = read_json(out / "results.json")
    assert results["outcome"] == "failed", results
    failure = results["failure"]
    assert (failure["where"], failure["reason"], failure["role"], failure["command"]) == (
        "role",
        "over_cap",
        "speaker",
        "playback.play",
    ), failure
    assert results["sources"] == [] and results["played"]["plays_started"] == 0, results
    assert not (out / "sources").exists()
    manifest = read_json(out / "manifest.json")
    [started] = manifest["roles"]["mic"]["captures"]
    spans = spans_db(raw_mic_0(out, started))
    print("run 2, 1 kHz per second, dB:", [round(v, 2) for v in spans])
    assert spans, "run 2 captured less than a second"
    for level in spans:
        assert level <= quiet - ABOVE_FLOOR_DB, (level, quiet)
