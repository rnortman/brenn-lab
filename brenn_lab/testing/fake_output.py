"""An in-process output that tests put in place of a device: a card with a
mixer whose controls tests can move, and a stream that takes frames at its
rate, sped up by `speed`, keeping every frame it was handed.

Faults are queued by tests: `underruns` and `errors` make the coming writes
fail, `stalls` makes them take no frames, `stuck` stops the stream playing
what it holds, `read_failure` is what every
mixer read returns while it is set, `refuse` chooses mixer writes that fail,
and `gone` makes every later call fail as a device that went away does. Every
mixer write is recorded with the controls' values after it. `raises` names a
call (the stream's `delay`, `drop` or `close`, the output's `read`, `set` or
`detach`) and an exception its next occurrence raises, once; `raised` records
each call that raised one.
"""

import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from brenn_lab.data import Failure
from brenn_lab.playback.messages import Channel, Library, VolumeRange
from brenn_lab.playback.output import Attached, ControlName, Element, Keeping, WriteError
from brenn_lab.sound import SAMPLE_BYTES, CardIdentity, Granted

CARD = CardIdentity(
    selector="Fake",
    card_index=3,
    card_name="Fake",
    card_long_name="Fake card 1",
    pcm="hw:3,0",
    usb_id=None,
)
LIBRARY = Library("fake", "1.0")

# The calls `raises` can name.
type Armable = Literal["delay", "drop", "close", "read", "set", "detach"]


@dataclass
class FakeControl:
    """A mixer control: its channels, volume range and step (None for no
    volume), and whether it has a switch. `values` holds each channel's.
    """

    control: ControlName
    channels: tuple[str, ...] = ("Front Left", "Front Right")
    range: VolumeRange | None = field(default_factory=lambda: VolumeRange(-6000, 0))
    step_mb: int = 1
    switch: bool = True
    values: list[Channel] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.values:
            volume = None if self.range is None else self.range.max_mb
            switch = True if self.switch else None
            self.values = [Channel(c, volume, switch) for c in self.channels]

    def element(self) -> Element:
        return Element(self.control, self.channels, self.range, self.switch)


class FakeStream:
    """A stream that plays at `rate_hz * speed` frames a second from its
    first write, and keeps every frame handed to it in `frames`.
    """

    def __init__(self, output: FakeOutput, granted: Granted) -> None:
        self._output = output
        self._granted = granted
        self._rate = granted.rate_hz * output.speed
        self._frame_bytes = granted.channels * SAMPLE_BYTES[granted.sample_format]
        self.handed = 0
        self.played = 0.0
        self._at: float | None = None
        self.frames = bytearray()
        self.dropped = False
        self.drained = False
        self.closed = False

    @property
    def granted(self) -> Granted:
        return self._granted

    def _advance(self) -> None:
        t = time.monotonic()
        if self._at is not None and not self._output.stuck:
            self.played = min(float(self.handed), self.played + (t - self._at) * self._rate)
        self._at = t

    def write(self, data: bytes) -> int | WriteError:
        out = self._output
        out.writes += 1
        if out.on_write is not None:
            out.on_write(out.writes)
        with out.lock:
            if out.gone:
                return WriteError(False, "no such device")
            if out.underruns:
                out.underruns -= 1
                return WriteError(True, "underrun")
            if out.errors:
                out.errors -= 1
                return WriteError(False, "write error")
            if out.stalls:
                out.stalls -= 1
                return 0
        frames = len(data) // self._frame_bytes
        buffer = self._granted.buffer_frames
        while True:
            self._advance()
            space = buffer - (self.handed - int(self.played))
            if space > 0:
                break
            time.sleep(min(frames, buffer) / self._rate / 4)
        taken = min(frames, space)
        self.handed += taken
        self.frames += data[: taken * self._frame_bytes]
        return taken

    def recover(self, error: WriteError) -> Failure | None:
        if self._output.gone:
            return Failure("stream_died", "no such device")
        self._output.recoveries += 1
        return None

    def delay(self) -> int | None:
        self._output.raise_if_named("delay")
        if self._output.gone:
            return None
        self._advance()
        return self.handed - int(self.played)

    def drain(self) -> Failure | None:
        if self._output.gone:
            return Failure("stream_died", "no such device")
        self.drained = True
        return None

    def drop(self) -> None:
        self._output.raise_if_named("drop")
        self.dropped = True

    def close(self) -> None:
        self._output.raise_if_named("close")
        self.closed = True
        self._output.open_streams -= 1


