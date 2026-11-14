"""The dry run and the run.

A dry run checks a script against a lab without recording anything: the
configuration, the script, every role the script declares, and a trace of
the script with every command dry. A run creates a run directory, runs the
script against the roles the runner hosts, and leaves the directory to
explain what happened.
"""

import hashlib
import signal
import sys
import threading
import time
import traceback
import types
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from brenn_lab import labconfig
from brenn_lab.account import Account
from brenn_lab.buildinfo import Revision
from brenn_lab.data import (
    COMMANDS,
    Check,
    Command,
    Event,
    Failure,
    JsonObject,
    JsonValue,
    Location,
    Release,
    Reply,
    Request,
    encode,
    encode_object,
    encode_value,
    failure_of,
    now,
)
from brenn_lab.labconfig import Installed, LabConfig
from brenn_lab.place import Place
from brenn_lab.roles import KINDS, Role
from brenn_lab.rundir import LAYOUT, RunDir
from brenn_lab.script import Lab, RoleFailure

SCHEMA = 1

UNREACHABLE = "this build has no transport"


@dataclass(frozen=True)
class Problem:
    """One thing wrong: in a dry run's report, a refusal, or a run's `failure`.

    `where` says what found it. `role`, `command` (the command whose reply
    failed) and `key` (the configuration's key at fault) are null where they
    do not apply; `data` is whatever the reason carries.
    """

    where: str
    reason: str
    detail: str
    role: str | None = None
    command: str | None = None
    key: str | None = None
    data: JsonObject = field(default_factory=dict)


def _role_problem(role: str, failure: Failure, command: str | None = None) -> Problem:
    return Problem("role", failure.reason, failure.detail, role, command, None, failure.data)


@dataclass(frozen=True)
class Refused:
    """The invocation was refused before anything ran or was created."""

    problems: list[Problem]

    def report(self) -> JsonObject:
        return {"ok": False, "problems": [encode_object(p) for p in self.problems]}


class Interrupted(BaseException):
    """The runner was told to stop. Not an `Exception`, so a script's
    `except Exception` does not swallow it.
    """


# Loading the inputs.


@dataclass(frozen=True)
class Script:
    """A loaded script: its `ROLES` and its `run`."""

    path: Path
    roles: dict[str, str]
    run: Callable[[Lab], object]


_loaded = 0


def load_script(path: Path, data: bytes) -> Script | Problem:
    """The script in `data`, read from `path`, or the problem with it."""
    global _loaded
    _loaded += 1
    name = f"_brenn_lab_script_{_loaded}"
    module = types.ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    loaded = _load(module, path, data)
    if not isinstance(loaded, Script):
        sys.modules.pop(name, None)
    return loaded


def _load(module: types.ModuleType, path: Path, data: bytes) -> Script | Problem:
    try:
        code = compile(data, str(path), "exec")
        exec(code, module.__dict__)
    except KeyboardInterrupt:
        # The operator's interrupt arrives as KeyboardInterrupt (SIGINT, and
        # SIGTERM in a dry run); it propagates rather than blaming the script.
        raise
    except BaseException as e:
        return _script_problem("not_loadable", _describe(e))
    roles = getattr(module, "ROLES", None)
    if not (
        isinstance(roles, Mapping)
        and all(isinstance(k, str) and isinstance(v, str) for k, v in roles.items())
    ):
        return _script_problem("bad_roles", "ROLES must be a mapping from role name to kind name")
    run = getattr(module, "run", None)
    if not callable(run):
        return _script_problem("bad_run", "run must be a function taking the lab")
    return Script(path, dict(roles), run)


def _script_problem(reason: str, detail: str) -> Problem:
    return Problem("script", reason, detail)


def _describe(e: BaseException) -> str:
    return f"{type(e).__name__}: {_text(e)}"


def _text(e: BaseException) -> str:
    # `str` of a script's exception runs the script's code, which may raise.
    try:
        return str(e)
    except BaseException:
        return f"<unprintable {type(e).__name__}>"


def _read(path: Path, what: str) -> bytes | Problem:
    try:
        return path.read_bytes()
    except OSError as e:
        return Problem("arguments", "unreadable", f"{what} {path}: {e}")


