"""A capture's samples, read through its owner's reader.

brenn-pod's `frame-log export-wav <log> <out>` writes the capture as a WAV and
prints a map of it: the runs of the recorder's frame index the WAV holds, and
the holes between them with the silence written for each. This module runs
that program, checks the map, reads the WAV with the standard library, and
places positions in the WAV on the capture's own frame index. It never reads
a frame log itself.
"""

import array
import json
import shutil
import stat
import subprocess
import sys
import wave
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from brenn_lab.data import Failure
from brenn_lab.sound import SAMPLE_BYTES, TYPECODES

SCRATCH = ".alignment.tmp"
"""The directory, inside a run, that exported captures are written to."""
UNMEASURED = frozenset({"clock_estimate", "unknown"})
"""Hole bases across which the capture's frame index did not advance."""
BASES = frozenset({"measured", "not_sent"}) | UNMEASURED
MAP_KEYS = frozenset({"wav_frames", "runs", "holes", "resent", "tail"})
BLOCK_FRAMES = 1 << 16


@dataclass(frozen=True)
class Run:
    """Contiguous frames of the capture: `frames` of them, from capture frame
    `first_frame`, at `wav_frame` in the exported file.
    """

    wav_frame: int
    first_frame: int
    frames: int


@dataclass(frozen=True)
class Hole:
    """A hole in the capture's index at capture frame `at_frame`, standing at
    `wav_frame` in the exported file as `filled_frames` of silence.
    """

    wav_frame: int
    at_frame: int
    filled_frames: int
    basis: str


@dataclass(frozen=True)
class Exported:
    """A capture as its reader exported it: the exported file at `path`, of
    `frames` frames, and the map that places them. The samples are not held;
    `blocks` reads one channel's from the file while it exists.
    """

    rate_hz: int
    path: Path
    sample_format: str
    frames: int
    runs: tuple[Run, ...]
    holes: tuple[Hole, ...]
    tail: str

    def blocks(self, channel: int) -> Iterator[array.array[int]]:
        """The samples of channel `channel` in order, in blocks of up to
        `BLOCK_FRAMES` frames, read afresh from the exported file. Raises when
        the file holds fewer than `frames` frames.
        """
        seen = 0
        with wave.open(str(self.path), "rb") as reader:
            count = reader.getnchannels()
            for samples in _interleaved(reader, self.sample_format):
                seen += len(samples) // count
                yield samples[channel::count]
        if seen != self.frames:
            raise RuntimeError(f"{self.path} held {seen} frames, not {self.frames}")

    def capture_frame(self, position: float) -> float:
        """The capture frame at `position` in the exported file. Raises when
        the position is in no run.
        """
        for run in self.runs:
            if run.wav_frame <= position < run.wav_frame + run.frames:
                return run.first_frame + (position - run.wav_frame)
        raise ValueError(f"exported frame {position} is in no run")

    def touches_hole(self, first: int, frames: int) -> bool:
        """Whether `[first, first + frames)` in the exported file overlaps a
        hole's silence or straddles a hole that has none.
        """
        end = first + frames
        for hole in self.holes:
            if hole.filled_frames:
                if hole.wav_frame < end and first < hole.wav_frame + hole.filled_frames:
                    return True
            elif first < hole.wav_frame < end:
                return True
        return False

    def unmeasured_between(self, a: float, b: float) -> bool:
        """Whether a hole across which the index did not advance lies between
        exported positions `a` and `b`.
        """
        low, high = min(a, b), max(a, b)
        return any(low < h.wav_frame <= high for h in self.holes if h.basis in UNMEASURED)


class Unwritable(Exception):
    """`scratch` could not clear or create its directory: the run cannot be
    written. Carries the system's reason and, when a leftover could not be
    cleared, the path that failed; `leftover` is None when the failure was
    creating the directory.
    """

    def __init__(self, reason: str, leftover: Path | None):
        super().__init__(reason)
        self.reason = reason
        self.leftover = leftover


@contextmanager
def scratch(run: Path) -> Iterator[Path]:
    """`<run>/.alignment.tmp`, created empty, and removed when the block ends.
    Whatever stands under the name first is removed: a link or a file by
    unlinking it, never its target, a directory with what it holds. A failure
    to clear or create it raises `Unwritable`; nothing else raises that.
    """
    path = run / SCRATCH
    _clear(path)
    try:
        path.mkdir()
    except OSError as e:
        raise Unwritable(e.strerror or str(e), None) from e
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _clear(path: Path) -> None:
    try:
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            return
        if stat.S_ISDIR(mode):
            shutil.rmtree(path, onexc=_not_cleared)
        else:
            path.unlink()
    except OSError as e:
        raise Unwritable(e.strerror or str(e), path) from e


def _not_cleared(_function: Callable[..., object], failed: str, e: BaseException) -> None:
    if not isinstance(e, OSError):
        raise e
    raise Unwritable(e.strerror or str(e), Path(failed)) from e


def read(
    log: Path,
    out: Path,
    reader: str,
    rate_hz: int,
    sample_format: str,
    layout: tuple[str, ...],
) -> Exported | Failure:
    """The capture at `log`, exported by `reader` to `out` and checked. The
    exported file must hold `rate_hz`, `sample_format` and one channel per
    name in `layout`. On success `out` stays for `Exported.blocks` to read,
    and is the caller's to remove; on a failure or a raise it is removed.
    """
    try:
        got = _export(log, out, reader, rate_hz, sample_format, layout)
    except BaseException:
        out.unlink(missing_ok=True)
        raise
    if isinstance(got, Failure):
        out.unlink(missing_ok=True)
    return got


