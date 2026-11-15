"""The one module that touches libasound: an output that opens an ALSA card
directly, through `ctypes`.

The library is loaded the first time an output resolves a card or a stream is
opened, never at import. Nothing checks the declarations below against the C
headers: only calls whose signatures have been stable across alsa-lib 1.x are
declared, and the library's version is recorded with every play.

A card's stream is `hw:<index>,0`, opened with the rate, channels and
format demanded exactly and resampling off. Every millibel figure is ALSA's own
dB unit, hundredths of a decibel.
"""

import ctypes
import errno
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from brenn_lab.data import Failure, JsonValue, encode_value
from brenn_lab.playback.messages import Channel, Library, VolumeRange
from brenn_lab.playback.output import Attached, ControlName, Element, Keeping, WriteError
from brenn_lab.sound import SAMPLE_BYTES, CardIdentity, Granted

LIBRARY = "libasound.so.2"
PROC_ASOUND = Path("/proc/asound")

_FORMATS = {"s16_le": 2, "s32_le": 10}
_STREAM_PLAYBACK = 0
_ACCESS_RW_INTERLEAVED = 3
_CHANNEL_IDS = range(32)
_ROUND_DOWN = -1

_P = ctypes.c_void_p
_PP = ctypes.POINTER(ctypes.c_void_p)
_INT = ctypes.c_int
_UINT = ctypes.c_uint
_LONG = ctypes.c_long
_STR = ctypes.c_char_p
_UFRAMES = ctypes.c_ulong
_SFRAMES = ctypes.c_long


