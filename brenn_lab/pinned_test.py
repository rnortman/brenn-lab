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
