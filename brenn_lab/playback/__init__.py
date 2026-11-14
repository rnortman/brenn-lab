"""The playback kind: a role that plays a source file through one output, below a cap."""

from brenn_lab.playback.messages import Checked, Play, Stop

KIND = "playback"

__all__ = ["KIND", "Checked", "Play", "Stop"]
