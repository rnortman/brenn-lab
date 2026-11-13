"""The script API: what an experiment script is given to reach its devices.

A script is a Python file that defines `ROLES`, a mapping from each role it
uses to the kind it expects, and `run(lab)`. Its only way to a device is
`lab.role(name).send(command)`, with any command but `release`, which the
runner sends when the script ends; `lab.wait(seconds)` passes time.
"""

from collections.abc import Mapping
from typing import Protocol, cast

from brenn_lab.data import Failure, Release, Reply, Request


class RoleFailure(Exception):
    """A role answered a command with a failure. A script may catch it."""

    def __init__(self, role: str, command: str, failure: Failure):
        super().__init__(f"{role}: {command} failed: {failure.reason}: {failure.detail}")
        self.role = role
        self.command = command
        self.failure = failure


class UndeclaredRole(LookupError):
    """A script asked for a role its `ROLES` does not declare."""


class RunnerCommand(TypeError):
    """A script sent `release`, which only the runner sends, once the script
    has ended.
    """


class Session(Protocol):
    """What a `Lab` sends through: the runner, live or tracing."""

    def send(self, role: str, body: Request[object]) -> Reply: ...

    def wait(self, seconds: float) -> None: ...


class RoleHandle:
    """One role, as a script sees it."""

    def __init__(self, name: str, session: Session):
        self._name = name
        self._session = session

    @property
    def name(self) -> str:
        return self._name

    def send[R](self, body: Request[R]) -> R:
        """Send one command and return its result; a failure raises `RoleFailure`."""
        if isinstance(body, Release):
            raise RunnerCommand(f"a script cannot send release to role {self._name!r}")
        reply = self._session.send(self._name, cast(Request[object], body))
        if isinstance(reply.outcome, Failure):
            raise RoleFailure(reply.role, reply.command, reply.outcome)
        return cast(R, reply.outcome)


class Lab:
    """The lab a script runs in."""

    def __init__(self, roles: Mapping[str, str], session: Session):
        self._roles = dict(roles)
        self._session = session

    def role(self, name: str) -> RoleHandle:
        """The handle of role `name`, which the script's `ROLES` must declare."""
        if name not in self._roles:
            raise UndeclaredRole(f"role {name!r} is not in the script's ROLES")
        return RoleHandle(name, self._session)

    def wait(self, seconds: float) -> None:
        """Let `seconds` pass on the runner's monotonic clock; at once in a dry run."""
        if not seconds >= 0:
            raise ValueError(f"cannot wait {seconds!r} seconds")
        self._session.wait(seconds)
