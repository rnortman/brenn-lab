"""Aligning a run: where each marked play lies in each capture, written to
`<run>/alignment.json`.

The run's manifest names the plays and the captures; the results, when the
run left them, say how much of each play was handed to its device. Every
marked play is paired with every readable capture and every channel of the
capture is searched for both of the play's markers. No clock reading is used
to decide anything. Which instance of a code belongs to which play is decided
by the attribution rule in `_attribute`.
"""

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from pathlib import Path

from brenn_lab import capture as capture_kind
from brenn_lab import marker, rundir
from brenn_lab import playback as playback_kind
from brenn_lab.align import capture, search
from brenn_lab.align.capture import Exported
from brenn_lab.data import Failure, JsonObject, JsonValue
from brenn_lab.marker import Carried, Span
from brenn_lab.playback import source
from brenn_lab.rundir import LAYOUT

REPORT = "alignment.json"
SCHEMA = 1
_HALF_S = marker.DURATION_MS / 2000
"""A marker's midpoint, in seconds from its start."""
EXPORT = "export.wav"
"""The one capture export in the scratch directory at a time. Its name holds
nothing from the manifest.
"""


class _Malformed(Exception):
    """The manifest or results hold a value of the wrong shape."""


@dataclass
class _Play:
    role: str
    play: int
    file: JsonValue
    entry: JsonObject
    state: str = "marked"
    reason: str | None = None
    detail: str | None = None
    carried: Carried | None = None
    frames_handed: int | None = None
    underruns: tuple[int, ...] = ()

    def key(self) -> JsonObject:
        return {"role": self.role, "play": self.play}

    def handed(self, span: Span) -> bool:
        """Whether the marker at `span` counts as handed whole: the results
        say its last frame was handed, or say nothing of the play.
        """
        return self.frames_handed is None or self.frames_handed >= span.frame + span.frames


@dataclass
class _Capture:
    role: str
    capture: int
    file: JsonValue
    rate_hz: int
    sample_format: str
    channels: int
    layout: tuple[str, ...]
    exported: Exported | None = None
    failure: Failure | None = None

    def key(self) -> JsonObject:
        return {"role": self.role, "capture": self.capture}


@dataclass
class _Outcome:
    """One marker of one play in one channel of one capture."""

    outcome: str
    reason: str | None = None
    score: float | None = None
    instances: int | None = None
    position: float | None = None
    """The midpoint, in the exported file's frames, when found."""
    midpoint: float | None = None
    """The midpoint, in the capture's frames, when found."""
    detail: str | None = None

    def encode(self) -> JsonObject:
        return {
            "outcome": self.outcome,
            "reason": self.reason,
            "score": self.score,
            "midpoint_frame": self.midpoint,
            "instances": self.instances,
        }


@dataclass
class _Pair:
    play: _Play
    capture: _Capture
    not_tried: str | None
    channels: dict[str, dict[str, _Outcome]] = field(default_factory=dict)
    measures: dict[str, JsonObject] = field(default_factory=dict)
    """Per channel, what `_measures` made of its two outcomes."""


def align(run: Path, reader: str, pinned: bool, harness: JsonObject) -> JsonObject | Failure:
    """Align the run at `run` with the capture reader `reader` and write
    `<run>/alignment.json`; the report, or the refusal with nothing written:
    `unreadable` or `layout` when the run cannot be read, decided first, then
    `not_writable` when the scratch directory cannot be cleared or made in it.
    """
    loaded = _load(run)
    if isinstance(loaded, Failure):
        return loaded
    manifest, results = loaded
    try:
        plays, captures = _entries(manifest)
        if results is not None:
            _read_results(results, plays)
        for play in plays:
            _judge(run, play)
    except _Malformed as e:
        return Failure("unreadable", str(e))
    pairs: list[_Pair] = []
    try:
        with capture.scratch(run) as scratch:
            for c in captures:
                _read_capture(run, scratch, reader, c)
                pairs.extend(_align_capture(c, plays))
                if c.exported is not None:
                    c.exported.path.unlink()
    except capture.Unwritable as e:
        if e.leftover is None:
            return Failure("not_writable", f"{run} cannot be written: {e.reason}")
        return Failure(
            "not_writable",
            f"{run} cannot be written: the leftover {e.leftover} cannot be cleared: {e.reason}",
        )
    report = _report(manifest, results is not None, reader, pinned, harness, plays, captures, pairs)
    _write(run, report)
    return report


