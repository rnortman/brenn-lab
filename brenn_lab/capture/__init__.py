"""The capture kind: a role that records every channel of one device to a file."""

from brenn_lab.capture.messages import Start, Stop

KIND = "capture"

__all__ = ["KIND", "Start", "Stop"]
