"""Assessing a run: whether it is usable, written to `<run>/assessment.json`.

The assessment rides alignment's pass. Every capture is exported once by
alignment, measured channel by channel while its export stands, and searched
for markers by alignment; the pass writes `alignment.json`, and this module
then states the run's findings, what it could not assess, and one `answer`.

A finding states a fact established about the run: the record is not what it
claims, nothing was recorded, or a part of the run that is here could not be
read. A `not_assessed` entry states that something the run holds was not
examined. No finding rests on a tunable number.
"""

from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from brenn_lab import capture as capture_kind
from brenn_lab import playback as playback_kind
from brenn_lab import rundir
from brenn_lab.align import report as alignment
from brenn_lab.align.capture import Exported, Hole
from brenn_lab.assess.measure import Measured, measure
from brenn_lab.capture.messages import device_state_problem
from brenn_lab.data import DecodeError, Failure, JsonObject, JsonValue, get, get_or_null
from brenn_lab.marker import Span
from brenn_lab.playback.output import OUTPUTS, PLAYS_ON_AFTER_UNDERRUN

REPORT = "assessment.json"
SCHEMA = 1
UNREADABLE = frozenset({"missing", "export_refused", "capture_differs", "reader_contract"})
"""Capture read failures in which the capture is in the run and could not be
read: each is a finding as well as a capture not assessed.
"""
NOT_READ = frozenset({"not_in_run", "reader_missing", "reader_usage"})
"""Capture read failures in which nothing of the capture was read."""
COPY_UNUSABLE = frozenset({"copy_missing", "copy_differs"})
LENGTHS = frozenset({"measured", "not_sent"})
"""Hole bases whose fill is the loss's length."""


@dataclass(frozen=True)
class _Gap:
    cause: str
    at_frame: int
    frames: int | None


@dataclass(frozen=True)
class _CaptureRecord:
    ended: str
    frames: int | None
    gaps_reported: int | None
    gaps: tuple[_Gap, ...]
    late: int
    loss_free: bool
    failure: JsonObject | None


@dataclass(frozen=True)
class _PlayRecord:
    ended: str
    stopped_by: str | None
    frames: int
    frames_handed: int | None
    unplayed_frames: int | None
    underruns: tuple[int, ...]
    as_sent: bool
    restored: bool
    failure: JsonObject | None
    asked_start: JsonObject | None
    started_after_us: int | None


@dataclass(frozen=True)
class _Role:
    name: str
    kind: str
    entries: tuple[int, ...]
    """Capture or play numbers; empty for a role of a kind not known here."""


@dataclass(frozen=True)
class _Run:
    """Every value the assessment reads from the manifest and the results."""

    roles: tuple[_Role, ...]
    device_states: dict[tuple[str, int], JsonObject]
    """Each capture's device state as its assessment states it: the outcome,
    and the recorder's `why` and `detail` for a state that was not read.
    """
    results_read: bool
    outcome: str | None
    failure: JsonObject | None
    captures: dict[tuple[str, int], _CaptureRecord]
    plays: dict[tuple[str, int], _PlayRecord]
    outputs: dict[tuple[str, int], str]
    """The output kind of each play, known to this harness or not. A play of a
    kind that is no key of `OUTPUTS` has its `unplayed_frames` read as null:
    the figure is by its output's own account, which this harness cannot read.
    """


@dataclass(frozen=True)
class Assessed:
    """The report `assess` wrote."""

    report: JsonObject