def _load(run: Path) -> tuple[JsonObject, JsonObject | None] | Failure:
    if not run.is_dir():
        return Failure("unreadable", f"{run} is not a directory")
    manifest = _json(run / "manifest.json")
    if isinstance(manifest, Failure):
        return manifest
    if manifest.get("layout") != LAYOUT:
        return Failure(
            "layout", f"the manifest's layout is {manifest.get('layout')!r}, not {LAYOUT}"
        )
    path = run / "results.json"
    if not path.exists():
        return manifest, None
    results = _json(path)
    if isinstance(results, Failure):
        return results
    return manifest, results


def _json(path: Path) -> JsonObject | Failure:
    try:
        value = json.loads(path.read_text())
    except OSError as e:
        return Failure("unreadable", f"{path} cannot be read: {e.strerror}")
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return Failure("unreadable", f"{path} is not JSON: {e}")
    if not isinstance(value, dict):
        return Failure("unreadable", f"{path} is not a JSON object")
    return value


def _get[T](obj: JsonValue, key: str, kind: type[T], where: str) -> T:
    if not isinstance(obj, dict) or key not in obj:
        raise _Malformed(f"{where} has no {key!r}")
    value = obj[key]
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        raise _Malformed(f"{where}.{key} is not a {kind.__name__}")
    return value


def _entries(manifest: JsonObject) -> tuple[list[_Play], list[_Capture]]:
    """Every play and every capture the manifest names, in its order. A role
    of a kind this code does not know is passed over.
    """
    plays: list[_Play] = []
    captures: list[_Capture] = []
    for name, section in _get(manifest, "roles", dict, "manifest").items():
        where = f"manifest.roles.{name}"
        kind = _get(section, "kind", str, where)
        if kind == playback_kind.KIND:
            for entry in _get(section, "sources", list, where):
                at = f"{where}.sources"
                plays.append(
                    _Play(name, _get(entry, "play", int, at), _get(entry, "file", dict, at), entry)
                )
        elif kind == capture_kind.KIND:
            for entry in _get(section, "captures", list, where):
                at = f"{where}.captures"
                granted = _get(entry, "granted", dict, at)
                layout = _get(entry, "layout", list, at)
                if not all(isinstance(n, str) for n in layout):
                    raise _Malformed(f"{at}.layout is not a list of names")
                captures.append(
                    _Capture(
                        name,
                        _get(entry, "capture", int, at),
                        _get(entry, "file", dict, at),
                        _get(granted, "rate_hz", int, f"{at}.granted"),
                        _get(granted, "sample_format", str, f"{at}.granted"),
                        _get(granted, "channels", int, f"{at}.granted"),
                        tuple(str(n) for n in layout),
                    )
                )
    return plays, captures


def _read_results(results: JsonObject, plays: list[_Play]) -> None:
    """Each play's frames handed and underruns, from its results record.
    Results without `sources`, from a harness before the playback kind, hold
    no play records.
    """
    by_key = {(p.role, p.play): p for p in plays}
    records = _get(results, "sources", list, "results") if "sources" in results else []
    for record in records:
        at = "results.sources"
        play = by_key.get((_get(record, "role", str, at), _get(record, "play", int, at)))
        if play is None:
            continue
        handed = record.get("frames_handed") if isinstance(record, dict) else None
        if handed is not None and (not isinstance(handed, int) or isinstance(handed, bool)):
            raise _Malformed(f"{at}.frames_handed is not an integer")
        play.frames_handed = handed
        play.underruns = tuple(
            _get(u, "at_frame", int, f"{at}.underruns") for u in _get(record, "underruns", list, at)
        )


