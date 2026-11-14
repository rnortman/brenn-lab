import dataclasses

import pytest

from brenn_lab import playback
from brenn_lab.data import (
    COMMANDS,
    EVENTS,
    MONOTONIC,
    REALTIME,
    RESULTS,
    ClockReading,
    ClockValue,
    Command,
    Event,
    Failure,
    Location,
    Reply,
    decode,
    dumps,
    encode,
    failure_of,
    loads,
)
from brenn_lab.marker import Carried, Span
from brenn_lab.playback import messages as m
from brenn_lab.sound import CardIdentity, Granted

AT = ClockReading(
    machine="bench",
    monotonic=ClockValue(MONOTONIC, 81234567),
    wall=ClockValue(REALTIME, 1794000000000000),
)
CARD = CardIdentity(
    selector="Flex",
    card_index=2,
    card_name="reSpeaker Flex",
    card_long_name="Seeed reSpeaker Flex at usb-1",
    pcm="hw:2,0",
    usb_id=None,
)
GRANTED = Granted(
    rate_hz=16000, channels=2, sample_format="s16_le", period_frames=320, buffer_frames=8000
)
SOURCE = m.Source(
    name="clips/a.wav",
    sha256="ab" * 32,
    frames=312000,
    rate_hz=16000,
    channels=2,
    sample_format="s16_le",
    peak_mb=-602,
    marker=Carried(
        definition="gold-bpsk-1",
        member=3,
        start_code=6,
        end_code=7,
        start=Span(frame=8000, frames=8193),
        end=Span(frame=303807, frames=8193),
    ),
)
OUTPUT = m.Output(kind="alsa", library=m.Library(name="libasound.so.2", version="1.2.14"))
LEVEL = m.Control(
    element="PCM",
    index=1,
    part="level",
    channels=(m.Channel("Front Left", -1800, True), m.Channel("Front Right", -1800, True)),
)
HELD = m.Control(element="Speaker", index=0, part="held", channels=(m.Channel("Mono", None, True),))
MARK = m.Mark(frames_handed=1600, delay_frames=None, at=AT)
STOPPED = m.Stopped(
    play=1,
    frames_handed=16000,
    unplayed_frames=None,
    underruns=0,
    last=MARK,
    end=AT,
    restored=True,
)
STARTED = m.Started(
    play=1,
    source=SOURCE,
    copy=Location(machine="bench", path="/runs/r/sources/speaker/0001.wav"),
    output=OUTPUT,
    device=CARD,
    granted=GRANTED,
    controls=(LEVEL, HELD),
    found=(LEVEL, HELD),
    level_mb=-1800,
    cap_mb=-1800,
    local_time="2026-11-13T14:00:00-05:00",
    first=MARK,
)
FAILURE = Failure("controls_moved", "PCM 1 moved", {"element": "PCM", "index": 1})