class Lib:
    """libasound's functions, declared."""

    def __init__(self, dll: ctypes.CDLL) -> None:
        self.dll = dll
        p_int = ctypes.POINTER(_INT)
        p_uint = ctypes.POINTER(_UINT)
        p_long = ctypes.POINTER(_LONG)
        p_ufr = ctypes.POINTER(_UFRAMES)
        p_sfr = ctypes.POINTER(_SFRAMES)
        f = self._fn
        self.version = f("snd_asoundlib_version", _STR)
        self.strerror = f("snd_strerror", _STR, _INT)
        self.card_next = f("snd_card_next", _INT, p_int)
        self.card_get_name = f("snd_card_get_name", _INT, _INT, _PP)
        self.card_get_longname = f("snd_card_get_longname", _INT, _INT, _PP)

        self.pcm_open = f("snd_pcm_open", _INT, _PP, _STR, _INT, _INT)
        self.pcm_close = f("snd_pcm_close", _INT, _P)
        self.hw_malloc = f("snd_pcm_hw_params_malloc", _INT, _PP)
        self.hw_free = f("snd_pcm_hw_params_free", None, _P)
        self.hw_any = f("snd_pcm_hw_params_any", _INT, _P, _P)
        self.hw_set_resample = f("snd_pcm_hw_params_set_rate_resample", _INT, _P, _P, _UINT)
        self.hw_set_access = f("snd_pcm_hw_params_set_access", _INT, _P, _P, _INT)
        self.hw_set_format = f("snd_pcm_hw_params_set_format", _INT, _P, _P, _INT)
        self.hw_set_channels = f("snd_pcm_hw_params_set_channels", _INT, _P, _P, _UINT)
        self.hw_set_rate = f("snd_pcm_hw_params_set_rate", _INT, _P, _P, _UINT, _INT)
        self.hw_set_period_near = f(
            "snd_pcm_hw_params_set_period_size_near", _INT, _P, _P, p_ufr, p_int
        )
        self.hw_set_buffer_near = f("snd_pcm_hw_params_set_buffer_size_near", _INT, _P, _P, p_ufr)
        self.hw_params = f("snd_pcm_hw_params", _INT, _P, _P)
        self.hw_get_rate = f("snd_pcm_hw_params_get_rate", _INT, _P, p_uint, p_int)
        self.hw_get_channels = f("snd_pcm_hw_params_get_channels", _INT, _P, p_uint)
        self.hw_get_format = f("snd_pcm_hw_params_get_format", _INT, _P, p_int)
        self.hw_get_period = f("snd_pcm_hw_params_get_period_size", _INT, _P, p_ufr, p_int)
        self.hw_get_buffer = f("snd_pcm_hw_params_get_buffer_size", _INT, _P, p_ufr)
        self.pcm_writei = f("snd_pcm_writei", _SFRAMES, _P, _STR, _UFRAMES)
        self.pcm_delay = f("snd_pcm_delay", _INT, _P, p_sfr)
        self.pcm_recover = f("snd_pcm_recover", _INT, _P, _INT, _INT)
        self.pcm_drain = f("snd_pcm_drain", _INT, _P)
        self.pcm_drop = f("snd_pcm_drop", _INT, _P)

        self.mixer_open = f("snd_mixer_open", _INT, _PP, _INT)
        self.mixer_attach = f("snd_mixer_attach", _INT, _P, _STR)
        self.mixer_get_hctl = f("snd_mixer_get_hctl", _INT, _P, _STR, _PP)
        self.hctl_nonblock = f("snd_hctl_nonblock", _INT, _P, _INT)
        self.mixer_register = f("snd_mixer_selem_register", _INT, _P, _P, _P)
        self.mixer_load = f("snd_mixer_load", _INT, _P)
        self.mixer_close = f("snd_mixer_close", _INT, _P)
        self.mixer_handle_events = f("snd_mixer_handle_events", _INT, _P)
        self.mixer_first = f("snd_mixer_first_elem", _P, _P)
        self.mixer_next = f("snd_mixer_elem_next", _P, _P)
        self.selem_name = f("snd_mixer_selem_get_name", _STR, _P)
        self.selem_index = f("snd_mixer_selem_get_index", _UINT, _P)
        self.selem_has_volume = f("snd_mixer_selem_has_playback_volume", _INT, _P)
        self.selem_has_switch = f("snd_mixer_selem_has_playback_switch", _INT, _P)
        self.selem_is_mono = f("snd_mixer_selem_is_playback_mono", _INT, _P)
        self.selem_has_channel = f("snd_mixer_selem_has_playback_channel", _INT, _P, _INT)
        self.selem_channel_name = f("snd_mixer_selem_channel_name", _STR, _INT)
        self.selem_db_range = f("snd_mixer_selem_get_playback_dB_range", _INT, _P, p_long, p_long)
        self.selem_get_db = f("snd_mixer_selem_get_playback_dB", _INT, _P, _INT, p_long)
        self.selem_set_db = f("snd_mixer_selem_set_playback_dB", _INT, _P, _INT, _LONG, _INT)
        self.selem_get_switch = f("snd_mixer_selem_get_playback_switch", _INT, _P, _INT, p_int)
        self.selem_set_switch = f("snd_mixer_selem_set_playback_switch", _INT, _P, _INT, _INT)

        # The process's own C library, already mapped; nothing new is loaded.
        self.free = ctypes.CDLL(None).free
        self.free.restype = None
        self.free.argtypes = (_P,)

    def _fn(self, name: str, restype: Any, *argtypes: Any) -> Any:
        fn = getattr(self.dll, name)
        fn.restype = restype
        fn.argtypes = argtypes
        return fn

    def error(self, rc: int) -> str:
        return bytes(self.strerror(rc)).decode(errors="replace")


_loaded: Lib | None = None
_load_lock = threading.Lock()


def library() -> Lib | Failure:
    """libasound, loaded on first use; `alsa_missing` when the host has none."""
    global _loaded
    with _load_lock:
        if _loaded is None:
            try:
                # TODO(lanes-load-host-alsa-runtime)
                _loaded = Lib(ctypes.CDLL(LIBRARY, use_errno=True))
            except OSError as e:
                return Failure("alsa_missing", f"the host's {LIBRARY} could not be loaded: {e}")
        return _loaded


def library_record(lib: Lib) -> Library:
    return Library(LIBRARY, bytes(lib.version()).decode())


@dataclass(frozen=True)
class Card:
    """A sound card as ALSA enumerated it."""

    index: int
    name: str
    long_name: str


