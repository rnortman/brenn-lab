"""Where a role hosted in a run writes, and how it says which programs it
started there.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from brenn_lab.data import Event, Location, ProcessStarted, StreamLog


@dataclass(frozen=True)
class Place:
    """A hosted role's directories in its run: its data, under its kind's
    section, and the output of the programs it calls.
    """

    data: Path
    logs: Path


def log_paths(logs: Path, stem: str) -> tuple[Path, Path]:
    """The stdout and stderr log of the program kept as `stem` in `logs`,
    which is made.
    """
    logs.mkdir(parents=True, exist_ok=True)
    return logs / f"{stem}.stdout.jsonl", logs / f"{stem}.stderr.log"


def create_logs(paths: tuple[Path, Path]) -> tuple[IO[bytes], IO[bytes]]:
    """Both `paths` created for writing bytes; neither left behind if the
    second cannot be.
    """
    out = paths[0].open("xb")
    try:
        err = paths[1].open("xb")
    except BaseException:
        out.close()
        paths[0].unlink()
        raise
    return out, err


def discard_logs(paths: tuple[Path, Path], files: tuple[IO[bytes], IO[bytes]]) -> None:
    """Close `files` and remove `paths`: the logs of a program that could not
    be spawned.
    """
    for f, path in zip(files, paths, strict=True):
        f.close()
        path.unlink()


class SinkRaised(Exception):
    """The event sink raised `error` while a role reported a program it had
    started. It is not an `OSError`, so it passes a role's handling of its
    own files; `sink_raised_unwrapped` raises `error` itself.
    """

    def __init__(self, error: Exception) -> None:
        super().__init__(f"the event sink raised {type(error).__name__}")
        self.error = error


def sink_raised_unwrapped[T](handle: Callable[[], T]) -> T:
    """What `handle` returns; what the event sink raised in it is raised
    itself, out of the `SinkRaised` that carried it.
    """
    try:
        return handle()
    except SinkRaised as e:
        error = e.error
    raise error


def report_started(
    sink: Callable[[Event], None],
    role: str,
    machine: str,
    process: str,
    pid: int,
    stdout_log: Path,
    stderr_log: Path,
) -> None:
    """Report through `sink` that `role` started `process` as `pid`, its
    stdout kept at `stdout_log` and its stderr at `stderr_log` on `machine`.
    What the sink raises is raised as `SinkRaised`.
    """
    body = ProcessStarted(
        process=process,
        pid=pid,
        logs=(
            StreamLog("stdout", Location(machine, str(stdout_log))),
            StreamLog("stderr", Location(machine, str(stderr_log))),
        ),
    )
    try:
        sink(Event(role=role, body=body))
    except Exception as e:
        raise SinkRaised(e) from e
