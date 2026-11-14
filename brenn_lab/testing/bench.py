"""What the audible bench test decides from what it measured, and the lab
configurations it writes.

The agreed window and the quiet hours it gives every playback role, the
levels a ramp steps through, alone or under another output's play, and the
loop that runs it, ending at the first level where a play is detected
CONFIRM_RUNS runs in a row, whether a play was detected, the level a ramp's
result gives a play, why a ramp stops short of detection, whether a silence
can be judged against a reference tone; and the levels read off a capture
exported by the pinned reader. Every decision is a pure function of its
inputs, held by the default check on synthetic inputs; the loop is held with
stand-in runs.
"""

import wave
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol

import numpy as np
from numpy.typing import NDArray

from brenn_lab import marker
from brenn_lab.align.search import bandpass_taps
from brenn_lab.playback.limits import QuietMinutes, parse_clock, quiet_span_during
from brenn_lab.playback.role import machine_zone
from brenn_lab.testing import signals
from brenn_lab.testing.lab_driver import HELD_MB, lab_toml, speaker_toml, talker_toml

FLEX_CEILING_MB = -1800
"""No playback role on the Flex's output is configured with a cap above this."""
SINK_CEILING_MB = -2000
"""No playback role on a sink is configured with a cap above this."""
STEP_MB = 600
"""The ramp's step, and the margin of a play over the level a ramp found."""
RAMP_FROM_MB = -12000
"""Where the ramp's even steps begin, above the role's minimum."""
CHECKPOINT_ABOVE_MB = -4000
"""An undetected run above this level is classified before the next run."""
CONFIRM_RUNS = 3
"""How many runs in a row at one level must detect a play for that level to
be the level a ramp found. One is not enough: near the threshold one level
is detected in some runs and missed in others, so a single detection can
stop a ramp below the level at which the play is detected reliably.
"""
RAW_MIC = "raw_mic_"
"""The prefix of the capture channels a detection is judged on."""
ARRIVED_DB = 10.0
"""The primer tone above the room by at least this much has arrived."""
RECOGNISABLE_DB = 3.0
"""The marked play's band at least this much above the room's should have scored."""
JUDGED_DB = 15.0
"""A reference tone at least this far above the room lets a silence be judged."""
SILENT_UNDER_DB = 10.0
"""A judged silence is at least this far under the reference tone."""
STANDS_OUT_DB = 10.0
"""A tone at least this far above its neighbouring lines stands out of a span."""
NEIGHBOUR_HZ = (900, 1100)
"""The lines either side of the 1 kHz tone a stand-out is judged against;
each a whole bin of a MEASURE_S span at 16 kHz, so the tone leaks into
neither."""
MEASURE_S = 0.8
"""The span a tone's level is read over."""
OVERLAP_RISE_MB = 2000
"""How far above the first step of a ramp under another play an undetected
play whose tone stands out may go before it stops as `not_recognised`.

From the score rule: a marker's score is about the square root of its share
of the band's energy. The search's floor, about 0.1, is a marker some 20 dB
under the rest of the band; the threshold, 0.25, about 12 dB under. So 8 dB
of level carries a marker from the lowest score that says anything to the
threshold, and 12 dB more, two steps, is allowed before an undetected play
that is in the capture is a play the level does not explain.
"""
PINNED = "{ pinned = true }"
"""A machine's program as TOML: the one this harness's build carries."""

Stop = Literal["not_arrived", "not_recognised", "faint"]
RampStop = Stop | Literal["found", "ceiling", "not_played", "detected_in_silence", "dry_run"]
"""How a ramp ended: CONFIRM_RUNS detections in a row at one level, the
ceiling, a play that did not run as asked,
a checkpoint's class, a detection at a silent minimum, or a dry run that
could not read the minimum.
"""
Floats = NDArray[np.float64]


@dataclass(frozen=True)
class Window:
    """The agreed window, `start` to `end` in local civil time, `"HH:MM"`, and
    whether the time it was judged at falls inside it. A window whose end is
    not after its start wraps midnight.
    """

    start: str
    end: str
    inside: bool

    @property
    def quiet(self) -> tuple[str, str]:
        """The rest of the day, as a playback role's quiet span."""
        return (self.end, self.start)


