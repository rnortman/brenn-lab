"""The one module that runs the sink client: an output on one sink of a sound
server that speaks the PulseAudio native protocol.

The client, `pulse-client`, is a program the role runs as a child; it loads
no host sound library. This module talks to it one JSON line at a time on its
stdin and stdout. It connects to `$XDG_RUNTIME_DIR/pulse/native` and nothing
else: no environment variable redirects a role to another server.

The sink's volume and mute are the output's one control, `sink` index 0. The
stream the role opens is made at 0 dB, unmuted and pinned to the sink, and
every read while it is open checks that it still is.

The protocol's volume `v` is cubic, `dB = 60 log10(v / 65536)`. A volume reads
as the millibels rounded up, so a reading never understates; `v = 0` reads
`MIN_MB`. A level is set as the largest volume whose reading is at or under
it, except that a channel asked for exactly the reading it had at the first
read after `attach` is set to the raw volume it had then.
"""

import collections
import contextlib
import json
import math
import os
import queue
import subprocess
import threading
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from brenn_lab.data import DecodeError, Failure, JsonObject, JsonValue, get, get_or_null
from brenn_lab.place import create_logs, discard_logs, log_paths
from brenn_lab.playback.device import SINK_LEVEL
from brenn_lab.playback.messages import Channel, Library, SinkIdentity, VolumeRange
from brenn_lab.playback.output import Attached, ControlName, Element, Keeping, WriteError
from brenn_lab.sound import SAMPLE_BYTES, Granted

PROGRAM = "pulse-client"
"""The sink client's name, as the run names the process."""

NORM = 65536
"""The raw volume of 0 dB."""
VOLUME_MAX = 2**31 - 1
"""The largest raw volume the protocol carries."""

REPLY_TIMEOUT_S = 5.0
"""How long the client has to answer a request; a write and a drain also have
the time their own bound allows. Longer than the client's own bound on a
request to the server, 4 s, so a server that answers nothing is the client's
refusal and a client that answers nothing is the client's fault.
"""
STREAM_GRACE_S = 2.0
"""The client's own grace past the buffer's length for a write and a drain."""
EXIT_TIMEOUT_S = 2.0
"""How long the client has to exit once its stdin is closed."""
STDERR_LINES = 20
"""How many of the client's last stderr lines a `client` failure carries."""


def reading_mb(raw: int) -> int:
    """The millibels a raw volume reads as, rounded up; `MIN_MB` for 0."""
    if raw <= 0:
        return MIN_MB
    mb = math.ceil(6000 * math.log10(raw / NORM))
    # The float logarithm can land a hair either side of a whole millibel.
    while _threshold(mb - 1) >= raw:
        mb -= 1
    while _threshold(mb) < raw:
        mb += 1
    return mb


def _threshold(mb: int) -> float:
    """The largest raw volume, unrounded, that reads at or under `mb`."""
    return float(NORM * 10 ** (mb / 6000))


def raw_volume(mb: int) -> int:
    """The largest raw volume whose reading is at or under `mb`; 0 for `MIN_MB`
    or under.
    """
    if mb <= MIN_MB:
        return 0
    raw = min(VOLUME_MAX, math.floor(_threshold(mb)))
    while raw < VOLUME_MAX and reading_mb(raw + 1) <= mb:
        raw += 1
    while raw > 0 and reading_mb(raw) > mb:
        raw -= 1
    return raw


MIN_MB = math.ceil(6000 * math.log10(1 / NORM)) - 1
"""The reading of raw volume 0: one below the reading of raw volume 1."""
MAX_MB = reading_mb(VOLUME_MAX)
"""The reading of the protocol's largest volume."""
RANGE = VolumeRange(MIN_MB, MAX_MB)


@dataclass(frozen=True)
class Sink:
    """A sink as the client reported it, raw volumes as the protocol has them."""

    name: str
    description: str | None
    rate_hz: int
    channels: int
    format: str
    positions: tuple[str, ...]
    volumes: tuple[int, ...]
    base_volume: int
    muted: bool
    decibel_volume: bool
    flat_volume: bool
    hardware_volume: bool