def _export(
    log: Path,
    out: Path,
    reader: str,
    rate_hz: int,
    sample_format: str,
    layout: tuple[str, ...],
) -> Exported | Failure:
    if not log.is_file():
        return Failure("missing", f"{log} is not a file")
    try:
        done = subprocess.run(
            [reader, "export-wav", str(log), str(out)],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as e:
        return Failure("reader_missing", f"{reader} cannot be run: {e.strerror}")
    if done.returncode == 1:
        return Failure("export_refused", done.stderr.strip())
    if done.returncode == 2:
        return Failure("reader_usage", done.stderr.strip())
    if done.returncode != 0:
        return Failure(
            "reader_contract",
            f"{reader} exited with status {done.returncode}: {done.stderr.strip()}",
        )
    parsed = _parse_map(done.stdout)
    if isinstance(parsed, Failure):
        return parsed
    frames, runs, holes, tail = parsed
    return _check_wav(out, rate_hz, sample_format, layout, frames, runs, holes, tail)


def _parse_map(
    stdout: str,
) -> tuple[int, tuple[Run, ...], tuple[Hole, ...], str] | Failure:
    """The reader's printed map, checked: its keys, and its runs and holes
    tiling the exported file in order.
    """
    try:
        found = json.loads(stdout)
    except json.JSONDecodeError as e:
        return Failure("reader_contract", f"the reader's map is not JSON: {e}")
    if not isinstance(found, dict) or not MAP_KEYS <= found.keys():
        return Failure("reader_contract", f"the reader's map lacks one of {sorted(MAP_KEYS)}")
    try:
        frames = _count(found["wav_frames"])
        runs = tuple(
            Run(_count(r["wav_frame"]), _count(r["first_frame"]), _count(r["frames"]))
            for r in found["runs"]
        )
        holes = tuple(
            Hole(
                _count(h["wav_frame"]),
                _count(h["at_frame"]),
                _count(h["filled_frames"]),
                _basis(h["basis"]),
            )
            for h in found["holes"]
        )
        tail = found["tail"]["verdict"]
    except (KeyError, TypeError, ValueError) as e:
        return Failure("reader_contract", f"the reader's map is malformed: {e!r}")
    if not isinstance(tail, str):
        return Failure("reader_contract", f"the reader's map has a tail verdict {tail!r}")
    pieces = sorted(
        [(r.wav_frame, r.frames) for r in runs] + [(h.wav_frame, h.filled_frames) for h in holes]
    )
    at = 0
    for wav_frame, length in pieces:
        if wav_frame != at:
            return Failure(
                "reader_contract",
                f"the reader's map leaves frames {at} to {wav_frame} unaccounted for",
            )
        at += length
    if at != frames:
        return Failure(
            "reader_contract",
            f"the reader's runs and holes cover {at} frames of {frames}",
        )
    return frames, runs, holes, tail


def _count(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{value!r} is not a count")
    return value


def _basis(value: object) -> str:
    if not isinstance(value, str) or value not in BASES:
        raise ValueError(f"{value!r} is not a hole basis")
    return value


def _check_wav(
    out: Path,
    rate_hz: int,
    sample_format: str,
    layout: tuple[str, ...],
    frames: int,
    runs: tuple[Run, ...],
    holes: tuple[Hole, ...],
    tail: str,
) -> Exported | Failure:
    try:
        reader = wave.open(str(out), "rb")
    except (OSError, wave.Error, EOFError) as e:
        return Failure("reader_contract", f"the exported file cannot be read: {e}")
    with reader:
        params = reader.getparams()
        if (
            params.framerate != rate_hz
            or params.nchannels != len(layout)
            or params.sampwidth != SAMPLE_BYTES.get(sample_format)
        ):
            return Failure(
                "capture_differs",
                f"the export holds {params.nchannels} channels of "
                f"{8 * params.sampwidth}-bit samples at {params.framerate} Hz; "
                f"the capture was granted {len(layout)} channels of {sample_format} "
                f"at {rate_hz} Hz",
            )
        if params.nframes != frames:
            return Failure(
                "reader_contract",
                f"the export holds {params.nframes} frames and its map says {frames}",
            )
        counted = sum(len(s) for s in _interleaved(reader, sample_format)) // params.nchannels
    if counted != frames:
        return Failure(
            "reader_contract",
            f"the export holds {counted} frames and its map says {frames}",
        )
    return Exported(rate_hz, out, sample_format, frames, runs, holes, tail)


def _interleaved(reader: wave.Wave_read, sample_format: str) -> Iterator[array.array[int]]:
    """The whole frames of the open file, interleaved, in blocks of up to
    `BLOCK_FRAMES` frames; a partial frame at the end of the data is dropped.
    """
    frame_bytes = reader.getsampwidth() * reader.getnchannels()
    while block := reader.readframes(BLOCK_FRAMES):
        whole = len(block) - len(block) % frame_bytes
        samples = array.array(TYPECODES[sample_format], block[:whole])
        if sys.byteorder == "big":
            samples.byteswap()
        yield samples
