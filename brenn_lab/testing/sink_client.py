"""The sink client as tests run it, and the host sound libraries it must not load."""

from collections.abc import Sequence

from brenn_lab import pinned

SOUND_LIBRARIES = ("libasound", "libpulse", "libpipewire")
"""The host sound libraries a role on a sink, and its client, never map."""


def client_program() -> str:
    """The sink client's absolute path in this test's runfiles."""
    return str(pinned.locate(pinned.runner_runfiles(), pinned.PROGRAMS["pulse_client"]))


def mapped_sound_libraries(maps: Sequence[str]) -> list[str]:
    """Each of `SOUND_LIBRARIES` named in the lines of a `/proc/<pid>/maps`."""
    return [lib for lib in SOUND_LIBRARIES if any(lib in m for m in maps)]
