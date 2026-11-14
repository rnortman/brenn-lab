import dataclasses

import pytest

from brenn_lab.data import (
    MONOTONIC,
    REALTIME,
    ClockReading,
    ClockValue,
    Event,
    Failure,
    JsonObject,
    Location,
    Reply,
    encode_value,
)
from brenn_lab.playback import messages as m
from brenn_lab.playback.account import PlaybackAccount
from brenn_lab.sound import CardIdentity, Granted

AT = ClockReading(
    machine="bench",
    monotonic=ClockValue(MONOTONIC, 81_000_000),
    wall=ClockValue(REALTIME, 1_794_000_000_000_000),
)
START = AT.after(2.0)
WOKE = AT.after(2.0001)
FIRST = m.Mark(frames_handed=1600, delay_frames=320, at=AT.after(2.0003))
END = AT.after(3.0)
LEVEL = m.Control("PCM", 1, "level", (m.Channel("Front Left", -1800, True),))
STARTED = m.Started(
    play=1,
    source=m.Source(
        name="a.wav",
        sha256="ab" * 32,
        frames=4800,
        rate_hz=16000,
        channels=1,
        sample_format="s16_le",
        peak_mb=-602,
        marker=None,
    ),
    copy=Location(machine="bench", path="/runs/r/sources/speaker/0001.wav"),
    output=m.Output(kind="alsa", library=m.Library(name="libasound.so.2", version="1.2.14")),
    device=CardIdentity(
        selector="Fake",
        card_index=3,
        card_name="Fake",
        card_long_name="Fake card 1",
        pcm="hw:3,0",
        usb_id=None,
    ),
    granted=Granted(
        rate_hz=16000, channels=1, sample_format="s16_le", period_frames=320, buffer_frames=8000
    ),
    controls=(LEVEL,),
    found=(LEVEL,),
    level_mb=-1800,
    cap_mb=-1800,
    local_time="2026-11-14T12:00:00-05:00",
    asked_start=START,
    first=None,
)
ENDED = m.Ended(
    play=1,
    frames_handed=4800,
    unplayed_frames=0,
    underruns=0,
    last=m.Mark(frames_handed=4800, delay_frames=0, at=AT.after(2.4)),
    end=END,
    restored=True,
    woke=WOKE,
    first=FIRST,
)


def pointer(location: Location) -> JsonObject:
    return {"machine": location.machine, "path": location.path, "in_run": True}


def record(messages: list[Reply | Event]) -> JsonObject:
    account = PlaybackAccount("speaker", pointer)
    for message in messages:
        if isinstance(message, Reply):
            account.reply(message, "script")
        else:
            account.event(message)
    [one] = account.records()
    return one


REPLY = Reply(role="speaker", command="playback.play", dry=False, outcome=STARTED)


@pytest.mark.parametrize("end_first", [False, True])
def test_a_timed_play_s_first_mark_whichever_arrives_first(end_first: bool) -> None:
    end = Event(role="speaker", body=ENDED)
    done = record([end, REPLY] if end_first else [REPLY, end])
    assert done["asked_start"] == encode_value(START)
    assert done["woke"] == encode_value(WOKE)
    assert done["first"] == encode_value(FIRST)
    assert done["started_after_us"] == 300


@pytest.mark.parametrize("end_first", [False, True])
def test_an_untimed_play_s_first_mark_whichever_arrives_first(end_first: bool) -> None:
    reply = dataclasses.replace(
        REPLY, outcome=dataclasses.replace(STARTED, asked_start=None, first=FIRST)
    )
    end = Event(role="speaker", body=dataclasses.replace(ENDED, woke=None, first=None))
    done = record([end, reply] if end_first else [reply, end])
    assert done["first"] == encode_value(FIRST)
    assert (done["asked_start"], done["woke"], done["started_after_us"]) == (None, None, None)


def test_a_timed_play_that_woke_too_late() -> None:
    failed = m.Failed(
        play=1,
        failure=Failure("start_passed", "late", {"late_us": 20_001}),
        frames_handed=0,
        unplayed_frames=0,
        underruns=0,
        last=None,
        end=END,
        restored=True,
        woke=START.after(0.020001),
        first=None,
    )
    done = record([REPLY, Event(role="speaker", body=failed)])
    assert done["ended"] == "failed" and done["failure"] == encode_value(failed.failure)
    assert done["frames_handed"] == 0
    assert done["woke"] == encode_value(failed.woke)
    assert (done["first"], done["last"], done["started_after_us"]) == (None, None, None)
    assert done["asked_start"] == encode_value(START)