class FakeOutput:
    """An output of one card with `controls`. `grant` changes what an open
    grants; `busy` makes an open fail.
    """

    kind = "alsa"

    def __init__(
        self,
        controls: list[FakeControl],
        speed: float = 1.0,
        grant: dict[str, int | str] | None = None,
        attach_failure: Failure | None = None,
    ) -> None:
        self.controls = {c.control: c for c in controls}
        self.speed = speed
        self.grant = grant or {}
        self.attach_failure = attach_failure
        self.lock = threading.Lock()
        self.on_write: Callable[[int], None] | None = None
        self.underruns = 0
        self.errors = 0
        self.stalls = 0
        self.stuck = False
        self.read_failure: Failure | None = None
        self.gone = False
        self.busy = False
        self.writes = 0
        self.recoveries = 0
        self.attached = False
        self.attaches = 0
        self.opens = 0
        self.open_streams = 0
        self.sets: list[tuple[ControlName, tuple[Channel, ...]]] = []
        # A `set` for which `refuse` is true changes nothing and fails; it is
        # recorded in `sets` and in `refused`.
        self.refuse: Callable[[ControlName, tuple[Channel, ...]], bool] | None = None
        self.refused: list[tuple[ControlName, tuple[Channel, ...]]] = []
        # Every control's values after each `set`, a refused one included.
        self.states: list[dict[ControlName, tuple[Channel, ...]]] = []
        self.streams: list[FakeStream] = []
        self.raises: dict[Armable, BaseException] = {}
        self.raised: list[Armable] = []

    def raise_if_named(self, call: Armable) -> None:
        with self.lock:
            error = self.raises.pop(call, None)
            if error is None:
                return
            self.raised.append(call)
        raise error

    def attach(self, keeping: Keeping | None) -> Attached | Failure:
        if self.attach_failure is not None:
            return self.attach_failure
        assert not self.attached
        self.attached = True
        self.attaches += 1
        return Attached(CARD, LIBRARY)

    def detach(self) -> None:
        self.attached = False
        self.raise_if_named("detach")

    def elements(self) -> tuple[Element, ...] | Failure:
        assert self.attached
        return tuple(c.element() for c in self.controls.values())

    def read(self, controls: Sequence[ControlName]) -> tuple[tuple[Channel, ...], ...] | Failure:
        assert self.attached
        self.raise_if_named("read")
        if self.gone:
            return Failure("stream_died", "no such device")
        if self.read_failure is not None:
            return self.read_failure
        with self.lock:
            return tuple(tuple(self.controls[c].values) for c in controls)

    def set(self, control: ControlName, channels: tuple[Channel, ...]) -> Failure | None:
        assert self.attached
        self.raise_if_named("set")
        if self.gone:
            return Failure("stream_died", "no such device")
        self.sets.append((control, channels))
        try:
            if self.refuse is not None and self.refuse(control, channels):
                self.refused.append((control, channels))
                return Failure("mixer", f"the fake refused a write to {control.element!r}")
            fake = self.controls[control]
            with self.lock:
                for i, old in enumerate(fake.values):
                    new = next((c for c in channels if c.channel == old.channel), None)
                    if new is None:
                        continue
                    volume = old.volume_mb
                    if new.volume_mb is not None and fake.range is not None:
                        steps = (new.volume_mb - fake.range.min_mb) // fake.step_mb
                        volume = fake.range.min_mb + steps * fake.step_mb
                    switch = old.switch if new.switch is None else new.switch
                    fake.values[i] = Channel(old.channel, volume, switch)
            return None
        finally:
            with self.lock:
                self.states.append({c: tuple(f.values) for c, f in self.controls.items()})

    def move(
        self,
        control: ControlName,
        channel: int,
        volume_mb: int | None = None,
        switch: bool | None = None,
    ) -> None:
        """Change one channel of `control` as another program would; None
        leaves that part as it is.
        """
        fake = self.controls[control]
        with self.lock:
            old = fake.values[channel]
            fake.values[channel] = Channel(
                old.channel,
                old.volume_mb if volume_mb is None else volume_mb,
                old.switch if switch is None else switch,
            )

    def open(
        self,
        rate_hz: int,
        channels: int,
        sample_format: str,
        period_frames: int,
        buffer_frames: int,
    ) -> FakeStream | Failure:
        assert self.attached
        self.opens += 1
        if self.busy:
            return Failure("open", "the device is busy")
        granted = Granted(
            rate_hz=int(self.grant.get("rate_hz", rate_hz)),
            channels=int(self.grant.get("channels", channels)),
            sample_format=str(self.grant.get("sample_format", sample_format)),
            period_frames=period_frames,
            buffer_frames=int(self.grant.get("buffer_frames", buffer_frames)),
        )
        stream = FakeStream(self, granted)
        self.open_streams += 1
        self.streams.append(stream)
        return stream
