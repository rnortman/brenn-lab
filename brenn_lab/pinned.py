"""The programs this harness's build pins, and where this process carries them.

A machine whose programs name `{ pinned = true }` runs the program found here.
`PROGRAMS`'s keys are exactly the programs the role kinds use, so every pinned
program the lab configuration accepts resolves. `TOOLS` are the pinned
programs the runner's own commands run. `locate` is the runner's one lookup
in its runfiles, for these programs and for its build stamp.
"""

from pathlib import Path

from python.runfiles import runfiles

PROGRAMS: dict[str, str] = {
    "pod_capture": "_main/tools/brenn_pod/pod-capture",
}
"""Each pinned program's runfiles path."""

TOOLS: dict[str, str] = {
    "frame_log": "_main/tools/brenn_pod/frame-log",
}
"""Each pinned tool the runner's own commands run, by runfiles path. No role
kind uses these, so a machine's configuration cannot name them.
"""


def runner_runfiles() -> runfiles.Runfiles:
    """This process's runfiles. Raises when they cannot be found."""
    found = runfiles.Create()
    if found is None:
        raise RuntimeError("the runner's runfiles cannot be found")
    return found


def locate(found: runfiles.Runfiles, location: str) -> Path:
    """The absolute path of `location` in `found`. Raises when it is not there."""
    path = found.Rlocation(location)
    if path is None:
        raise RuntimeError(f"{location} is not in the runner's runfiles")
    return Path(path).absolute()


def resolve(found: runfiles.Runfiles) -> dict[str, str]:
    """The absolute path of every pinned program in `found`. Raises when a
    program is not there.
    """
    return {program: str(locate(found, location)) for program, location in PROGRAMS.items()}
