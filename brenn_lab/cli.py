"""The entry point of `//brenn_lab:lab`: `dry-run`, `run`, `mark`, `align`
and `assess`.

    lab dry-run --lab <lab.toml> --machine <name> --script <script.py>
    lab run --lab <lab.toml> --machine <name> --script <script.py> --out <dir>
        [--notes <file>] [--frame-log <path>]
    lab mark --source <in.wav> --out <out.wav> --member <0..510>
    lab align --run <dir> [--frame-log <path>]
    lab assess --run <dir> [--frame-log <path>]

On exit status 0, 1 or 2, stdout carries one JSON document and nothing else:
the dry-run report, the run's pointer to its results, what was marked, the
pointer to the alignment report, the pointer to the assessment, or the
refusal. Prose goes to stderr.

- `dry-run`: 0 no problem; 1 problems; 2 no report could be made.
- `run`: 0 completed; 1 a run directory was made and the run did not
  complete; 2 refused before anything was created. A run that was not
  interrupted is assessed at its end; the pointer's `usability` says what the
  assessment answered, or why there is none. The exit status is the run's,
  whatever the assessment did.
- `mark`: 0 the marked file was written; 2 refused, nothing written.
- `align`: 0 a report was written, every capture and play was read and every
  marked play located; 1 a report was written that says otherwise; 2 the run
  cannot be read or cannot be written, and no report was written.
- `assess`: 0 both reports were written and the run is `clean`; 1 both were
  written and the answer is `findings` or `incomplete`; 2 the run cannot be
  read or cannot be written, and nothing was written.
- Any: 2 also for `-h` or `--help`, which prints the help on stderr and
  runs nothing.
- Any: 3 when the command raised instead of returning, with nothing on
  stdout and the traceback on stderr.

Under `bazel run` a relative path is taken from the directory `bazel run`
was started in, and a run directory inside the workspace is refused.
"""

import argparse
import io
import os
import signal
import sys
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

from python.runfiles import runfiles

if TYPE_CHECKING:
    from _typeshed import SupportsWrite

from brenn_lab import buildinfo, marker, pinned, rundir, runner
from brenn_lab.data import Failure, JsonObject, encode_object, to_text
from brenn_lab.runner import Problem, Refused

BUILD_INFO = "brenn_lab/tools/buildinfo/build_info.json"


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
        prog="lab",
        description="Dry-run or run an experiment script, mark a source, or align or assess a run.",
        allow_abbrev=False,
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
    run.add_argument(
        "--frame-log", help="a frame-log reader for the run's assessment instead of the pinned one"
    )
    mark = commands.add_parser(
        "mark", help="write a source with a marker before and after it", allow_abbrev=False
    )
    mark.add_argument("--source", required=True, help="the WAV to mark")
    mark.add_argument("--out", required=True, help="the marked WAV; must not exist")
    mark.add_argument(
        "--member", required=True, type=int, help=f"the member, 0 to {marker.MEMBERS - 1}"
    )
    mark.add_argument(
        "--channels",
        nargs="+",
        type=int,
        metavar="INDEX",
        help="the zero-based channels that carry the marker; every channel when omitted",
    )
    align = commands.add_parser(
        "align", help="find each marked play in each capture of a run", allow_abbrev=False
    )
    assess = commands.add_parser(
        "assess", help="state whether a run is usable, aligning it too", allow_abbrev=False
    )
    for command in (align, assess):
        command.add_argument("--run", required=True, help="the run directory")
        command.add_argument(
            "--frame-log", help="a frame-log reader to run instead of the pinned one"
        )
    return parser


def _path(given: str) -> Path:
    path = Path(given)
    base = os.environ.get("BUILD_WORKING_DIRECTORY")
    if base and not path.is_absolute():
        return Path(base) / path
    return path


def _stamp(found: runfiles.Runfiles) -> buildinfo.Stamp:
    """The revisions the runner's build stamp states."""
    return buildinfo.parse(pinned.locate(found, BUILD_INFO).read_text())


def _reader(found: runfiles.Runfiles, frame_log: str | None) -> tuple[str, bool]:
    """The frame-log reader to run, and whether it is the pinned one."""
    if frame_log is None:
        return str(pinned.locate(found, pinned.TOOLS["frame_log"])), True
    return str(_path(frame_log)), False


def _experiment(args: argparse.Namespace) -> tuple[JsonObject, int]:
    """`dry-run` or `run`: the document and the exit status."""
    workspace = os.environ.get("BUILD_WORKSPACE_DIRECTORY")
    found = pinned.runner_runfiles()
    programs = pinned.resolve(found)
    if args.command == "dry-run":
        checked = runner.dry_run(_path(args.lab), args.machine, _path(args.script), programs)
        if isinstance(checked, Refused):
            return checked.report(), 2
        return checked, 0 if checked["ok"] is True else 1
    if args.frame_log is not None:
        given = _path(args.frame_log)
        if not (given.is_file() and os.access(given, os.X_OK)):
            problem = Problem(
                "arguments", "unreadable", f"--frame-log {given} is not an executable file"
            )
            return Refused([problem]).report(), 2
    stamp = _stamp(found)
    result = runner.run(
        _path(args.lab),
        args.machine,
        _path(args.script),
        args.script,
        _path(args.out),
        None if args.notes is None else _path(args.notes),
        stamp,
        Path(workspace) if workspace else None,
        programs,
    )
    if isinstance(result, Refused):
        return result.report(), 2
    run = Path(str(result["run"]))
    reader, is_pinned = _reader(found, args.frame_log)

    def assess_run() -> JsonObject:
        return _assess_at_end(run, reader, is_pinned, stamp.harness.encode())

    result["usability"] = closing_assessment(str(result["outcome"]), assess_run, run, args.machine)
    return result, 0 if result["completed"] is True else 1