def _parse(manifest: JsonObject, results: JsonObject | None) -> _Run:
    """The run's manifest and results as the assessment reads them; raises
    `DecodeError` naming the file and the key.
    """
    roles: list[_Role] = []
    outputs: dict[tuple[str, int], str] = {}
    states: dict[tuple[str, int], JsonObject] = {}
    for name, section in get(manifest, "roles", dict, "manifest.json").items():
        where = f"manifest.json: roles.{name}"
        kind = get(section, "kind", str, where)
        if kind == capture_kind.KIND:
            entries = get(section, "captures", list, where)
            numbers = tuple(get(e, "capture", int, f"{where}.captures") for e in entries)
            for n, e in zip(numbers, entries, strict=True):
                at = f"{where}.captures[capture {n}]"
                state = get(e, "device_state", dict, at)
                problem = device_state_problem(state)
                if problem is not None:
                    raise DecodeError(f"{at}: {problem}")
                read = state["outcome"] == "read"
                states[(name, n)] = {
                    "outcome": state["outcome"],
                    "why": None if read else state["why"],
                    "detail": None if read else state["detail"],
                }
        elif kind == playback_kind.KIND:
            entries = get(section, "sources", list, where)
            numbers = tuple(get(e, "play", int, f"{where}.sources") for e in entries)
            for n, e in zip(numbers, entries, strict=True):
                at = f"{where}.sources[play {n}]"
                outputs[(name, n)] = get(get(e, "output", dict, at), "kind", str, f"{at}.output")
        else:
            numbers = ()
        roles.append(_Role(name, kind, numbers))
    if results is None:
        return _Run(tuple(roles), states, False, None, None, {}, {}, outputs)
    captures: dict[tuple[str, int], _CaptureRecord] = {}
    for i, record in enumerate(get(results, "captures", list, "results.json")):
        where = f"results.json: captures[{i}]"
        key = (get(record, "role", str, where), get(record, "capture", int, where))
        captures[key] = _capture_record(record, where)
    plays: dict[tuple[str, int], _PlayRecord] = {}
    if "sources" in results:
        for i, record in enumerate(get(results, "sources", list, "results.json")):
            where = f"results.json: sources[{i}]"
            key = (get(record, "role", str, where), get(record, "play", int, where))
            plays[key] = _play_record(record, where)
            if key in outputs and outputs[key] not in OUTPUTS:
                plays[key] = replace(plays[key], unplayed_frames=None)
    return _Run(
        tuple(roles),
        states,
        True,
        get(results, "outcome", str, "results.json"),
        get_or_null(results, "failure", dict, "results.json"),
        captures,
        plays,
        outputs,
    )


def _capture_record(record: JsonValue, where: str) -> _CaptureRecord:
    gaps = []
    for j, gap in enumerate(get(record, "gaps", list, where)):
        at = f"{where}.gaps[{j}]"
        gaps.append(
            _Gap(
                get(gap, "cause", str, at),
                get(gap, "at_frame", int, at),
                get_or_null(gap, "frames", int, at),
            )
        )
    return _CaptureRecord(
        get(record, "ended", str, where),
        get_or_null(record, "frames", int, where),
        get_or_null(record, "gaps_reported", int, where),
        tuple(gaps),
        len(get(record, "late", list, where)),
        get(record, "loss_free", bool, where),
        get_or_null(record, "failure", dict, where),
    )


def _play_record(record: JsonValue, where: str) -> _PlayRecord:
    return _PlayRecord(
        get(record, "ended", str, where),
        get_or_null(record, "stopped_by", str, where),
        get(record, "frames", int, where),
        get_or_null(record, "frames_handed", int, where),
        get_or_null(record, "unplayed_frames", int, where),
        tuple(
            get(u, "at_frame", int, f"{where}.underruns")
            for u in get(record, "underruns", list, where)
        ),
        get(record, "as_sent", bool, where),
        get(record, "restored", bool, where),
        get_or_null(record, "failure", dict, where),
        get_or_null(record, "asked_start", dict, where),
        get_or_null(record, "started_after_us", int, where),
    )


class _Assessor:
    """The observer on alignment's pass: the run's values, and each capture's
    read with its channels measured while its export stands.
    """

    def __init__(self) -> None:
        self.run: _Run | None = None
        self.reads: dict[tuple[str, int], tuple[alignment.CaptureRead, tuple[Measured, ...]]] = {}

    def begin(self, manifest: JsonObject, results: JsonObject | None) -> Failure | None:
        try:
            self.run = _parse(manifest, results)
        except DecodeError as e:
            return Failure("unreadable", str(e))
        return None

    def capture(self, read: alignment.CaptureRead) -> None:
        measured: tuple[Measured, ...] = ()
        if isinstance(read.read, Exported):
            exported = read.read
            measured = tuple(
                measure(name, exported.run_blocks(i), read.sample_format)
                for i, name in enumerate(read.layout)
            )
        self.reads[(read.role, read.capture)] = (read, measured)


def assess(run: Path, reader: str, pinned: bool, harness: JsonObject) -> Assessed | Failure:
    """Assess the run at `run` with the capture reader `reader`, writing
    `<run>/alignment.json` and then `<run>/assessment.json`; the assessment,
    or alignment's refusal with nothing written. A manifest or results with
    the wrong shape in any key the assessment reads is refused `unreadable`.
    """
    assessor = _Assessor()
    aligned = alignment.align(run, reader, pinned, harness, assessor)
    if isinstance(aligned, Failure):
        return aligned
    assert assessor.run is not None
    report = _Report(assessor.run, assessor.reads, aligned).build(harness)
    rundir.replace_report(run, REPORT, report)
    return Assessed(report)