def _lab(data: bytes, machine: str) -> LabConfig | list[Problem]:
    config = labconfig.parse(data)
    if isinstance(config, list):
        return [Problem("lab", p.reason, p.detail, key=p.key) for p in config]
    if machine not in config.machines:
        return [
            Problem(
                "arguments",
                "unknown_machine",
                f"--machine {machine!r} is not in the configuration's machines",
            )
        ]
    return config


def _binding(config: LabConfig, machine: str, name: str, kind: str) -> Failure | None:
    """Why the runner cannot host the configured role `name` of `kind`, if it cannot."""
    role = config.roles.get(name)
    if role is None:
        return Failure("not_configured", f"role {name!r} is not in the lab configuration")
    if role.kind != kind:
        return Failure(
            "kind_mismatch", f"the script expects a {kind} role; the configuration has {role.kind}"
        )
    if role.machine != machine:
        return Failure(
            "unreachable",
            f"role {name!r} is on machine {role.machine!r}; {UNREACHABLE}",
            {"machine": role.machine},
        )
    return None


def _make_role(
    config: LabConfig,
    pinned: Mapping[str, str],
    name: str,
    sink: Callable[[Event], None],
    place: Place | None,
) -> Role:
    """The hosted role `name`, handed the path of each program its machine
    names: an installed program's configured path, or the path at which this
    process carries a pinned one.
    """
    role = config.roles[name]
    programs = {
        program: source.path if isinstance(source, Installed) else pinned[program]
        for program, source in config.machines[role.machine].programs.items()
    }
    return KINDS[role.kind].make(name, role.machine, programs, role.parameters, sink, place)


# The dry run.


class _Trace:
    """The session a dry run's trace sends through: every command dry, no time."""

    def __init__(
        self, roles: dict[str, Role], blocked: dict[str, Failure], add: Callable[[Reply], None]
    ):
        self._roles = roles
        self._blocked = blocked
        self._add = add
        self.commands = 0

    def send(self, role: str, body: Request[object]) -> Reply:
        command = Command(role=role, body=body, dry=True)
        self.commands += 1
        blocked = self._blocked.get(role)
        if blocked is not None:
            reply = Reply(role=role, command=command.name, dry=True, outcome=blocked)
        else:
            reply = self._roles[role].handle(command)
        self._add(reply)
        return reply

    def wait(self, seconds: float) -> None:
        pass


def dry_run(
    lab_path: Path, machine: str, script_path: Path, pinned: Mapping[str, str]
) -> JsonObject | Refused:
    """The dry-run report, or the refusal of the invocation. `pinned` maps
    each program the build pins to the path this process carries it at.

    Must be called on the main thread: for the length of the dry run SIGTERM
    raises `KeyboardInterrupt`, as SIGINT does, so that a probe interrupted
    either way kills its recorder before the exception leaves.
    """
    previous = signal.signal(signal.SIGTERM, signal.default_int_handler)
    try:
        return _dry_run(lab_path, machine, script_path, pinned)
    finally:
        signal.signal(signal.SIGTERM, previous)


def _no_events(event: Event) -> None:
    raise AssertionError(f"a role reported an event in a dry run: {event}")


