import datetime

import pytest

from brenn_lab.playback import limits
from brenn_lab.playback.limits import QuietMinutes

EASTERN = datetime.timezone(datetime.timedelta(hours=-5))


def at(hour: int, minute: int, second: int = 0, day: int = 13) -> datetime.datetime:
    return datetime.datetime(2026, 11, day, hour, minute, second, tzinfo=EASTERN)


@pytest.mark.parametrize(
    ("peak", "bits", "expected"),
    [
        (32768, 16, 0),
        (16384, 16, -602),
        (32767, 16, 0),
        (1, 16, -9030),
        (1 << 31, 32, 0),
        (1 << 30, 32, -602),
        (1, 32, -18663),
    ],
)
def test_peak_is_relative_to_full_scale_rounded_up(peak: int, bits: int, expected: int) -> None:
    assert limits.peak_mb(peak, bits) == expected


def test_silence_has_no_peak() -> None:
    assert limits.peak_mb(0, 16) is None


def test_level_sums_the_level_control_and_held_controls() -> None:
    assert limits.level_mb(-1800, [0]) == -1800
    assert limits.level_mb(-3000, [0]) == -3000
    assert limits.level_mb(-1000, [-300, -200]) == -1500
    assert limits.level_mb(-1000, []) == -1000


def test_the_cap_is_the_highest_level_the_controls_are_set_to() -> None:
    assert limits.over_cap(-1800, [-600], -2400) is None
    failure = limits.over_cap(-1799, [-600], -2400)
    assert failure is not None
    assert failure.reason == "over_cap"
    assert failure.data == {
        "level_control_mb": -1799,
        "held_mb": -600,
        "level_mb": -2399,
        "cap_mb": -2400,
    }


def test_the_audible_test_levels() -> None:
    assert limits.over_cap(-1800, [0], -1800) is None
    assert limits.over_cap(-3000, [0], -1800) is None
    failure = limits.over_cap(-1700, [0], -1800)
    assert failure is not None and failure.data["level_mb"] == -1700


@pytest.mark.parametrize(
    ("text", "minute"),
    [("00:00", 0), ("07:30", 450), ("23:59", 1439), ("22:00", 1320)],
)
def test_clock_parses_and_formats(text: str, minute: int) -> None:
    assert limits.parse_clock(text) == minute
    assert limits.format_clock(minute) == text


@pytest.mark.parametrize("text", ["24:00", "7:30", "07:60", "07:30:00", " 07:30", "", "ab:cd"])
def test_clock_refuses_other_forms(text: str) -> None:
    assert limits.parse_clock(text) is None


NIGHT = QuietMinutes(22 * 60, 7 * 60)
LUNCH = QuietMinutes(12 * 60, 13 * 60)


def test_no_spans_is_never_quiet() -> None:
    assert limits.quiet_span_during([], at(23, 0), 86400 * 3) is None


@pytest.mark.parametrize(
    ("now", "seconds", "quiet"),
    [
        (at(11, 0), 0, False),
        (at(12, 0), 0, True),
        (at(12, 59, 59), 0, True),
        (at(13, 0), 0, False),
        (at(11, 59, 50), 9.9, False),
        (at(11, 59, 50), 10, True),
        (at(11, 0), 7200, True),
    ],
)
def test_a_span_within_a_day(now: datetime.datetime, seconds: float, quiet: bool) -> None:
    assert (limits.quiet_span_during([LUNCH], now, seconds) == LUNCH) == quiet


@pytest.mark.parametrize(
    ("now", "seconds", "quiet"),
    [
        (at(21, 59), 0, False),
        (at(22, 0), 0, True),
        (at(23, 59, 59), 0, True),
        (at(0, 0), 0, True),
        (at(3, 0), 0, True),
        (at(6, 59, 59), 0, True),
        (at(7, 0), 0, False),
        (at(12, 0), 0, False),
        (at(21, 59, 50), 9, False),
        (at(21, 59, 50), 10, True),
        (at(7, 0), 15 * 3600 - 1, False),
        (at(7, 0), 15 * 3600, True),
    ],
)
def test_a_span_over_midnight(now: datetime.datetime, seconds: float, quiet: bool) -> None:
    assert (limits.quiet_span_during([NIGHT], now, seconds) == NIGHT) == quiet


def test_a_source_that_would_run_into_a_span_is_quiet() -> None:
    assert limits.quiet_span_during([NIGHT, LUNCH], at(11, 59, 41), 19.5) == LUNCH
    assert limits.quiet_span_during([NIGHT, LUNCH], at(11, 59, 40), 19.5) is None


