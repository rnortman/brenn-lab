"""What a role that opens a sound card says about the card and its stream."""

from dataclasses import dataclass

SAMPLE_BYTES = {"s16_le": 2, "s32_le": 4}
"""The sample formats a role speaks, and the bytes one sample of each takes."""


@dataclass(frozen=True)
class CardIdentity:
    """The card as ALSA and USB name it. Not the device's firmware or routing."""

    selector: str
    card_index: int
    card_name: str
    card_long_name: str
    pcm: str
    usb_id: str | None


@dataclass(frozen=True)
class Granted:
    """What the device granted, read back from the open stream."""

    rate_hz: int
    channels: int
    sample_format: str
    period_frames: int
    buffer_frames: int