def _dry_run(
    lab_path: Path, machine: str, script_path: Path, pinned: Mapping[str, str]
) -> JsonObject | Refused:
    lab_data = _read(lab_path, "lab configuration")
    script_data = _read(script_path, "script")
    unread = [d for d in (lab_data, script_data) if isinstance(d, Problem)]
    if unread:
        return Refused(unread)
    assert isinstance(lab_data, bytes) and isinstance(script_data, bytes)

    problems: list[Problem] = []

    def report(roles: JsonObject, trace: JsonObject | None) -> JsonObject:
        return {
            "schema": SCHEMA,
            "ok": not problems,
            "problems": [encode_object(p) for p in problems],
            "roles": roles,
            "trace": trace,
        }

    config = _lab(lab_data, machine)
    if isinstance(config, list):
        if any(p.where == "arguments" for p in config):
            return Refused(config)
        problems.extend(config)
        return report({}, None)

    script = load_script(script_path, script_data)
    if not isinstance(script, Script):
        problems.append(script)
        return report({}, None)

    roles: dict[str, Role] = {}
    blocked: dict[str, Failure] = {}
    known: set[tuple[str, str]] = set()
    report_roles: JsonObject = {}
    for name, kind in script.roles.items():
        configured = config.roles.get(name)
        report_roles[name] = {
            "kind": kind,
            "machine": None if configured is None else configured.machine,
            "check": None,
        }
        failure = _binding(config, machine, name, kind)
        if failure is not None:
            blocked[name] = failure
            known.add((name, failure.reason))
            problems.append(_role_problem(name, failure))
        else:
            # A role with no place to write sends only dry commands and probes,
            # and neither reports an event.
            roles[name] = _make_role(config, pinned, name, _no_events, None)

    for name, role in roles.items():
        reply = role.handle(Command(role=name, body=Check(), dry=True))
        entry = report_roles[name]
        assert isinstance(entry, dict)
        entry["check"] = encode(reply)
        if isinstance(reply.outcome, Failure):
            known.add((name, reply.outcome.reason))
            problems.append(_role_problem(name, reply.outcome, reply.command))

    def add(reply: Reply) -> None:
        if isinstance(reply.outcome, Failure) and (reply.role, reply.outcome.reason) not in known:
            problems.append(_role_problem(reply.role, reply.outcome, reply.command))

    trace = _Trace(roles, blocked, add)
    trace_report: JsonObject = {"complete": True, "commands": 0, "stopped_at": None}
    try:
        script.run(Lab(script.roles, trace))
    except KeyboardInterrupt:
        # SIGINT and SIGTERM arrive as this, so the trace cannot tell a
        # script's own from an interrupt, and takes it as the interrupt.
        raise
    except RoleFailure as e:
        trace_report.update(complete=False, stopped_at=_stopped_at(e, script.path))
    except BaseException as e:
        trace_report.update(complete=False, stopped_at=_stopped_at(e, script.path))
        problems.append(Problem("trace", "script_error", _describe(e)))
    trace_report["commands"] = trace.commands

    for name, role in roles.items():
        reply = role.handle(Command(role=name, body=Release(), dry=True))
        outcome = reply.outcome
        if isinstance(outcome, Failure):
            add(reply)
            continue
        running = KINDS[script.roles[name]].left_running(outcome)
        if running is not None:
            problems.append(
                Problem(
                    "role", "left_running", f"the script ends with {running} running", role=name
                )
            )

    return report(report_roles, trace_report)


def _stopped_at(e: BaseException, path: Path) -> str:
    line = None
    for frame in traceback.extract_tb(e.__traceback__):
        if frame.filename == str(path):
            line = frame.lineno
    where = f"{path.name}:{line}" if line is not None else path.name
    return f"{_describe(e)} at {where}"


# The run.