def local_now() -> datetime:
    """The machine's local civil time, in the zone a playback role judges
    quiet hours in.
    """
    return datetime.now(machine_zone())


def window(text: str, now: datetime) -> Window:
    """Parse `HH:MM-HH:MM` and judge `now`, an aware local time, against it
    by the playback role's quiet-hours rule: inside is outside the window's
    quiet span. Anything else, or a window that starts where it ends, is a
    `ValueError`.
    """
    parts = text.split("-")
    minutes = [parse_clock(p) for p in parts]
    if len(parts) != 2 or None in minutes:
        raise ValueError(f"the window {text!r} is not HH:MM-HH:MM")
    start, end = parts
    s, e = minutes
    assert s is not None and e is not None
    if s == e:
        raise ValueError(f"the window {text!r} starts where it ends")
    inside = quiet_span_during([QuietMinutes(e, s)], now, 0.0) is None
    return Window(start, end, inside)


def schedule(min_mb: int, ceiling_mb: int) -> list[int]:
    """The ramp's levels, in order: `min_mb`, then every STEP_MB from
    RAMP_FROM_MB that lies above `min_mb` and under `ceiling_mb`, then
    `ceiling_mb`.
    """
    if min_mb >= ceiling_mb:
        raise ValueError(f"the minimum {min_mb} mB is not under the ceiling {ceiling_mb} mB")
    even = [v for v in range(RAMP_FROM_MB, ceiling_mb, STEP_MB) if v > min_mb]
    return [min_mb, *even, ceiling_mb]


def pair(alignment: Any, role: str, play: int) -> Any:
    """The one pair of `alignment.json` that holds `role`'s play `play`: the
    bench has one capture.
    """
    found = [p for p in alignment["pairs"] if p["play"] == {"role": role, "play": play}]
    if len(found) != 1:
        raise ValueError(f"{len(found)} pairs hold {role}'s play {play}; the bench has one capture")
    return found[0]


def raw_channels(pair_: Any) -> list[Any]:
    """The channels of a tried pair whose names start RAW_MIC."""
    return [c for c in pair_["channels"] if str(c["channel"]).startswith(RAW_MIC)]


def detected(alignment: Any, role: str, play: int) -> bool:
    """Whether `role`'s play `play` was detected: on every raw microphone of
    the one capture, both markers found. A pair not tried is not detected; a
    tried pair with no raw microphone is a `ValueError`.
    """
    found = pair(alignment, role, play)
    if not found["tried"]:
        return False
    raws = raw_channels(found)
    if not raws:
        raise ValueError(f"the capture has no channel named {RAW_MIC}*")
    return all(c["start"]["outcome"] == "found" and c["end"]["outcome"] == "found" for c in raws)


def overlap_schedule(found_mb: int, ceiling_mb: int) -> list[int]:
    """The levels of a ramp under another output's play, in order: `found_mb`,
    the level the output's ramp alone found, then every STEP_MB above it that
    lies under `ceiling_mb`, then `ceiling_mb`. A `found_mb` above the ceiling
    is a `ValueError`.
    """
    if found_mb > ceiling_mb:
        raise ValueError(f"the level found, {found_mb} mB, is above the ceiling {ceiling_mb} mB")
    return [*range(found_mb, ceiling_mb, STEP_MB), ceiling_mb]


def play_level(found_mb: int, ceiling_mb: int) -> int:
    """The level a play is given from the level `found_mb` a ramp found: one
    step above it, never above the output's ceiling.
    """
    return min(found_mb + STEP_MB, ceiling_mb)


def healthy(record: Any) -> bool:
    """Whether a play's results record shows it ran as asked: `completed`
    with no underrun and no failure, its controls restored.
    """
    return (
        record["ended"] == "completed"
        and record["underruns"] == []
        and record["restored"] is True
        and record["failure"] is None
    )


def first_mark(records: Sequence[Any], role: str) -> Any:
    """The first mark of `role`'s one play, from its `results.json` record,
    where a play with a stated start has it; the manifest's is null for such
    a play. A `ValueError` if the role has no one play or it handed no frame.
    """
    found = [r for r in records if r["role"] == role]
    if len(found) != 1:
        raise ValueError(f"{len(found)} records of {role}'s plays; the scenario has one")
    if found[0]["first"] is None:
        raise ValueError(f"{role}'s play has no first mark: {found[0]}")
    return found[0]["first"]