def _judge(run: Path, play: _Play) -> None:
    """The play's state: whether it carries a marker, and whether its kept
    copy bears the record out.
    """
    described = _get(play.entry, "source", dict, f"play {play.role}/{play.play}")
    if "marker" not in described:
        play.state = "no_marker_record"
        return
    record = described["marker"]
    if record is None:
        play.state = "no_marker"
        return
    if not isinstance(record, dict) or record.get("definition") != marker.DEFINITION:
        definition = record.get("definition") if isinstance(record, dict) else None
        play.state, play.reason = "failed", "unknown_definition"
        play.detail = f"the record's definition is {definition!r}, not {marker.DEFINITION!r}"
        return
    play.carried = _carried(record, f"play {play.role}/{play.play}: source.marker")
    file = play.file
    assert isinstance(file, dict)
    path = file.get("path")
    if file.get("in_run") is not True or not isinstance(path, str):
        play.state, play.reason = "failed", "copy_missing"
        play.detail = "the kept copy is not in the run"
        return
    if rundir.resolve(run, path) is None:
        play.state, play.reason = "failed", "copy_missing"
        play.detail = f"the kept copy's path {path!r} does not lie inside the run"
        return
    judged = source.judge(str(run), path)
    if isinstance(judged, Failure):
        play.state = "failed"
        play.reason = "copy_missing" if judged.reason == "source_missing" else "copy_differs"
        play.detail = judged.detail
        return
    copy = judged.source
    said = tuple(described.get(k) for k in ("frames", "rate_hz", "channels", "sample_format"))
    held = (copy.frames, copy.rate_hz, copy.channels, copy.sample_format)
    if said != held:
        play.state, play.reason = "failed", "copy_differs"
        play.detail = f"the kept copy holds {held}; the manifest says {said}"
    elif copy.marker != play.carried:
        play.state, play.reason = "failed", "copy_differs"
        play.detail = "the kept copy's samples do not carry the recorded marker"


def _carried(record: JsonObject, where: str) -> Carried:
    """The marker record's fields; keys beyond them are ignored."""

    def span(key: str) -> Span:
        at = f"{where}.{key}"
        found = _get(record, key, dict, where)
        return Span(_get(found, "frame", int, at), _get(found, "frames", int, at))

    return Carried(
        _get(record, "definition", str, where),
        _get(record, "member", int, where),
        _get(record, "start_code", int, where),
        _get(record, "end_code", int, where),
        span("start"),
        span("end"),
    )


def _read_capture(run: Path, scratch: Path, reader: str, c: _Capture) -> None:
    file = c.file
    assert isinstance(file, dict)
    path = file.get("path")
    if file.get("in_run") is not True or not isinstance(path, str):
        c.failure = Failure("not_in_run", f"the capture is on {file.get('machine')!r} at {path}")
        return
    resolved = rundir.resolve(run, path)
    if resolved is None:
        c.failure = Failure(
            "not_in_run", f"the capture's path {path!r} does not lie inside the run"
        )
        return
    if c.channels != len(c.layout):
        c.failure = Failure(
            "capture_differs",
            f"the capture was granted {c.channels} channels and its layout names {len(c.layout)}",
        )
        return
    read = capture.read(resolved, scratch / EXPORT, reader, c.rate_hz, c.sample_format, c.layout)
    if isinstance(read, Failure):
        c.failure = read
    else:
        c.exported = read


def _align_capture(c: _Capture, plays: list[_Play]) -> list[_Pair]:
    """Every play paired with capture `c`, each marked play searched for in
    every channel when the capture was read.
    """
    pairs = []
    for play in plays:
        if play.state != "marked":
            not_tried: str | None = "play_not_marked"
        elif c.failure is not None:
            not_tried = "capture_failed"
        elif c.rate_hz < marker.MIN_RATE_HZ:
            not_tried = "capture_rate_too_low"
        else:
            not_tried = None
        pairs.append(_Pair(play, c, not_tried))
    tried = [p for p in pairs if p.not_tried is None]
    exported = c.exported
    if not tried or exported is None:
        return pairs
    markers: dict[int, list[tuple[_Pair, str]]] = {}
    for pair in tried:
        carried = pair.play.carried
        assert carried is not None
        for which, code, span in (
            ("start", carried.start_code, carried.start),
            ("end", carried.end_code, carried.end),
        ):
            if pair.play.handed(span):
                markers.setdefault(code, []).append((pair, which))
    codes = sorted(markers)
    templates = [search.template(marker.code(code), c.rate_hz) for code in codes]
    length = marker.length(c.rate_hz)
    for index, name in enumerate(c.layout):
        outcomes = {
            (id(pair), which): _Outcome("not_tried", "not_handed")
            for pair in tried
            for which in ("start", "end")
        }
        found_all = search.search(partial(exported.blocks, index), c.rate_hz, templates)
        for code, found in zip(codes, found_all, strict=True):
            for (pair, which), outcome in _attribute(markers[code], found, exported, length):
                outcomes[(id(pair), which)] = outcome
        for pair in tried:
            start, end = outcomes[(id(pair), "start")], outcomes[(id(pair), "end")]
            pair.channels[name] = {"start": start, "end": end}
            pair.measures[name] = _measures(pair, exported, start, end)
    return pairs


