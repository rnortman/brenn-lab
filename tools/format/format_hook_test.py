"""Tests for the format hook's payload parsing, containment and dispatch."""

from pathlib import Path

import pytest
from format_hook import BUILDIFIER, RUFF, Formatter, file_path_from_hook, formatter_for, target_path


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        "[]",
        '{"tool_input": []}',
        '{"tool_input": {"file_path": 3}}',
        '{"tool_input": {"file_path": ""}}',
    ],
)
def test_file_path_from_hook_rejects(payload: str) -> None:
    assert file_path_from_hook(payload) is None


def test_file_path_from_hook_reads_file_path() -> None:
    assert file_path_from_hook('{"tool_input": {"file_path": "/w/x.py"}}') == "/w/x.py"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("x.py", RUFF),
        ("BUILD", BUILDIFIER),
        ("BUILD.bazel", BUILDIFIER),
        ("MODULE.bazel", BUILDIFIER),
        ("defs.bzl", BUILDIFIER),
        ("README.md", None),
        ("x.pyi", None),
    ],
)
def test_formatter_for(name: str, expected: Formatter | None) -> None:
    assert formatter_for(Path("pkg", name)) is expected


def test_target_path_inside(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    inside = (workspace / "pkg" / "x.py").resolve()
    assert target_path("pkg/x.py", str(workspace)) == inside
    assert target_path(str(workspace / "pkg" / "x.py"), str(workspace)) == inside


def test_target_path_outside(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    (workspace / "link").symlink_to(other)
    assert target_path(str(other / "d.py"), str(workspace)) is None
    assert target_path("../other/d.py", str(workspace)) is None
    assert target_path("link/d.py", str(workspace)) is None
    assert target_path("x.py", "") is None