class _Report:
    def __init__(
        self,
        run: _Run,
        reads: dict[tuple[str, int], tuple[alignment.CaptureRead, tuple[Measured, ...]]],
        aligned: alignment.Aligned,
    ) -> None:
        self.run = run
        self.reads = reads
        self.aligned = aligned
        self.findings: list[JsonValue] = []
        self.not_assessed: list[JsonValue] = []

    def finding(
        self,
        name: str,
        detail: str,
        *,
        role: str | None = None,
        capture: int | None = None,
        play: int | None = None,
        channel: str | None = None,
        at_frame: int | None = None,
        frames: int | None = None,
        data: JsonObject | None = None,
    ) -> None:
        self.findings.append(
            {
                "finding": name,
                "role": role,
                "capture": capture,
                "play": play,
                "channel": channel,
                "at_frame": at_frame,
                "frames": frames,
                "detail": detail,
                "data": {} if data is None else data,
            }
        )

    def unexamined(
        self,
        what: str,
        reason: str,
        detail: str,
        *,
        role: str | None = None,
        capture: int | None = None,
        play: int | None = None,
    ) -> None:
        self.not_assessed.append(
            {
                "what": what,
                "reason": reason,
                "role": role,
                "capture": capture,
                "play": play,
                "detail": detail,
            }
        )

    def build(self, harness: JsonObject) -> JsonObject:
        run, aligned = self.run, self.aligned
        if not run.results_read:
            self.finding(
                "run_not_completed",
                "the run left no results.json",
                data={"outcome": None, "reason": "no_results"},
            )
            self.unexamined(
                "results",
                "no_results",
                "the run left no results.json: no recorder's account and no play side",
            )
        elif run.outcome != "completed":
            failure = run.failure or {}
            self.finding(
                "run_not_completed",
                f"the run's outcome is {run.outcome}",
                data={
                    "outcome": run.outcome,
                    "where": failure.get("where"),
                    "reason": failure.get("reason"),
                },
            )
        captures: list[JsonValue] = []
        plays: list[JsonValue] = []
        for role in run.roles:
            if role.kind == capture_kind.KIND:
                if not role.entries:
                    self.finding(
                        "role_recorded_nothing",
                        f"the capture role {role.name} has no capture",
                        role=role.name,
                    )
                captures.extend(self.capture(role.name, n) for n in role.entries)
            elif role.kind == playback_kind.KIND:
                plays.extend(self.play(role.name, n) for n in role.entries)
            else:
                self.unexamined(
                    "role",
                    "unknown_kind",
                    f"the role's kind {role.kind!r} is not one this harness assesses",
                    role=role.name,
                )
        if self.findings:
            answer = "findings"
        elif self.not_assessed:
            answer = "incomplete"
        else:
            answer = "clean"
        summary = aligned.report["summary"]
        assert isinstance(summary, dict)
        return {
            "schema": SCHEMA,
            "run_id": aligned.report["run_id"],
            "harness": harness,
            "written": datetime.now().astimezone().isoformat(timespec="seconds"),
            "replaces": None,
            "reader": aligned.report["reader"],
            "alignment": {
                "file": alignment.REPORT,
                "sha256": aligned.sha256,
                "written": aligned.report["written"],
            },
            "results_read": run.results_read,
            "clock_readings": "not_used",
            "answer": answer,
            "complete": not self.not_assessed,
            "findings": self.findings,
            "not_assessed": self.not_assessed,
            "run": {"outcome": run.outcome, "failure": run.failure},
            "captures": captures,
            "plays": plays,
            "summary": {
                "captures": len(captures),
                "captures_read": sum(
                    1 for r, _ in self.reads.values() if isinstance(r.read, Exported)
                ),
                "channels": sum(len(m) for _, m in self.reads.values()),
                "plays": len(plays),
                "marked": summary["marked"],
                "located": summary["located"],
                "findings": len(self.findings),
                "not_assessed": len(self.not_assessed),
            },
        }

    def capture(self, role: str, n: int) -> JsonObject:
        read, measured = self.reads[(role, n)]
        record = self.run.captures.get((role, n))
        if self.run.results_read and record is None:
            self.unexamined(
                "recorder_account",
                "no_record",
                "results.json holds no record of the capture",
                role=role,
                capture=n,
            )
        if record is not None and record.ended == "failed":
            self.finding(
                "capture_failed",
                "the recorder's record says the capture failed",
                role=role,
                capture=n,
                data=record.failure or {},
            )
        exported = read.read if isinstance(read.read, Exported) else None
        failure = read.read if isinstance(read.read, Failure) else None
        if failure is not None:
            if failure.reason in UNREADABLE:
                self.finding(
                    "capture_unreadable",
                    failure.detail,
                    role=role,
                    capture=n,
                    data={"reason": failure.reason},
                )
            elif failure.reason not in NOT_READ:
                raise ValueError(f"alignment read the capture with {failure.reason!r}")
            self.unexamined("capture", failure.reason, failure.detail, role=role, capture=n)
        frames: int | None = None
        if exported is not None:
            frames = sum(r.frames for r in exported.runs)
            if frames == 0:
                self.finding("capture_empty", "the log holds no frame", role=role, capture=n)
            if exported.tail != "ended":
                self.finding(
                    "log_not_ended",
                    f"the reader's verdict on the log's tail is {exported.tail}",
                    role=role,
                    capture=n,
                    data={"verdict": exported.tail},
                )
        loss = self.witnesses(role, n, exported, record)
        for m in measured:
            self.channel(role, n, m)
        return {
            "role": role,
            "capture": n,
            "file": read.file,
            "rate_hz": read.rate_hz,
            "sample_format": read.sample_format,
            "layout": list(read.layout),
            "read": exported is not None,
            "failure": None
            if failure is None
            else {"reason": failure.reason, "detail": failure.detail},
            "frames": frames,
            "tail": None if exported is None else exported.tail,
            "recorder": None
            if record is None
            else {
                "ended": record.ended,
                "frames": record.frames,
                "gaps_reported": record.gaps_reported,
                "late": record.late,
                "loss_free": record.loss_free,
                "failure": record.failure,
            },
            "device_state": self.run.device_states[(role, n)],
            "loss": loss,
            "channels": None if exported is None else [_encode_measured(m) for m in measured],
        }

    def witnesses(
        self, role: str, n: int, exported: Exported | None, record: _CaptureRecord | None
    ) -> list[JsonValue]:
        """The capture's loss, merged from the reader's holes and the
        recorder's gaps by `at_frame`, each stated as a `loss` finding; and,
        when both witnesses are present, one finding per difference between
        them.
        """
        holes: dict[int, list[Hole]] = {}
        gaps: dict[int, list[_Gap]] = {}
        for h in exported.holes if exported is not None else ():
            holes.setdefault(h.at_frame, []).append(h)
        for g in record.gaps if record is not None else ():
            gaps.setdefault(g.at_frame, []).append(g)
        both = exported is not None and record is not None
        loss: list[JsonValue] = []
        for at in sorted(holes.keys() | gaps.keys()):
            here_holes, here_gaps = holes.get(at, []), gaps.get(at, [])
            for i in range(max(len(here_holes), len(here_gaps))):
                hole = here_holes[i] if i < len(here_holes) else None
                gap = here_gaps[i] if i < len(here_gaps) else None
                length = _length(hole, gap)
                entry = _loss(at, length, hole, gap)
                loss.append(entry)
                self.finding(
                    "loss",
                    f"frames were lost at capture frame {at}",
                    role=role,
                    capture=n,
                    at_frame=at,
                    frames=length,
                    data=entry,
                )
                if not both:
                    continue
                if gap is None:
                    assert hole is not None
                    self.disagree(role, n, "hole_without_gap", None, hole.encode(), at_frame=at)
                elif hole is None:
                    self.disagree(role, n, "gap_without_hole", _encode_gap(gap), None, at_frame=at)
                elif _hole_length(hole) != gap.frames:
                    self.disagree(role, n, "length", _encode_gap(gap), hole.encode(), at_frame=at)
        if exported is None or record is None:
            return loss
        if record.gaps_reported is not None and record.gaps_reported != len(record.gaps):
            self.disagree(
                role,
                n,
                "gaps_reported",
                {"gaps_reported": record.gaps_reported, "gaps": len(record.gaps)},
                {"holes": len(exported.holes)},
            )
        run_frames = sum(r.frames for r in exported.runs)
        fill = sum(h.filled_frames for h in exported.holes if h.basis == "measured")
        if record.frames is not None and record.frames != run_frames + fill:
            self.disagree(
                role,
                n,
                "frames",
                {"frames": record.frames},
                {"run_frames": run_frames, "measured_fill": fill},
            )
        if record.ended == "stopped" and exported.tail != "ended":
            self.disagree(role, n, "end", {"ended": record.ended}, {"tail": exported.tail})
        return loss

    def disagree(
        self,
        role: str,
        n: int,
        difference: str,
        recorder: JsonValue,
        reader: JsonValue,
        at_frame: int | None = None,
    ) -> None:
        self.finding(
            "loss_witnesses_disagree",
            f"the recorder's record and the reader's map differ: {difference}",
            role=role,
            capture=n,
            at_frame=at_frame,
            data={"difference": difference, "recorder": recorder, "reader": reader},
        )

    def channel(self, role: str, n: int, m: Measured) -> None:
        if m.silent:
            self.finding(
                "channel_silent",
                "every sample is 0",
                role=role,
                capture=n,
                channel=m.channel,
            )
        if m.constant:
            self.finding(
                "channel_constant",
                f"every sample is {m.min}",
                role=role,
                capture=n,
                channel=m.channel,
                data={"value": m.min},
            )
        if m.limit_samples:
            first = m.first_limit_spans[0]
            self.finding(
                "channel_at_limit",
                f"{m.limit_samples} samples are at a limit of the format",
                role=role,
                capture=n,
                channel=m.channel,
                at_frame=first.at_frame,
                frames=first.frames,
                data={
                    "limit_samples": m.limit_samples,
                    "limit_spans": m.limit_spans,
                    "longest_limit_span": m.longest_limit_span,
                },
            )

    def play(self, role: str, n: int) -> JsonObject:
        [aligned] = [p for p in self.aligned.plays if p.role == role and p.play == n]
        output = self.run.outputs[(role, n)]
        if output not in OUTPUTS:
            self.unexamined(
                "play_account",
                "unknown_output_kind",
                f"play {n} of {role} is on an output of kind {output!r}, which this "
                "harness does not know: its unplayed_frames is not read",
                role=role,
                play=n,
            )
        record = self.run.plays.get((role, n))
        if record is not None:
            self.play_record(role, n, record)
        return {
            "role": role,
            "play": n,
            "file": aligned.file,
            "record": None
            if record is None
            else {
                "ended": record.ended,
                "stopped_by": record.stopped_by,
                "frames": record.frames,
                "frames_handed": record.frames_handed,
                "unplayed_frames": record.unplayed_frames,
                "underruns": len(record.underruns),
                "as_sent": record.as_sent,
                "restored": record.restored,
                "failure": record.failure,
                "asked_start": record.asked_start,
                "started_after_us": record.started_after_us,
            },
            "marker": self.marker(aligned, record),
        }

    def play_record(self, role: str, n: int, record: _PlayRecord) -> None:
        if record.ended == "failed":
            self.finding(
                "play_failed",
                "the play's record says it failed",
                role=role,
                play=n,
                data=record.failure or {},
            )
        if record.ended == "stopped" and record.stopped_by == "runner":
            self.finding(
                "play_cut_by_runner",
                "the runner cut the play at release",
                role=role,
                play=n,
                data={
                    "frames_handed": record.frames_handed,
                    "unplayed_frames": record.unplayed_frames,
                },
            )
        for at in record.underruns:
            self.finding(
                "play_underrun",
                f"sound was interrupted at frame {at} of the kept copy",
                role=role,
                play=n,
                at_frame=at,
            )
        if not record.restored:
            self.finding(
                "controls_not_restored",
                "the role did not put every control back as found",
                role=role,
                play=n,
            )

    def marker(self, aligned: alignment.Play, record: _PlayRecord | None) -> JsonObject:
        """The play's markers as alignment found them, and what of that is a
        finding or not assessed.
        """
        role, n = aligned.role, aligned.play
        state, reason = aligned.state, aligned.reason
        detail = aligned.detail or ""
        unmarked: JsonObject = {
            "state": state,
            "start": None,
            "end": None,
            "located": None,
            "found_in": [],
        }
        if state == "no_marker_record":
            self.unexamined(
                "markers",
                "no_marker_record",
                "the play's entry has no marker record",
                role=role,
                play=n,
            )
        if state == "failed" and reason == "unknown_definition":
            self.unexamined("markers", "unknown_definition", detail, role=role, play=n)
        if state == "failed" and reason in COPY_UNUSABLE:
            self.finding("kept_copy_unusable", detail, role=role, play=n, data={"reason": reason})
            self.unexamined("markers", "copy_unusable", detail, role=role, play=n)
        if state != "marked":
            return unmarked
        carried = aligned.carried
        assert carried is not None
        kind = self.run.outputs[(role, n)]
        start = _word(aligned, carried.start, record, kind)
        end = _word(aligned, carried.end, record, kind)
        tried = [p for p in self.aligned.pairs if p.play is aligned and p.not_tried is None]
        outcomes: Counter[str] = Counter()
        located = False
        found_in: list[JsonValue] = []
        for pair in tried:
            for channel, both in pair.channels.items():
                for o in both.values():
                    outcomes[o.outcome if o.reason is None else f"{o.outcome}/{o.reason}"] += 1
                located = located or pair.located_on(channel)
                start_found = both["start"].outcome == "found"
                end_found = both["end"].outcome == "found"
                if start_found and (end != "expected" or end_found):
                    measures = pair.measures[channel]
                    found_in.append(
                        {
                            "capture": pair.capture.key(),
                            "channel": channel,
                            "offset_frames": measures["offset_frames"],
                            "ratio_basis": measures["ratio_basis"],
                            "drift_ppm": measures["drift_ppm"],
                            "drift_unmeasured": measures["drift_unmeasured"],
                        }
                    )
        if not tried:
            self.unexamined(
                "markers",
                "no_capture_searched",
                "no capture was searched for the play's markers",
                role=role,
                play=n,
            )
        elif start == "expected" and not found_in:
            self.finding(
                "play_not_located",
                "the play's start marker was found on no channel of any capture searched",
                role=role,
                play=n,
                data={
                    "start": start,
                    "end": end,
                    "pairs_tried": len(tried),
                    "outcomes": dict(sorted(outcomes.items())),
                },
            )
        return {
            "state": state,
            "start": start,
            "end": end,
            "located": located,
            "found_in": found_in,
        }


