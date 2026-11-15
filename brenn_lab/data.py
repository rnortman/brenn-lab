"""The seam between a script and a device role: commands, replies and events.

A script reaches a device only by sending a command, a plain value; what comes
back is a reply to that command or an event no command asked for. Each kind
registers its command, result and event types under a name, and this module
encodes and decodes every registered type to and from JSON, so whatever
crosses the seam survives a wire.

JSON forms:

    {"role": "mic", "command": "capture.start", "dry": false, "args": {}}
    {"role": "mic", "command": "capture.start", "dry": false, "ok": true,
     "result_type": "capture.started", "result": {...}}
    {"role": "mic", "command": "capture.start", "dry": false, "ok": false,
     "failure": {"reason": "no_card", "detail": "...", "data": {...}}}
    {"role": "mic", "event": "capture.gap", "data": {...}}
"""

import dataclasses
import json
import time
import types
import typing
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]


class DecodeError(ValueError):
    """A JSON value that is not the encoding of what was expected."""


class Request[R]:
    """The base of every command body. `R` is the type of the command's result."""


_COMMANDS: dict[str, type] = {}
_RESULTS: dict[str, type] = {}
_EVENTS: dict[str, type] = {}
_NAMES: dict[type, str] = {}

COMMANDS: Mapping[str, type] = MappingProxyType(_COMMANDS)
RESULTS: Mapping[str, type] = MappingProxyType(_RESULTS)
EVENTS: Mapping[str, type] = MappingProxyType(_EVENTS)


def _registrar[T](table: dict[str, type], name: str) -> Callable[[type[T]], type[T]]:
    def register(cls: type[T]) -> type[T]:
        if not dataclasses.is_dataclass(cls):
            raise TypeError(f"{cls.__name__} is not a dataclass")
        if name in table or cls in _NAMES:
            raise ValueError(f"{name!r} or {cls.__name__} is already registered")
        table[name] = cls
        _NAMES[cls] = name
        return cls

    return register


def command[T](name: str) -> Callable[[type[T]], type[T]]:
    """Register a dataclass, a subclass of `Request`, as the body of command `name`."""
    return _registrar(_COMMANDS, name)


def result[T](name: str) -> Callable[[type[T]], type[T]]:
    """Register a dataclass as the result type `name`."""
    return _registrar(_RESULTS, name)


def event[T](name: str) -> Callable[[type[T]], type[T]]:
    """Register a dataclass as the body of event `name`.

    An event that reports a failure carries it in a field named `failure`, of
    type `Failure`; a dataclass whose `failure` field has another type is
    refused.
    """
    register = _registrar(_EVENTS, name)

    def checked(cls: type[T]) -> type[T]:
        if dataclasses.is_dataclass(cls):
            fields = {f.name for f in dataclasses.fields(cls)}
            if "failure" in fields and typing.get_type_hints(cls)["failure"] is not Failure:
                raise TypeError(f"{cls.__name__}.failure is not of type Failure")
        return register(cls)

    return checked


def failure_of(body: object) -> Failure | None:
    """The failure an event's body reports, if it reports one."""
    failure = getattr(body, "failure", None)
    return failure if isinstance(failure, Failure) else None


def _name_in(table: Mapping[str, type], body: object, what: str) -> str:
    name = _NAMES.get(type(body))
    if name is None or table.get(name) is not type(body):
        raise TypeError(f"{type(body).__name__} is not a registered {what} type")
    return name


def _survives_wire(name: str, body: object) -> None:
    """`TypeError` naming command `name` and the field, unless each of the
    body's fields decodes from its own JSON encoding to a value equal to it.
    """
    assert dataclasses.is_dataclass(body) and not isinstance(body, type)
    hints = typing.get_type_hints(type(body))
    for f in dataclasses.fields(body):
        value = getattr(body, f.name)
        where = f"{name}.{f.name}"
        try:
            back = decode_value(hints[f.name], encode_value(value), where)
        except (TypeError, DecodeError) as e:
            raise TypeError(f"command {where}: {value!r} does not survive a wire: {e}") from e
        if back != value:
            raise TypeError(
                f"command {where}: {value!r} does not survive a wire: it decodes as {back!r}"
            )


