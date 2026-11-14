"""One step of a level ramp: while `mic` records, `speaker` plays `primer.wav`
and then `marked.wav`, both at the role's cap.

The level is read from the role's `check`, so the lab configuration's
`cap_mb` is both the step's level and its limit: nothing in the run can be
louder than the step. `primer.wav` is any unmarked sound a few seconds long; it
holds the output open and sounding before the marked play, so a speaker that
fades in after a silence has done so before the marked play's start marker.
`marked.wav` is any file made by `lab mark`. Whether the step was heard is read
from the run's `alignment.json`: both markers found on the channels that
matter.
"""

from brenn_lab import capture, playback
from brenn_lab.data import Check
from brenn_lab.script import Lab

ROLES = {"mic": capture.KIND, "speaker": playback.KIND}

AMBIENT_S = 1.0
"""The capture before the first play, and after each play has ended."""


def run(lab: Lab) -> None:
    mic, speaker = lab.role("mic"), lab.role("speaker")
    checked = speaker.send(Check())
    assert isinstance(checked, playback.Checked)
    level = checked.cap_mb
    mic.send(capture.Start())
    lab.wait(AMBIENT_S)
    for source in ("primer.wav", "marked.wav"):
        played = speaker.send(playback.Play(source=source, volume_mb=level))
        lab.wait(played.source.frames / played.source.rate_hz + AMBIENT_S)
    mic.send(capture.Stop())
