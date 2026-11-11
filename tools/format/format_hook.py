"""Formats the file a Claude Code write hook names, with the pinned formatters.

Reads the hook's JSON from stdin and takes `tool_input.file_path`. A file
outside the workspace is left alone. A `.py` file goes through `ruff format`,
a Bazel file through buildifier, both from this binary's runfiles; any other
file is left alone. Formatting only: lint fixes
are `make fix`. Exits 0 whatever happens, because a file that does not parse in
the middle of an edit is the gate's business, not this hook's.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

from python.runfiles import runfiles

# .claude/hooks/format.sh filters payloads on the same suffixes before calling
# this binary; a suffix added here is added to its pattern too.
BAZEL_NAMES = frozenset({"BUILD", "MODULE.bazel"})
BAZEL_SUFFIXES = frozenset({".bazel", ".bzl"})


class Formatter(NamedTuple):
    """A pinned formatter: the env var holding its rlocation path, and the
    arguments that go before the file to format it in place."""

    env_var: str
    args: tuple[str, ...]


RUFF = Formatter("RUFF_BIN", ("format", "--no-cache", "--quiet"))
BUILDIFIER = Formatter("BUILDIFIER_BIN", ())


def file_path_from_hook(payload: str) -> str | None:
    """Returns the hook's `tool_input.file_path`, or None if it has none."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    path = tool_input.get("file_path")
    return path if isinstance(path, str) and path else None


def target_path(file_path: str, workspace: str) -> Path | None:
    """`file_path` resolved against `workspace`, or None if it lies outside it.

    The hook payload carries absolute paths, and a session in this repository
    may write to other checkouts; those are never formatted with this
    repository's configuration. Symlinks are resolved first, so `bazel-*` and
    any other link leading out of the workspace are outside it too.
    """
    if not workspace:
        return None
    root = Path(workspace).resolve()
    path = (root / file_path).resolve()
    return path if path.is_relative_to(root) else None


def formatter_for(path: Path) -> Formatter | None:
    """The formatter for `path`, or None if this hook leaves it alone."""
    if path.suffix == ".py":
        return RUFF
    if path.name in BAZEL_NAMES or path.suffix in BAZEL_SUFFIXES:
        return BUILDIFIER
    return None


def main() -> int:
    file_path = file_path_from_hook(sys.stdin.read())
    if file_path is None:
        return 0
    workspace = os.environ.get("BUILD_WORKSPACE_DIRECTORY", "")
    path = target_path(file_path, workspace)
    if path is None:
        return 0
    formatter = formatter_for(path)
    if formatter is None or not path.is_file():
        return 0
    files = runfiles.Create()
    if files is None:
        return 0
    binary = files.Rlocation(os.environ[formatter.env_var])
    if binary is None:
        return 0
    subprocess.run(
        [binary, *formatter.args, str(path)],
        cwd=workspace,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        env={**os.environ, **files.EnvVars()},
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
