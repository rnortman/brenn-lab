"""Reading a play's source file, and writing the kept copy the role plays from.

A source is a WAV of integer PCM, 16 or 32 bits, read with the standard
library. The copy holds exactly the frames the role hands to the device.
"""

import array
import hashlib
import os
import sys
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from brenn_lab import marker
from brenn_lab.data import Failure
from brenn_lab.marker import Carried
from brenn_lab.playback.limits import peak_mb
from brenn_lab.playback.messages import Source
from brenn_lab.sound import FORMAT_OF_WIDTH, SAMPLE_BYTES, TYPECODES

_CHUNK_FRAMES = 65536
_READ_BYTES = 1 << 20


@dataclass(frozen=True)
class Judged:
    """A source as read: where it is, and what the play records of it."""

    path: Path
    source: Source


class _Hashing:
    """A file read through `read` alone, every byte read hashed."""

    def __init__(self, file: BinaryIO) -> None:
        self._file = file
        self.hash = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        data = self._file.read(size)
        self.hash.update(data)
        return data

    def rest(self) -> None:
        while self.read(_READ_BYTES):
            pass


def resolve(source_dir: str, name: str) -> Path | Failure:
    """The file `name` names under `source_dir`, or `source_outside` when it
    leaves it or is not a path at all (empty, absolute, or not representable
    as a file name).
    """
    root = Path(source_dir).resolve()
    not_relative = Failure(
        "source_outside", f"{name!r} is not a path relative to the source directory"
    )
    if name == "" or Path(name).is_absolute():
        return not_relative
    try:
        path = (root / name).resolve()
    except ValueError:
        return not_relative
    if not path.is_relative_to(root):
        return Failure("source_outside", f"{name!r} leaves the source directory {source_dir}")
    return path


def judge(source_dir: str, name: str) -> Judged | Failure:
    """Read source `name` once: its hash, frame count, parameters, peak and
    the marker it carries.
    """
    path = resolve(source_dir, name)
    if isinstance(path, Failure):
        return path
    try:
        with path.open("rb") as file:
            hashing = _Hashing(file)
            read = _read(hashing, name)
            if isinstance(read, Failure):
                return read
            hashing.rest()
    except OSError as e:
        return Failure("source_missing", f"{name!r} is not a readable file: {e.strerror}")
    rate_hz, channels, sample_format, frames, peak, carried = read
    bits = 8 * SAMPLE_BYTES[sample_format]
    return Judged(
        path,
        Source(
            name=name,
            sha256=hashing.hash.hexdigest(),
            frames=frames,
            rate_hz=rate_hz,
            channels=channels,
            sample_format=sample_format,
            peak_mb=peak_mb(peak, bits),
            marker=carried,
        ),
    )


def write_copy(judged: Judged, copy: Path) -> Failure | None:
    """Write the kept copy of `judged` at `copy`, reading the source a second
    time, and sync it. `source_changed`, with no copy left, when the source is
    no longer the file `judge` read; `role_io` when the copy cannot be written.
    """
    try:
        out = copy.open("xb")
    except OSError as e:
        return _copy_failed(copy, e)
    try:
        with out:
            failure = _copy_into(judged, out)
            if failure is None:
                out.flush()
                os.fsync(out.fileno())
        if failure is None:
            _sync_dir(copy.parent)
    except _WriteFailed as e:
        failure = _copy_failed(copy, e.error)
    except OSError as e:
        failure = _copy_failed(copy, e)
    if failure is not None:
        copy.unlink(missing_ok=True)
    return failure


class _WriteFailed(Exception):
    """Writing the copy failed, as distinct from reading the source."""

    def __init__(self, error: OSError) -> None:
        super().__init__(str(error))
        self.error = error


def _copy_into(judged: Judged, out: BinaryIO) -> Failure | None:
    """Copy the source's frames into `out` as a WAV, hashing the source as it
    is read; `source_changed` when it is not the file `judge` read.
    """
    source = judged.source
    changed = Failure(
        "source_changed", f"{source.name!r} changed after it was judged", {"sha256": source.sha256}
    )
    try:
        with judged.path.open("rb") as file:
            hashing = _Hashing(file)
            with wave.open(hashing, "rb") as reader:  # type: ignore[call-overload]
                shape = (reader.getnchannels(), reader.getframerate(), reader.getnframes())
                width = reader.getsampwidth()
                if shape != (source.channels, source.rate_hz, source.frames):
                    return changed
                if width != SAMPLE_BYTES[source.sample_format]:
                    return changed
                writer = wave.Wave_write(out)
                try:
                    writer.setnchannels(source.channels)
                    writer.setsampwidth(width)
                    writer.setframerate(source.rate_hz)
                    writer.setnframes(source.frames)
                    while data := reader.readframes(_CHUNK_FRAMES):
                        _writing(writer.writeframesraw, data)
                finally:
                    _writing(writer.close)
            hashing.rest()
    except _WriteFailed:
        raise
    except (OSError, wave.Error, EOFError) as e:
        return Failure(
            "source_changed",
            f"{source.name!r} can no longer be read: {e}",
            {"sha256": source.sha256},
        )
    if hashing.hash.hexdigest() != source.sha256:
        return changed
    return None


def _writing(write: Callable[..., object], *args: object) -> None:
    try:
        write(*args)
    except OSError as e:
        raise _WriteFailed(e) from e


def _copy_failed(copy: Path, e: OSError) -> Failure:
    return Failure("role_io", f"the kept copy {copy} could not be written: {e.strerror}")


def _sync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _read(file: _Hashing, name: str) -> tuple[int, int, str, int, int, Carried | None] | Failure:
    """Rate, channels, format, frame count, peak magnitude and carried marker
    of the WAV in `file`. The frames that may hold a marker are kept as they
    pass and recognised once the whole file is read.
    """
    try:
        with wave.open(file, "rb") as reader:  # type: ignore[call-overload]
            params = reader.getparams()
            sample_format = FORMAT_OF_WIDTH.get(params.sampwidth)
            if sample_format is None:
                return Failure(
                    "source_format",
                    f"{name!r} holds {8 * params.sampwidth}-bit samples; "
                    "a source is 16- or 32-bit integer PCM",
                )
            typecode = TYPECODES[sample_format]
            frame_bytes = params.sampwidth * params.nchannels
            head_samples = params.nchannels * marker.head_frames(params.framerate)
            tail_samples = params.nchannels * marker.tail_frames(params.framerate)
            head = array.array(typecode)
            tail = array.array(typecode)
            frames = 0
            peak = 0
            while data := reader.readframes(_CHUNK_FRAMES):
                if len(data) % frame_bytes:
                    return Failure(
                        "source_format",
                        f"{name!r} has a data chunk that is not a whole number of frames",
                    )
                frames += len(data) // frame_bytes
                samples = array.array(typecode, data)
                if sys.byteorder == "big":
                    samples.byteswap()
                if samples:
                    peak = max(peak, max(samples), -min(samples))
                if len(head) < head_samples:
                    head.extend(samples[: head_samples - len(head)])
                tail.extend(samples[-tail_samples:])
                del tail[:-tail_samples]
    except (wave.Error, EOFError) as e:
        return Failure("source_format", f"{name!r} is not a WAV of integer PCM: {e}")
    if frames != params.nframes:
        return Failure(
            "source_format", f"{name!r} declares {params.nframes} frames and holds {frames}"
        )
    carried = marker.recognise(
        head, tail, params.framerate, params.nchannels, sample_format, frames
    )
    return params.framerate, params.nchannels, sample_format, frames, peak, carried