def _word(play: alignment.Play, span: Span, record: _PlayRecord | None, kind: str) -> str:
    """Whether the marker at `span` of the kept copy is expected in a capture:
    `not_handed` when alignment does not count it handed whole, else
    `expected` or `unknown` from the play's record and its output kind. Every
    marker of a play with an underrun on a kind that may skip frames after one
    is `unknown`: where the skipped frames lie is not known, and a sink's
    report of an underrun can trail the server's running out by one write.
    """
    if not play.handed(span):
        return "not_handed"
    if record is not None and record.underruns and kind not in PLAYS_ON_AFTER_UNDERRUN:
        return "unknown"
    if record is None or record.ended == "completed":
        return "expected"
    handed, unplayed = record.frames_handed, record.unplayed_frames
    if (
        handed is not None
        and unplayed is not None
        and handed - unplayed >= span.frame + span.frames
    ):
        return "expected"
    return "unknown"


def _hole_length(hole: Hole) -> int | None:
    return hole.filled_frames if hole.basis in LENGTHS else None


def _length(hole: Hole | None, gap: _Gap | None) -> int | None:
    """The loss's length where a witness measured it, the recorder's first."""
    if gap is not None and gap.frames is not None:
        return gap.frames
    return None if hole is None else _hole_length(hole)