def stated_offset_us(journal: Sequence[Any], early: str, late: str) -> int:
    """The offset the script stated from `early`'s start to `late`'s: each
    role's asked start less the clock reading it gave last before the play,
    `late`'s less `early`'s. Each difference is on its own role's clock, so
    this holds whether or not the roles share a machine.
    """

    def lead(role: str) -> int:
        reading: Any = None
        for line in journal:
            if line.get("role") != role or line.get("ok") is not True:
                continue
            if line["command"] == "playback.clock":
                reading = line["result"]["at"]
            elif line["command"] == "playback.play":
                asked = line["result"]["asked_start"]
                if reading is None or asked is None:
                    raise ValueError(f"{role}'s play states no start from a reading of its clock")
                return int(asked["monotonic"]["us"]) - int(reading["monotonic"]["us"])
        raise ValueError(f"the journal holds no play of {role}")

    return lead(late) - lead(early)


def marks_gap_us(early: Any, late: Any) -> int:
    """The time from one first mark to another, both on one machine's clock."""
    if early["at"]["machine"] != late["at"]["machine"]:
        raise ValueError("the two first marks are on different machines' clocks")
    return int(late["at"]["monotonic"]["us"]) - int(early["at"]["monotonic"]["us"])


def raw_offsets(early: Any, late: Any) -> dict[str, tuple[float, float]]:
    """Each raw microphone's `offset_frames` in two pairs of one capture, the
    earlier pair's then the later's. A `ValueError` unless both pairs have
    the same raw microphones, at least one, and every one located both plays.
    """
    found = [{c["channel"]: c["offset_frames"] for c in raw_channels(p)} for p in (early, late)]
    if found[0].keys() != found[1].keys() or not found[0]:
        raise ValueError(f"the pairs' raw microphones differ or are none: {found}")
    missing = [c for c in found[0] if found[0][c] is None or found[1][c] is None]
    if missing:
        raise ValueError(f"raw microphones that did not locate both plays: {missing}")
    return {c: (float(found[0][c]), float(found[1][c])) for c in found[0]}


def capture_gap_s(offsets: dict[str, tuple[float, float]], rate_hz: int) -> float:
    """The time in the capture from one play to another: the later
    `offset_frames` less the earlier, the mean over the raw microphones of
    `raw_offsets`, at the capture's rate.
    """
    gaps = [late - early for early, late in offsets.values()]
    return sum(gaps) / len(gaps) / rate_hz


def played(records: Sequence[Any], entries: Sequence[Any], level_mb: int) -> bool:
    """Whether every play of a step ran as asked: each results record
    `healthy`, and no control of any source entry read back above what was
    asked, `level_mb` for the level control and HELD_MB for a held one.
    """
    if not records or len(records) != len(entries) or not all(map(healthy, records)):
        return False
    for entry in entries:
        for control in entry["controls"]:
            asked = level_mb if control["part"] == "level" else HELD_MB
            for channel in control["channels"]:
                if channel["volume_mb"] is not None and channel["volume_mb"] > asked:
                    return False
    return True


@dataclass(frozen=True)
class Heard:
    """One raw microphone in an undetected step's capture.

    `primer_db` is the primer tone's 1 kHz level over 0.8 s starting 3.0 s
    into the primer, `primer_ambient_db` the same measure over the capture's
    first second; `band_db` is the 500-3500 Hz level over the marked play,
    `band_ambient_db` the same band over the capture's first second.
    """

    channel: str
    primer_db: float
    primer_ambient_db: float
    band_db: float
    band_ambient_db: float


def arrived(h: Heard) -> bool:
    """Whether the primer tone stood ARRIVED_DB or more above the room on one
    raw microphone. A microphone that read digital silence over both spans
    did not hear it.
    """
    return h.primer_db - h.primer_ambient_db >= ARRIVED_DB


