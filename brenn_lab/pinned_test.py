"""The table of pinned programs, and their resolution through runfiles."""

import os

from brenn_lab import pinned
from brenn_lab.roles import KINDS


def test_the_pinned_programs_are_the_programs_the_kinds_use() -> None:
    used = frozenset().union(*(kind.programs for kind in KINDS.values()))
    assert pinned.PROGRAMS.keys() == used


def test_every_pinned_program_resolves_to_an_executable() -> None:
    paths = pinned.resolve(pinned.runner_runfiles())
    assert paths.keys() == pinned.PROGRAMS.keys()
    for program, path in paths.items():
        assert os.path.isabs(path), program
        assert os.path.isfile(path), program
        assert os.access(path, os.X_OK), program


def test_every_pinned_tool_resolves_to_an_executable() -> None:
    found = pinned.runner_runfiles()
    assert pinned.TOOLS.keys() == {"frame_log"}
    assert not pinned.TOOLS.keys() & pinned.PROGRAMS.keys()
    for tool, location in pinned.TOOLS.items():
        path = pinned.locate(found, location)
        assert path.is_absolute(), tool
        assert path.is_file(), tool
        assert os.access(path, os.X_OK), tool
