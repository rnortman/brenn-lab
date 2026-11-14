"""A talker on `talker` and the robot's own sound on `speaker`, overlapping,
while `mic` records: play `talker.wav`, one second later play `own.wav`, and
stop the capture a second after the later of the two has ended. Both sources
are marked with `lab mark`, one member each, so that each play can be found
in the capture.

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
"""The time from the talker's play to the speaker's."""


def cap_of(role: RoleHandle) -> int:
    """The playback role's cap, from its `check`."""
    checked = role.send(Check())
    assert isinstance(checked, playback.Checked)
    return checked.cap_mb


def run(lab: Lab) -> None:
    mic, speaker, talker = lab.role("mic"), lab.role("speaker"), lab.role("talker")
    talker_mb, speaker_mb = cap_of(talker), cap_of(speaker)
    mic.send(capture.Start())
    talking = talker.send(playback.Play(source="talker.wav", volume_mb=talker_mb))
    # TODO(scenario-play-offset-varies-between-runs): the two plays sound
    # about 0.8 or about 1.0 s apart in the capture; what moves is not known.
    lab.wait(OFFSET_S)
    own = speaker.send(playback.Play(source="own.wav", volume_mb=speaker_mb))
    talker_left = talking.source.frames / talking.source.rate_hz - OFFSET_S
    own_left = own.source.frames / own.source.rate_hz
    lab.wait(max(talker_left, own_left) + 1.0)
    mic.send(capture.Stop())
