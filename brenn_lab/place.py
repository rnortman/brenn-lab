"""Where a role hosted in a run writes."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Place:
    """A hosted role's directories in its run: what it records, and the output
    of the programs it calls; and a way to add a line to the runner's log.
    """

    captures: Path
    logs: Path
    note: Callable[[str], None]
