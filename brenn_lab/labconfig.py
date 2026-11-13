"""The lab configuration: which machines there are, where each has the
programs its roles call, and which roles there are, of what kind, on which
machine, with which device parameters.

Schema 1, TOML. The loader returns every problem it finds; a configuration
with any problem is not used.
"""

import re
import tomllib
from dataclasses import dataclass
from pathlib import PurePosixPath

from brenn_lab.config_table import ConfigProblem, Table
from brenn_lab.data import JsonObject
from brenn_lab.roles import KINDS

SCHEMA = 1

_ROLE_NAME = re.compile(r"[a-z][a-z0-9_]*")
_ROLE_NAME_MAX_BYTES = 32


@dataclass(frozen=True)
class Installed:
    """A program installed at this absolute path on the machine."""

    path: str


@dataclass(frozen=True)
class Pinned:
    """The program this harness's build pins, as carried by whichever harness
    process hosts the machine's roles.
    """


type Program = Installed | Pinned


@dataclass(frozen=True)
class Machine:
    """A machine, and where it has each program its roles call: one source per
    program, never a second to fall back to.
    """

    name: str
    programs: dict[str, Program]


@dataclass(frozen=True)
class RoleConfig:
    """One role. `device` is the kind's parameters as configured, and
    `parameters` the same as the kind read them; `stated` is what a person
    states about the device, null when nothing is stated.
    """

    name: str
    kind: str
    machine: str
    device: JsonObject
    parameters: object
    stated: JsonObject | None


@dataclass(frozen=True)
class LabConfig:
    machines: dict[str, Machine]
    roles: dict[str, RoleConfig]


def parse(data: bytes) -> LabConfig | list[ConfigProblem]:
    """The configuration in `data`, or every problem found in it."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        return [ConfigProblem("", "not_toml", f"not UTF-8: {e}")]
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        return [ConfigProblem("", "not_toml", str(e))]

    problems: list[ConfigProblem] = []
    top = Table(document, "", problems)
    schema = top.integer("schema")
    if schema is not None and schema != SCHEMA:
        top.problem("schema", "unsupported_schema", f"this harness reads schema {SCHEMA}")
        return problems

    machines: dict[str, Machine] = {}
    machines_table = top.table("machines")
    if machines_table is not None:
        for name in machines_table.value:
            machine = _machine(machines_table, name)
            if machine is not None:
                machines[name] = machine
        machines_table.done()

    roles: dict[str, RoleConfig] = {}
    roles_table = top.table("roles")
    if roles_table is not None:
        for name in roles_table.value:
            role = _role(roles_table, name, machines_table, machines)
            if role is not None:
                roles[name] = role
        roles_table.done()
    top.done()

    if problems:
        return problems
    return LabConfig(machines, roles)


def _machine(machines: Table, name: str) -> Machine | None:
    table = machines.table(name)
    if table is None:
        return None
    before = len(table.problems)
    programs: dict[str, Program] = {}
    known = frozenset().union(*(kind.programs for kind in KINDS.values()))
    programs_table = table.table("programs", required=False)
    if programs_table is not None:
        for program in sorted(known & programs_table.value.keys()):
            source = _program(programs_table, program)
            if source is not None:
                programs[program] = source
        programs_table.done()
    table.done()
    if len(table.problems) != before:
        return None
    return Machine(name, programs)


def _program(programs: Table, name: str) -> Program | None:
    """`name` in a machine's programs: an absolute path, or `{ pinned = true }`."""
    value = programs.raw(name)
    if isinstance(value, str):
        if not PurePosixPath(value).is_absolute():
            programs.problem(name, "relative_path", "must be an absolute path")
            return None
        return Installed(value)
    table = programs.table(name) if isinstance(value, dict) else None
    if table is not None:
        pinned = table.boolean("pinned")
        table.done()
        if pinned is None:
            return None
        if not pinned:
            table.problem(
                "pinned", "bad_value", "must be true; to use an installed program, name its path"
            )
            return None
        return Pinned()
    programs.wrong_type(name, "an absolute path or { pinned = true }")
    return None


def _role(
    roles: Table, name: str, machines_table: Table | None, machines: dict[str, Machine]
) -> RoleConfig | None:
    table = roles.table(name)
    if table is None:
        return None
    before = len(table.problems)
    if not _ROLE_NAME.fullmatch(name) or len(name.encode()) > _ROLE_NAME_MAX_BYTES:
        rule = f"matches {_ROLE_NAME.pattern} and is at most {_ROLE_NAME_MAX_BYTES} bytes"
        roles.problem(name, "bad_name", f"a role name {rule}")

    kind_name = table.string("kind")
    kind = None
    if kind_name is not None:
        kind = KINDS.get(kind_name)
        if kind is None:
            table.problem("kind", "unknown_kind", f"known kinds: {sorted(KINDS)}")

    machine_name = table.string("machine")
    if machine_name is not None and machines_table is not None:
        if machine_name not in machines_table.value:
            table.problem("machine", "unknown_machine", f"{machine_name!r} is not in machines")
        elif kind is not None and machine_name in machines:
            for program in sorted(kind.programs - machines[machine_name].programs.keys()):
                table.problem(
                    "machine",
                    "missing_program",
                    f"a {kind.name} role needs machines.{machine_name}.programs.{program}",
                )

    device_table = table.table("device")
    device = None if device_table is None else device_table.as_json()
    parameters = None
    if device_table is not None and device is not None and kind is not None:
        parameters = kind.read_device(device_table)
    stated = table.json_table("stated", required=False)
    table.done()

    if len(table.problems) != before:
        return None
    assert kind_name is not None and machine_name is not None
    assert device is not None and parameters is not None
    return RoleConfig(name, kind_name, machine_name, device, parameters, stated)