def test_a_play_over_a_whole_day_meets_every_span() -> None:
    assert limits.quiet_span_during([LUNCH], at(13, 0), 86400) == LUNCH


_HOUR = datetime.timedelta(hours=1)
_SPRING = datetime.datetime(2027, 3, 14, 2, 0)
_FALL = datetime.datetime(2027, 11, 7, 1, 0)


class Eastern2027(datetime.tzinfo):
    """US Eastern time through 2027: summer time from 02:00 on 14 March, when
    02:00 to 03:00 is skipped, to 02:00 on 7 November, when 01:00 to 02:00
    occurs twice. A skipped or repeated time is read as PEP 495 reads it.
    """

    def utcoffset(self, dt: datetime.datetime | None) -> datetime.timedelta:
        assert dt is not None
        wall = dt.replace(tzinfo=None, fold=0)
        if _SPRING <= wall < _SPRING + _HOUR:
            summer = dt.fold == 1
        elif _FALL <= wall < _FALL + _HOUR:
            summer = dt.fold == 0
        else:
            summer = _SPRING <= wall < _FALL
        return -5 * _HOUR + (_HOUR if summer else datetime.timedelta())

    def dst(self, dt: datetime.datetime | None) -> datetime.timedelta:
        return self.utcoffset(dt) + 5 * _HOUR

    def tzname(self, dt: datetime.datetime | None) -> str:
        return "EDT" if self.dst(dt) else "EST"


NEW_YORK = Eastern2027()


def eastern(month: int, day: int, hour: int, minute: int, fold: int = 0) -> datetime.datetime:
    return datetime.datetime(2027, month, day, hour, minute, tzinfo=NEW_YORK, fold=fold)


@pytest.mark.parametrize(
    ("now", "seconds", "span", "quiet"),
    [
        # Clocks go forward: 01:50 EST and 30 minutes is 03:20 EDT.
        (eastern(3, 14, 1, 50), 1800, QuietMinutes(3 * 60, 7 * 60), True),
        (eastern(3, 14, 1, 50), 590, QuietMinutes(3 * 60, 7 * 60), False),
        (eastern(3, 14, 1, 50), 610, QuietMinutes(3 * 60, 7 * 60), True),
        # Clocks go back: 00:50 EDT and two hours is 01:50 EST.
        (eastern(11, 7, 0, 50), 7200, QuietMinutes(2 * 60 + 30, 7 * 60), False),
        (eastern(11, 7, 0, 50), 3 * 3600 + 2400, QuietMinutes(2 * 60 + 30, 7 * 60), True),
        # A span ending at 01:30 lasts to its second reading.
        (eastern(11, 7, 1, 40, fold=1), 0, QuietMinutes(22 * 60, 90), False),
        (eastern(11, 7, 1, 20, fold=1), 0, QuietMinutes(22 * 60, 90), True),
        # A span starting at 02:30, which the clock skips, starts at its earlier reading.
        (eastern(3, 14, 1, 20), 0, QuietMinutes(2 * 60 + 30, 7 * 60), False),
        (eastern(3, 14, 1, 40), 0, QuietMinutes(2 * 60 + 30, 7 * 60), True),
    ],
)
def test_quiet_hours_across_a_change_of_offset(
    now: datetime.datetime, seconds: float, span: QuietMinutes, quiet: bool
) -> None:
    assert (limits.quiet_span_during([span], now, seconds) == span) == quiet


def test_quiet_hours_failure_carries_the_span_and_the_local_time() -> None:
    assert limits.quiet_hours([NIGHT], at(12, 0), 10) is None
    failure = limits.quiet_hours([NIGHT], at(22, 30), 10)
    assert failure is not None
    assert failure.reason == "quiet_hours"
    assert failure.data == {
        "start": "22:00",
        "end": "07:00",
        "local_time": "2026-11-13T22:30:00-05:00",
        "seconds": 10,
    }


def test_local_time_has_its_offset() -> None:
    assert limits.local_time(at(7, 30, 5)) == "2026-11-13T07:30:05-05:00"


def test_a_naive_time_is_refused() -> None:
    with pytest.raises(AssertionError):
        limits.quiet_span_during([NIGHT], datetime.datetime(2026, 11, 13, 12, 0), 0)


def test_a_span_must_not_be_empty() -> None:
    with pytest.raises(AssertionError):
        QuietMinutes(600, 600)
