"""Where a role hosted in a run writes."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Place:
    """A hosted role's directories in its run: its data, under its kind's
    section, and the output of the programs it calls; and a way to add a line
    to the runner's log.
    """

    data: Path
    logs: Path
    note: Callable[[str], None]
