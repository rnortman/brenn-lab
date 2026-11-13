"""The entry point of `//brenn_lab:lab`: `dry-run` and `run`.

    lab dry-run --lab <lab.toml> --machine <name> --script <script.py>
    lab run --lab <lab.toml> --machine <name> --script <script.py> --out <dir> [--notes <file>]

On exit status 0, 1 or 2, stdout carries one JSON document and nothing else:
the dry-run report, the run's pointer to its results, or the refusal. Prose
goes to stderr.

- `dry-run`: 0 no problem; 1 problems; 2 no report could be made.
- `run`: 0 completed; 1 a run directory was made and the run did not
  complete; 2 refused before anything was created.
- Either: 2 also for `-h` or `--help`, which prints the help on stderr and
  runs nothing.
- Either: 3 when the runner raised instead of returning, with nothing on
  stdout and the traceback on stderr.

Under `bazel run` a relative path is taken from the directory `bazel run`
was started in, and a run directory inside the workspace is refused.
"""

import argparse
import os
import sys
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

from python.runfiles import runfiles

if TYPE_CHECKING:
    from _typeshed import SupportsWrite

from brenn_lab import buildinfo, runner
from brenn_lab.data import JsonObject, to_text
from brenn_lab.runner import Problem, Refused

BUILD_INFO = "_main/tools/buildinfo/build_info.json"


class _Usage(Exception):
    """The parser refused the command line."""


class _Parser(argparse.ArgumentParser):
    """A parser whose refusal is a refusal document, and whose help is prose
    on stderr, so that stdout holds nothing but the one JSON document. Asking
    for help runs nothing, so it is refused too. No flag may be abbreviated.
    """

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        raise _Usage(message)

    def print_help(self, file: SupportsWrite[str] | None = None) -> None:
        super().print_help(sys.stderr if file is None else file)

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        # Reached only from `-h` or `--help`, once the help is printed.
        raise _Usage("help was asked for; nothing was run")


def _parser() -> _Parser:
    parser = _Parser(
        prog="lab", description="Dry-run or run an experiment script.", allow_abbrev=False
    )
    commands = parser.add_subparsers(dest="command", required=True)
    dry = commands.add_parser(
        "dry-run", help="check a script against a lab; records nothing", allow_abbrev=False
    )
    run = commands.add_parser(
        "run", help="run a script and write its run directory", allow_abbrev=False
    )
    for command in (dry, run):
        command.add_argument("--lab", required=True, help="the lab configuration")
        command.add_argument("--machine", required=True, help="the machine the runner is on")
        command.add_argument("--script", required=True, help="the experiment script")
    run.add_argument("--out", required=True, help="the run directory; must not exist")
    run.add_argument("--notes", help="a free-form notes file, copied into the run")
    return parser


def _path(given: str) -> Path:
    path = Path(given)
    base = os.environ.get("BUILD_WORKING_DIRECTORY")
    if base and not path.is_absolute():
        return Path(base) / path
    return path


def _harness() -> buildinfo.Revision:
    """The revision the runner's build stamp states."""
    found = runfiles.Create()
    if found is None:
        raise RuntimeError("the runner's runfiles cannot be found")
    stamp = found.Rlocation(BUILD_INFO)
    if stamp is None:
        raise RuntimeError(f"{BUILD_INFO} is not in the runner's runfiles")
    return buildinfo.parse(Path(stamp).read_text())


def _emit(document: JsonObject) -> None:
    sys.stdout.write(to_text(document) + "\n")
    sys.stdout.flush()


def main(argv: list[str]) -> int:
    try:
        args = _parser().parse_args(argv)
    except _Usage as e:
        _emit(Refused([Problem("arguments", "usage", str(e))]).report())
        return 2

    workspace = os.environ.get("BUILD_WORKSPACE_DIRECTORY")
    try:
        if args.command == "dry-run":
            result = runner.dry_run(_path(args.lab), args.machine, _path(args.script))
        else:
            result = runner.run(
                _path(args.lab),
                args.machine,
                _path(args.script),
                args.script,
                _path(args.out),
                None if args.notes is None else _path(args.notes),
                _harness(),
                Path(workspace) if workspace else None,
            )
    except BaseException:
        traceback.print_exc()
        return 3

    if isinstance(result, Refused):
        _emit(result.report())
        return 2
    _emit(result)
    if args.command == "dry-run":
        return 0 if result["ok"] is True else 1
    return 0 if result["completed"] is True else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