COMMAND_BODIES = [m.Play(source="clips/a.wav", volume_mb=-1800), m.Stop()]
RESULT_BODIES: list[object] = [
    STARTED,
    dataclasses.replace(
        STARTED,
        play=0,
        copy=None,
        first=None,
        source=dataclasses.replace(SOURCE, peak_mb=None, marker=None),
    ),
    STOPPED,
    dataclasses.replace(STOPPED, last=None, unplayed_frames=320),
    m.Checked(
        output=OUTPUT,
        device=CARD,
        controls=(
            m.CheckedControl("PCM", 1, "level", m.VolumeRange(-6000, 0), LEVEL.channels),
            m.CheckedControl("Speaker", 0, "held", None, HELD.channels),
        ),
        cap_mb=-2400,
        quiet_hours=(m.QuietSpan("22:00", "07:00"),),
        local_time="2026-11-13T14:00:00-05:00",
        quiet_now=False,
    ),
    m.Released(stopped=STOPPED),
    m.Released(stopped=None),
]
SINK = m.SinkIdentity(
    selector="desk",
    sink_name="alsa_output.usb-Desk_Speaker-00.analog-stereo",
    description="Desk Speaker Analog Stereo",
    server_name="PulseAudio (on PipeWire 1.6.9)",
    server_version="15.0.0",
    rate_hz=48000,
    channels=2,
    server_sample_format="s32le",
    hardware_volume=True,
    base_volume_mb=-1200,
)
SINK_OUTPUT = m.Output(kind="pulse", library=m.Library(name="pulseaudio-rs", version="0.3.2"))
SINK_LEVEL = m.Control(
    element="sink",
    index=0,
    part="level",
    channels=(m.Channel("front-left", -1800, True), m.Channel("front-right", -1800, True)),
)
SINK_BODIES: list[object] = [
    dataclasses.replace(
        STARTED, output=SINK_OUTPUT, device=SINK, controls=(SINK_LEVEL,), found=(SINK_LEVEL,)
    ),
    m.Checked(
        output=SINK_OUTPUT,
        device=dataclasses.replace(SINK, description=None),
        controls=(m.CheckedControl("sink", 0, "level", m.VolumeRange(-28899, 27093), ()),),
        cap_mb=-2400,
        quiet_hours=(),
        local_time="2026-11-13T14:00:00-05:00",
        quiet_now=False,
    ),
]
RESULT_BODIES += SINK_BODIES


def test_a_device_decodes_as_the_identity_whose_keys_it_has() -> None:
    for body, kind in [(STARTED, CardIdentity), (SINK_BODIES[0], m.SinkIdentity)]:
        message = Reply(role="speaker", command="playback.play", dry=False, outcome=body)
        decoded = loads(dumps(message))
        assert isinstance(decoded, Reply) and isinstance(decoded.outcome, m.Started)
        assert type(decoded.outcome.device) is kind


EVENT_BODIES: list[object] = [
    m.Ended(
        play=1,
        frames_handed=16000,
        unplayed_frames=0,
        underruns=1,
        last=MARK,
        end=AT,
        restored=True,
    ),
    m.Underrun(play=1, at_frame=8000, at=AT, detail="EPIPE"),
    m.Failed(
        play=1,
        failure=FAILURE,
        frames_handed=8000,
        unplayed_frames=None,
        underruns=0,
        last=MARK,
        end=AT,
        restored=False,
    ),
]


def test_the_kinds_names_are_registered() -> None:
    assert playback.KIND == "playback"
    assert COMMANDS["playback.play"] is m.Play
    assert COMMANDS["playback.stop"] is m.Stop
    assert {n for n in RESULTS if n.startswith("playback.")} == {
        "playback.started",
        "playback.stopped",
        "playback.checked",
        "playback.released",
    }
    assert {n for n in EVENTS if n.startswith("playback.")} == {
        "playback.ended",
        "playback.underrun",
        "playback.failed",
    }


@pytest.mark.parametrize("body", COMMAND_BODIES)
def test_commands_round_trip(body: m.Play | m.Stop) -> None:
    message = Command(role="speaker", body=body, dry=True)
    assert loads(dumps(message)) == message


@pytest.mark.parametrize("body", RESULT_BODIES)
def test_results_round_trip(body: object) -> None:
    message = Reply(role="speaker", command="playback.play", dry=False, outcome=body)
    assert loads(dumps(message)) == message
    assert decode(encode(message)) == message


@pytest.mark.parametrize("body", EVENT_BODIES)
def test_events_round_trip(body: object) -> None:
    message = Event(role="speaker", body=body)
    assert loads(dumps(message)) == message


def test_only_the_failed_event_reports_a_failure() -> None:
    assert [failure_of(body) for body in EVENT_BODIES] == [None, None, FAILURE]


def test_a_part_other_than_level_or_held_is_refused() -> None:
    message = Reply(role="speaker", command="playback.play", dry=False, outcome=STARTED)
    text = dumps(message).replace('"part":"held"', '"part":"other"')
    with pytest.raises(ValueError, match="part"):
        loads(text)