def channel_names(positions: Sequence[str]) -> tuple[str, ...]:
    """Each channel by its position; a position's second and later
    occurrences get `#<n>`, its occurrence count.
    """
    seen: collections.Counter[str] = collections.Counter()
    names = []
    for position in positions:
        seen[position] += 1
        names.append(position if seen[position] == 1 else f"{position}#{seen[position]}")
    return tuple(names)


def select(sinks: Sequence[Sink], selector: str) -> Sink | Failure:
    """The one sink whose name or description contains `selector`,
    case-insensitively: `no_sink` for none, `ambiguous_sink` for several.
    """
    needle = selector.lower()
    matched = [
        s
        for s in sinks
        if needle in s.name.lower() or (s.description and needle in s.description.lower())
    ]
    if len(matched) == 1:
        return matched[0]
    listed = "; ".join(f"{s.name} ({s.description})" for s in sinks) or "none"
    data: dict[str, JsonValue] = {
        "selector": selector,
        "sinks": [{"name": s.name, "description": s.description} for s in sinks],
    }
    if not matched:
        return Failure(
            "no_sink", f"no sink's name or description contains {selector!r}; sinks: {listed}", data
        )
    data["matched"] = [s.name for s in matched]
    return Failure(
        "ambiguous_sink",
        f"{len(matched)} sinks' names or descriptions contain {selector!r}; name one by more "
        f"of its name; sinks: {listed}",
        data,
    )


def volume_refusal(sink: Sink) -> Failure | None:
    """`sink_volume` for a sink whose volume the role cannot hold: flat, where
    the server raises the sink to its loudest stream, or with no decibel scale.
    """
    if sink.flat_volume:
        return Failure(
            "sink_volume",
            f"sink {sink.name} has flat volume: the server would raise it to the loudest "
            "stream's volume",
            {"sink": sink.name},
        )
    if not sink.decibel_volume:
        return Failure(
            "sink_volume", f"sink {sink.name} has no decibel volume", {"sink": sink.name}
        )
    return None


def _sink(value: JsonValue, where: str) -> Sink:
    def ints(key: str) -> tuple[int, ...]:
        items = get(value, key, list, where)
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in items):
            raise DecodeError(f"{where}.{key} is not a list of integers")
        return tuple(items)

    positions = get(value, "positions", list, where)
    if not all(isinstance(p, str) for p in positions):
        raise DecodeError(f"{where}.positions is not a list of strings")
    sink = Sink(
        name=get(value, "name", str, where),
        description=get_or_null(value, "description", str, where),
        rate_hz=get(value, "rate_hz", int, where),
        channels=get(value, "channels", int, where),
        format=get(value, "format", str, where),
        positions=tuple(positions),
        volumes=ints("volumes"),
        base_volume=get(value, "base_volume", int, where),
        muted=get(value, "muted", bool, where),
        decibel_volume=get(value, "decibel_volume", bool, where),
        flat_volume=get(value, "flat_volume", bool, where),
        hardware_volume=get(value, "hardware_volume", bool, where),
    )
    if not (len(sink.positions) == len(sink.volumes) == sink.channels):
        raise DecodeError(f"{where}: positions, volumes and channels disagree")
    return sink


class _Refused(Exception):
    """The client answered a request with a refusal."""

    def __init__(self, error: str, detail: str) -> None:
        super().__init__(f"{error}: {detail}")
        self.error = error
        self.detail = detail


class _Broken(Exception):
    """The client is dead, silent past its bound or unreadable."""