def checkpoint(heard: Sequence[Heard]) -> Stop:
    """Why a step above CHECKPOINT_ABOVE_MB whose plays ran as asked was not
    detected.

    - `not_arrived`: on no raw microphone has the primer tone `arrived`.
    - `not_recognised`: on some raw microphone the marked play's band is
      RECOGNISABLE_DB or more above the room's, so its marker carried at
      least half the band's energy and should have scored about 0.7.
    - `faint`: the tone arrived and the marker is still under the room's
      noise; the ramp goes on. Every other class stops it.
    """
    if not heard:
        raise ValueError("no raw microphone was measured")
    if not any(map(arrived, heard)):
        return "not_arrived"
    if any(h.band_db - h.band_ambient_db >= RECOGNISABLE_DB for h in heard):
        return "not_recognised"
    return "faint"


@dataclass(frozen=True)
class Standout:
    """One raw microphone's reading of a span that should hold the 1 kHz
    tone: its 1 kHz level and the larger of its levels at NEIGHBOUR_HZ.
    """

    channel: str
    tone_db: float
    neighbour_db: float


def standout(channel: str, samples: Floats) -> Standout:
    """The 1 kHz and neighbouring levels of a span of a 16 kHz capture."""
    values = samples.tolist()
    neighbour = max(signals.level_db(values, hz) for hz in NEIGHBOUR_HZ)
    return Standout(channel, tone_db(samples), neighbour)


def stands_out(s: Standout) -> bool:
    """Whether the tone stood STANDS_OUT_DB or more above its neighbours.
    Speech has no line that does across a MEASURE_S span; a tone that
    reached the microphone does. A span of digital silence does not.
    """
    return s.tone_db - s.neighbour_db >= STANDS_OUT_DB


def overlap_checkpoint(read: Sequence[Standout], level_mb: int, first_mb: int) -> Stop:
    """Why a step at `level_mb` of a ramp under another play, whose plays ran
    as asked, was not detected; `first_mb` is the ramp's first level.

    - `not_arrived`: on no raw microphone does the play's tone stand out.
    - `not_recognised`: the tone stands out and the step is OVERLAP_RISE_MB
      or more above the first.
    - `faint`: the play is in the capture and has not yet been raised by
      what the score rule gives it; the ramp goes on. Every other class
      stops it.
    """
    if not read:
        raise ValueError("no raw microphone was measured")
    if not any(map(stands_out, read)):
        return "not_arrived"
    if level_mb - first_mb >= OVERLAP_RISE_MB:
        return "not_recognised"
    return "faint"


MARKED_TONE_FROM_S = marker.LEAD_S + marker.DURATION_MS / 1000 + marker.GAP_S + 0.1
"""Where a MEASURE_S span inside a marked one-second tone starts, in
seconds from the marked file's first frame: past the lead, the start
marker and the gap, and 0.1 s into the tone.
"""


def marked_tone(
    first: Any, capture_start: Any, mics: Mapping[str, Floats], rate_hz: int
) -> tuple[Standout, ...]:
    """Each raw microphone's reading of a marked tone's play: MEASURE_S from
    MARKED_TONE_FROM_S into the source, placed in the capture by the clock
    tie of the play's first mark against the capture's start.
    """
    at = tie(first, capture_start, rate_hz) + MARKED_TONE_FROM_S * rate_hz
    return tuple(
        standout(name, span(samples, rate_hz, at, MEASURE_S))
        for name, samples in mics.items()
        if name.startswith(RAW_MIC)
    )


class RampStep(Protocol):
    """One run of a ramp, at a level that may have up to CONFIRM_RUNS runs:
    its level, whether its marked play was detected, and whether every play
    of the run ran as asked.
    """

    @property
    def level_mb(self) -> int: ...

    @property
    def detected(self) -> bool: ...

    @property
    def played(self) -> bool: ...


@dataclass
class Ramp[S: RampStep]:
    """A ramp on one output: every run in the order made, several of which
    may share a level, and how it ended, `None` until it has; `detail` names
    the run a `not_played` or checkpoint stop ended on, and why. `found_mb`
    is the last run's level when the ramp ended `found`.
    """

    output: str
    steps: list[S] = field(default_factory=list)
    stop: RampStop | None = None
    detail: str = ""

    @property
    def found_mb(self) -> int | None:
        return self.steps[-1].level_mb if self.stop == "found" else None


