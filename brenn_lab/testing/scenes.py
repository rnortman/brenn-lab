"""Synthetic run directories for alignment tests.

A scene is a run's plays and captures given by construction: where each kept
copy's frame 0 lies in each capture's sound, at what level, with what drift
between the two clocks, and what the capture lost. Markers are placed by
evaluating `marker.value` at shifted and scaled times, so every answer a test
expects is one of the scene's own parameters.

A capture's sound is a stream of frames the device produced. A loss takes
frames out of it; the exported file and the reader's map are made from the
stream and its losses as brenn-pod's reader describes them, and the capture
"log" is a file the stand-in reader takes (`export_stand_in`), never a frame
log.
"""

import array
import hashlib
import json
import math
import wave
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from brenn_lab import marker
from brenn_lab.data import JsonObject, JsonValue, encode_value
from brenn_lab.marker import Carried

MACHINE = "bench"
RUN_ID = "00000000-0000-4000-8000-000000000000"
SEED = 20261113
UNMEASURED = ("clock_estimate", "unknown")


@dataclass(frozen=True)
class Play:
    """A play of `member`'s marked source (None: an unmarked one) of
    `content_frames` of silence at `rate_hz`. Its kept copy's frame 0 lies at
    stream frame `at` of every capture, at `level` times the marker's own,
    with the capture counting `drift_ppm` more frames per source frame than
    the nominal rates say. `handed` is the frames the results say were handed
    (None: all); a marker not handed whole is not sounded. `record` False
    leaves the manifest's source entry without a `marker` key.
    """

    role: str
    play: int
    member: int | None
    at: float
    rate_hz: int = 16000
    level: float = 1.0
    drift_ppm: float = 0.0
    content_frames: int = 16000
    handed: int | None = None
    record: bool = True
    underruns: tuple[int, ...] = ()


@dataclass(frozen=True)
class Loss:
    """`frames` frames lost from the stream at stream frame `at`, of `basis`."""

    at: int
    frames: int
    basis: str


@dataclass(frozen=True)
class Extra:
    """A marker that no play of the scene accounts for, sounded at stream
    frame `at`.
    """

    member: int
    which: marker.Which
    at: float
    rate_hz: int = 16000
    level: float = 1.0


@dataclass(frozen=True)
class Capture:
    """A capture of `frames` stream frames at `rate_hz`, one channel per name
    in `layout` with the gain in `gains` (1 for each when empty), noise of rms
    `noise` of full scale, and `losses`. `refuse` makes the reader refuse it
    with that message.
    """

    role: str = "mic"
    capture: int = 1
    rate_hz: int = 16000
    frames: int = 64000
    layout: tuple[str, ...] = ("raw_mic_0",)
    gains: tuple[float, ...] = ()
    noise: float = 0.001
    losses: tuple[Loss, ...] = ()
    refuse: str | None = None

    def capture_frame(self, x: float) -> float:
        """The capture frame of stream frame `x`: losses across which the
        index did not advance are taken out.
        """
        return x - sum(
            loss.frames
            for loss in self.losses
            if loss.basis in UNMEASURED and loss.at + loss.frames <= x
        )


@dataclass
class Built:
    """What a scene wrote: each play's marker record, by role and play."""

    run: Path
    carried: dict[tuple[str, int], Carried | None] = field(default_factory=dict)


def ratio(play: Play, capture: Capture) -> float:
    """Capture frames per source frame, as the scene sounds them."""
    return capture.rate_hz / play.rate_hz * (1 + play.drift_ppm / 1e6)


def midpoint(play: Play, capture: Capture, carried: Carried, which: marker.Which) -> float:
    """The capture frame at which `which` marker's midpoint sounds."""
    span = carried.start if which == "start" else carried.end
    mid = span.frame + marker.DURATION_MS / 2000 * play.rate_hz
    return capture.capture_frame(play.at + ratio(play, capture) * mid)


def build(
    run: Path,
    plays: Sequence[Play],
    captures: Sequence[Capture] = (Capture(),),
    extras: Sequence[Extra] = (),
    results: bool = True,
) -> Built:
    """Write the run directory `run` for the scene."""
    run.mkdir()
    built = Built(run)
    roles: dict[str, JsonObject] = {}
    records: list[JsonValue] = []
    for play in plays:
        entry, record = _play(run, play, built)
        section = roles.setdefault(play.role, {"kind": "playback", "sources": []})
        sources = section["sources"]
        assert isinstance(sources, list)
        sources.append(entry)
        records.append(record)
    for c in captures:
        entry = _capture(run, c, plays, built, extras)
        section = roles.setdefault(c.role, {"kind": "capture", "captures": []})
        entries = section["captures"]
        assert isinstance(entries, list)
        entries.append(entry)
    manifest: JsonObject = {"layout": 1, "run_id": RUN_ID, "roles": dict(roles)}
    (run / "manifest.json").write_text(json.dumps(manifest) + "\n")
    if results:
        out: JsonObject = {"schema": 1, "run_id": RUN_ID, "sources": records, "captures": []}
        (run / "results.json").write_text(json.dumps(out) + "\n")
    return built


