"""The audible bench: the playback role on a reSpeaker Flex XVF3800's own
output and on a sink of the desktop's sound server, recorded by the Flex's
microphones, each output played no louder than the quietest level at which a
marked play is detected.

Audible: it plays through the Flex's output and through the sink, so it runs
only in the audible lane, inside a window the maintainer agreed, given as
`LAB_WINDOW`, `HH:MM-HH:MM` in local time. Outside it, or without it, every
test fails before any role is checked, and every lab file the test writes
gives every playback role the rest of the day as quiet hours, so the role
itself refuses to sound outside the window. `LAB_SINK` names the sink, as a
substring of its name or description; without it the tests that play on a
sink fail.

For each output a ramp finds the level: `examples/level_step.py` run at each
level, from the role's minimum upward, each run under a cap of its level. A
level is found when the Flex's raw microphones find both markers of the
marked play in three runs in a row there; a run that misses ends its level
and the ramp goes on to the next. The ramp plays nothing louder than the
level it stops at, and nothing above the output's ceiling. Every later play
on an output alone is one step above the level found, under a cap of that
level: on the Flex a marked tone, run twice, aligned and assessed; on the
sink `level_step.py` with the marked speech clip; on each a play asked over
its cap, which must be refused with nothing heard.
`examples/anchoring_scenario.py` runs with its talker on the sink, at the
sink's level, and its speaker on the Flex, overlapping. The Flex's level for
it is found by a second ramp, the scenario itself run at each level from the
Flex's level found alone upward with the talker playing, under the same
three-in-a-row rule, and the scenario is then run three times with the Flex
one step above the level that ramp found.
Last, the Flex's microphones record while a dry run opens the Flex's output.
Every run directory and exported capture is kept in the test's outputs.
"""

# TODO(audible-lane-not-run-on-moved-pin): this bench has not run on the pinned
# recorder that reads the Flex's state before a capture starts.

import array
import json
import os
import shutil
import subprocess
import time
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from brenn_lab import marker
from brenn_lab.playback.limits import peak_mb
from brenn_lab.testing import bench
from brenn_lab.testing.bench import FlexOut, Heard, SinkOut, Stop, Window
from brenn_lab.testing.lab_driver import HELD_MB, document, dry_args, run_args

LAB = os.path.abspath(os.environ["LAB"])
FRAME_LOG = os.path.abspath(os.environ["FRAME_LOG"])
LEVEL_STEP = Path(os.path.abspath(os.environ["LEVEL_STEP"]))
ANCHORING = Path(os.path.abspath(os.environ["ANCHORING"]))
TEN_SECONDS = Path(os.path.abspath(os.environ["TEN_SECONDS"]))
CARD = "Flex XVF3800 L16K6Ch"
RATE_HZ = 16000
TIMEOUT_S = 180
PRIMER_S = 4.0
MARKED_TONE_S = 1.0
MARKED_MEMBER = 4
PRIMER_PLAY, MARKED_PLAY = 1, 2
AMBIENT_FROM_S = 0.1
"""Where the room's measure starts in the capture's first second."""
PRIMER_MEASURED_FROM_S = 3.0
"""Where the primer's measure starts, after any fade-in."""
FADE_WINDOW_S = 0.25
SPEECH = Path(os.path.abspath(os.environ["SPEECH"]))
SPEECH_MEMBER = 3
REFUSED_UNDER_MB = 100
"""A refusal test's cap under the level it asks for."""
DRIFT_WITHIN_PPM = 250.0
"""Half the drift alignment searches; 8 capture frames between the Flex
marked play's two marker midpoints."""
SAME_OFFSET_FRAMES = 16.0
"""1 ms: farther than sound travels between any two of the Flex's microphones."""
TIE_WITHIN_S = 0.15
"""The clock tie's bound the run-directory reference states."""


def agreed_window() -> Window:
    text = os.environ.get("LAB_WINDOW", "")
    try:
        agreed = bench.window(text, bench.local_now())
    except ValueError as e:
        pytest.fail(f"LAB_WINDOW: {e}; run the lane with make audible-test WINDOW=HH:MM-HH:MM")
    if not agreed.inside:
        pytest.fail(f"the local time {bench.local_now():%H:%M} is outside the agreed window {text}")
    return agreed


@pytest.fixture(scope="session", autouse=True)
def agreed() -> Window:
    """The agreed window, judged before anything else in the session."""
    return agreed_window()


@pytest.fixture(scope="session")
def root() -> Path:
    path = Path(os.environ["TEST_TMPDIR"]) / "bench"
    path.mkdir()
    return path


def keep(path: Path, root: Path) -> None:
    """Copy `path`, a file or a directory under `root`, to the test's outputs
    under its path from `root`, so a reading can be looked at again without
    playing again.
    """
    saved = os.environ.get("TEST_UNDECLARED_OUTPUTS_DIR")
    if saved is None or not path.exists():
        return
    to = Path(saved) / path.relative_to(root)
    to.parent.mkdir(parents=True, exist_ok=True)
    if path.is_dir():
        shutil.copytree(path, to, dirs_exist_ok=True)
    else:
        shutil.copyfile(path, to)


@pytest.fixture(scope="session")
def sources(root: Path) -> Path:
    """`primer.wav`, PRIMER_S of the 1 kHz tone, and `marked.wav`, a
    MARKED_TONE_S tone marked with member MARKED_MEMBER.
    """
    folder = root / "sources"
    folder.mkdir()
    bench.write_tone(folder / "primer.wav", PRIMER_S)
    tone = bench.write_tone(root / "tone.wav", MARKED_TONE_S)
    assert isinstance(marker.mark(tone, folder / "marked.wav", MARKED_MEMBER), marker.Marked)
    keep(folder, root)
    return folder