def select(cards: list[Card], selector: str) -> Card | Failure:
    """The one card whose name or long name contains `selector`,
    case-insensitively: `no_card` for none, `ambiguous_card` for several.
    """
    needle = selector.lower()
    matched = [c for c in cards if needle in c.name.lower() or needle in c.long_name.lower()]
    if len(matched) == 1:
        return matched[0]
    listed = "; ".join(f"{c.index}: {c.name} ({c.long_name})" for c in cards) or "none"
    data: dict[str, JsonValue] = {"selector": selector, "cards": encode_value(cards)}
    if not matched:
        return Failure(
            "no_card", f"no sound card's name contains {selector!r}; cards: {listed}", data
        )
    data["matched"] = [c.index for c in matched]
    return Failure(
        "ambiguous_card",
        f"{len(matched)} sound cards' names contain {selector!r}; name one by more of its "
        f"long name; cards: {listed}",
        data,
    )


def usb_id(card_index: int, proc_asound: Path = PROC_ASOUND) -> str | None:
    """The USB vendor and product id the kernel reports for a card, as it
    prints them; None for a card the kernel has no such file for.
    """
    try:
        text = (proc_asound / f"card{card_index}" / "usbid").read_text()
    except FileNotFoundError:
        return None
    return text.rstrip("\n")


def _owned_string(lib: Lib, ptr: ctypes.c_void_p) -> str:
    try:
        return ctypes.string_at(ptr.value).decode(errors="replace") if ptr.value else ""
    finally:
        if ptr.value:
            lib.free(ptr)


def cards(lib: Lib) -> list[Card] | Failure:
    """Every card ALSA enumerates, in index order."""
    found: list[Card] = []
    index = _INT(-1)
    while True:
        rc = lib.card_next(ctypes.byref(index))
        if rc < 0:
            return Failure("no_card", f"cannot enumerate sound cards: {lib.error(rc)}")
        if index.value < 0:
            return found
        name, long_name = _P(), _P()
        rc = lib.card_get_name(index.value, ctypes.byref(name))
        if rc >= 0:
            rc = lib.card_get_longname(index.value, ctypes.byref(long_name))
        n, ln = _owned_string(lib, name), _owned_string(lib, long_name)
        if rc < 0:
            return Failure(
                "no_card", f"cannot read the names of card {index.value}: {lib.error(rc)}"
            )
        found.append(Card(int(index.value), n, ln))


@dataclass(frozen=True)
class AlsaWriteError(WriteError):
    """A write the device did not take, with ALSA's error code for the recovery."""

    code: int


class AlsaStream:
    """A playback stream open on an ALSA PCM."""

    def __init__(self, lib: Lib, pcm: ctypes.c_void_p, granted: Granted) -> None:
        self._lib = lib
        self._pcm = pcm
        self._granted = granted
        self._frame_bytes = granted.channels * SAMPLE_BYTES[granted.sample_format]
        self._closed = False

    @property
    def granted(self) -> Granted:
        return self._granted

    def write(self, data: bytes) -> int | WriteError:
        frames = len(data) // self._frame_bytes
        while True:
            rc = int(self._lib.pcm_writei(self._pcm, data, frames))
            if rc == -errno.EINTR:
                continue
            if rc < 0:
                return AlsaWriteError(rc == -errno.EPIPE, self._lib.error(rc), rc)
            return rc

    def recover(self, error: WriteError) -> Failure | None:
        assert isinstance(error, AlsaWriteError)
        rc = self._lib.pcm_recover(self._pcm, error.code, 1)
        if rc < 0:
            return Failure("stream_died", f"recovery from {error.detail!r}: {self._lib.error(rc)}")
        return None

    def delay(self) -> int | None:
        frames = _SFRAMES()
        if self._lib.pcm_delay(self._pcm, ctypes.byref(frames)) < 0:
            return None
        return int(frames.value)

    def drain(self) -> Failure | None:
        rc = self._lib.pcm_drain(self._pcm)
        if rc < 0:
            return Failure("stream_died", f"the buffer was not played out: {self._lib.error(rc)}")
        return None

    def drop(self) -> None:
        self._lib.pcm_drop(self._pcm)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._lib.pcm_close(self._pcm)