def _loss(at: int, length: int | None, hole: Hole | None, gap: _Gap | None) -> JsonObject:
    witnesses: list[JsonValue] = []
    if hole is not None:
        witnesses.append("reader")
    if gap is not None:
        witnesses.append("recorder")
    return {
        "at_frame": at,
        "frames": length,
        "basis": None if hole is None else hole.basis,
        "estimated_frames": hole.filled_frames
        if hole is not None and hole.basis == "clock_estimate"
        else None,
        "cause": None if gap is None else gap.cause,
        "witnesses": witnesses,
    }


def _encode_gap(gap: _Gap) -> JsonObject:
    return {"cause": gap.cause, "at_frame": gap.at_frame, "frames": gap.frames}


def _encode_measured(m: Measured) -> JsonObject:
    return {
        "channel": m.channel,
        "frames": m.frames,
        "min": m.min,
        "max": m.max,
        "mean": m.mean,
        "peak_mb": m.peak_mb,
        "rms_mb": m.rms_mb,
        "silent": m.silent,
        "constant": m.constant,
        "limit_samples": m.limit_samples,
        "limit_spans": m.limit_spans,
        "longest_limit_span": m.longest_limit_span,
        "first_limit_spans": [
            {"at_frame": s.at_frame, "frames": s.frames} for s in m.first_limit_spans
        ],
    }
