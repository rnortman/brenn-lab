"""A talker on `talker` and the robot's own sound on `speaker`, overlapping,
while `mic` records: play `talker.wav` and, a stated second after its start,
`own.wav`, and stop the capture a second after the later of the two has
ended. Both sources are marked with `lab mark`, one member each, so that each
play can be found in the capture.

Each play's start is stated on its own role's clock, from a reading that role
gave: the two roles' readings are asked one after the other and taken as one
moment, so the script is the same whether the roles share a machine or not.
On one machine the gap between the two plays' first marks is the stated
offset plus the time between the two readings; on two it is the stated offset
to within that time. Where each play lies in the capture is alignment's to
say.

Each source plays at its role's cap, read from the role's `check` before the
capture starts: the level is the lab configuration's, not the script's. A
cap found for an output alone is found in a quiet room; the role whose play
starts under the other's sound needs its cap found with the other playing.
"""

from brenn_lab import capture, playback
from brenn_lab.data import Check
from brenn_lab.script import Lab, RoleHandle

ROLES = {"mic": capture.KIND, "speaker": playback.KIND, "talker": playback.KIND}

OFFSET_S = 1.0
"""From the talker's start to the speaker's."""

LEAD_S = 2.0
"""From the clock readings to the talker's start: room for both plays to be readied."""


def cap_of(role: RoleHandle) -> int:
    """The playback role's cap, from its `check`."""
    checked = role.send(Check())
    assert isinstance(checked, playback.Checked)
    return checked.cap_mb


def run(lab: Lab) -> None:
    mic, speaker, talker = lab.role("mic"), lab.role("speaker"), lab.role("talker")
    talker_mb, speaker_mb = cap_of(talker), cap_of(speaker)
    mic.send(capture.Start())
    talker_now = talker.send(playback.Clock()).at
    speaker_now = speaker.send(playback.Clock()).at
    talking = talker.send(
        playback.Play(source="talker.wav", volume_mb=talker_mb, start=talker_now.after(LEAD_S))
    )
    own = speaker.send(
        playback.Play(
            source="own.wav", volume_mb=speaker_mb, start=speaker_now.after(LEAD_S + OFFSET_S)
        )
    )
    talker_s = talking.source.frames / talking.source.rate_hz
    own_s = own.source.frames / own.source.rate_hz
    lab.wait(LEAD_S + max(talker_s, OFFSET_S + own_s) + 1.0)
    mic.send(capture.Stop())
