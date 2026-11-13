"""Record ten seconds from the role `mic`."""

from brenn_lab import capture
from brenn_lab.script import Lab

ROLES = {"mic": capture.KIND}


def run(lab: Lab) -> None:
    mic = lab.role("mic")
    mic.send(capture.Start())
    lab.wait(10.0)
    mic.send(capture.Stop())