def open_stream(
    lib: Lib,
    pcm_name: str,
    rate_hz: int,
    channels: int,
    sample_format: str,
    period_frames: int,
    buffer_frames: int,
) -> AlsaStream | Failure:
    """Open `pcm_name` for playing, demanding the rate, channels and format
    exactly with resampling off, the period and buffer at the nearest the
    device has; the stream with what it granted, read back.
    """
    if sample_format not in _FORMATS:
        return Failure("params", f"no ALSA format is known for {sample_format!r}")
    pcm = _P()
    rc = lib.pcm_open(ctypes.byref(pcm), pcm_name.encode(), _STREAM_PLAYBACK, 0)
    if rc < 0:
        return Failure("open", f"cannot open {pcm_name}: {lib.error(rc)}", {"pcm": pcm_name})
    hw = _P()
    if lib.hw_malloc(ctypes.byref(hw)) < 0:
        lib.pcm_close(pcm)
        raise MemoryError("snd_pcm_hw_params_malloc")
    try:
        granted = _negotiate(
            lib, pcm, hw, pcm_name, rate_hz, channels, sample_format, period_frames, buffer_frames
        )
    finally:
        lib.hw_free(hw)
    if isinstance(granted, Failure):
        lib.pcm_close(pcm)
        return granted
    return AlsaStream(lib, pcm, granted)


def _negotiate(
    lib: Lib,
    pcm: ctypes.c_void_p,
    hw: ctypes.c_void_p,
    pcm_name: str,
    rate_hz: int,
    channels: int,
    sample_format: str,
    period_frames: int,
    buffer_frames: int,
) -> Granted | Failure:
    period = _UFRAMES(period_frames)
    buffer = _UFRAMES(buffer_frames)
    steps: tuple[tuple[str, Callable[[], int]], ...] = (
        ("the hardware parameters", lambda: lib.hw_any(pcm, hw)),
        ("resampling off", lambda: lib.hw_set_resample(pcm, hw, 0)),
        ("interleaved access", lambda: lib.hw_set_access(pcm, hw, _ACCESS_RW_INTERLEAVED)),
        (sample_format, lambda: lib.hw_set_format(pcm, hw, _FORMATS[sample_format])),
        (f"{channels} channels", lambda: lib.hw_set_channels(pcm, hw, channels)),
        (f"{rate_hz} Hz", lambda: lib.hw_set_rate(pcm, hw, rate_hz, 0)),
        (
            f"a period near {period_frames} frames",
            lambda: lib.hw_set_period_near(pcm, hw, ctypes.byref(period), None),
        ),
        (
            f"a buffer near {buffer_frames} frames",
            lambda: lib.hw_set_buffer_near(pcm, hw, ctypes.byref(buffer)),
        ),
        ("the parameters as a whole", lambda: lib.hw_params(pcm, hw)),
    )
    for what, step in steps:
        rc = step()
        if rc < 0:
            return Failure(
                "params",
                f"{pcm_name} refused {what}: {lib.error(rc)}",
                {"rate_hz": rate_hz, "channels": channels, "sample_format": sample_format},
            )
    rate, chans, fmt = _UINT(), _UINT(), _INT()
    period_got, buffer_got = _UFRAMES(), _UFRAMES()
    for rc in (
        lib.hw_get_rate(hw, ctypes.byref(rate), None),
        lib.hw_get_channels(hw, ctypes.byref(chans)),
        lib.hw_get_format(hw, ctypes.byref(fmt)),
        lib.hw_get_period(hw, ctypes.byref(period_got), None),
        lib.hw_get_buffer(hw, ctypes.byref(buffer_got)),
    ):
        if rc < 0:
            return Failure("params", f"{pcm_name}: the grant cannot be read: {lib.error(rc)}")
    names = {v: k for k, v in _FORMATS.items()}
    return Granted(
        rate_hz=int(rate.value),
        channels=int(chans.value),
        sample_format=names.get(int(fmt.value), f"alsa format {fmt.value}"),
        period_frames=int(period_got.value),
        buffer_frames=int(buffer_got.value),
    )