def _play(run: Path, play: Play, built: Built) -> tuple[JsonObject, JsonObject]:
    folder = run / "sources" / play.role
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{play.play:04d}.wav"
    content = folder / f".{name}.content"
    _write_wav(content, array.array("h", bytes(2 * play.content_frames)), play.rate_hz, 1)
    copy = folder / name
    carried: Carried | None = None
    if play.member is None:
        content.rename(copy)
    else:
        marked = marker.mark(content, copy, play.member)
        assert isinstance(marked, marker.Marked), marked
        carried = marked.marker
        content.unlink()
    built.carried[(play.role, play.play)] = carried
    with wave.open(str(copy), "rb") as w:
        frames = w.getnframes()
    described: JsonObject = {
        "name": f"{play.role}-{play.play}.wav",
        "sha256": hashlib.sha256(copy.read_bytes()).hexdigest(),
        "frames": frames,
        "rate_hz": play.rate_hz,
        "channels": 1,
        "sample_format": "s16_le",
        "peak_mb": None,
    }
    if play.record:
        described["marker"] = encode_value(carried)
    entry: JsonObject = {
        "play": play.play,
        "source": described,
        "file": {"machine": MACHINE, "path": f"sources/{play.role}/{name}", "in_run": True},
        # A clock reading the search must never use, rough by construction.
        "first": {"frame": 0, "at": {"machine": MACHINE, "ns": int(play.at * 62500)}},
    }
    record: JsonObject = {
        "role": play.role,
        "play": play.play,
        "marker": encode_value(carried),
        "frames": frames,
        "frames_handed": frames if play.handed is None else play.handed,
        "underruns": [{"at_frame": u, "at": None, "detail": ""} for u in play.underruns],
    }
    return entry, record


def _capture(
    run: Path, c: Capture, plays: Sequence[Play], built: Built, extras: Sequence[Extra]
) -> JsonObject:
    folder = run / "captures" / c.role
    folder.mkdir(parents=True, exist_ok=True)
    log = folder / f"{c.capture:04d}.framelog"
    entry: JsonObject = {
        "capture": c.capture,
        "file": {"machine": MACHINE, "path": f"captures/{c.role}/{log.name}", "in_run": True},
        "granted": {
            "rate_hz": c.rate_hz,
            "channels": len(c.layout),
            "sample_format": "s16_le",
            "period_frames": 1024,
            "buffer_frames": 4096,
        },
        "layout": list(c.layout),
    }
    if c.refuse is not None:
        log.write_text(json.dumps({"status": 1, "stderr": c.refuse}))
        return entry
    sound = np.zeros(c.frames)
    for play in plays:
        carried = built.carried[(play.role, play.play)]
        if carried is None:
            continue
        r = ratio(play, c)
        for code, span in (
            (carried.start_code, carried.start),
            (carried.end_code, carried.end),
        ):
            if play.handed is None or play.handed >= span.frame + span.frames:
                _sound(sound, code, play.at + r * span.frame, play.rate_hz * r, play.level)
    for extra in extras:
        start, end = marker.codes(extra.member)
        code = start if extra.which == "start" else end
        scale = c.rate_hz / extra.rate_hz
        _sound(sound, code, extra.at, extra.rate_hz * scale, extra.level)
    gains = c.gains or (1.0,) * len(c.layout)
    rng = np.random.default_rng(SEED)
    channels = [g * sound + c.noise * rng.standard_normal(c.frames) for g in gains]
    stream = np.stack(channels, axis=1)
    exported, export_map = _export(stream, c.losses)
    prepared = folder / f"{c.capture:04d}.prepared.wav"
    scaled = np.clip(np.floor(exported * 32768 + 0.5), -32768, 32767).astype(np.int16)
    _write_wav(prepared, array.array("h", scaled.ravel().tobytes()), c.rate_hz, len(c.layout))
    log.write_text(json.dumps({"wav": prepared.name, "map": export_map}))
    return entry


def _sound(sound: np.ndarray, code: int, first: float, frames_per_s: float, level: float) -> None:
    """Add code `code`'s marker starting at frame `first`, its time running at
    `frames_per_s` frames of `sound` per second.
    """
    chips = marker.code(code)
    last = first + marker.DURATION_MS / 1000 * frames_per_s
    for n in range(max(0, math.ceil(first)), min(len(sound), math.floor(last) + 1)):
        value = marker.value(chips, (n - first) / frames_per_s)
        sound[n] += level * marker.AMPLITUDE * value.real


def _export(stream: np.ndarray, losses: Sequence[Loss]) -> tuple[np.ndarray, JsonObject]:
    """The exported file and the reader's map of `stream` with `losses`."""
    pieces: list[np.ndarray] = []
    runs: list[JsonValue] = []
    holes: list[JsonValue] = []
    t = wav = index = 0
    for loss in sorted(losses, key=lambda x: x.at):
        if loss.at > t:
            runs.append({"wav_frame": wav, "first_frame": index, "frames": loss.at - t})
            pieces.append(stream[t : loss.at])
            wav += loss.at - t
            index += loss.at - t
        filled = 0 if loss.basis == "unknown" else loss.frames
        holes.append(
            {"wav_frame": wav, "at_frame": index, "filled_frames": filled, "basis": loss.basis}
        )
        pieces.append(np.zeros((filled, stream.shape[1])))
        wav += filled
        if loss.basis not in UNMEASURED:
            index += loss.frames
        t = loss.at + loss.frames
    if t < len(stream):
        runs.append({"wav_frame": wav, "first_frame": index, "frames": len(stream) - t})
        pieces.append(stream[t:])
        wav += len(stream) - t
    export_map: JsonObject = {
        "wav_frames": wav,
        "runs": runs,
        "holes": holes,
        "resent": [],
        "tail": {"verdict": "ended", "offset": 0, "reason": "", "open_segment": None},
    }
    return np.concatenate(pieces), export_map


def _write_wav(path: Path, samples: array.array[int], rate_hz: int, channels: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate_hz)
        w.writeframes(samples.tobytes())