def _attribute(
    tried: list[tuple[_Pair, str]],
    found: search.Found,
    exported: Exported,
    length: int,
) -> Iterator[tuple[tuple[_Pair, str], _Outcome]]:
    """The outcome of each marker in `tried`, all of one code, given that
    code's instances in one channel. Nothing here reads a clock.
    """
    instances = found.instances
    count = len(instances)
    best = found.best

    def missing(reason: str, detail: str) -> _Outcome:
        return _Outcome("not_found", reason, best, count, detail=detail)

    def ambiguous(reason: str, detail: str) -> _Outcome:
        return _Outcome("ambiguous", reason, best, count, detail=detail)

    if count == 0:
        for m in tried:
            yield m, missing("below_threshold", f"the largest score seen is {best:.3f}")
        return
    if len(tried) == 1:
        if count == 1:
            yield tried[0], _found(instances[0], count, exported, length)
        else:
            yield tried[0], ambiguous("instances_exceed_plays", f"{count} instances for one play")
        return
    roles = {pair.play.role for pair, _ in tried}
    if len(roles) == 1 and count == len(tried):
        in_order = sorted(tried, key=lambda m: m[0].play.play)
        for m, instance in zip(in_order, instances, strict=True):
            yield m, _found(instance, count, exported, length)
        return
    reason = "shared_across_roles" if len(roles) > 1 else "count_mismatch"
    for m in tried:
        yield m, ambiguous(reason, f"{count} instances for {len(tried)} plays")


def _found(instance: search.Instance, count: int, exported: Exported, length: int) -> _Outcome:
    if exported.touches_hole(instance.first, length):
        return _Outcome(
            "not_found",
            "in_hole",
            instance.score,
            count,
            detail=f"the instance at exported frame {instance.first} touches a hole",
        )
    return _Outcome(
        "found",
        None,
        instance.score,
        count,
        instance.midpoint,
        exported.capture_frame(instance.midpoint),
    )


def _measures(pair: _Pair, exported: Exported, start: _Outcome, end: _Outcome) -> JsonObject:
    """`ratio`, `drift_ppm`, `drift_unmeasured`, `offset_frames` and
    `ratio_basis` of one channel of a tried pair.
    """
    carried = pair.play.carried
    assert carried is not None
    source_rate = _get(pair.play.entry["source"], "rate_hz", int, "source")
    nominal = pair.capture.rate_hz / source_rate
    start_mid = carried.start.frame + _HALF_S * source_rate
    end_mid = carried.end.frame + _HALF_S * source_rate
    ratio: float | None = None
    unmeasured: str | None = None
    if start.outcome == "found" and end.outcome == "found":
        assert start.position is not None and end.position is not None
        if exported.unmeasured_between(start.position, end.position):
            unmeasured = "capture_hole"
        elif any(start_mid < u <= end_mid for u in pair.play.underruns):
            unmeasured = "underrun"
        else:
            assert start.midpoint is not None and end.midpoint is not None
            ratio = (end.midpoint - start.midpoint) / (end_mid - start_mid)
    offset: float | None = None
    basis: str | None = None
    if start.outcome == "found":
        assert start.midpoint is not None
        r = nominal if ratio is None else ratio
        offset = start.midpoint - r * start_mid
        basis = "nominal" if ratio is None else "measured"
    return {
        "ratio": ratio,
        "ratio_basis": basis,
        "drift_ppm": None if ratio is None else (ratio / nominal - 1) * 1e6,
        "drift_unmeasured": unmeasured,
        "offset_frames": offset,
    }


