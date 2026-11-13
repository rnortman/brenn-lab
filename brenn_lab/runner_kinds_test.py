"""The runner knows no kind: it imports none, and a kind it has never seen
runs through it unmodified.
"""

import ast
import json
import textwrap
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

import brenn_lab
from brenn_lab import roles, runner
from brenn_lab.account import Account, Pointer
from brenn_lab.buildinfo import Revision
from brenn_lab.config_table import Table
from brenn_lab.data import (
    Check,
    Command,
    Event,
    Failure,
    JsonObject,
    Location,
    Release,
    Reply,
    Request,
    command,
    encode_object,
    event,
    result,
)
from brenn_lab.place import Place
from brenn_lab.roles import EventSink, Kind, Role

MACHINE = "bench"
HARNESS = Revision(commit="0123456789abcdef0123456789abcdef01234567", dirty=False, source="git")


def _kind_packages() -> set[str]:
    root = Path(brenn_lab.__file__).parent
    return {p.parent.name for p in root.glob("*/__init__.py")}


@pytest.mark.parametrize("module", ["runner.py", "script.py"])
def test_the_runner_and_the_script_api_import_no_kind(module: str) -> None:
    packages = _kind_packages()
    assert "capture" in packages
    source = (Path(brenn_lab.__file__).parent / module).read_text()
    imported: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
            if node.module == "brenn_lab":
                imported.extend(f"brenn_lab.{a.name}" for a in node.names)
    kinds = [
        name
        for name in imported
        if name.startswith("brenn_lab.") and name.split(".")[1] in packages
    ]
    assert kinds == []


# A toy kind, registered by this test and by nothing else.


@command("toy.ping")
@dataclass(frozen=True)
class Ping(Request["Pinged"]):
    breaks: bool


@result("toy.pinged")
@dataclass(frozen=True)
class Pinged:
    ping: int
    location: Location | None


@result("toy.checked")
@dataclass(frozen=True)
class Checked:
    pass


@result("toy.released")
@dataclass(frozen=True)
class Released:
    running: int | None


@event("toy.broke")
@dataclass(frozen=True)
class Broke:
    ping: int
    failure: Failure


class ToyRole:
    """Each ping writes a file, is left running until `release`, and, when
    asked to, reports `toy.broke`.
    """

    def __init__(self, name: str, machine: str, sink: EventSink, place: Place | None):
        self._name = name
        self._machine = machine
        self._sink = sink
        self._place = place
        self._pings = 0
        self._running: int | None = None

    def handle(self, c: Command) -> Reply:
        outcome: object
        if isinstance(c.body, Check):
            outcome = Checked()
        elif isinstance(c.body, Release):
            outcome, self._running = Released(running=self._running), None
        elif c.dry:
            outcome, self._running = Pinged(ping=0, location=None), 0
        else:
            assert isinstance(c.body, Ping) and self._place is not None
            self._pings += 1
            self._running = self._pings
            self._place.data.mkdir(parents=True, exist_ok=True)
            path = self._place.data / f"{self._pings}.toy"
            path.write_text("toy\n")
            outcome = Pinged(ping=self._pings, location=Location(self._machine, str(path)))
            if c.body.breaks:
                self._sink(Event(self._name, Broke(self._pings, Failure("broke", "it broke"))))
        return Reply(role=self._name, command=c.name, dry=c.dry, outcome=outcome)


class ToyAccount:
    def __init__(self, role: str, pointer: Pointer):
        self._role = role
        self._pointer = pointer
        self._entries: list[JsonObject] = []
        self._broke: list[int] = []

    def reply(self, reply: Reply, by: str) -> bool:
        outcome = reply.outcome
        if isinstance(outcome, Pinged) and outcome.location is not None:
            self._entries.append({"ping": outcome.ping, "file": self._pointer(outcome.location)})
            return True
        return False

    def event(self, e: Event) -> bool:
        if isinstance(e.body, Broke):
            self._broke.append(e.body.ping)
        return False

    def entries(self) -> list[JsonObject]:
        return list(self._entries)

    def records(self) -> list[JsonObject]:
        return [
            {"role": self._role, **entry, "broke": entry["ping"] in self._broke}
            for entry in self._entries
        ]

    def repositories(self) -> list[JsonObject]:
        return [
            {"repository": "toybox", "commit": "1" * 40, "from": f"ping {entry['ping']}"}
            for entry in self._entries
        ]


def _make(
    name: str,
    machine: str,
    programs: Mapping[str, str],
    parameters: object,
    sink: EventSink,
    place: Place | None,
) -> Role:
    return ToyRole(name, machine, sink, place)


def _read_device(table: Table) -> object | None:
    table.done()
    return "toy"


def _account(role: str, pointer: Pointer) -> Account:
    return ToyAccount(role, pointer)