def _not_assessed(reason: str, detail: str) -> JsonObject:
    return {"answer": "not_assessed", "reason": reason, "detail": detail}


def closing_assessment(
    outcome: str, assess: Callable[[], JsonObject], run: Path, machine: str
) -> JsonObject:
    """The pointer's `usability` for the run in `run`, whose results are
    written: what `assess` returns, or why no assessment was made. A run that
    was interrupted is not assessed. For the length of `assess`, SIGTERM
    raises `KeyboardInterrupt` as SIGINT does, so that either signal ends the
    assessment and leaves the run's pointer and exit status; the handler found
    before is put back afterwards. An `Exception` from `assess` is noted in
    the run's `logs/runner.log`, timed by `machine`'s clock, printed on
    stderr, and named in the pointer.
    """
    if outcome == "interrupted":
        return _not_assessed(
            "interrupted", "the run was interrupted; `lab assess` assesses it when wanted"
        )
    previous = signal.signal(signal.SIGTERM, signal.default_int_handler)
    try:
        return assess()
    except KeyboardInterrupt:
        return _not_assessed(
            "assessment_interrupted", "a signal arrived during the run's closing assessment"
        )
    except Exception as e:
        traceback.print_exc()
        try:
            rundir.note(
                run,
                machine,
                "the closing assessment raised:\n" + "".join(traceback.format_exception(e)),
            )
        except Exception:
            # The run's exit status is its own whatever the assessment did;
            # the note that could not be written is on stderr instead.
            traceback.print_exc()
        return _not_assessed("raised", f"{type(e).__name__}: {e}")
    finally:
        signal.signal(signal.SIGTERM, previous)


def _assess_at_end(run: Path, reader: str, is_pinned: bool, harness: JsonObject) -> JsonObject:
    """Assess a run at its end: the pointer's `usability`."""
    # Imported here, so that numpy is loaded only where a run is aligned.
    from brenn_lab.assess import report

    done = report.assess(run, reader, is_pinned, harness)
    if isinstance(done, Failure):
        return _not_assessed("refused", f"{done.reason}: {done.detail}")
    return {
        "answer": done.report["answer"],
        "complete": done.report["complete"],
        "assessment": str(run / report.REPORT),
    }


def _mark(args: argparse.Namespace) -> tuple[JsonObject, int]:
    """`mark`: the document and the exit status. Resolves nothing pinned."""
    source, out = _path(args.source), _path(args.out)
    marked = marker.mark(source, out, args.member, args.channels)
    if isinstance(marked, Failure):
        return Refused([Problem("mark", marked.reason, marked.detail)]).report(), 2
    document: JsonObject = {
        "marked": str(out),
        "source": {
            "frames": marked.frames,
            "rate_hz": marked.rate_hz,
            "channels": marked.channels,
            "sample_format": marked.sample_format,
        },
        "marker": encode_object(marked.marker),
        "marker_channels": list(marked.marker_channels),
    }
    return document, 0


def _align(args: argparse.Namespace) -> tuple[JsonObject, int]:
    """`align`: the document and the exit status. Resolves the pinned reader
    unless `--frame-log` names another.
    """
    # Imported here, so that numpy is loaded only where a run is aligned.
    from brenn_lab.align import report

    found = pinned.runner_runfiles()
    reader, is_pinned = _reader(found, args.frame_log)
    run = _path(args.run)
    done = report.align(run, reader, is_pinned, _stamp(found).harness.encode())
    if isinstance(done, Failure):
        return Refused([Problem("run", done.reason, done.detail)]).report(), 2
    ok = report.ok(done.report)
    document: JsonObject = {
        "run": str(run),
        "report": str(run / report.REPORT),
        "ok": ok,
        "summary": done.report["summary"],
    }
    return document, 0 if ok else 1


def _assess(args: argparse.Namespace) -> tuple[JsonObject, int]:
    """`assess`: the document and the exit status. Resolves the pinned reader
    unless `--frame-log` names another.
    """
    # Imported here, so that numpy is loaded only where a run is aligned.
    from brenn_lab.align.report import REPORT as ALIGNMENT
    from brenn_lab.assess import report

    found = pinned.runner_runfiles()
    reader, is_pinned = _reader(found, args.frame_log)
    run = _path(args.run)
    done = report.assess(run, reader, is_pinned, _stamp(found).harness.encode())
    if isinstance(done, Failure):
        return Refused([Problem("run", done.reason, done.detail)]).report(), 2
    written = done.report
    findings, not_assessed = written["findings"], written["not_assessed"]
    assert isinstance(findings, list) and isinstance(not_assessed, list)
    document: JsonObject = {
        "run": str(run),
        "assessment": str(run / report.REPORT),
        "alignment": str(run / ALIGNMENT),
        "answer": written["answer"],
        "complete": written["complete"],
        "findings": len(findings),
        "not_assessed": len(not_assessed),
    }
    return document, 0 if written["answer"] == "clean" else 1


def _emit(document: JsonObject) -> None:
    sys.stdout.write(to_text(document) + "\n")
    sys.stdout.flush()


def main(argv: list[str]) -> int:
    # Each line a script prints reaches the run's log as it is printed. A
    # stdout that is not a text file wrapper buffers as its owner chose.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(line_buffering=True)
    try:
        args = _parser().parse_args(argv)
    except _Usage as e:
        _emit(Refused([Problem("arguments", "usage", str(e))]).report())
        return 2

    try:
        if args.command == "mark":
            document, status = _mark(args)
        elif args.command == "align":
            document, status = _align(args)
        elif args.command == "assess":
            document, status = _assess(args)
        else:
            document, status = _experiment(args)
    except BaseException:
        traceback.print_exc()
        return 3

    _emit(document)
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