def climb[S: RampStep](
    output: str,
    levels: Sequence[int],
    take: Callable[[int, int], S | str],
    read: Callable[[S], Stop],
    silent_min: bool,
) -> Ramp[S]:
    """Run through `levels` until a play is detected CONFIRM_RUNS runs in a
    row at one level: `take(index, level)` makes a run, `index` counting
    every run of the ramp from 0, or says why it could not be read.

    A run that could not be read, or whose plays did not run as asked, ends
    the ramp `not_played` at any level, before its detection is looked at.
    A detected run at the first level when `silent_min` says the output's
    minimum is silence ends it `detected_in_silence`. Any other detected run
    counts toward its level: at CONFIRM_RUNS in a row the ramp ends `found`,
    and under that the next run is at the same level. A run that was not
    detected ends its level, whatever the runs before it there read; above
    CHECKPOINT_ABOVE_MB it is classified by `read` first, and every class
    but `faint` ends the ramp. Past the last level it ends `ceiling`.
    """
    result: Ramp[S] = Ramp(output)
    index = 0
    for position, level in enumerate(levels):
        detections = 0
        while True:
            taken = take(index, level)
            run = f"run {index}, {level} mB"
            index += 1
            if isinstance(taken, str):
                result.stop, result.detail = "not_played", f"{run}: {taken}"
                return result
            result.steps.append(taken)
            if not taken.played:
                result.stop, result.detail = "not_played", run
                return result
            if taken.detected:
                if silent_min and position == 0:
                    result.stop = "detected_in_silence"
                    return result
                detections += 1
                if detections == CONFIRM_RUNS:
                    result.stop = "found"
                    return result
                continue
            if level > CHECKPOINT_ABOVE_MB:
                reading = read(taken)
                if reading != "faint":
                    result.stop, result.detail = reading, run
                    return result
            break
    result.stop = "ceiling"
    return result


def judged(reference_db: float, ambient_db: float) -> bool:
    """Whether a silence may be judged against a reference tone: the tone at
    least JUDGED_DB above the room by the same measure.
    """
    return reference_db - ambient_db >= JUDGED_DB


def silence(reference: Heard, spans_db: Sequence[float]) -> bool | None:
    """Whether a capture that should hold no play is silent on one raw
    microphone, against the primer `reference` heard there: `None` when the
    primer is not `judged` above its room, else whether every span's 1 kHz
    level is SILENT_UNDER_DB or more under the primer's. A capture with no
    span is a `ValueError`.
    """
    if not spans_db:
        raise ValueError("no span was measured")
    if not judged(reference.primer_db, reference.primer_ambient_db):
        return None
    return all(v <= reference.primer_db - SILENT_UNDER_DB for v in spans_db)


@dataclass(frozen=True)
class FlexOut:
    """Role `speaker` on the Flex's output, card `card`, under `cap_mb`."""

    card: str
    cap_mb: int


@dataclass(frozen=True)
class SinkOut:
    """Role `role` on the sound server's sink `sink`, under `cap_mb`."""

    role: str
    sink: str
    cap_mb: int


def lab_text(
    agreed: Window,
    mic_card: str,
    sources: Path,
    outputs: Sequence[FlexOut | SinkOut],
    pod_capture: str = PINNED,
    pulse_client: str = PINNED,
) -> str:
    """A lab configuration: `mic` capturing on `mic_card`, and a playback
    role per output playing from `sources`, every one quiet outside
    `agreed`. A cap above its output's ceiling, or two roles of one name, is
    a `ValueError`. The machine's programs are given as TOML.
    """
    quiet = [agreed.quiet]
    names = ["speaker" if isinstance(o, FlexOut) else o.role for o in outputs]
    if len(set(names)) != len(names) or "mic" in names:
        raise ValueError(f"role names {names} repeat")
    sinks = any(isinstance(o, SinkOut) for o in outputs)
    parts = [
        lab_toml(
            pod_capture, mic_card, sample_clock="flex", pulse_client=pulse_client if sinks else None
        )
    ]
    for output in outputs:
        if isinstance(output, FlexOut):
            if output.cap_mb > FLEX_CEILING_MB:
                raise ValueError(f"cap {output.cap_mb} mB is above the Flex's {FLEX_CEILING_MB} mB")
            parts.append(speaker_toml(output.card, sources, output.cap_mb, quiet))
        else:
            if output.cap_mb > SINK_CEILING_MB:
                raise ValueError(f"cap {output.cap_mb} mB is above a sink's {SINK_CEILING_MB} mB")
            parts.append(talker_toml(output.sink, sources, output.cap_mb, quiet, output.role))
    return "\n".join(parts)


