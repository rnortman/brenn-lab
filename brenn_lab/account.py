"""What a kind tells a run about one of its roles: its entries in the
manifest's role section, its records in the results, and the repositories
whose code it ran.
"""

from collections.abc import Callable
from typing import Protocol

from brenn_lab.data import Event, JsonObject, Location, Reply

type Pointer = Callable[[Location], JsonObject]
"""The run's name for a file a role wrote."""


class Account(Protocol):
    """One role's account in a run, fed every reply and every event of the
    role in the order the run saw them. `by` is who sent the command a reply
    answers: `script` or `runner`.
    """

    def reply(self, reply: Reply, by: str) -> bool:
        """Take in a reply; whether the manifest entries changed."""
        ...

    def event(self, event: Event) -> bool:
        """Take in an event; whether the manifest entries changed."""
        ...

    def entries(self) -> list[JsonObject]:
        """The role's entries in its manifest section."""
        ...

    def records(self) -> list[JsonObject]:
        """The role's records in the results, in order."""
        ...

    def repositories(self) -> list[JsonObject]:
        """Every repository whose code ran for the role, as a manifest
        `repositories` entry with its `from`, in the order learned. The same
        repository may be listed more than once; the run keeps the first.
        """
        ...