class _Client:
    """The running client: one request at a time, each answered by one line.

    With `logs`, each line of its stdout and of its stderr is copied to the
    first and the second file as it is read, flushed; a copy that fails
    breaks the client. Its output is read from `read` on.
    """

    def __init__(
        self, program: str, socket: Path, logs: tuple[IO[bytes], IO[bytes]] | None = None
    ) -> None:
        self._process = subprocess.Popen(
            [program, "--socket", str(socket)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert self._process.stdin and self._process.stdout and self._process.stderr
        self._stdin: IO[bytes] = self._process.stdin
        self._lines: queue.Queue[bytes] = queue.Queue()
        self._stderr: collections.deque[str] = collections.deque(maxlen=STDERR_LINES)
        out, err = (None, None) if logs is None else logs
        self._readers = [
            threading.Thread(
                target=self._read_stdout, args=(self._process.stdout, out), daemon=True
            ),
            threading.Thread(
                target=self._read_stderr, args=(self._process.stderr, err), daemon=True
            ),
        ]
        self._broken_lock = threading.Lock()
        self.broken: str | None = None

    @property
    def pid(self) -> int:
        return self._process.pid

    def read(self) -> None:
        """Start reading the client's output."""
        for reader in self._readers:
            reader.start()

    def _copy(self, log: IO[bytes] | None, line: bytes, what: str) -> IO[bytes] | None:
        """Copy `line` to `log`; the log to copy to next, None once a copy
        failed, which breaks the client: a client whose output was not kept
        is not one the run can explain.
        """
        if log is None:
            return None
        try:
            log.write(line)
            log.flush()
        except OSError as e:
            self._break(f"the client's {what} could not be kept: {e}")
            return None
        return log

    def _read_stdout(self, stream: IO[bytes], log: IO[bytes] | None) -> None:
        keeping = log is not None
        with _closing(log):
            for line in stream:
                log = self._copy(log, line, "stdout")
                if keeping and log is None:
                    # Not kept, so not taken: the client is broken.
                    continue
                self._lines.put(line)
        self._lines.put(b"")

    def _read_stderr(self, stream: IO[bytes], log: IO[bytes] | None) -> None:
        with _closing(log):
            for line in stream:
                log = self._copy(log, line, "stderr")
                self._stderr.append(line.decode(errors="replace").rstrip("\n"))

    def stderr(self) -> str:
        return " | ".join(self._stderr)

    def _break(self, why: str) -> _Broken:
        """Kill the client; it stays broken for the first reason given."""
        with self._broken_lock:
            if self.broken is None:
                self.broken = why
            reason = self.broken
        self._process.kill()
        return _Broken(reason)

    def abort(self) -> None:
        """Kill the client and wait for it and its readers, for a caller
        that cannot go on with it.
        """
        self._process.kill()
        self.close()

    def answer(self, timeout_s: float = REPLY_TIMEOUT_S) -> JsonObject:
        """The next line, which carries `ok`; `_Refused` for a refusal."""
        if self.broken is not None:
            raise _Broken(self.broken)
        try:
            line = self._lines.get(timeout=timeout_s)
        except queue.Empty:
            raise self._break(f"the client answered nothing in {timeout_s:g} s") from None
        if not line:
            self._process.wait()
            raise self._break(f"the client exited with status {self._process.returncode}")
        try:
            value = json.loads(line)
            ok = get(value, "ok", bool, "the client's answer")
            if not ok:
                raise _Refused(
                    get(value, "error", str, "the client's refusal"),
                    get(value, "detail", str, "the client's refusal"),
                )
        except (json.JSONDecodeError, UnicodeDecodeError, DecodeError) as e:
            raise self._break(f"the client's answer is unreadable: {e}") from None
        assert isinstance(value, dict)
        return value

    def request(
        self, request: JsonObject, data: bytes = b"", timeout_s: float = REPLY_TIMEOUT_S
    ) -> JsonObject:
        if self.broken is not None:
            raise _Broken(self.broken)
        try:
            self._stdin.write(json.dumps(request).encode() + b"\n" + data)
            self._stdin.flush()
        except OSError as e:
            raise self._break(f"the client cannot be written to: {e}") from None
        return self.answer(timeout_s)

    def close(self) -> None:
        """Close its stdin and wait for it to exit; kill it after `EXIT_TIMEOUT_S`."""
        try:
            self._stdin.close()
        except OSError:
            pass
        try:
            self._process.wait(EXIT_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()
        for reader in self._readers:
            reader.join()


@contextlib.contextmanager
def _closing(f: IO[bytes] | None) -> Iterator[None]:
    try:
        yield
    finally:
        if f is not None:
            # Every copy is flushed as it is made, so only a log whose copy
            # failed, which has already broken the client, can fail here.
            with contextlib.suppress(OSError):
                f.close()


def _client_failure(client: _Client, e: _Broken | DecodeError) -> Failure:
    stderr = client.stderr()
    detail = f"{e}; its stderr: {stderr}" if stderr else str(e)
    return Failure("client", detail)


@dataclass(frozen=True)
class _Kept:
    """A channel as the first read after `attach` found it."""

    raw: int
    reading_mb: int


class PulseStream:
    """A playback stream the client holds open on the sink."""

    def __init__(self, output: PulseOutput, client: _Client, granted: Granted) -> None:
        self._output = output
        self._client = client
        self._granted = granted
        self._frame_bytes = granted.channels * SAMPLE_BYTES[granted.sample_format]
        self._bound_s = granted.buffer_frames / granted.rate_hz + STREAM_GRACE_S + REPLY_TIMEOUT_S
        self._closed = False

    @property
    def granted(self) -> Granted:
        return self._granted

    def write(self, data: bytes) -> int | WriteError:
        try:
            answer = self._client.request(
                {"op": "write", "bytes": len(data)}, data, timeout_s=self._bound_s
            )
            return get(answer, "frames", int, "the client's write")
        except _Refused as e:
            return WriteError(e.error == "underrun", e.detail)
        except (_Broken, DecodeError) as e:
            return WriteError(False, _client_failure(self._client, e).detail)

    def recover(self, error: WriteError) -> Failure | None:
        # After an underrun the next write goes on: the server reads on.
        return None

    def delay(self) -> int | None:
        try:
            answer = self._client.request({"op": "delay"})
            return get_or_null(answer, "frames", int, "the client's delay")
        except _Refused, _Broken, DecodeError:
            return None

    def drain(self) -> Failure | None:
        try:
            self._client.request({"op": "drain"}, timeout_s=self._bound_s)
        except _Refused as e:
            return Failure("stream_died", e.detail)
        except _Broken as e:
            return Failure("stream_died", _client_failure(self._client, e).detail)
        return None

    def drop(self) -> None:
        try:
            self._client.request({"op": "drop"})
        except _Refused, _Broken:
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._output.stream_closed()
        try:
            self._client.request({"op": "close"})
        except _Refused, _Broken:
            pass


class PulseOutput:
    """An output on the one sink `selector` selects, through the client at
    `program`.
    """

    kind = "pulse"

    def __init__(self, selector: str, program: str) -> None:
        self._selector = selector
        self._program = program
        self._client: _Client | None = None
        self._clients = 0
        self._sink_name = ""
        self._kept: dict[str, _Kept] | None = None
        self._streaming = False

    @property
    def client_pid(self) -> int | None:
        """The running client's process id; None when not attached."""
        return None if self._client is None else self._client.pid

    def attach(self, keeping: Keeping | None) -> Attached | Failure:
        """Start a client and resolve the sink. With `keeping`, the client
        is the output's next, numbered from 1, and its stdout and stderr are
        kept as `pulse-client-<NNNN>.stdout.jsonl` and `.stderr.log` in its
        log directory; files that exist only for a client that started.
        """
        assert self._client is None
        runtime = os.environ.get("XDG_RUNTIME_DIR")
        if not runtime:
            return Failure("no_server", "XDG_RUNTIME_DIR is not set: no sound server to reach")
        socket = Path(runtime) / "pulse" / "native"
        paths = None if keeping is None else self._log_paths(keeping.logs)
        logs = None if paths is None else create_logs(paths)
        try:
            client = _Client(self._program, socket, logs)
        except OSError as e:
            if paths is not None and logs is not None:
                discard_logs(paths, logs)
            return Failure(
                "client_missing", f"the sink client {self._program} could not be run: {e}"
            )
        try:
            try:
                if keeping is not None and paths is not None:
                    keeping.started(PROGRAM, client.pid, *paths)
            finally:
                # Read whatever `started` did, so that the abort below is
                # seen through to the client's exit.
                client.read()
        except BaseException:
            client.abort()
            raise
        attached = self._start(client)
        if isinstance(attached, Failure):
            client.close()
            return attached
        self._client = client
        return attached

    def _start(self, client: _Client) -> Attached | Failure:
        try:
            first = client.answer()
            library = get(first, "library", dict, "the client's first line")
            server = get(first, "server", dict, "the client's first line")
            sinks = [
                _sink(s, f"sinks[{i}]")
                for i, s in enumerate(get(client.request({"op": "sinks"}), "sinks", list, "sinks"))
            ]
            record = Library(
                get(library, "name", str, "library"), get(library, "version", str, "library")
            )
            server_name = get(server, "name", str, "server")
            server_version = get(server, "version", str, "server")
        except _Refused as e:
            if e.error == "no_server":
                return Failure("no_server", e.detail)
            return Failure("client", f"the client refused {e.error}: {e.detail}")
        except (_Broken, DecodeError) as e:
            return _client_failure(client, e)
        sink = select(sinks, self._selector)
        if isinstance(sink, Failure):
            return sink
        refusal = volume_refusal(sink)
        if refusal is not None:
            return refusal
        self._sink_name = sink.name
        identity = SinkIdentity(
            selector=self._selector,
            sink_name=sink.name,
            description=sink.description,
            server_name=server_name,
            server_version=server_version,
            rate_hz=sink.rate_hz,
            channels=sink.channels,
            server_sample_format=sink.format,
            hardware_volume=sink.hardware_volume,
            base_volume_mb=reading_mb(sink.base_volume),
        )
        return Attached(identity, record)

    def _log_paths(self, logs: Path) -> tuple[Path, Path]:
        """The next client's stdout and stderr log in `logs`, which is made."""
        self._clients += 1
        return log_paths(logs, f"{PROGRAM}-{self._clients:04d}")

    def detach(self) -> None:
        client = self._client
        self._client = None
        self._kept = None
        self._streaming = False
        if client is not None:
            client.close()

    def _attached(self) -> _Client:
        assert self._client is not None, "not attached"
        return self._client

    def stream_closed(self) -> None:
        self._streaming = False

    def _read_sink(self) -> Sink | Failure:
        client = self._attached()
        try:
            return _sink(
                get(client.request({"op": "sink", "name": self._sink_name}), "sink", dict, "sink"),
                "sink",
            )
        except _Refused as e:
            if e.error == "no_sink":
                return Failure("no_sink", e.detail, {"sink": self._sink_name})
            return Failure("client", f"the client refused {e.error}: {e.detail}")
        except (_Broken, DecodeError) as e:
            return _client_failure(client, e)

    def _check_stream(self) -> Failure | None:
        """`stream_moved` unless the open stream is on the sink, at 0 dB on
        every channel and unmuted.
        """
        client = self._attached()
        try:
            answer = client.request({"op": "stream"})
            sink = get(answer, "sink", str, "stream")
            volumes = get(answer, "volumes", list, "stream")
            muted = get(answer, "muted", bool, "stream")
        except _Refused as e:
            return Failure("stream_moved", e.detail, {"sink": self._sink_name})
        except (_Broken, DecodeError) as e:
            return _client_failure(client, e)
        if sink != self._sink_name or muted or any(v != NORM for v in volumes):
            return Failure(
                "stream_moved",
                f"the play's stream should be on {self._sink_name} at 0 dB, unmuted; it is on "
                f"{sink} at raw volumes {volumes}, {'muted' if muted else 'unmuted'}",
                {"sink": sink, "volumes": volumes, "muted": muted},
            )
        return None

    def elements(self) -> tuple[Element, ...] | Failure:
        sink = self._read_sink()
        if isinstance(sink, Failure):
            return sink
        return (Element(SINK_LEVEL, channel_names(sink.positions), RANGE, True),)

    def read(self, controls: Sequence[ControlName]) -> tuple[tuple[Channel, ...], ...] | Failure:
        unknown = [c for c in controls if c != SINK_LEVEL]
        if unknown:
            return _no_control(unknown[0])
        if self._streaming:
            moved = self._check_stream()
            if moved is not None:
                return moved
        sink = self._read_sink()
        if isinstance(sink, Failure):
            return sink
        names = channel_names(sink.positions)
        if self._kept is None:
            self._kept = {
                n: _Kept(v, reading_mb(v)) for n, v in zip(names, sink.volumes, strict=True)
            }
        elif names != tuple(self._kept):
            # A sink is found by name at every read, and one of the same name
            # can come back with other channels.
            return Failure(
                "mixer",
                f"sink {self._sink_name} had the channels {list(self._kept)} and now has "
                f"{list(names)}",
            )
        channels = tuple(
            Channel(n, reading_mb(v), not sink.muted)
            for n, v in zip(names, sink.volumes, strict=True)
        )
        return tuple(channels for _ in controls)

    def set(self, control: ControlName, channels: tuple[Channel, ...]) -> Failure | None:
        if control != SINK_LEVEL:
            return _no_control(control)
        sink = self._read_sink()
        if isinstance(sink, Failure):
            return sink
        names = channel_names(sink.positions)
        asked = {c.channel: c for c in channels}
        if set(asked) != set(names) or len(asked) != len(channels):
            return Failure(
                "mixer",
                f"sink {self._sink_name} has the channels {list(names)}; the set names "
                f"{[c.channel for c in channels]}",
            )
        switches = {c.switch for c in channels}
        if len(switches) != 1:
            return Failure("mixer", f"sink {self._sink_name} takes one switch for every channel")
        [switch] = switches
        request: JsonObject = {"op": "set_sink", "name": self._sink_name}
        volumes = [asked[n].volume_mb for n in names]
        if any(mb is not None for mb in volumes):
            # A channel with no volume asked keeps the raw volume it has.
            request["volumes"] = [
                current if mb is None else self._raw(n, mb)
                for n, mb, current in zip(names, volumes, sink.volumes, strict=True)
            ]
        if switch is not None:
            request["muted"] = not switch
        client = self._attached()
        try:
            client.request(request)
        except _Refused as e:
            if e.error == "no_sink":
                return Failure("no_sink", e.detail, {"sink": self._sink_name})
            return Failure("mixer", f"sink {self._sink_name} could not be set: {e.detail}")
        except _Broken as e:
            return _client_failure(client, e)
        return None

    def _raw(self, name: str, mb: int) -> int:
        kept = None if self._kept is None else self._kept.get(name)
        if kept is not None and kept.reading_mb == mb:
            return kept.raw
        return raw_volume(mb)

    # TODO(playback-through-sound-server-sink): whether a corked stream leaves
    # a suspended sink silent is unmeasured on a real server.
    def open(
        self,
        rate_hz: int,
        channels: int,
        sample_format: str,
        period_frames: int,
        buffer_frames: int,
    ) -> PulseStream | Failure:
        client = self._attached()
        assert not self._streaming
        request: JsonObject = {
            "op": "open",
            "sink": self._sink_name,
            "rate_hz": rate_hz,
            "channels": channels,
            "sample_format": sample_format,
            "period_frames": period_frames,
            "buffer_frames": buffer_frames,
        }
        try:
            answer = client.request(request)
            granted = Granted(
                rate_hz=get(answer, "rate_hz", int, "open"),
                channels=get(answer, "channels", int, "open"),
                sample_format=get(answer, "sample_format", str, "open"),
                period_frames=get(answer, "period_frames", int, "open"),
                buffer_frames=get(answer, "buffer_frames", int, "open"),
            )
        except _Refused as e:
            data: dict[str, JsonValue] = {
                "rate_hz": rate_hz,
                "channels": channels,
                "sample_format": sample_format,
            }
            if e.error == "no_sink":
                return Failure("no_sink", e.detail, {"sink": self._sink_name})
            if e.error == "params":
                return Failure("params", e.detail, data)
            return Failure("open", f"the sink's stream could not be made: {e.detail}", data)
        except (_Broken, DecodeError) as e:
            return _client_failure(client, e)
        self._streaming = True
        return PulseStream(self, client, granted)


def _no_control(control: ControlName) -> Failure:
    return Failure(
        "no_control",
        f"a sink has one control, {SINK_LEVEL.element!r} index {SINK_LEVEL.index}; not "
        f"{control.element!r} index {control.index}",
    )