@dataclass(frozen=True)
class _Control:
    """A mixer element as found: its playback channels by name and ALSA's id."""

    element: Element
    channel_ids: dict[str, int]


class AlsaOutput:
    """An output on the one card `card` selects, opened directly."""

    kind = "alsa"

    def __init__(self, card: str, proc_asound: Path = PROC_ASOUND) -> None:
        self._selector = card
        self._proc_asound = proc_asound
        self._lib: Lib | None = None
        self._mixer: ctypes.c_void_p | None = None
        self._pcm_name = ""

    def attach(self, keeping: Keeping | None) -> Attached | Failure:
        # An ALSA output starts no program: `keeping` has nothing to keep.
        assert self._mixer is None
        lib = library()
        if isinstance(lib, Failure):
            return lib
        found = cards(lib)
        if isinstance(found, Failure):
            return found
        card = select(found, self._selector)
        if isinstance(card, Failure):
            return card
        ctl = f"hw:{card.index}"
        mixer = _P()
        rc = lib.mixer_open(ctypes.byref(mixer), 0)
        if rc < 0:
            return Failure("mixer", f"cannot open a mixer: {lib.error(rc)}")
        hctl = _P()
        # Taking in changes reads the card's events until none is left, which
        # blocks on a handle that is not non-blocking.
        steps: tuple[tuple[str, Callable[[], int]], ...] = (
            (f"attach to {ctl}", lambda: lib.mixer_attach(mixer, ctl.encode())),
            (
                "find its control handle",
                lambda: lib.mixer_get_hctl(mixer, ctl.encode(), ctypes.byref(hctl)),
            ),
            ("make its control handle non-blocking", lambda: lib.hctl_nonblock(hctl, 1)),
            ("register simple elements", lambda: lib.mixer_register(mixer, None, None)),
            ("load", lambda: lib.mixer_load(mixer)),
        )
        for what, call in steps:
            code = call()
            if code < 0:
                lib.mixer_close(mixer)
                return Failure("mixer", f"the mixer of {ctl} could not {what}: {lib.error(code)}")
        self._lib, self._mixer = lib, mixer
        self._pcm_name = f"{ctl},0"
        identity = CardIdentity(
            selector=self._selector,
            card_index=card.index,
            card_name=card.name,
            card_long_name=card.long_name,
            pcm=self._pcm_name,
            usb_id=usb_id(card.index, self._proc_asound),
        )
        return Attached(identity, library_record(lib))

    def detach(self) -> None:
        if self._lib is not None and self._mixer is not None:
            self._lib.mixer_close(self._mixer)
        self._mixer = None

    def _attached(self) -> tuple[Lib, ctypes.c_void_p]:
        assert self._lib is not None and self._mixer is not None, "not attached"
        return self._lib, self._mixer

    def _controls(self) -> dict[ControlName, tuple[int, _Control]] | Failure:
        """Every element with a playback volume or switch, as the mixer has it
        now, changes made by other programs taken in. Elements are found again
        on every call: an element's handle does not outlive a change of the card.
        """
        lib, mixer = self._attached()
        rc = lib.mixer_handle_events(mixer)
        if rc < 0:
            return Failure("mixer", f"the mixer could not take in changes: {lib.error(rc)}")
        found: dict[ControlName, tuple[int, _Control]] = {}
        elem = lib.mixer_first(mixer)
        while elem:
            has_volume = lib.selem_has_volume(elem) > 0
            has_switch = lib.selem_has_switch(elem) > 0
            if has_volume or has_switch:
                name = ControlName(bytes(lib.selem_name(elem)).decode(), int(lib.selem_index(elem)))
                control = _control(lib, elem, name, has_volume, has_switch)
                if isinstance(control, Failure):
                    return control
                found[name] = (elem, control)
            elem = lib.mixer_next(elem)
        return found

    def elements(self) -> tuple[Element, ...] | Failure:
        controls = self._controls()
        if isinstance(controls, Failure):
            return controls
        return tuple(c.element for _, c in controls.values())

    def read(self, controls: Sequence[ControlName]) -> tuple[tuple[Channel, ...], ...] | Failure:
        found = self._controls()
        if isinstance(found, Failure):
            return found
        read = []
        for control in controls:
            one = _one(found, control)
            if isinstance(one, Failure):
                return one
            channels = self._channels(control, *one)
            if isinstance(channels, Failure):
                return channels
            read.append(channels)
        return tuple(read)

    def _channels(
        self, control: ControlName, elem: int, c: _Control
    ) -> tuple[Channel, ...] | Failure:
        lib, _ = self._attached()
        channels = []
        for name, chn in c.channel_ids.items():
            volume: int | None = None
            switch: bool | None = None
            if c.element.range is not None:
                value = _LONG()
                rc = lib.selem_get_db(elem, chn, ctypes.byref(value))
                if rc < 0:
                    return _mixer_failure(lib, "read the volume of", control, name, rc)
                volume = int(value.value)
            if c.element.switch:
                on = _INT()
                rc = lib.selem_get_switch(elem, chn, ctypes.byref(on))
                if rc < 0:
                    return _mixer_failure(lib, "read the switch of", control, name, rc)
                switch = bool(on.value)
            channels.append(Channel(name, volume, switch))
        return tuple(channels)

    def set(self, control: ControlName, channels: tuple[Channel, ...]) -> Failure | None:
        lib, _ = self._attached()
        found = self._controls()
        if isinstance(found, Failure):
            return found
        one = _one(found, control)
        if isinstance(one, Failure):
            return one
        elem, c = one
        for channel in channels:
            chn = c.channel_ids.get(channel.channel)
            if chn is None:
                return Failure(
                    "mixer",
                    f"{control.element!r} index {control.index} has no playback channel "
                    f"{channel.channel!r}",
                )
            if channel.volume_mb is not None and c.element.range is not None:
                rc = lib.selem_set_db(elem, chn, channel.volume_mb, _ROUND_DOWN)
                if rc < 0:
                    return _mixer_failure(lib, "set the volume of", control, channel.channel, rc)
            if channel.switch is not None and c.element.switch:
                rc = lib.selem_set_switch(elem, chn, int(channel.switch))
                if rc < 0:
                    return _mixer_failure(lib, "set the switch of", control, channel.channel, rc)
        return None

    def open(
        self,
        rate_hz: int,
        channels: int,
        sample_format: str,
        period_frames: int,
        buffer_frames: int,
    ) -> AlsaStream | Failure:
        lib, _ = self._attached()
        return open_stream(
            lib, self._pcm_name, rate_hz, channels, sample_format, period_frames, buffer_frames
        )