def start(args: list[str]) -> subprocess.CompletedProcess[str]:
    done = subprocess.run(
        [LAB, *args], capture_output=True, text=True, timeout=TIMEOUT_S, check=False
    )
    print(done.stderr, end="")
    return done


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


@dataclass(frozen=True)
class Step:
    """One run of the ramp: its level, whether the marked play was detected,
    whether every play ran as asked, and each raw microphone's measures.
    """

    level_mb: int
    run: Path
    detected: bool
    played: bool
    heard: tuple[Heard, ...]


Ramp = bench.Ramp[Step]
"""A ramp on one output alone."""


@dataclass(frozen=True)
class OverlapStep:
    """One run of the ramp under the talker: the Flex speaker's level,
    whether its play and the talker's were detected, and whether every play
    of both roles ran as asked.
    """

    level_mb: int
    run: Path
    detected: bool
    talker_detected: bool
    played: bool


def export(run: Path, started: Any) -> Path:
    """The run's one capture as a WAV, exported by the pinned reader, which
    must report no hole.
    """
    wav = run.parent / "capture.wav"
    done = subprocess.run(
        [FRAME_LOG, "export-wav", str(run / str(started["file"]["path"])), str(wav)],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    exported = document(done.stdout)
    assert exported["holes"] == [], exported["holes"]
    return wav


def measure(run: Path, manifest: Any) -> tuple[Heard, ...]:
    """Each raw microphone's measures of a step's capture, printed with the
    primer's 1 kHz level in quarter-second windows, where a fade-in shows.
    """
    [started] = manifest["roles"]["mic"]["captures"]
    primer, marked = manifest["roles"]["speaker"]["sources"]
    rate, mics = bench.channels(export(run, started), started["layout"])
    assert rate == RATE_HZ, rate
    primer_at = bench.tie(primer["first"], started["start"], rate)
    marked_at = bench.tie(marked["first"], started["start"], rate)
    marked_s = int(marked["source"]["frames"]) / int(marked["source"]["rate_hz"])
    heard = []
    for name, samples in mics.items():
        if not name.startswith(bench.RAW_MIC):
            continue
        ambient = bench.span(samples, rate, AMBIENT_FROM_S * rate, bench.MEASURE_S)
        primer_from = primer_at + PRIMER_MEASURED_FROM_S * rate
        one = Heard(
            channel=name,
            primer_db=bench.tone_db(bench.span(samples, rate, primer_from, bench.MEASURE_S)),
            primer_ambient_db=bench.tone_db(ambient),
            band_db=bench.band_db(bench.span(samples, rate, marked_at, marked_s), rate),
            band_ambient_db=bench.band_db(ambient, rate),
        )
        fade = bench.windows_db(bench.span(samples, rate, primer_at, PRIMER_S), FADE_WINDOW_S)
        print(
            f"  {name}: primer 1 kHz {one.primer_db:.1f} dB against {one.primer_ambient_db:.1f};"
            f" marked play's band {one.band_db:.1f} dB against {one.band_ambient_db:.1f}"
        )
        print(f"  {name}: primer 1 kHz per {FADE_WINDOW_S} s, dB: {[round(v, 1) for v in fade]}")
        heard.append(one)
    return tuple(heard)


def step(
    ramp_dir: Path, index: int, level: int, out: FlexOut | SinkOut, agreed: Window, folder: Path
) -> Step | str:
    """Run `level_step.py` once under a cap of `level`, the ramp's run
    `index`: the run, or why it could not be read (`not_played`, with what
    the run printed).
    """
    here = ramp_dir / f"{index:02d}_{level}"
    here.mkdir()
    run = here / "run"
    try:
        lab = here / "lab.toml"
        lab.write_text(bench.lab_text(agreed, CARD, folder, [out]))
        done = start(run_args(lab, LEVEL_STEP, run))
        print(f"run {index}, {level} mB: lab run exited {done.returncode}")
        if done.returncode != 0 or not (run / "alignment.json").exists():
            return f"run exited {done.returncode}: {done.stdout}"
        alignment = read_json(run / "alignment.json")
        manifest, results = read_json(run / "manifest.json"), read_json(run / "results.json")
        found = bench.detected(alignment, "speaker", MARKED_PLAY)
        for channel, (s, e) in bench.scores(bench.pair(alignment, "speaker", MARKED_PLAY)).items():
            print(f"  {channel}: start marker {s}, end marker {e}")
        ran = bench.played(results["sources"], manifest["roles"]["speaker"]["sources"], level)
        print(f"run {index}, {level} mB: detected {found}, played {ran}")
        return Step(level, run, found, ran, measure(run, manifest))
    finally:
        keep(here, ramp_dir.parent)


def checkpoint(taken: Step) -> Stop:
    """The alone ramp's checkpoint on a step's measures, printed."""
    reading = bench.checkpoint(taken.heard)
    print(f"{taken.level_mb} mB: checkpoint {reading}")
    return reading


def ramp(
    name: str,
    make: Callable[[int], FlexOut | SinkOut],
    ceiling_mb: int,
    silent_min: bool,
    root: Path,
    agreed: Window,
    folder: Path,
) -> Ramp:
    """Find the quietest level at which `make(level)`'s marked play is
    detected three runs in a row, from the output's minimum, as
    `bench.climb` runs it.
    `silent_min` says the output's minimum is silence, so a detection there
    is a defect.
    """
    ramp_dir = root / name
    ramp_dir.mkdir()
    lab = ramp_dir / "minimum.toml"
    lab.write_text(bench.lab_text(agreed, CARD, folder, [make(ceiling_mb)]))
    keep(lab, root)
    done = start(dry_args(lab, LEVEL_STEP))
    report = document(done.stdout) if done.stdout.count("\n") == 1 else None
    if done.returncode != 0 or report is None or report["ok"] is not True:
        return Ramp(name, stop="dry_run", detail=done.stdout)
    controls = report["roles"]["speaker"]["check"]["result"]["controls"]
    [level_control] = [c for c in controls if c["part"] == "level"]
    min_mb = int(level_control["range"]["min_mb"])
    levels = bench.schedule(min_mb, ceiling_mb)
    print(f"{name}: minimum {min_mb} mB, ceiling {ceiling_mb} mB, levels {levels}")
    return bench.climb(
        name,
        levels,
        lambda index, level: step(ramp_dir, index, level, make(level), agreed, folder),
        checkpoint,
        silent_min,
    )


def report_ramp(result: bench.Ramp[Any]) -> None:
    print(f"{result.output} ramp: {result.stop} {result.detail}")
    for s in result.steps:
        print(f"  {s}")


def found_of(result: bench.Ramp[Any]) -> int:
    """The level the ramp found. A ramp that found none fails the test that
    needs it, naming how it stopped.
    """
    if result.found_mb is None:
        pytest.fail(f"the {result.output} ramp found no level: {result.stop} {result.detail}")
    return result.found_mb


def level_of(result: bench.Ramp[Any], ceiling_mb: int) -> int:
    """The level of the plays the ramp's result sets: one step above the
    level it found.
    """
    return bench.play_level(found_of(result), ceiling_mb)


@pytest.fixture(scope="session")
def flex_ramp(root: Path, agreed: Window, sources: Path) -> Ramp:
    return ramp(
        "flex",
        lambda level: FlexOut(CARD, level),
        bench.FLEX_CEILING_MB,
        False,
        root,
        agreed,
        sources,
    )


@pytest.fixture(scope="session")
def sink() -> str:
    selector = os.environ.get("LAB_SINK", "")
    if not selector:
        pytest.fail("LAB_SINK is empty: run the lane with make audible-test SINK=<sink>")
    return selector


NO_COMMAND = """\
from brenn_lab import playback
from brenn_lab.script import Lab

ROLES = {"speaker": playback.KIND}


def run(lab: Lab) -> None:
    pass
"""


def sink_volume(root: Path, name: str, agreed: Window, folder: Path, selector: str) -> Any:
    """Every channel of the sink as `check` finds it: a dry run of a script
    that sends no command, which makes no stream and sets nothing.
    """
    here = root / name
    here.mkdir()
    script = here / "no_command.py"
    script.write_text(NO_COMMAND)
    lab = here / "lab.toml"
    out = SinkOut("speaker", selector, bench.SINK_CEILING_MB)
    lab.write_text(bench.lab_text(agreed, CARD, folder, [out]))
    done = start(dry_args(lab, script))
    keep(here, root)
    assert done.returncode == 0, done.stdout
    report = document(done.stdout)
    assert report["ok"] is True, report["problems"]
    [control] = report["roles"]["speaker"]["check"]["result"]["controls"]
    return control["channels"]


@pytest.fixture(scope="session")
def sink_start(root: Path, agreed: Window, sources: Path, sink: str) -> Any:
    """The sink's volume before the bench played on it."""
    return sink_volume(root, "sink_start", agreed, sources, sink)


def sink_back(
    root: Path, name: str, agreed: Window, folder: Path, selector: str, start_: Any
) -> None:
    """The sink's volume, read after a test's plays, is where the bench found
    it on every channel.
    """
    after = sink_volume(root, name, agreed, folder, selector)
    print(f"sink volume after {name}: {after}")
    assert after == start_, (
        f"the sink's volume is {after}, not {start_} as the bench found it: a play left it"
        " there or something else on the desktop moved it during the run; put it back by hand"
    )


@pytest.fixture(scope="session")
def sink_ramp(root: Path, agreed: Window, sources: Path, sink: str, sink_start: Any) -> Ramp:
    """The sink's ramp, made after its starting volume was read and printed
    before its volume is read back, however it ended.
    """
    try:
        result = ramp(
            "sink",
            lambda level: SinkOut("speaker", sink, level),
            bench.SINK_CEILING_MB,
            True,
            root,
            agreed,
            sources,
        )
        report_ramp(result)
        return result
    finally:
        sink_back(root, "sink_after_ramp", agreed, sources, sink, sink_start)


def test_flex_level(flex_ramp: Ramp) -> None:
    report_ramp(flex_ramp)
    assert flex_ramp.stop == "found", (flex_ramp.stop, flex_ramp.detail)
    level = level_of(flex_ramp, bench.FLEX_CEILING_MB)
    print(f"flex: found {flex_ramp.found_mb} mB, plays at {level} mB")


def test_sink_level(sink_start: Any, sink_ramp: Ramp) -> None:
    print(f"sink volume before the bench: {sink_start}")
    assert sink_ramp.stop == "found", (sink_ramp.stop, sink_ramp.detail)
    level = level_of(sink_ramp, bench.SINK_CEILING_MB)
    print(f"sink: found {sink_ramp.found_mb} mB, plays at {level} mB")


@pytest.fixture(scope="session")
def flex_level(flex_ramp: Ramp) -> int:
    return level_of(flex_ramp, bench.FLEX_CEILING_MB)


@pytest.fixture(scope="session")
def sink_level(sink_ramp: Ramp) -> int:
    return level_of(sink_ramp, bench.SINK_CEILING_MB)


def script(path: Path, roles: str, body: str) -> Path:
    """A script whose ROLES are `mic` and the playback roles `roles`, each
    bound to a name of its own, running `body`.
    """
    names = roles.split()
    path.write_text(
        "from brenn_lab import capture, playback\n"
        "from brenn_lab.script import Lab, RoleFailure\n\n"
        'ROLES = {"mic": capture.KIND, '
        + ", ".join(f'"{n}": playback.KIND' for n in names)
        + "}\n\n\n"
        "def run(lab: Lab) -> None:\n"
        '    mic = lab.role("mic")\n'
        + "".join(f'    {n} = lab.role("{n}")\n' for n in names)
        + body
    )
    return path


def frames_of(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        return w.readframes(w.getnframes())


def source_peak_mb(path: Path) -> int | None:
    """A 16-bit WAV's peak, as the role states a source's."""
    samples = array.array("h", frames_of(path))
    return peak_mb(max(abs(v) for v in samples), 16)


def align(run: Path) -> Any:
    """`lab align --run` on a finished run, which must exit 0; the
    `alignment.json` it wrote.
    """
    done = start(["align", "--run", str(run)])
    assert done.returncode == 0, done.stdout
    assert document(done.stdout)["ok"] is True, done.stdout
    return read_json(run / "alignment.json")


def latency(pair_: Any, first: Any, capture_start: Any, rate: int) -> None:
    """Print, per raw microphone, where alignment put copy frame 0 against
    the clock tie of the play's first mark `first`, and the path's latency:
    the capture's arrival of copy frame 0 less the first mark's reading.
    """
    tied = bench.tie(first, capture_start, rate)
    mark_s = (int(first["at"]["monotonic"]["us"]) - int(capture_start["monotonic"]["us"])) / 1e6
    print(
        f"  first mark: frames_handed {first['frames_handed']}, delay_frames"
        f" {first['delay_frames']}, clock tie puts copy frame 0 at {tied:.1f}"
    )
    for c in bench.raw_channels(pair_):
        offset = c["offset_frames"]
        if offset is None:
            print(f"  {c['channel']}: not located")
            continue
        print(
            f"  {c['channel']}: drift_ppm {c['drift_ppm']}, offset_frames {offset:.1f},"
            f" ratio_basis {c['ratio_basis']}, offset less tie {(offset - tied) / rate * 1000:.1f}"
            f" ms, latency from the first mark {(offset / rate - mark_s) * 1000:.1f} ms"
        )


def run_failed_over_cap(out: Path) -> None:
    """A run whose one play was refused over its cap: nothing played."""
    results = read_json(out / "results.json")
    assert results["outcome"] == "failed", results
    failure = results["failure"]
    assert (failure["where"], failure["reason"], failure["command"]) == (
        "role",
        "over_cap",
        "playback.play",
    ), failure
    assert results["sources"] == [] and results["played"]["plays_started"] == 0, results
    assert not (out / "sources").exists()


def refused_silent(here: Path, reference: Step, role: str) -> None:
    """Run `here`'s `refused.py` under `here`'s lab file: the play over the
    cap is refused, and the capture is silent against the primer the ramp
    heard at the level it found, where that primer stood clear of the room.
    """
    out = here / "run"
    try:
        done = start(run_args(here / "lab.toml", here / "refused.py", out))
        assert done.returncode == 1, done.stdout
        run_failed_over_cap(out)
        assert read_json(out / "results.json")["failure"]["role"] == role
        manifest = read_json(out / "manifest.json")
        [started] = manifest["roles"]["mic"]["captures"]
        [captured] = read_json(out / "results.json")["captures"]
        assert captured["loss_free"] is True, captured
        rate, mics = bench.channels(export(out, started), started["layout"])
        assert rate == RATE_HZ, rate
    finally:
        keep(here, here.parent)
    references = {h.channel: h for h in reference.heard}
    loud = []
    for name, samples in mics.items():
        if not name.startswith(bench.RAW_MIC):
            continue
        spans = bench.windows_db(samples, bench.MEASURE_S)
        ref = references[name]
        verdict = bench.silence(ref, spans)
        print(
            f"  {name}: 1 kHz per {bench.MEASURE_S} s {[round(v, 1) for v in spans]} against the"
            " primer"
            f" {ref.primer_db:.1f} dB over a room of {ref.primer_ambient_db:.1f} dB"
        )
        if verdict is None:
            print(f"  {name}: silence not judged at this level")
        elif not verdict:
            loud.append(name)
    assert not loud, f"sound during a refused play on {loud}"


REFUSED = """\
    mic.send(capture.Start())
    try:
        {role}.send(playback.Play(source="primer.wav", volume_mb={level}))
    except RoleFailure:
        pass
    lab.wait(4.0)
    mic.send(capture.Stop())
"""


def flex_marked_play(
    root: Path, run: Path, lab: Path, play: Path, marked: Path, level: int
) -> None:
    """One run of the Flex's marked play into `run`, under `root`, held on
    its record, its kept copy, its alignment and its assessment.
    """
    run.parent.mkdir()
    try:
        done = start(run_args(lab, play, run))
        assert done.returncode == 0, done.stdout
        manifest, results = read_json(run / "manifest.json"), read_json(run / "results.json")
        assert (results["outcome"], results["failure"]) == ("completed", None), results
        [entry], [record] = manifest["roles"]["speaker"]["sources"], results["sources"]
        frames = len(frames_of(marked)) // 4
        assert bench.healthy(record), record
        assert record["stopped_by"] is None and record["as_sent"] is True, record
        assert record["frames"] == frames and record["frames_handed"] == frames, record
        assert record["file"] == entry["file"] and entry["file"]["in_run"] is True
        assert frames_of(run / str(entry["file"]["path"])) == frames_of(marked)
        assert entry["source"]["name"] == marked.name
        assert entry["source"]["peak_mb"] == source_peak_mb(marked)
        assert entry["source"]["marker"]["member"] == MARKED_MEMBER
        granted = entry["granted"]
        assert (granted["rate_hz"], granted["channels"], granted["sample_format"]) == (
            RATE_HZ,
            2,
            "s16_le",
        ), granted
        wanted = {"level": level, "held": HELD_MB}
        assert sorted(c["part"] for c in entry["controls"]) == ["held", "level"], entry
        for control in entry["controls"]:
            for channel in control["channels"]:
                assert channel["volume_mb"] == wanted[control["part"]], control
                assert channel["switch"] is True, control
        assert (entry["cap_mb"], entry["level_mb"]) == (level, level), entry
        assert results["played"] == {
            "plays_started": 1,
            "plays_completed": 1,
            "plays_marked": 1,
            "frames_handed": frames,
        }
        [started] = manifest["roles"]["mic"]["captures"]
        [captured] = results["captures"]
        assert captured["loss_free"] is True and captured["gaps"] == [], captured
        assessment = read_json(run / "assessment.json")
        alignment = align(run)
        wav = export(run, started)
    finally:
        keep(run.parent, root)

    pair_ = bench.pair(alignment, "speaker", 1)
    for channel, (s, e) in bench.scores(pair_).items():
        print(f"  {channel}: start marker {s}, end marker {e}")
    latency(pair_, entry["first"], started["start"], RATE_HZ)
    raws = bench.raw_channels(pair_)
    for c in raws:
        assert (c["start"]["outcome"], c["end"]["outcome"]) == ("found", "found"), c
        assert (c["start"]["instances"], c["end"]["instances"]) == (1, 1), c
        assert c["drift_ppm"] is not None and abs(c["drift_ppm"]) <= DRIFT_WITHIN_PPM, c
    offsets = [float(c["offset_frames"]) for c in raws]
    assert max(offsets) - min(offsets) <= SAME_OFFSET_FRAMES, offsets
    tied = bench.tie(entry["first"], started["start"], RATE_HZ)
    for offset in offsets:
        assert abs(offset - tied) <= TIE_WITHIN_S * RATE_HZ, (offset, tied)

    assert assessment["answer"] == "clean", assessment["findings"]
    assert assessment["not_assessed"] == [], assessment["not_assessed"]
    [assessed] = assessment["plays"]
    assert assessed["marker"]["located"] is True, assessed["marker"]
    found_in = {f["channel"]: f for f in assessed["marker"]["found_in"]}
    for c in raws:
        assert found_in[c["channel"]]["drift_ppm"] == c["drift_ppm"], (found_in, c)
    [capture_] = assessment["captures"]
    levels = {c["channel"]: c for c in capture_["channels"]}
    rate, mics = bench.channels(wav, started["layout"])
    for c in raws:
        name = c["channel"]
        assert name in levels and levels[name]["silent"] is False, levels.get(name)
        tone_at = float(c["offset_frames"]) + bench.MARKED_TONE_FROM_S * rate
        tone = bench.tone_db(bench.span(mics[name], rate, tone_at, bench.MEASURE_S))
        room = bench.tone_db(bench.span(mics[name], rate, AMBIENT_FROM_S * rate, bench.MEASURE_S))
        print(
            f"  {name}: rms_mb {levels[name]['rms_mb']}, peak_mb {levels[name]['peak_mb']};"
            f" marked tone 1 kHz {tone:.1f} dB against {room:.1f} dB in the first second"
        )


FLEX_PLAY = """\
    mic.send(capture.Start())
    lab.wait(1.0)
    played = speaker.send(playback.Play(source="marked.wav", volume_mb={level}))
    lab.wait(played.source.frames / played.source.rate_hz + 1.0)
    mic.send(capture.Stop())
"""


def test_flex_marked_play_aligned_and_assessed(
    root: Path, agreed: Window, sources: Path, flex_level: int
) -> None:
    here = root / "flex_marked"
    here.mkdir()
    lab = here / "lab.toml"
    lab.write_text(bench.lab_text(agreed, CARD, sources, [FlexOut(CARD, flex_level)]))
    play = script(here / "play.py", "speaker", FLEX_PLAY.format(level=flex_level))
    keep(here, root)
    for n in (1, 2):
        print(f"flex marked play, run {n}, at {flex_level} mB")
        flex_marked_play(
            root, here / f"run{n}" / "run", lab, play, sources / "marked.wav", flex_level
        )


def test_flex_over_cap_is_refused_and_silent(
    root: Path, agreed: Window, sources: Path, flex_ramp: Ramp, flex_level: int
) -> None:
    here = root / "flex_refused"
    here.mkdir()
    cap = flex_level - REFUSED_UNDER_MB
    (here / "lab.toml").write_text(bench.lab_text(agreed, CARD, sources, [FlexOut(CARD, cap)]))
    script(here / "refused.py", "speaker", REFUSED.format(role="speaker", level=flex_level))
    print(f"flex: asking {flex_level} mB under a cap of {cap} mB")
    refused_silent(here, flex_ramp.steps[-1], "speaker")


@pytest.fixture(scope="session")
def speech_sources(root: Path) -> Path:
    """`primer.wav` as in `sources`, and `marked.wav`, the speech clip marked
    with member SPEECH_MEMBER.
    """
    folder = root / "speech_sources"
    folder.mkdir()
    bench.write_tone(folder / "primer.wav", PRIMER_S)
    assert isinstance(marker.mark(SPEECH, folder / "marked.wav", SPEECH_MEMBER), marker.Marked)
    keep(folder, root)
    return folder


def test_sink_marked_play(
    root: Path,
    agreed: Window,
    sink: str,
    sink_start: Any,
    sink_level: int,
    speech_sources: Path,
) -> None:
    here = root / "sink_marked"
    here.mkdir()
    out = SinkOut("speaker", sink, sink_level)
    try:
        taken = step(here, 0, sink_level, out, agreed, speech_sources)
    finally:
        sink_back(root, "sink_after_marked", agreed, speech_sources, sink, sink_start)
    assert not isinstance(taken, str), taken
    run = taken.run
    manifest, results = read_json(run / "manifest.json"), read_json(run / "results.json")
    entries, records = manifest["roles"]["speaker"]["sources"], results["sources"]
    assert [r["play"] for r in records] == [PRIMER_PLAY, MARKED_PLAY], records
    for record in records:
        assert bench.healthy(record), record
    marked = entries[MARKED_PLAY - 1]
    granted = marked["granted"]
    assert (granted["rate_hz"], granted["channels"], granted["sample_format"]) == (
        RATE_HZ,
        1,
        "s16_le",
    ), granted
    device = marked["device"]
    named = [device["sink_name"], device["description"] or ""]
    assert device["selector"] == sink and any(sink in n for n in named), device
    print(f"  sink: {device}")
    for entry in entries:
        assert entry["cap_mb"] == sink_level, entry
        for control in entry["controls"]:
            for channel in control["channels"]:
                assert channel["volume_mb"] <= sink_level, control
                print(f"  {channel}: {sink_level - channel['volume_mb']} mB under {sink_level}")
    alignment = align(run)
    keep(here, root)
    assert bench.detected(alignment, "speaker", MARKED_PLAY), alignment["pairs"]
    pair_ = bench.pair(alignment, "speaker", MARKED_PLAY)
    [started] = manifest["roles"]["mic"]["captures"]
    latency(pair_, marked["first"], started["start"], RATE_HZ)
    for c in bench.raw_channels(pair_):
        assert c["ratio_basis"] == "measured", c


def test_sink_over_cap_is_refused_and_silent(
    root: Path,
    agreed: Window,
    sources: Path,
    sink: str,
    sink_start: Any,
    sink_ramp: Ramp,
    sink_level: int,
) -> None:
    here = root / "sink_refused"
    here.mkdir()
    cap = sink_level - REFUSED_UNDER_MB
    (here / "lab.toml").write_text(
        bench.lab_text(agreed, CARD, sources, [SinkOut("speaker", sink, cap)])
    )
    script(here / "refused.py", "speaker", REFUSED.format(role="speaker", level=sink_level))
    print(f"sink: asking {sink_level} mB under a cap of {cap} mB")
    try:
        refused_silent(here, sink_ramp.steps[-1], "speaker")
    finally:
        sink_back(root, "sink_after_refused", agreed, sources, sink, sink_start)


PRIMING = """\
from brenn_lab import playback
from brenn_lab.script import Lab

ROLES = {{"talker": playback.KIND}}


def run(lab: Lab) -> None:
    played = lab.role("talker").send(playback.Play(source="primer.wav", volume_mb={level}))
    lab.wait(played.source.frames / played.source.rate_hz + 1.0)
"""


@pytest.fixture(scope="session")
def anchoring_sources(root: Path) -> Path:
    """`primer.wav` as in `sources`; `talker.wav`, the speech clip marked
    with member SPEECH_MEMBER; `own.wav`, the MARKED_TONE_S tone marked with
    member MARKED_MEMBER.
    """
    folder = root / "anchoring_sources"
    folder.mkdir()
    bench.write_tone(folder / "primer.wav", PRIMER_S)
    assert isinstance(marker.mark(SPEECH, folder / "talker.wav", SPEECH_MEMBER), marker.Marked)
    tone = bench.write_tone(root / "anchoring_tone.wav", MARKED_TONE_S)
    assert isinstance(marker.mark(tone, folder / "own.wav", MARKED_MEMBER), marker.Marked)
    keep(folder, root)
    return folder


@dataclass(frozen=True)
class Scenario:
    """One run of the anchoring scenario: its run directory, and why it
    could not be read, with what the run printed, or `None`.
    """

    run: Path
    failed: str | None


def scenario(
    root: Path,
    here: Path,
    speaker_mb: int,
    talker_mb: int,
    agreed: Window,
    folder: Path,
    selector: str,
) -> Scenario:
    """Run `examples/anchoring_scenario.py` once into `here`, under `root`:
    `speaker` on the Flex under a cap of `speaker_mb`, `talker` on the sink
    `selector` under a cap of `talker_mb`, right after a priming run that
    plays `primer.wav` on `talker`. The example plays each source at its
    role's cap, so nothing in the run is louder than either.
    """
    here.mkdir(parents=True)
    lab = here / "lab.toml"
    outputs: list[FlexOut | SinkOut] = [
        FlexOut(CARD, speaker_mb),
        SinkOut("talker", selector, talker_mb),
    ]
    lab.write_text(bench.lab_text(agreed, CARD, folder, outputs))
    priming = here / "priming.py"
    priming.write_text(PRIMING.format(level=talker_mb))
    run = here / "run"
    try:
        primed = start(run_args(lab, priming, here / "priming"))
        print(f"anchoring: priming run on the sink at {talker_mb} mB exited {primed.returncode}")
        if primed.returncode != 0:
            return Scenario(run, f"priming run exited {primed.returncode}: {primed.stdout}")
        done = start(run_args(lab, ANCHORING, run))
        print(
            f"anchoring: speaker at {speaker_mb} mB, talker at {talker_mb} mB:"
            f" lab run exited {done.returncode}"
        )
        if done.returncode != 0 or not (run / "alignment.json").exists():
            return Scenario(run, f"run exited {done.returncode}: {done.stdout}")
        return Scenario(run, None)
    finally:
        keep(here, root)


def own_tone(root: Path, run: Path) -> tuple[bench.Standout, ...]:
    """Each raw microphone's reading of the Flex speaker's tone in a
    scenario run, as `bench.marked_tone` places it in the run's capture.
    """
    manifest, results = read_json(run / "manifest.json"), read_json(run / "results.json")
    [started] = manifest["roles"]["mic"]["captures"]
    first = bench.first_mark(results["sources"], "speaker")
    wav = export(run, started)
    keep(wav, root)
    rate, mics = bench.channels(wav, started["layout"])
    assert rate == RATE_HZ, rate
    read = bench.marked_tone(first, started["start"], mics, rate)
    for r in read:
        print(f"  {r.channel}: own tone 1 kHz {r.tone_db:.1f} dB, neighbours {r.neighbour_db:.1f}")
    return read


def overlap_step(
    root: Path,
    ramp_dir: Path,
    index: int,
    level: int,
    talker_mb: int,
    agreed: Window,
    folder: Path,
    selector: str,
) -> OverlapStep | str:
    """One scenario run with the Flex speaker under a cap of `level`, the
    ramp's run `index`: the run, or why it could not be read (`not_played`,
    with what it printed).
    """
    taken = scenario(
        root, ramp_dir / f"{index:02d}_{level}", level, talker_mb, agreed, folder, selector
    )
    if taken.failed is not None:
        return taken.failed
    run = taken.run
    alignment = read_json(run / "alignment.json")
    manifest, results = read_json(run / "manifest.json"), read_json(run / "results.json")
    for role in ("talker", "speaker"):
        print(f"  {role}:")
        for channel, (s, e) in bench.scores(bench.pair(alignment, role, 1)).items():
            print(f"  {channel}: start marker {s}, end marker {e}")
    found = bench.detected(alignment, "speaker", 1)
    talker_found = bench.detected(alignment, "talker", 1)
    ran = all(
        bench.played(
            [r for r in results["sources"] if r["role"] == role],
            manifest["roles"][role]["sources"],
            asked,
        )
        for role, asked in (("speaker", level), ("talker", talker_mb))
    )
    print(
        f"run {index}, {level} mB: speaker detected {found}, talker detected {talker_found},"
        f" played {ran}"
    )
    return OverlapStep(level, run, found, talker_found, ran)


Overlap = bench.Ramp[OverlapStep]
"""The ramp under the talker."""


def overlap(
    root: Path, found_mb: int, talker_mb: int, agreed: Window, folder: Path, selector: str
) -> Overlap:
    """Find the quietest level at which the Flex speaker's play is detected
    three runs in a row in the anchoring scenario with the talker at
    `talker_mb`, from `found_mb`, the level the Flex's ramp alone found, as
    `bench.climb` runs it. Its checkpoint reads the speaker's tone in the
    run's capture.
    """
    ramp_dir = root / "anchoring_ramp"
    ramp_dir.mkdir()
    levels = bench.overlap_schedule(found_mb, bench.FLEX_CEILING_MB)
    print(f"flex under the talker: talker at {talker_mb} mB, levels {levels}")

    def read_tone(taken: OverlapStep) -> Stop:
        reading = bench.overlap_checkpoint(own_tone(root, taken.run), taken.level_mb, levels[0])
        print(f"{taken.level_mb} mB: checkpoint {reading}")
        return reading

    return bench.climb(
        "flex under the talker",
        levels,
        lambda index, level: overlap_step(
            root, ramp_dir, index, level, talker_mb, agreed, folder, selector
        ),
        read_tone,
        False,
    )


@pytest.fixture(scope="session")
def anchoring_ramp(
    root: Path,
    agreed: Window,
    anchoring_sources: Path,
    sink: str,
    sink_start: Any,
    flex_ramp: Ramp,
    sink_level: int,
) -> Overlap:
    """The ramp under the talker at the sink's play level, made after both
    ramps alone and printed before the sink's volume is read back, however
    it ended.
    """
    found_mb = found_of(flex_ramp)
    try:
        result = overlap(root, found_mb, sink_level, agreed, anchoring_sources, sink)
        report_ramp(result)
        return result
    finally:
        sink_back(root, "sink_after_anchoring_ramp", agreed, anchoring_sources, sink, sink_start)


@pytest.fixture(scope="session")
def overlap_level(anchoring_ramp: Overlap) -> int:
    return level_of(anchoring_ramp, bench.FLEX_CEILING_MB)


def test_anchoring_level(anchoring_ramp: Overlap) -> None:
    result = anchoring_ramp
    assert result.stop == "found", (result.stop, result.detail)
    level = level_of(result, bench.FLEX_CEILING_MB)
    print(f"{result.output}: found {result.found_mb} mB, the scenario plays the Flex at {level} mB")


SCENARIO_RUNS = 3

START_WITHIN_S = 0.06
"""How long after its stated start a scenario play's first mark may fall:
the role's `LATE_SECONDS` and 40 ms for the first write and its mark, which
covers one step of the Flex's 32 ms grid.
"""


def located_both(run: Path) -> bool:
    """Whether a scenario run's assessment located both plays."""
    path = run / "assessment.json"
    if not path.exists():
        return False
    plays = read_json(path)["plays"]
    return len(plays) == 2 and all(p["marker"]["located"] is True for p in plays)


def test_anchoring_scenario(
    root: Path,
    agreed: Window,
    sink: str,
    sink_start: Any,
    sink_level: int,
    overlap_level: int,
    anchoring_sources: Path,
) -> None:
    failed = []
    located = 0
    try:
        for n in range(1, SCENARIO_RUNS + 1):
            print(f"anchoring scenario, run {n} of {SCENARIO_RUNS}")
            try:
                taken = scenario(
                    root,
                    root / "anchoring" / f"run{n}",
                    overlap_level,
                    sink_level,
                    agreed,
                    anchoring_sources,
                    sink,
                )
                located += located_both(taken.run)
                assert taken.failed is None, taken.failed
                scenario_held(taken.run, sink_level)
            except Exception as e:
                failed.append(f"run {n}: {type(e).__name__}: {e}")
                print(f"anchoring scenario, {failed[-1]}")
        print(f"located both plays in {located} of {SCENARIO_RUNS}")
    finally:
        sink_back(root, "sink_after_anchoring", agreed, anchoring_sources, sink, sink_start)
    assert not failed, "\n".join(failed)


def scenario_held(run: Path, sink_level: int) -> None:
    """A scenario run held to every assertion the anchoring test makes."""
    manifest, results = read_json(run / "manifest.json"), read_json(run / "results.json")
    assert (results["outcome"], results["failure"]) == ("completed", None), results
    assessment = read_json(run / "assessment.json")
    alignment = align(run)

    records = {r["role"]: r for r in results["sources"]}
    assert records.keys() == {"talker", "speaker"}, records
    for record in records.values():
        assert bench.healthy(record), record
    for role, record in records.items():
        asked, first = record["asked_start"], record["first"]
        assert asked is not None and first is not None, (role, record)
        after = record["started_after_us"]
        woke = int(record["woke"]["monotonic"]["us"]) - int(asked["monotonic"]["us"])
        print(f"  {role}: first mark {after} us after its stated start, woke {woke} us after it")
        assert 0 <= after <= START_WITHIN_S * 1e6, (role, after)
    [talker] = manifest["roles"]["talker"]["sources"]
    print(f"  sink: {talker['device']}")
    for control in talker["controls"]:
        for channel in control["channels"]:
            print(f"  {channel}: {sink_level - channel['volume_mb']} mB under {sink_level}")
    [started] = manifest["roles"]["mic"]["captures"]
    pairs = {}
    for role in ("talker", "speaker"):
        assert bench.detected(alignment, role, 1), (role, alignment["pairs"])
        pairs[role] = bench.pair(alignment, role, 1)
        print(f"  {role}:")
        for channel, (s, e) in bench.scores(pairs[role]).items():
            print(f"  {channel}: start marker {s}, end marker {e}")
        latency(pairs[role], records[role]["first"], started["start"], RATE_HZ)

    journal = [json.loads(line) for line in (run / "journal.jsonl").read_text().splitlines()]
    stated_us = bench.stated_offset_us(journal, "talker", "speaker")
    marks_us = bench.marks_gap_us(records["talker"]["first"], records["speaker"]["first"])
    offsets = bench.raw_offsets(pairs["talker"], pairs["speaker"])
    in_capture_s = bench.capture_gap_s(offsets, RATE_HZ)
    print(
        f"  stated offset {stated_us / 1000:.1f} ms, first marks {marks_us / 1000:.1f} ms apart,"
        f" gap in the capture {in_capture_s * 1000:.1f} ms"
    )

    talker_frames = int(talker["source"]["frames"]) * RATE_HZ / int(talker["source"]["rate_hz"])
    for channel, (talker_at, speaker_at) in offsets.items():
        within = talker_at <= speaker_at <= talker_at + talker_frames
        assert within, (channel, talker_at, talker_frames, speaker_at)

    assert assessment["answer"] == "clean", assessment["findings"]
    plays = {p["role"]: p for p in assessment["plays"]}
    assert plays.keys() == {"talker", "speaker"}, plays
    for role, play in plays.items():
        assert play["marker"]["located"] is True, (role, play["marker"])


OPEN_AFTER_S = 4.0
"""How far into the ten-second capture the Flex's output is opened."""
OPEN_WINDOW_S = 0.5


def monotonic_us() -> int:
    return time.clock_gettime_ns(time.CLOCK_MONOTONIC) // 1000


def test_flex_output_open_is_recorded(
    root: Path, agreed: Window, sources: Path, flex_level: int
) -> None:
    """The Flex's microphones record while a dry run of a play opens the
    Flex's output. Asserted only that both happened and the capture is
    loss-free; each raw microphone's 500-3500 Hz level is printed per half
    second, the half seconds the dry run spanned marked. No sound is judged.
    """
    here = root / "flex_open"
    here.mkdir()
    lab = here / "lab.toml"
    lab.write_text(bench.lab_text(agreed, CARD, sources, [FlexOut(CARD, flex_level)]))
    opens = here / "open.py"
    opens.write_text(OPENS.format(level=flex_level))
    run = here / "run"
    try:
        recording = subprocess.Popen(
            [LAB, *run_args(lab, TEN_SECONDS, run)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            time.sleep(OPEN_AFTER_S)
            opened_from = monotonic_us()
            dry = start(dry_args(lab, opens))
            opened_to = monotonic_us()
        finally:
            out, err = recording.communicate(timeout=TIMEOUT_S)
            print(err, end="")
        assert dry.returncode == 0, dry.stdout
        assert document(dry.stdout)["ok"] is True, dry.stdout
        assert recording.returncode == 0, out
        manifest, results = read_json(run / "manifest.json"), read_json(run / "results.json")
        assert (results["outcome"], results["failure"]) == ("completed", None), results
        [started] = manifest["roles"]["mic"]["captures"]
        [captured] = results["captures"]
        assert captured["loss_free"] is True and captured["gaps"] == [], captured
        wav = export(run, started)
    finally:
        keep(here, root)

    rate, mics = bench.channels(wav, started["layout"])
    assert rate == RATE_HZ, rate
    began_us = int(started["start"]["monotonic"]["us"])
    open_s = ((opened_from - began_us) / 1e6, (opened_to - began_us) / 1e6)
    print(f"flex output open: dry run from {open_s[0]:.2f} s to {open_s[1]:.2f} s into the capture")
    size = round(OPEN_WINDOW_S * rate)
    for name, samples in mics.items():
        if not name.startswith(bench.RAW_MIC):
            continue
        levels = []
        for i in range(0, len(samples) - size + 1, size):
            begin, end = i / rate, (i + size) / rate
            mark = "*" if begin < open_s[1] and end > open_s[0] else ""
            level = bench.band_db(bench.span(samples, rate, i, OPEN_WINDOW_S), rate)
            levels.append(f"{begin:.1f}{mark}: {level:.1f}")
        print(f"  {name}: 500-3500 Hz per {OPEN_WINDOW_S} s, dB, * during the dry run: {levels}")


OPENS = """\
from brenn_lab import playback
from brenn_lab.script import Lab

ROLES = {{"speaker": playback.KIND}}


def run(lab: Lab) -> None:
    lab.role("speaker").send(playback.Play(source="primer.wav", volume_mb={level}))
"""