def _report(
    manifest: JsonObject,
    results_read: bool,
    reader: str,
    pinned: bool,
    harness: JsonObject,
    plays: list[_Play],
    captures: list[_Capture],
    pairs: list[_Pair],
) -> JsonObject:
    failures: list[JsonValue] = []
    for play in plays:
        if play.state == "failed":
            failures.append(
                {"kind": "play", "play": play.key(), "reason": play.reason, "detail": play.detail}
            )
    for c in captures:
        if c.failure is not None:
            failures.append(
                {
                    "kind": "capture",
                    "capture": c.key(),
                    "reason": c.failure.reason,
                    "detail": c.failure.detail,
                }
            )
    read_failures = len(failures)
    located: set[tuple[str, int]] = set()
    encoded_pairs: list[JsonValue] = []
    for pair in pairs:
        channels: list[JsonValue] = []
        for name, both in pair.channels.items():
            start, end = both["start"], both["end"]
            if start.outcome == "found" and end.outcome == "found":
                located.add((pair.play.role, pair.play.play))
            for which, outcome in both.items():
                if outcome.outcome in ("not_found", "ambiguous"):
                    failures.append(
                        {
                            "kind": "marker",
                            "play": pair.play.key(),
                            "capture": pair.capture.key(),
                            "channel": name,
                            "marker": which,
                            "outcome": outcome.outcome,
                            "reason": outcome.reason,
                            "detail": outcome.detail,
                        }
                    )
            channels.append(
                {
                    "channel": name,
                    "start": start.encode(),
                    "end": end.encode(),
                    **pair.measures[name],
                }
            )
        encoded_pairs.append(
            {
                "play": pair.play.key(),
                "capture": pair.capture.key(),
                "tried": pair.not_tried is None,
                "not_tried": pair.not_tried,
                "channels": channels,
            }
        )
    marked = sum(1 for p in plays if p.state == "marked")
    return {
        "schema": SCHEMA,
        "run_id": manifest.get("run_id"),
        "harness": harness,
        "written": datetime.now().astimezone().isoformat(timespec="seconds"),
        "replaces": None,
        "reader": {
            "program": "frame-log",
            "pinned": pinned,
            "path": None if pinned else reader,
        },
        "results_read": results_read,
        "clock_readings": "not_used",
        "marker": {
            "definition": marker.DEFINITION,
            "statistic": "rho",
            "threshold": search.THRESHOLD,
            "band_hz": list(search.BAND_HZ),
        },
        "plays": [
            {
                "role": p.role,
                "play": p.play,
                "file": p.file,
                "state": p.state,
                "reason": p.reason,
                "detail": p.detail,
                "marker": _get(p.entry, "source", dict, "source").get("marker"),
            }
            for p in plays
        ],
        "captures": [_encode_capture(c) for c in captures],
        "pairs": encoded_pairs,
        "failures": failures,
        "summary": {
            "plays": len(plays),
            "marked": marked,
            "located": len(located),
            "captures": len(captures),
            "marker_failures": len(failures) - read_failures,
            "read_failures": read_failures,
        },
    }


def _encode_capture(c: _Capture) -> JsonObject:
    exported = c.exported
    return {
        "role": c.role,
        "capture": c.capture,
        "file": c.file,
        "rate_hz": c.rate_hz,
        "layout": list(c.layout),
        "exported_frames": None if exported is None else exported.frames,
        "holes": None
        if exported is None
        else [
            {"at_frame": h.at_frame, "filled_frames": h.filled_frames, "basis": h.basis}
            for h in exported.holes
        ],
        "tail": None if exported is None else exported.tail,
        "failure": None
        if c.failure is None
        else {"reason": c.failure.reason, "detail": c.failure.detail},
    }


def ok(report: JsonObject) -> bool:
    """Whether every capture and play was read and every marked play located."""
    summary = report["summary"]
    assert isinstance(summary, dict)
    return summary["read_failures"] == 0 and summary["located"] == summary["marked"]


def _write(run: Path, report: JsonObject) -> None:
    """Write the report by a temporary file and a rename, naming the report it
    replaces.
    """
    path = run / REPORT
    if path.exists():
        previous = path.read_bytes()
        try:
            old = json.loads(previous)
            harness = old.get("harness") if isinstance(old, dict) else None
            written = old.get("written") if isinstance(old, dict) else None
        except json.JSONDecodeError, UnicodeDecodeError:
            harness = written = None
        report["replaces"] = {
            "harness": harness,
            "written": written,
            "sha256": hashlib.sha256(previous).hexdigest(),
        }
    rundir.replace_json(run, REPORT, report)