def _one(
    found: dict[ControlName, tuple[int, _Control]], control: ControlName
) -> tuple[int, _Control] | Failure:
    if control not in found:
        return Failure(
            "mixer", f"the card has no control {control.element!r} index {control.index}"
        )
    return found[control]


def _control(
    lib: Lib, elem: int, name: ControlName, has_volume: bool, has_switch: bool
) -> _Control | Failure:
    if lib.selem_is_mono(elem) > 0:
        ids = {"Mono": 0}
    else:
        ids = {
            bytes(lib.selem_channel_name(chn)).decode(): chn
            for chn in _CHANNEL_IDS
            if lib.selem_has_channel(elem, chn) > 0
        }
    volume_range = None
    if has_volume:
        low, high = _LONG(), _LONG()
        rc = lib.selem_db_range(elem, ctypes.byref(low), ctypes.byref(high))
        if rc < 0:
            return Failure(
                "mixer",
                f"{name.element!r} index {name.index} has a playback volume with no dB scale: "
                f"{lib.error(rc)}",
            )
        volume_range = VolumeRange(int(low.value), int(high.value))
    return _Control(Element(name, tuple(ids), volume_range, has_switch), ids)


def _mixer_failure(lib: Lib, what: str, control: ControlName, channel: str, rc: int) -> Failure:
    return Failure(
        "mixer",
        f"could not {what} {control.element!r} index {control.index} channel {channel}: "
        f"{lib.error(rc)}",
    )