class _Ledger:
    """Everything a run has seen, in the order it saw it: the journal, the
    manifest's device facts, and what the results will say. What a role's
    replies and events mean for the manifest and the results is its kind's
    account's to say; the ledger reads only what the seam states for every
    kind.
    """

    def __init__(self, rundir: RunDir, machine: str, manifest: JsonObject, roles: dict[str, str]):
        self._rundir = rundir
        self._machine = machine
        self._manifest = manifest
        self._lock = threading.Lock()
        self._seq = 0
        self._kinds = dict(roles)
        self.commands = dict.fromkeys(roles, 0)
        self.failures: dict[str, list[JsonValue]] = {r: [] for r in roles}
        self.accounts: dict[str, Account] = {
            r: KINDS[kind].account(r, self.pointer) for r, kind in roles.items()
        }
        self.first: Problem | None = None

    def _journal(self, message: Command | Reply | Event, by: str | None) -> None:
        self._seq += 1
        line: JsonObject = {"seq": self._seq, "at": encode_value(now(self._machine))}
        if by is not None:
            line["by"] = by
        line.update(encode(message))
        self._rundir.journal(line)

    def _fail(self, problem: Problem) -> None:
        if self.first is None:
            self.first = problem

    def _role_failure(self, role: str, failure: Failure, by: str, name: str) -> None:
        """A failure of `role`, carried by the reply to command `name` (`by`
        "command") or by event `name` (`by` "event").
        """
        self.failures[role].append({by: name, **encode_object(failure)})
        self._fail(_role_problem(role, failure, name if by == "command" else None))

    def command(self, command: Command, by: str) -> None:
        with self._lock:
            self.commands[command.role] += 1
            self._journal(command, by)

    def reply(self, reply: Reply, by: str) -> None:
        with self._lock:
            self._journal(reply, by)
            outcome = reply.outcome
            if isinstance(outcome, Failure):
                self._role_failure(reply.role, outcome, "command", reply.command)
            elif COMMANDS[reply.command] is Release:
                running = KINDS[self._kinds[reply.role]].left_running(outcome)
                if running is not None:
                    self._fail(
                        Problem(
                            "runner",
                            "left_running",
                            f"{running} was running when the script ended",
                            role=reply.role,
                        )
                    )
            if self.accounts[reply.role].reply(reply, by):
                self._entries_changed(reply.role)

    def event(self, event: Event) -> None:
        with self._lock:
            self._journal(event, None)
            failure = failure_of(event.body)
            if failure is not None:
                self._role_failure(event.role, failure, "event", event.name)
            if self.accounts[event.role].event(event):
                self._entries_changed(event.role)

    def script_error(self, e: BaseException) -> bool:
        """Record what left the script's `run`; whether it is the run's first
        failure.
        """
        with self._lock:
            first = self.first is None
            self._fail(Problem("script", "script_error", _describe(e)))
            return first

    def role_raised(self, command: Command, e: BaseException) -> None:
        """Record that a role raised `e` instead of replying to `command`,
        then note its traceback. The failure is recorded first, so a note
        that cannot be written leaves it recorded.
        """
        with self._lock:
            failure = Failure("role_raised", _describe(e))
            self._role_failure(command.role, failure, "command", command.name)
        self._rundir.note(
            f"role {command.role} raised on {command.name}:\n"
            + "".join(traceback.format_exception(e))
        )

    def _entries_changed(self, role: str) -> None:
        """Write the manifest with the role's entries and the repositories
        its account has learned that the manifest names in no entry yet.
        """
        account = self.accounts[role]
        roles = self._manifest["roles"]
        assert isinstance(roles, dict)
        section = roles[role]
        assert isinstance(section, dict)
        section[KINDS[self._kinds[role]].section] = list[JsonValue](account.entries())
        repositories = self._manifest["repositories"]
        assert isinstance(repositories, list)
        for learned in account.repositories():
            named = {k: v for k, v in learned.items() if k != "from"}
            if not any(
                isinstance(r, dict) and all(r.get(k) == v for k, v in named.items())
                for r in repositories
            ):
                repositories.append(learned)
        self._rundir.write_manifest(self._manifest)

    def pointer(self, location: Location) -> JsonObject:
        """Where a role wrote a file, as the run names it. A file inside this
        run on the runner's machine is named relative to the run; any other
        is named by its machine and absolute path, not brought into the run.
        """
        if location.machine == self._machine:
            inside = self._rundir.in_run(Path(location.path))
            if inside is not None:
                return {"machine": location.machine, "path": inside, "in_run": True}
        return {"machine": location.machine, "path": location.path, "in_run": False}


class _Stop:
    """SIGINT and SIGTERM. While armed, a signal ends the script by raising
    `Interrupted`, though never inside a role's handling of a command: one
    that arrives there is raised once the command's reply, or the role's
    raise, is recorded.
    """

    def __init__(self) -> None:
        self.requested = False
        self.armed = False
        self.in_call = False

    def __call__(self, signum: int, frame: Any) -> None:
        self.requested = True
        if self.armed and not self.in_call:
            self._raise()

    def check(self) -> None:
        if self.requested and self.armed:
            self._raise()

    def _raise(self) -> None:
        # Once: a second signal while the first is being handled is recorded,
        # not raised through the handling.
        self.armed = False
        raise Interrupted


class _Live:
    """The session a run's script sends through."""

    def __init__(
        self,
        ledger: _Ledger,
        hosted: dict[str, Role],
        unreachable: dict[str, Failure],
        stop: _Stop,
    ):
        self._ledger = ledger
        self._hosted = hosted
        self._unreachable = unreachable
        self._stop = stop
        self.used: list[str] = []

    def send(self, role: str, body: Request[object]) -> Reply:
        self._stop.check()
        self._stop.in_call = True
        try:
            # Used from the moment it is sent: whatever then fails, a role
            # that may have started something is released.
            if role not in self.used:
                self.used.append(role)
            reply = self.exchange(Command(role=role, body=body), "script")
        finally:
            self._stop.in_call = False
            # A stop that arrived during the command is raised whether the
            # command returned or raised; raised, its error is the context.
            self._stop.check()
        return reply

    def exchange(self, command: Command, by: str) -> Reply:
        self._ledger.command(command, by)
        failure = self._unreachable.get(command.role)
        if failure is not None:
            reply = Reply(role=command.role, command=command.name, dry=False, outcome=failure)
        else:
            try:
                reply = self._hosted[command.role].handle(command)
            except BaseException as e:
                # Whatever the script then does with it, the run has failed.
                self._ledger.role_raised(command, e)
                raise
        self._ledger.reply(reply, by)
        return reply

    def release(self, role: str) -> list[Exception]:
        """Send the runner's `release` to a hosted role, and return what was
        raised on the way. The role is sent its `release` whether or not the
        command's journal line could be written.
        """
        errors: list[Exception] = []
        command = Command(role=role, body=Release())
        try:
            self._ledger.command(command, "runner")
        except Exception as e:
            errors.append(e)
        try:
            reply = self._hosted[role].handle(command)
        except Exception as e:
            errors.append(e)
            return errors
        try:
            self._ledger.reply(reply, "runner")
        except Exception as e:
            errors.append(e)
        return errors

    def wait(self, seconds: float) -> None:
        time.sleep(seconds)


