"""The playback kind's device parameters, `roles.<name>.device` in the lab configuration.

The cap and quiet hours have no other source: no command, script, environment
variable or flag supplies, raises or suspends them.
"""

import posixpath
from dataclasses import dataclass

from brenn_lab.config_table import Table
from brenn_lab.playback.limits import QuietMinutes, parse_clock
from brenn_lab.playback.output import OUTPUTS, ControlName

SINK_LEVEL = ControlName("sink", 0)
"""The one control of a sound server's sink: its volume and mute."""


@dataclass(frozen=True)
class HeldControl:
    """A control the role holds at `volume_mb` on every playback channel while
    it plays; `volume_mb` is None for a control with a switch and no volume.
    """

    control: ControlName
    volume_mb: int | None


@dataclass(frozen=True)
class PlaybackDevice:
    """Which output to play through and the selector of its device (`card`
    for `alsa`, `sink` for `pulse`), where sources are read from, the cap and
    quiet hours, the control a play sets and the controls held.
    """

    output: str
    selector: str
    source_dir: str
    cap_mb: int
    quiet_hours: tuple[QuietMinutes, ...]
    level: ControlName
    held: tuple[HeldControl, ...]


def read_device(table: Table) -> PlaybackDevice | None:
    """The parameters in `table`, or None with every problem recorded in it.
    `alsa` reads `card`, `level` and `held`; `pulse` reads `sink`, its level
    is the sink's control and it holds none. A key of the other kind is
    `unknown_key`; with no known `output`, no key is judged unknown.
    """
    before = len(table.problems)
    output = table.string("output")
    if output is not None and output not in OUTPUTS:
        table.problem("output", "bad_value", f"known outputs: {list(OUTPUTS)}")
    selector = None
    level: ControlName | None = None
    held: tuple[HeldControl, ...] | None = None
    if output == "alsa":
        selector = _selector(table, "card")
        level_table = table.table("level")
        level = None if level_table is None else _control(level_table)
        if level_table is not None:
            level_table.done()
        held = _held(table)
        if level is not None and held is not None:
            names = [level, *(h.control for h in held)]
            if len(set(names)) != len(names):
                table.problem("held", "bad_value", "a control is named more than once")
    elif output == "pulse":
        selector = _selector(table, "sink")
        level, held = SINK_LEVEL, ()
    source_dir = table.string("source_dir")
    if source_dir is not None and not posixpath.isabs(source_dir):
        table.problem("source_dir", "bad_value", "must be an absolute path")
    cap_mb = table.integer("cap_mb")
    quiet_hours = _quiet_hours(table)
    if output in OUTPUTS:
        # Which other keys are unknown depends on the kind.
        table.done()
    if len(table.problems) != before:
        return None
    assert output is not None and selector is not None and source_dir is not None
    assert cap_mb is not None and quiet_hours is not None
    assert level is not None and held is not None
    return PlaybackDevice(output, selector, source_dir, cap_mb, quiet_hours, level, held)


def _selector(table: Table, key: str) -> str | None:
    value = table.string(key)
    if value == "":
        table.problem(key, "bad_value", "must not be empty")
        return None
    return value


def _quiet_hours(table: Table) -> tuple[QuietMinutes, ...] | None:
    value = table.raw("quiet_hours")
    if value is None:
        return None
    if not isinstance(value, list):
        table.wrong_type("quiet_hours", 'a list of ["HH:MM", "HH:MM"] pairs')
        return None
    spans = []
    for i, pair in enumerate(value):
        key = f"quiet_hours[{i}]"
        if not (isinstance(pair, list) and len(pair) == 2):
            table.wrong_type(key, 'a ["HH:MM", "HH:MM"] pair')
            continue
        start, end = (parse_clock(t) if isinstance(t, str) else None for t in pair)
        if start is None or end is None:
            table.problem(key, "bad_value", 'each end is a string "HH:MM", 00:00 to 23:59')
        elif start == end:
            table.problem(key, "bad_value", "a span's start and end must differ")
        else:
            spans.append(QuietMinutes(start, end))
    if len(spans) != len(value):
        return None
    return tuple(spans)


def _control(table: Table) -> ControlName | None:
    element = table.string("element")
    if element == "":
        table.problem("element", "bad_value", "must not be empty")
    index = table.integer("index")
    if index is not None and index < 0:
        table.problem("index", "bad_value", "must not be negative")
    if not element or index is None or index < 0:
        return None
    return ControlName(element, index)


def _held(table: Table) -> tuple[HeldControl, ...] | None:
    tables = table.tables("held", required=False)
    if tables is None:
        return () if "held" not in table.value else None
    held = []
    for entry in tables:
        control = _control(entry)
        volume_mb = entry.integer("volume_mb", required=False)
        entry.done()
        if control is not None:
            held.append(HeldControl(control, volume_mb))
    if len(held) != len(tables):
        return None
    return tuple(held)
