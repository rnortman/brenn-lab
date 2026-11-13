"""The playback kind's section of a run's manifest and results."""

from dataclasses import dataclass, field

from brenn_lab.account import Pointer
from brenn_lab.data import (
    ClockReading,
    Event,
    Failure,
    JsonObject,
    JsonValue,
    Reply,
    encode_object,
    encode_value,
)
from brenn_lab.playback.messages import Ended, Failed, Mark, Released, Started, Stopped, Underrun


@dataclass
class _Play:
    """One play's record in the results, filled as replies and events arrive."""

    role: str
    play: int
    file: JsonObject | None = None
    source: JsonObject | None = None
    frames: int | None = None
    ended: str | None = None
    stopped_by: str | None = None
    frames_handed: int | None = None
    unplayed_frames: int | None = None
    underruns: list[JsonValue] = field(default_factory=list)
    first: Mark | None = None
    last: Mark | None = None
    end: ClockReading | None = None
    restored: bool | None = None
    failure: Failure | None = None

    def finished(self, how: str, body: Stopped | Ended | Failed) -> None:
        self.ended = how
        self.frames_handed, self.unplayed_frames = body.frames_handed, body.unplayed_frames
        self.last, self.end, self.restored = body.last, body.end, body.restored

    def encode(self) -> JsonObject:
        as_sent = self.ended == "completed" and not self.underruns
        return {
            "role": self.role,
            "play": self.play,
            "file": self.file,
            "source": self.source,
            "ended": self.ended,
            "stopped_by": self.stopped_by,
            "frames": self.frames,
            "frames_handed": self.frames_handed,
            "unplayed_frames": self.unplayed_frames,
            "underruns": self.underruns,
            "as_sent": as_sent,
            "first": None if self.first is None else encode_value(self.first),
            "last": None if self.last is None else encode_value(self.last),
            "end": None if self.end is None else encode_value(self.end),
            "restored": self.restored,
            "failure": None if self.failure is None else encode_value(self.failure),
        }


class PlaybackAccount:
    """A playback role's account: one manifest entry per play started, the
    `Started` result with its kept copy given as the run's pointer, and one
    results record per such play. No other repository's code plays, so it
    learns no repository.
    """

    def __init__(self, role: str, pointer: Pointer):
        self._role = role
        self._pointer = pointer
        self._plays: dict[int, _Play] = {}
        self._entries: list[JsonObject] = []

    def _play(self, n: int) -> _Play:
        return self._plays.setdefault(n, _Play(self._role, n))

    def reply(self, reply: Reply, by: str) -> bool:
        outcome = reply.outcome
        if isinstance(outcome, Started) and outcome.copy is not None:
            self._started(outcome, self._pointer(outcome.copy))
            return True
        stopped = outcome.stopped if isinstance(outcome, Released) else outcome
        if isinstance(stopped, Stopped) and not reply.dry:
            record = self._play(stopped.play)
            record.finished("stopped", stopped)
            record.stopped_by = by
        return False

    def _started(self, started: Started, file: JsonObject) -> None:
        record = self._play(started.play)
        record.file = file
        record.source = {"name": started.source.name, "sha256": started.source.sha256}
        record.frames = started.source.frames
        record.first = started.first
        entry = encode_object(started)
        del entry["copy"]
        entry["file"] = file
        self._entries.append(entry)

    def event(self, event: Event) -> bool:
        body = event.body
        if isinstance(body, Underrun):
            value = encode_value(body)
            assert isinstance(value, dict)
            del value["play"]
            self._play(body.play).underruns.append(value)
        elif isinstance(body, Ended):
            self._play(body.play).finished("completed", body)
        elif isinstance(body, Failed):
            record = self._play(body.play)
            record.finished("failed", body)
            record.failure = body.failure
        return False

    def entries(self) -> list[JsonObject]:
        return list(self._entries)

    def records(self) -> list[JsonObject]:
        return [p.encode() for _, p in sorted(self._plays.items()) if p.file is not None]

    def repositories(self) -> list[JsonObject]:
        return []


def left_running(outcome: object) -> str | None:
    """What the role's answer to `release` says was still running."""
    if isinstance(outcome, Released) and outcome.stopped is not None:
        return f"play {outcome.stopped.play}"
    return None


def totals(records: list[JsonObject]) -> JsonObject:
    """The results' `played`, from every play record of the run."""
    frames = 0
    for r in records:
        if isinstance(r["frames_handed"], int):
            frames += r["frames_handed"]
    return {
        "plays_started": len(records),
        "plays_completed": sum(1 for r in records if r["ended"] == "completed"),
        "frames_handed": frames,
    }