def run(
    lab_path: Path,
    machine: str,
    script_path: Path,
    script_given_as: str,
    out: Path,
    notes_path: Path | None,
    harness: Revision,
    workspace: Path | None,
    pinned: Mapping[str, str],
) -> JsonObject | Refused:
    """Run the script and return the pointer to its results, or refuse. A
    relative `out` is taken from the current directory. `pinned` maps each
    program the build pins to the path this process carries it at.

    Must be called on the main thread: it takes SIGINT and SIGTERM for the
    length of the run.
    """
    out = out.absolute()
    reads = [(lab_path, "lab configuration"), (script_path, "script")]
    if notes_path is not None:
        reads.append((notes_path, "notes file"))
    data = [_read(p, what) for p, what in reads]
    unread = [d for d in data if isinstance(d, Problem)]
    if unread:
        return Refused(unread)
    lab_data, script_data = data[0], data[1]
    assert isinstance(lab_data, bytes) and isinstance(script_data, bytes)
    notes: tuple[str, bytes] | None = None
    if notes_path is not None:
        assert isinstance(data[2], bytes)
        notes = (notes_path.name, data[2])

    config = _lab(lab_data, machine)
    if isinstance(config, list):
        return Refused(config)
    script = load_script(script_path, script_data)
    if not isinstance(script, Script):
        return Refused([script])
    problems: list[Problem] = []
    unreachable: dict[str, Failure] = {}
    for name, kind in script.roles.items():
        failure = _binding(config, machine, name, kind)
        if failure is None:
            continue
        if failure.reason == "unreachable":
            unreachable[name] = failure
        else:
            problems.append(_role_problem(name, failure))
    problems.extend(_out_problems(out, workspace))
    if problems:
        return Refused(problems)

    rundir = RunDir.create(out)
    try:
        return _run(
            rundir,
            config,
            machine,
            script,
            script_given_as,
            (lab_path.name, lab_data),
            (script_path.name, script_data),
            notes,
            harness,
            unreachable,
            pinned,
        )
    finally:
        rundir.close()


def _out_problems(out: Path, workspace: Path | None) -> list[Problem]:
    def problem(reason: str, detail: str) -> Problem:
        return Problem("out", reason, detail)

    if out.exists() or out.is_symlink():
        return [problem("out_exists", f"{out} exists; a run never writes into another")]
    if not out.parent.is_dir():
        return [problem("out_parent_missing", f"{out.parent} is not a directory")]
    # TODO(run-refused-inside-workspace)
    if workspace is not None:
        target = out.parent.resolve() / out.name
        if target.is_relative_to(workspace.resolve()):
            return [
                problem(
                    "out_in_workspace",
                    f"{out} is inside the workspace {workspace}; recordings never enter "
                    "the repository",
                )
            ]
    return []