def tie(first: Any, capture_start: Any, rate_hz: int) -> float:
    """Where the clock tie puts copy frame 0 in the capture, in capture
    frames: the first mark's frame at the output, at the mark's reading,
    against the capture's start reading. Both readings are the machine's
    monotonic clock; the source's rate is the capture's.
    """
    if first["delay_frames"] is None:
        raise ValueError(f"the first mark has no delay: {first}")
    at_output = int(first["frames_handed"]) - int(first["delay_frames"])
    elapsed_us = int(first["at"]["monotonic"]["us"]) - int(capture_start["monotonic"]["us"])
    return elapsed_us * rate_hz / 1e6 - at_output


def channels(wav: Path, layout: Sequence[str]) -> tuple[int, dict[str, Floats]]:
    """The rate and every channel, by its layout name, of a 16-bit WAV."""
    with wave.open(str(wav), "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != len(layout):
            raise ValueError(f"{wav}: not 16-bit with {len(layout)} channels")
        rate = w.getframerate()
        data = w.readframes(w.getnframes())
    samples = np.frombuffer(data, dtype="<i2").astype(np.float64).reshape(-1, len(layout))
    return rate, {name: samples[:, i] for i, name in enumerate(layout)}


def span(samples: Floats, rate_hz: int, start_frame: float, seconds: float) -> Floats:
    """`seconds` of `samples` from `start_frame`, rounded to a frame; a span
    that does not lie wholly inside is a `ValueError`.
    """
    begin = round(start_frame)
    end = begin + round(seconds * rate_hz)
    if begin < 0 or end > len(samples):
        raise ValueError(f"frames {begin} to {end} are not inside {len(samples)}")
    return samples[begin:end]


def tone_db(samples: Floats) -> float:
    """The 1 kHz level of a span of a 16 kHz capture (`signals.level_db`)."""
    return signals.level_db(samples.tolist(), signals.TONE_HZ)


def band_db(samples: Floats, rate_hz: int) -> float:
    """The RMS level of `samples` band-passed to 500-3500 Hz with alignment's
    own filter, in dB relative to full 16-bit scale; only the outputs the
    filter computes from samples wholly inside the span count.
    """
    taps = bandpass_taps(rate_hz)
    if len(samples) < len(taps):
        raise ValueError(f"{len(samples)} frames are fewer than the filter's {len(taps)} taps")
    passed = np.convolve(samples, taps, mode="valid")
    rms = float(np.sqrt(np.mean(passed * passed)))
    return -np.inf if rms == 0.0 else float(20 * np.log10(rms / 32768))


def windows_db(samples: Floats, seconds: float) -> list[float]:
    """The 1 kHz level of every whole window of `seconds` of a 16 kHz span,
    in order.
    """
    size = round(seconds * signals.RATE_HZ)
    return [tone_db(samples[i : i + size]) for i in range(0, len(samples) - size + 1, size)]


def write_tone(path: Path, seconds: float) -> Path:
    """Write `seconds` of the 1 kHz tone at half of full scale, 16 kHz, two
    identical channels, 16-bit, as a WAV at `path`.
    """
    one = np.round(signals.AMPLITUDE * np.array(signals.tone(seconds))).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(signals.RATE_HZ)
        w.writeframes(np.repeat(one, 2).tobytes())
    return path


def scores(pair_: Any) -> Mapping[str, tuple[float | None, float | None]]:
    """Each channel's start and end marker scores in a pair."""
    return {c["channel"]: (c["start"]["score"], c["end"]["score"]) for c in pair_["channels"]}
