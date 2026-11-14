"""A talker on `talker` and the robot's own sound on `speaker`, overlapping,
while `mic` records: play `talker.wav`, one second later play `own.wav`, and
stop the capture a second after the later of the two has ended. Both sources
are marked with `lab mark`, one member each, so that each play can be found
in the capture.
"""

from brenn_lab import capture, playback
from brenn_lab.script import Lab

ROLES = {"mic": capture.KIND, "speaker": playback.KIND, "talker": playback.KIND}

OFFSET_S = 1.0
"""The time from the talker's play to the speaker's."""
TALKER_MB = -3000
SPEAKER_MB = -3000


def run(lab: Lab) -> None:
    mic, speaker, talker = lab.role("mic"), lab.role("speaker"), lab.role("talker")
    mic.send(capture.Start())
    talking = talker.send(playback.Play(source="talker.wav", volume_mb=TALKER_MB))
    lab.wait(OFFSET_S)
    own = speaker.send(playback.Play(source="own.wav", volume_mb=SPEAKER_MB))
    talker_left = talking.source.frames / talking.source.rate_hz - OFFSET_S
    own_left = own.source.frames / own.source.rate_hz
    lab.wait(max(talker_left, own_left) + 1.0)
    mic.send(capture.Stop())
