"""The playback role's limits: the level its controls are set to against its cap, and quiet hours.

Pure functions of values and a clock reading; nothing here touches a device.
The role enforces both on everything sent to it. A script runs in the role's
own process, so one that opens a sound device itself is not held by them.
TODO(script-shares-the-playback-roles-process)
"""

import datetime
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass

from brenn_lab.data import Failure

_CLOCK = re.compile(r"([01][0-9]|2[0-3]):([0-5][0-9])")
_DAY_MINUTES = 24 * 60


@dataclass(frozen=True)
class QuietMinutes:
    """Quiet from minute `start` of the local day to minute `end`, end
    excluded. A span whose end is not after its start wraps midnight; the two
    are never equal.
    """

    start: int
    end: int

    def __post_init__(self) -> None:
        assert 0 <= self.start < _DAY_MINUTES and 0 <= self.end < _DAY_MINUTES
        assert self.start != self.end


def parse_clock(text: str) -> int | None:
    """The minute of the day `"HH:MM"` names, or None if it is not of that form."""
    match = _CLOCK.fullmatch(text)
    if match is None:
        return None
    return int(match[1]) * 60 + int(match[2])


def format_clock(minute: int) -> str:
    """Minute `minute` of the day as `"HH:MM"`."""
    return f"{minute // 60:02d}:{minute % 60:02d}"


def peak_mb(peak: int, bits: int) -> int | None:
    """The magnitude `peak` of a `bits`-bit integer sample relative to full
    scale, in millibels rounded up; None for silence. Full scale is the
    magnitude of the most negative sample, so a peak of half of it is -602.
    """
    full_scale = 1 << (bits - 1)
    assert 0 <= peak <= full_scale
    if peak == 0:
        return None
    return math.ceil(2000 * math.log10(peak / full_scale))


def level_mb(level_volume_mb: int, held_volumes_mb: Sequence[int]) -> int:
    """The level of a play: the level control's volume plus every held
    control's, which is the level at which a full-scale sample is played. The
    source does not enter it.
    """
    return level_volume_mb + sum(held_volumes_mb)


def over_cap(level_volume_mb: int, held_volumes_mb: Sequence[int], cap_mb: int) -> Failure | None:
    """`over_cap` when the controls' level exceeds the cap, with the numbers it was found from."""
    level = level_mb(level_volume_mb, held_volumes_mb)
    if level <= cap_mb:
        return None
    held = sum(held_volumes_mb)
    return Failure(
        "over_cap",
        f"the play's level, {level} mB, is above the cap of {cap_mb} mB",
        {
            "level_control_mb": level_volume_mb,
            "held_mb": held,
            "level_mb": level,
            "cap_mb": cap_mb,
        },
    )


def local_time(now: datetime.datetime) -> str:
    """`now`, an aware local time, as ISO 8601 with its UTC offset."""
    assert now.tzinfo is not None
    return now.isoformat(timespec="seconds")


def quiet_span_during(
    spans: Sequence[QuietMinutes], now: datetime.datetime, seconds: float
) -> QuietMinutes | None:
    """The first span that any instant from `now` to `now + seconds` falls in,
    or None. `now` is an aware datetime in the zone whose civil time the spans
    are stated in; the play's length is elapsed time, so across a change of
    the zone's offset the span is met at its wall time on the new offset. A
    span edge at a wall time the clock skips or repeats that night is taken at
    whichever reading makes the span longer.
    """
    assert now.tzinfo is not None and seconds >= 0
    start = now.astimezone(datetime.UTC)
    end = start + datetime.timedelta(seconds=seconds)
    first_day = now.date() - datetime.timedelta(days=1)
    days = ((now + datetime.timedelta(seconds=seconds)).date() - first_day).days + 2
    for span in spans:
        for n in range(days):
            day = datetime.datetime.combine(
                first_day + datetime.timedelta(days=n), datetime.time(), tzinfo=now.tzinfo
            )
            span_start = day + datetime.timedelta(minutes=span.start)
            span_end = day + datetime.timedelta(minutes=span.end)
            if span.end < span.start:
                span_end += datetime.timedelta(days=1)
            if min(_readings(span_start)) <= end and start < max(_readings(span_end)):
                return span
    return None


def _readings(wall: datetime.datetime) -> tuple[datetime.datetime, datetime.datetime]:
    """The instants, in UTC, that the aware wall time `wall` can be read as."""
    return (
        wall.replace(fold=0).astimezone(datetime.UTC),
        wall.replace(fold=1).astimezone(datetime.UTC),
    )


def quiet_hours(
    spans: Sequence[QuietMinutes], now: datetime.datetime, seconds: float
) -> Failure | None:
    """`quiet_hours` when a play of `seconds` from `now` would fall in a quiet span."""
    span = quiet_span_during(spans, now, seconds)
    if span is None:
        return None
    start, end = format_clock(span.start), format_clock(span.end)
    return Failure(
        "quiet_hours",
        f"quiet from {start} to {end}; the local time is {local_time(now)}",
        {"start": start, "end": end, "local_time": local_time(now), "seconds": seconds},
    )
