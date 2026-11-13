"""Play `clip.wav` through the role `speaker` while the role `mic` records."""

from brenn_lab import capture, playback
from brenn_lab.script import Lab

ROLES = {"mic": capture.KIND, "speaker": playback.KIND}


def run(lab: Lab) -> None:
    mic, speaker = lab.role("mic"), lab.role("speaker")
    mic.send(capture.Start())
    played = speaker.send(playback.Play(source="clip.wav", volume_mb=-3000))
    lab.wait(played.source.frames / played.source.rate_hz + 1.0)
    mic.send(capture.Stop())