def _left_running(outcome: object) -> str | None:
    assert isinstance(outcome, Released)
    return None if outcome.running is None else f"toy {outcome.running}"


TOY = Kind(
    name="toy",
    programs=frozenset(),
    read_device=_read_device,
    make=_make,
    section="toys",
    totals_key="toyed",
    totals=lambda records: {"pings": len(records)},
    account=_account,
    left_running=_left_running,
)


@pytest.fixture
def toy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(roles.KINDS, "toy", TOY)


def _inputs(tmp_path: Path, breaks: bool, pings: int = 1) -> tuple[Path, Path]:
    lab = tmp_path / "lab.toml"
    lab.write_text(
        textwrap.dedent("""\
            schema = 1
            [machines.bench]
            [roles.t]
            kind = "toy"
            machine = "bench"
            sample_clock = "toybox"
            [roles.t.device]
            """)
    )
    script = tmp_path / "script.py"
    # The script takes the command from this module as loaded, so that the
    # toy's types are registered once.
    script.write_text(
        textwrap.dedent(f"""\
            import sys

            Ping = sys.modules[{__name__!r}].Ping
            ROLES = {{"t": "toy"}}


            def run(lab):
                for _ in range({pings}):
                    lab.role("t").send(Ping(breaks={breaks}))
            """)
    )
    return lab, script


def _run(tmp_path: Path, lab: Path, script: Path) -> tuple[Path, Any, Any]:
    out = tmp_path / "run"
    pointer = runner.run(lab, MACHINE, script, str(script), out, None, HARNESS, None, {})
    assert isinstance(pointer, dict), pointer
    manifest = json.loads((out / "manifest.json").read_text())
    results = json.loads((out / "results.json").read_text())
    return out, manifest, results


def test_a_kind_the_runner_has_never_seen(tmp_path: Path, toy: None) -> None:
    lab, script = _inputs(tmp_path, breaks=True)

    report = runner.dry_run(lab, MACHINE, script, {})
    assert isinstance(report, dict)
    problems = report["problems"]
    assert isinstance(problems, list)
    assert [(p["reason"], p["detail"]) for p in problems if isinstance(p, dict)] == [
        ("left_running", "the script ends with toy 0 running")
    ]

    out, manifest, results = _run(tmp_path, lab, script)

    role = manifest["roles"]["t"]
    assert role["sample_clock"] == "toybox"
    file = {"machine": MACHINE, "path": "toys/t/1.toy", "in_run": True}
    assert role["toys"] == [{"ping": 1, "file": file}]
    assert "captures" not in role
    assert (out / "toys" / "t" / "1.toy").is_file()
    assert manifest["repositories"][1] == {
        "repository": "toybox",
        "commit": "1" * 40,
        "from": "ping 1",
    }
    assert len(manifest["repositories"]) == 2

    assert results["outcome"] == "failed"
    assert results["failure"] == encode_object(
        runner.Problem("role", "broke", "it broke", role="t")
    )
    assert results["roles"]["t"]["failures"] == [
        {"event": "toy.broke", "reason": "broke", "detail": "it broke", "data": {}}
    ]
    assert results["toys"] == [{"role": "t", "ping": 1, "file": file, "broke": True}]
    assert results["toyed"] == {"pings": 1}
    assert results["captures"] == []
    assert results["recorded"] == {"captures_started": 0, "captures_stopped": 0, "frames": 0}
    assert results["sources"] == []
    assert results["played"] == {"plays_started": 0, "plays_completed": 0, "frames_handed": 0}

    journal = [json.loads(t) for t in (out / "journal.jsonl").read_text().splitlines()]
    assert [e.get("command", e.get("event")) for e in journal] == [
        "toy.ping",
        "toy.broke",
        "toy.ping",
        "release",
        "release",
    ]


def test_a_repository_learned_twice_is_named_once_by_its_first(tmp_path: Path, toy: None) -> None:
    lab, script = _inputs(tmp_path, breaks=False, pings=2)
    _, manifest, _ = _run(tmp_path, lab, script)
    assert [e["ping"] for e in manifest["roles"]["t"]["toys"]] == [1, 2]
    assert manifest["repositories"][1:] == [
        {"repository": "toybox", "commit": "1" * 40, "from": "ping 1"}
    ]


def test_what_a_kind_left_running_fails_the_run(tmp_path: Path, toy: None) -> None:
    lab, script = _inputs(tmp_path, breaks=False)
    _, _, results = _run(tmp_path, lab, script)
    assert results["outcome"] == "failed"
    assert results["failure"] == encode_object(
        runner.Problem("runner", "left_running", "toy 1 was running when the script ended", "t")
    )
    assert results["roles"]["t"]["failures"] == []
