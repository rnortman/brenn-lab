"""What a kind tells a run about one of its roles: its entries in the
manifest's role section, its records in the results, and the repositories
whose code it ran.
"""

from collections.abc import Callable
from typing import Protocol

from brenn_lab.data import Event, Failure, JsonObject, Location, Reply

type Pointer = Callable[[Location], JsonObject]
"""The run's name for a file a role wrote."""


def end_not_reported(what: str) -> Failure:
    """The failure of a record whose end never reached the run, given once
    every role is released: nothing is running, so its end was lost.
    """
    return Failure(
        "end_not_reported",
        f"no end of the {what} reached the run; the role's failures say what was raised",
    )


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
        """The role's records in the results, in order. Called once every
        role is released, so a record with no end is given `ended` `failed`
        and the failure `end_not_reported`.
        """
        ...

    def repositories(self) -> list[JsonObject]:
        """Every repository whose code ran for the role, as a manifest
        `repositories` entry with its `from`, in the order learned. The same
        repository may be listed more than once; the run keeps the first.
        """
        ...