def _run(
    rundir: RunDir,
    config: LabConfig,
    machine: str,
    script: Script,
    given_as: str,
    lab: tuple[str, bytes],
    source: tuple[str, bytes],
    notes: tuple[str, bytes] | None,
    harness: Revision,
    unreachable: dict[str, Failure],
    pinned: Mapping[str, str],
) -> JsonObject:
    run_id = str(uuid.uuid4())
    started = now(machine)
    script_file = rundir.keep("script", source[0], source[1])
    lab_file = rundir.keep("lab", lab[0], lab[1])
    notes_file = None if notes is None else rundir.keep("notes", notes[0], notes[1])

    manifest_roles: JsonObject = {}
    for name, kind in script.roles.items():
        role = config.roles[name]
        manifest_roles[name] = {
            "kind": kind,
            "machine": role.machine,
            "configured": role.device,
            "stated_by_configuration": role.stated,
            "sample_clock": role.sample_clock,
            KINDS[kind].section: [],
        }
    manifest: JsonObject = {
        "layout": LAYOUT,
        "run_id": run_id,
        "created": encode_value(started),
        "runner": {"machine": machine},
        "harness": harness.encode(),
        "script": {
            "file": script_file,
            "sha256": hashlib.sha256(source[1]).hexdigest(),
            "given_as": given_as,
            "roles": dict(script.roles),
        },
        "lab": {"file": lab_file, "sha256": hashlib.sha256(lab[1]).hexdigest()},
        "notes": None if notes_file is None else {"file": notes_file},
        "repositories": [{**harness.encode(), "from": "the harness's build stamp"}],
        "roles": manifest_roles,
    }
    rundir.write_manifest(manifest)
    rundir.open_journal()

    ledger = _Ledger(rundir, machine, manifest, script.roles)
    hosted: dict[str, Role] = {}
    for name, kind in script.roles.items():
        if name not in unreachable:
            data = rundir.data(KINDS[kind].section, name)
            place = Place(data=data, logs=rundir.logs(name), note=rundir.note)
            hosted[name] = _make_role(config, pinned, name, ledger.event, place)

    stop = _Stop()
    session = _Live(ledger, hosted, unreachable, stop)
    previous = {s: signal.signal(s, stop) for s in (signal.SIGINT, signal.SIGTERM)}
    returned = False
    note: str | None = None
    errors: list[Exception] = []
    try:
        try:
            try:
                stop.armed = True
                script.run(Lab(script.roles, session))
                returned = True
            finally:
                # Disarmed before anything below runs: a signal from here on
                # is recorded, not raised. One that lands before this line
                # raises from here and is caught below like any other stop.
                stop.armed = False
        except BaseException as e:
            # Whatever leaves the script, the roles are released below, even
            # when recording what left it fails.
            try:
                if stop.requested and isinstance(e, Interrupted):
                    note = "the runner was told to stop"
                elif not ledger.script_error(e) and isinstance(e, RoleFailure):
                    note = f"the script ended on a role's failure: {_text(e)}"
                else:
                    note = "the script raised:\n" + "".join(traceback.format_exception(e))
            except Exception as failed:
                errors.append(failed)
        # Every role is released, whatever one release raises and whatever the
        # runner cannot write; what was raised is raised once all are handled.
        for name in session.used:
            if name in hosted:
                errors.extend(session.release(name))
        if note is not None:
            try:
                rundir.note(note)
            except Exception as e:
                errors.append(e)
    finally:
        for s, handler in previous.items():
            signal.signal(s, handler)
    if errors:
        raise ExceptionGroup("the run could not be finished", errors)

    if stop.requested:
        failure: Problem | None = Problem("runner", "interrupted", "the runner was told to stop")
        outcome = "interrupted"
    else:
        failure = ledger.first
        outcome = "completed" if returned and failure is None else "failed"
    completed = outcome == "completed"

    sections: JsonObject = {}
    for kind_name, of_kind in KINDS.items():
        records = [
            record
            for name in sorted(script.roles)
            if script.roles[name] == kind_name
            for record in ledger.accounts[name].records()
        ]
        sections[of_kind.totals_key] = of_kind.totals(records)
        sections[of_kind.section] = list[JsonValue](records)
    results: JsonObject = {
        "schema": SCHEMA,
        "run_id": run_id,
        "outcome": outcome,
        "completed": completed,
        "failure": None if failure is None else encode_object(failure),
        "started": encode_value(started),
        "ended": encode_value(now(machine)),
        **sections,
        "roles": {
            name: {
                "kind": kind,
                "commands": ledger.commands[name],
                "failures": ledger.failures[name],
            }
            for name, kind in script.roles.items()
        },
        "usability": {
            "assessed": False,
            "reason": (
                "results.json states no usability assessment; the run's assessment, "
                "when one was made, is assessment.json"
            ),
            "see": "assessment.json",
        },
    }
    rundir.write_results(results)
    return {
        "run": str(rundir.path),
        "outcome": outcome,
        "completed": completed,
        "results": str(rundir.path / "results.json"),
    }
