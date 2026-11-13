"""Finding and reading the transcripts the stand-in recorder replays.

A transcript is `transcripts/recorded/<name>.jsonl` or
`transcripts/authored/<name>.jsonl`: the lines a recorder printed, or lines
written from the recorder's contract. An authored transcript may hold a line
that is not JSON on purpose; the readers here skip such a line.
"""

import json
from pathlib import Path

from brenn_lab.data import MONOTONIC, REALTIME, JsonObject

DIR = Path(__file__).absolute().parent / "transcripts"
KINDS = ("recorded", "authored")


def find(name: str) -> Path | None:
    """The transcript called `name`, if there is one."""
    for kind in KINDS:
        path = DIR / kind / f"{name}.jsonl"
        if path.is_file():
            return path
    return None


def path(name: str) -> Path:
    """The transcript called `name`, which must exist."""
    found = find(name)
    assert found is not None, f"no transcript {name!r}"
    return found


def objects(transcript: Path) -> list[JsonObject]:
    """Every line of `transcript` that is JSON, each of which must be an object."""
    out = []
    for text in transcript.read_text().splitlines():
        try:
            value = json.loads(text)
        except ValueError:
            continue
        assert isinstance(value, dict), text
        out.append(value)
    return out


def line(name: str, event: str) -> JsonObject:
    """The one line of transcript `name` whose event is `event`."""
    found = [v for v in objects(path(name)) if v["event"] == event]
    assert len(found) == 1, (name, event)
    return found[0]


def layout_of(name: str) -> tuple[str, ...]:
    """The channel names transcript `name`'s capture started with."""
    layout = line(name, "capture_started")["layout"]
    assert isinstance(layout, list)
    return tuple(str(x) for x in layout)


def reading(value: JsonObject, machine: str) -> JsonObject:
    """The encoded clock reading a line's `mono_us` and `wall_us` give on `machine`."""
    return {
        "machine": machine,
        "monotonic": {"clock": MONOTONIC, "us": value["mono_us"]},
        "wall": {"clock": REALTIME, "us": value["wall_us"]},
    }
