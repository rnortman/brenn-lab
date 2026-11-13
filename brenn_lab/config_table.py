"""Reading a TOML table strictly, collecting every problem rather than the first."""

import datetime
import math
from dataclasses import dataclass

from brenn_lab.data import JsonObject, JsonValue


@dataclass(frozen=True)
class ConfigProblem:
    """One thing wrong with a configuration. `key` is the dotted path of the
    value at fault, empty for the file as a whole.
    """

    key: str
    reason: str
    detail: str


class Table:
    """One TOML table under a dotted path. Every key read is marked; `done`
    reports the keys nothing read. A getter returns None when the value is
    missing or wrong, having recorded why.
    """

    def __init__(self, value: dict[str, object], path: str, problems: list[ConfigProblem]):
        self.value = value
        self.path = path
        self.problems = problems
        self._read: set[str] = set()

    def key(self, name: str) -> str:
        return f"{self.path}.{name}" if self.path else name

    def problem(self, name: str, reason: str, detail: str) -> None:
        self.problems.append(ConfigProblem(self.key(name), reason, detail))

    def _take(self, name: str, required: bool) -> object:
        self._read.add(name)
        if name not in self.value:
            if required:
                self.problem(name, "missing_key", "required")
            return None
        return self.value[name]

    def wrong_type(self, name: str, expected: str) -> None:
        self.problem(name, "wrong_type", f"expected {expected}")

    def raw(self, name: str) -> object:
        """The required value of any type, for a caller that reads more than
        one form; None when missing, having recorded why.
        """
        return self._take(name, True)

    def boolean(self, name: str) -> bool | None:
        value = self._take(name, True)
        if value is None or isinstance(value, bool):
            return value
        self.wrong_type(name, "a boolean")
        return None

    def string(self, name: str) -> str | None:
        value = self._take(name, True)
        if value is None or isinstance(value, str):
            return value
        self.wrong_type(name, "a string")
        return None

    def integer(self, name: str) -> int | None:
        value = self._take(name, True)
        if value is None or (isinstance(value, int) and not isinstance(value, bool)):
            return value
        self.wrong_type(name, "an integer")
        return None

    def string_list(self, name: str) -> list[str] | None:
        value = self._take(name, True)
        if value is None:
            return None
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            return [str(v) for v in value]
        self.wrong_type(name, "a list of strings")
        return None

    def table(self, name: str, required: bool = True) -> Table | None:
        value = self._take(name, required)
        if value is None:
            return None
        if isinstance(value, dict):
            return Table(value, self.key(name), self.problems)
        self.wrong_type(name, "a table")
        return None

    def json_table(self, name: str, required: bool = True) -> JsonObject | None:
        """A table of any shape whose every value has a JSON form."""
        table = self.table(name, required)
        return None if table is None else table.as_json()

    def as_json(self) -> JsonObject | None:
        """This table, when every value in it has a JSON form."""
        before = len(self.problems)
        value = _as_json(self.value, self.path, self.problems)
        if len(self.problems) != before or not isinstance(value, dict):
            return None
        return value

    def done(self) -> None:
        for name in sorted(self.value.keys() - self._read):
            self.problem(name, "unknown_key", "not a key of this table")


def _as_json(value: object, path: str, problems: list[ConfigProblem]) -> JsonValue:
    if isinstance(value, dict):
        return {k: _as_json(v, f"{path}.{k}", problems) for k, v in value.items()}
    if isinstance(value, list):
        return [_as_json(v, f"{path}[{i}]", problems) for i, v in enumerate(value)]
    if isinstance(value, float) and not math.isfinite(value):
        problems.append(ConfigProblem(path, "wrong_type", "not a finite number"))
        return None
    if value is None or isinstance(value, bool | int | float | str):
        return value
    assert isinstance(value, datetime.date | datetime.time)
    problems.append(ConfigProblem(path, "wrong_type", "a date or time has no JSON form"))
    return None
