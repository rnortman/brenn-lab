"""The capture kind's section of a run's manifest and results."""

from dataclasses import dataclass, field

from brenn_lab.account import Pointer, end_not_reported
from brenn_lab.capture.messages import Failed, Gap, Late, Released, Started, Stop, Stopped
from brenn_lab.data import (
    COMMANDS,
    ClockReading,
    Event,
    Failure,
    JsonObject,
    JsonValue,
    Release,
    Reply,
    encode_object,
    encode_value,
)


@dataclass
class _Capture:
    """One capture's record in the results, filled as replies and events arrive."""

    role: str
    capture: int
    capture_id: str | None = None
    file: JsonObject | None = None
    ended: str | None = None
    stopped_by: str | None = None
    frames: int | None = None
    gaps_reported: int | None = None
    gaps: list[JsonValue] = field(default_factory=list)
    late: list[JsonValue] = field(default_factory=list)
    failure: Failure | None = None
    end: ClockReading | None = None
    addressed: bool = False

    def stopped(self, s: Stopped, by: str) -> None:
        self.ended, self.stopped_by = "stopped", by
        self.frames, self.gaps_reported, self.end = s.frames, s.gaps_reported, s.end

    def failed(self, failure: Failure) -> None:
        if self.ended is not None:
            return
        self.ended, self.failure = "failed", failure

    def counted(self, s: Stopped) -> None:
        """The recorder's counts for a capture that stopped with nobody asking."""
        self.frames, self.gaps_reported = s.frames, s.gaps_reported

    def encode(self) -> JsonObject:
        loss_free = self.ended == "stopped" and not self.gaps and self.gaps_reported == 0
        return {
            "role": self.role,
            "capture": self.capture,
            "capture_id": self.capture_id,
            "file": self.file,
            "ended": self.ended,
            "stopped_by": self.stopped_by,
            "frames": self.frames,
            "gaps_reported": self.gaps_reported,
            "gaps": self.gaps,
            "late": self.late,
            "loss_free": loss_free,
            "failure": None if self.failure is None else encode_value(self.failure),
            "end": None if self.end is None else encode_value(self.end),
        }


class CaptureAccount:
    """A capture role's account: one manifest entry per capture whose start
    was reported, the `Started` result with its location given as the run's
    pointer, and one results record per such capture.
    """

    def __init__(self, role: str, pointer: Pointer):
        self._role = role
        self._pointer = pointer
        self._captures: dict[int, _Capture] = {}
        self._entries: list[JsonObject] = []
        self._repositories: list[JsonObject] = []

    def _capture(self, n: int) -> _Capture:
        return self._captures.setdefault(n, _Capture(self._role, n))

    def _open(self) -> _Capture | None:
        mine = [c for _, c in sorted(self._captures.items()) if not c.addressed]
        return mine[-1] if mine else None

    def reply(self, reply: Reply, by: str) -> bool:
        outcome = reply.outcome
        if isinstance(outcome, Failure):
            # A failed stop or release is taken to address the capture still open.
            if COMMANDS[reply.command] in (Stop, Release):
                target = self._open()
                if target is not None:
                    target.addressed = True
                    target.failed(outcome)
        elif isinstance(outcome, Started) and outcome.location is not None:
            self._started(outcome, self._pointer(outcome.location))
            return True
        elif isinstance(outcome, Stopped):
            self._capture(outcome.capture).stopped(outcome, by)
            self._capture(outcome.capture).addressed = True
        elif isinstance(outcome, Released) and outcome.stopped is not None:
            record = self._capture(outcome.stopped.capture)
            record.stopped(outcome.stopped, by)
            record.addressed = True
        return False

    def _started(self, started: Started, file: JsonObject) -> None:
        record = self._capture(started.capture)
        record.capture_id = started.capture_id
        record.file = file
        entry = encode_object(started)
        del entry["location"]
        entry["file"] = file
        self._entries.append(entry)
        build = encode_value(started.recorder.build)
        assert isinstance(build, dict)
        self._repositories.append(
            {
                "repository": started.recorder.repository,
                **build,
                "from": f"the recorder's report at the start of capture "
                f"{self._role}/{started.capture:04d}",
            }
        )

    def event(self, event: Event) -> bool:
        body = event.body
        if isinstance(body, Gap | Late):
            value = encode_value(body)
            assert isinstance(value, dict)
            del value["capture"]
            record = self._capture(body.capture)
            (record.gaps if isinstance(body, Gap) else record.late).append(value)
        elif isinstance(body, Failed):
            record = self._capture(body.capture)
            record.failed(body.failure)
            if body.stopped is not None:
                record.counted(body.stopped)
        return False

    def entries(self) -> list[JsonObject]:
        return list(self._entries)

    def records(self) -> list[JsonObject]:
        records = [c for _, c in sorted(self._captures.items()) if c.capture_id is not None]
        for c in records:
            c.failed(end_not_reported("capture"))
        return [c.encode() for c in records]

    def repositories(self) -> list[JsonObject]:
        return list(self._repositories)


def left_running(outcome: object) -> str | None:
    """What the role's answer to `release` says was still running."""
    if isinstance(outcome, Released) and outcome.stopped is not None:
        return f"capture {outcome.stopped.capture}"
    return None


def totals(records: list[JsonObject]) -> JsonObject:
    """The results' `recorded`, from every capture record of the run."""
    frames = 0
    for r in records:
        if isinstance(r["frames"], int):
            frames += r["frames"]
    return {
        "captures_started": len(records),
        "captures_stopped": sum(1 for r in records if r["ended"] == "stopped"),
        "frames": frames,
    }
