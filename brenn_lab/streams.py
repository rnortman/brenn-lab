"""The `lab` process's file descriptors 1 and 2, held for the length of a run
or a dry run.

Holding them, not `sys.stdout` and `sys.stderr`, keeps what reaches the
descriptors without passing through Python: a library's own writes, a
program the script starts, `os.write`. While they are held, stdout carries
nothing, so the one JSON document the entry point prints is all it carries.

- `hold` points both at anonymous temporary files.
- `Held.settle` copies what was held into `<logs>/lab.stdout.log` and
  `<logs>/lab.stderr.log` and points the descriptors at those files.
- `Held.release` puts the descriptors back. What was held and never settled
  is then written to the real stderr, stdout's part first.

Both Python streams are flushed before the descriptors move, every time.

A program started during the hold keeps the temporary files as its 1 and 2:
what it writes after `settle` is not kept.
"""

import os
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import IO

STREAMS = ("stdout", "stderr")


def log_name(stream: str) -> str:
    """The name, in the run's `logs/`, of the file the stream is kept in."""
    return f"lab.{stream}.log"


def _flush() -> None:
    sys.stdout.flush()
    sys.stderr.flush()


class Held:
    """Descriptors 1 and 2 as `hold` left them."""

    def __init__(self) -> None:
        self._saved: list[int] = []
        self._files: list[IO[bytes]] = []
        self._settled = False

    def _hold(self) -> None:
        _flush()
        for fd in (1, 2):
            self._saved.append(os.dup(fd))
        for fd in (1, 2):
            held = tempfile.TemporaryFile()
            self._files.append(held)
            os.dup2(held.fileno(), fd)

    def settle(self, logs: Path) -> None:
        """Create the two files in `logs`, which must not exist, copy what was
        held into them, and point the descriptors at them. Nothing moves
        unless both files were created and filled.
        """
        _flush()
        kept: list[IO[bytes]] = []
        try:
            for stream, held in zip(STREAMS, self._files, strict=True):
                log: IO[bytes] = (logs / log_name(stream)).open("xb")
                kept.append(log)
                _copy(held, log.write)
                log.flush()
        except BaseException:
            for log in kept:
                log.close()
            raise
        for fd, log in zip((1, 2), kept, strict=True):
            os.dup2(log.fileno(), fd)
        for held in self._files:
            held.close()
        self._files = kept
        self._settled = True

    def release(self) -> None:
        """Put the saved descriptors back, whatever the flush before it
        raised; if nothing was settled, write what was held to the real
        stderr, stdout's part first. What a flush that raised left in a
        Python stream is then written to the real stderr, and what that flush
        raised is raised.
        """
        unflushed: BaseException | None = None
        try:
            _flush()
        except BaseException as e:
            unflushed = e
        # Fewer than two when `hold` failed part way.
        for fd, saved in zip((1, 2), self._saved, strict=False):
            os.dup2(saved, fd)
            os.close(saved)
        self._saved = []
        try:
            if not self._settled:
                for held in self._files:
                    _copy(held, lambda chunk: _write_all(2, chunk))
        finally:
            for f in self._files:
                f.close()
            self._files = []
        if unflushed is not None:
            _flush_onto_stderr()
            raise unflushed


def _flush_onto_stderr() -> None:
    """Flush both Python streams with descriptor 1 pointed at descriptor 2."""
    stdout = os.dup(1)
    try:
        os.dup2(2, 1)
        _flush()
    finally:
        os.dup2(stdout, 1)
        os.close(stdout)


def _copy(held: IO[bytes], write: Callable[[bytes], object]) -> None:
    """Pass everything in `held` to `write`. Read with `pread`, so the file
    offset, which a program started during the hold shares, is not moved.
    """
    fd, at = held.fileno(), 0
    while chunk := os.pread(fd, 1 << 16, at):
        write(chunk)
        at += len(chunk)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


def hold() -> Held:
    """Point descriptors 1 and 2 at two anonymous temporary files."""
    held = Held()
    try:
        held._hold()
    except BaseException:
        held.release()
        raise
    return held
