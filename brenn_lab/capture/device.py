"""The capture kind's device parameters, `roles.<name>.device` in the lab configuration.

Only their shape is checked here. What the recorder's command line or the
device refuses of them is the recorder's to say.
"""

from dataclasses import dataclass

from brenn_lab.config_table import Table


@dataclass(frozen=True)
class CaptureDevice:
    """Which card to record, and the rate, sample format and channel names to demand."""

    card: str
    rate_hz: int
    sample_format: str
    layout: tuple[str, ...]


def read_device(table: Table) -> CaptureDevice | None:
    """The parameters in `table`, or None with every problem recorded in it."""
    before = len(table.problems)
    card = table.string("card")
    if card == "":
        table.problem("card", "bad_value", "must not be empty")
    rate_hz = table.integer("rate_hz")
    if rate_hz is not None and rate_hz <= 0:
        table.problem("rate_hz", "bad_value", "must be positive")
    sample_format = table.string("sample_format")
    layout = table.string_list("layout")
    if layout is not None:
        if not layout:
            table.problem("layout", "bad_value", "must name at least one channel")
        if any(name == "" for name in layout):
            table.problem("layout", "bad_value", "a channel name is empty")
        if len(set(layout)) != len(layout):
            table.problem("layout", "bad_value", "channel names must be distinct")
    table.done()
    if len(table.problems) != before:
        return None
    assert card is not None and rate_hz is not None
    assert sample_format is not None and layout is not None
    return CaptureDevice(card, rate_hz, sample_format, tuple(layout))