MONOTONIC = "CLOCK_MONOTONIC"
REALTIME = "CLOCK_REALTIME"


@dataclass(frozen=True)
class ClockValue:
    """One reading of one named clock, in microseconds."""

    clock: str
    us: int


@dataclass(frozen=True)
class ClockReading:
    """A monotonic and a wall reading taken together on one machine.

    Readings on different machines are never compared: nothing says their
    clocks agree.
    """

    machine: str
    monotonic: ClockValue
    wall: ClockValue

    def after(self, seconds: float) -> ClockReading:
        """The moment `seconds` after this one on the same machine and clocks:
        both values later by `seconds`, rounded to microseconds.
        """
        if not seconds >= 0:
            raise ValueError(f"cannot move a clock reading by {seconds} s; give 0 or more")
        us = round(seconds * 1_000_000)
        return ClockReading(
            machine=self.machine,
            monotonic=ClockValue(self.monotonic.clock, self.monotonic.us + us),
            wall=ClockValue(self.wall.clock, self.wall.us + us),
        )


def now(machine: str) -> ClockReading:
    """This process's clock reading on `machine`, its own."""
    return ClockReading(
        machine=machine,
        monotonic=ClockValue(MONOTONIC, time.clock_gettime_ns(time.CLOCK_MONOTONIC) // 1000),
        wall=ClockValue(REALTIME, time.clock_gettime_ns(time.CLOCK_REALTIME) // 1000),
    )


@dataclass(frozen=True)
class Failure:
    """Why a command failed or a role reports a failure.

    `reason` is a word from the vocabulary the role's kind documents, `detail`
    is prose, and `data` holds whatever the reason carries.
    """

    reason: str
    detail: str
    data: JsonObject = field(default_factory=dict)


# The reason of a failure event by which a role reports that a thread of its
# own raised; the traceback is wherever the run keeps tracebacks.
ROLE_ERROR = "role_error"


@dataclass(frozen=True)
class Location:
    """Where a role wrote a file: an absolute path on the named machine."""

    machine: str
    path: str


@dataclass(frozen=True)
class StreamLog:
    """Where one output stream of a process a role started is kept.
    `stream` is "stdout" or "stderr".
    """

    stream: str
    location: Location


@event("process.started")
@dataclass(frozen=True)
class ProcessStarted:
    """A role started a program whose output it keeps: the program's name,
    its process id on the role's machine, and where each stream is kept.
    Reported once the program is spawned and before the role reads its
    output for meaning; any kind's role reports it.
    """

    process: str
    pid: int
    logs: tuple[StreamLog, ...]


@command("check")
@dataclass(frozen=True)
class Check(Request[object]):
    """Is the device there, and does it grant what the configuration asks.

    Never plays or moves; the same whether dry or not.
    """


@command("release")
@dataclass(frozen=True)
class Release(Request[object]):
    """The session is over: stop whatever is still running and say what it was."""


@dataclass(frozen=True)
class Command:
    """One command to one role. `dry` asks the role to establish whether the
    command would succeed without playing, moving or leaving anything in a run.
    """

    role: str
    body: Request[object]
    dry: bool = False

    def __post_init__(self) -> None:
        name = _name_in(_COMMANDS, self.body, "command")
        _survives_wire(name, self.body)

    @property
    def name(self) -> str:
        return _name_in(_COMMANDS, self.body, "command")


@dataclass(frozen=True)
class Reply:
    """The answer to exactly one command: its result, or a `Failure`."""

    role: str
    command: str
    dry: bool
    outcome: object

    def __post_init__(self) -> None:
        if self.command not in _COMMANDS:
            raise TypeError(f"{self.command!r} is not a registered command")
        if not isinstance(self.outcome, Failure):
            _name_in(_RESULTS, self.outcome, "result")

    @property
    def ok(self) -> bool:
        return not isinstance(self.outcome, Failure)


@dataclass(frozen=True)
class Event:
    """Something a role reports that no command asked for at that moment."""

    role: str
    body: object

    def __post_init__(self) -> None:
        _name_in(_EVENTS, self.body, "event")

    @property
    def name(self) -> str:
        return _name_in(_EVENTS, self.body, "event")


type Message = Command | Reply | Event


def encode(message: Message) -> JsonObject:
    """`message` as a JSON object."""
    match message:
        case Command():
            return {
                "role": message.role,
                "command": message.name,
                "dry": message.dry,
                "args": encode_object(message.body),
            }
        case Reply():
            head: JsonObject = {
                "role": message.role,
                "command": message.command,
                "dry": message.dry,
            }
            if isinstance(message.outcome, Failure):
                return {**head, "ok": False, "failure": encode_object(message.outcome)}
            return {
                **head,
                "ok": True,
                "result_type": _name_in(_RESULTS, message.outcome, "result"),
                "result": encode_object(message.outcome),
            }
        case Event():
            return {
                "role": message.role,
                "event": message.name,
                "data": encode_object(message.body),
            }


def decode(value: JsonValue) -> Message:
    """The message `value` encodes; `DecodeError` if it encodes none."""
    obj = _object(value, "message")
    if "event" in obj:
        _keys(obj, {"role", "event", "data"}, "event")
        cls = _lookup(_EVENTS, obj["event"], "event")
        return Event(role=_str(obj["role"], "role"), body=decode_value(cls, obj["data"], "data"))
    if "ok" in obj:
        ok = obj["ok"]
        if ok is True:
            _keys(obj, {"role", "command", "dry", "ok", "result_type", "result"}, "reply")
            cls = _lookup(_RESULTS, obj["result_type"], "result")
            outcome: object = decode_value(cls, obj["result"], "result")
        elif ok is False:
            _keys(obj, {"role", "command", "dry", "ok", "failure"}, "reply")
            outcome = decode_value(Failure, obj["failure"], "failure")
        else:
            raise DecodeError("reply: 'ok' is not a boolean")
        _lookup(_COMMANDS, obj["command"], "command")
        return Reply(
            role=_str(obj["role"], "role"),
            command=_str(obj["command"], "command"),
            dry=_bool(obj["dry"], "dry"),
            outcome=outcome,
        )
    _keys(obj, {"role", "command", "dry", "args"}, "command")
    cls = _lookup(_COMMANDS, obj["command"], "command")
    body = decode_value(cls, obj["args"], "args")
    assert isinstance(body, Request)
    return Command(role=_str(obj["role"], "role"), body=body, dry=_bool(obj["dry"], "dry"))


def dumps(message: Message) -> str:
    """`message` as one line of JSON text."""
    return to_text(encode(message))


def loads(text: str) -> Message:
    """The message a line of JSON text encodes; `DecodeError` if it encodes none."""
    try:
        value = json.loads(text, parse_constant=_refuse_constant)
    except json.JSONDecodeError as e:
        raise DecodeError(f"not JSON: {e}") from e
    return decode(value)


def _refuse_constant(name: str) -> JsonValue:
    raise DecodeError(f"not JSON: {name}")


def to_text(value: JsonValue) -> str:
    """`value` as one line of JSON text. NaN and infinities are refused."""
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def encode_value(value: object) -> JsonValue:
    """A registered body, a seam dataclass, or a value inside one, as JSON."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return encode_object(value)
    if isinstance(value, tuple | list):
        return [encode_value(v) for v in value]
    if isinstance(value, dict):
        if not all(isinstance(k, str) for k in value):
            raise TypeError("a JSON object's keys are strings")
        return {k: encode_value(v) for k, v in value.items()}
    if value is None or isinstance(value, bool | int | float | str):
        return value
    raise TypeError(f"{type(value).__name__} has no JSON encoding")


def encode_object(obj: object) -> JsonObject:
    """A dataclass instance as a JSON object of its fields."""
    assert dataclasses.is_dataclass(obj) and not isinstance(obj, type)
    return {f.name: encode_value(getattr(obj, f.name)) for f in dataclasses.fields(obj)}


def decode_value(tp: object, value: JsonValue, where: str) -> typing.Any:
    """`value` decoded as type `tp`. Nothing is coerced: a value of another type,
    a missing field or an unknown field is a `DecodeError` naming `where`.
    """
    if tp is JsonValue:
        _check_json(value, where)
        return value
    if isinstance(tp, typing.TypeAliasType):
        return decode_value(tp.__value__, value, where)
    if isinstance(tp, type) and dataclasses.is_dataclass(tp):
        obj = _object(value, where)
        hints = typing.get_type_hints(tp)
        names = [f.name for f in dataclasses.fields(tp)]
        _keys(obj, set(names), where)
        return tp(**{n: decode_value(hints[n], obj[n], f"{where}.{n}") for n in names})
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if origin is typing.Literal:
        for allowed in args:
            if type(value) is type(allowed) and value == allowed:
                return value
        raise DecodeError(f"{where}: {value!r} is not one of {list(args)!r}")
    if origin is types.UnionType or origin is typing.Union:
        if value is None and type(None) in args:
            return None
        rest = [a for a in args if a is not type(None)]
        errors = []
        for alternative in rest:
            try:
                return decode_value(alternative, value, where)
            except DecodeError as e:
                errors.append(str(e))
        raise DecodeError("; ".join(errors))
    if origin is tuple and len(args) == 2 and args[1] is Ellipsis:
        if not isinstance(value, list):
            raise DecodeError(f"{where}: expected a list")
        return tuple(decode_value(args[0], v, f"{where}[{i}]") for i, v in enumerate(value))
    if origin is dict and args[0] is str:
        obj = _object(value, where)
        return {k: decode_value(args[1], v, f"{where}.{k}") for k, v in obj.items()}
    if tp is int:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        raise DecodeError(f"{where}: expected an integer")
    if tp is str:
        return _str(value, where)
    if tp is bool:
        return _bool(value, where)
    if tp is type(None):
        if value is None:
            return None
        raise DecodeError(f"{where}: expected null")
    raise TypeError(f"{where}: {tp!r} has no JSON decoding")


def get[T](obj: JsonValue, key: str, kind: type[T], where: str) -> T:
    """`obj[key]`, which must be present and of type `kind`, a `bool` never
    counting as an `int`; `DecodeError` naming `where` otherwise.
    """
    if not isinstance(obj, dict) or key not in obj:
        raise DecodeError(f"{where} has no {key!r}")
    value = obj[key]
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        raise DecodeError(f"{where}.{key} is not a {kind.__name__}")
    return value


def get_or_null[T](obj: JsonValue, key: str, kind: type[T], where: str) -> T | None:
    """`obj[key]`, which must be present and either null or of type `kind`."""
    if isinstance(obj, dict) and obj.get(key, False) is None:
        return None
    return get(obj, key, kind, where)


def _check_json(value: object, where: str) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            _str(k, where)
            _check_json(v, f"{where}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _check_json(v, f"{where}[{i}]")
    elif not (value is None or isinstance(value, bool | int | float | str)):
        raise DecodeError(f"{where}: {type(value).__name__} is not a JSON value")


def _object(value: JsonValue, where: str) -> JsonObject:
    if not isinstance(value, dict):
        raise DecodeError(f"{where}: expected an object")
    return value


def _keys(obj: JsonObject, expected: set[str], where: str) -> None:
    missing = expected - obj.keys()
    unknown = obj.keys() - expected
    if missing or unknown:
        raise DecodeError(f"{where}: missing {sorted(missing)}, unknown {sorted(unknown)}")


def _lookup(table: Mapping[str, type], name: JsonValue, what: str) -> type:
    cls = table.get(_str(name, what))
    if cls is None:
        raise DecodeError(f"{name!r} is not a registered {what}")
    return cls


def _str(value: object, where: str) -> str:
    if not isinstance(value, str):
        raise DecodeError(f"{where}: expected a string")
    return value


def _bool(value: object, where: str) -> bool:
    if not isinstance(value, bool):
        raise DecodeError(f"{where}: expected a boolean")
    return value
