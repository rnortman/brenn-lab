"""The search, judged against signals whose markers are placed by construction."""

import cmath
import math
import tracemalloc
from collections.abc import Iterator

import numpy as np
import pytest

from brenn_lab import marker
from brenn_lab.align import search
from brenn_lab.align.search import Floats

RNG_SEED = 20261113


def placed(
    total: int,
    rate_hz: int,
    shift: int,
    at: float,
    level: float = 0.5,
    ratio: float = 1.0,
    phase: float = 0.0,
) -> Floats:
    """`total` frames holding code `shift`'s marker with its start at frame
    `at`, at `level`, its time scaled by `ratio` (the capture counts `ratio`
    frames per frame of the marker's own rate), its carrier turned by `phase`.
    """
    chips = marker.code(shift)
    turn = cmath.exp(1j * phase)
    out = np.zeros(total)
    last = at + marker.DURATION_MS / 1000 * rate_hz * ratio
    for n in range(math.ceil(at), min(total, math.floor(last) + 1)):
        out[n] = level * (marker.value(chips, (n - at) / (rate_hz * ratio)) * turn).real
    return out


def one(found: list[search.Found]) -> search.Instance:
    [result] = found
    [instance] = result.instances
    return instance


def midpoint(at: float, rate_hz: int) -> float:
    return at + marker.DURATION_MS / 1000 * rate_hz / 2


def handed(x: Floats, block: int | None = None) -> search.Blocks:
    """`x` handed to the search whole, or in blocks of `block` frames."""
    if block is None:
        return lambda: [x]
    return lambda: (x[i : i + block] for i in range(0, len(x), block))


def scan(
    x: Floats, rate_hz: int, templates: list[search.Complexes], block: int | None = None
) -> list[search.Found]:
    return search.search(handed(x, block), rate_hz, templates)


@pytest.mark.parametrize("rate_hz", [8000, 16000, 48000])
@pytest.mark.parametrize("phase", [0.0, math.pi])
def test_a_marker_is_found_at_its_frame(rate_hz: int, phase: float) -> None:
    at = rate_hz + 123
    x = placed(4 * rate_hz, rate_hz, 6, at, phase=phase)
    found = one(scan(x, rate_hz, [search.template(marker.code(6), rate_hz)]))
    assert found.first == at
    assert found.midpoint == pytest.approx(midpoint(at, rate_hz), abs=0.05)
    assert found.score > 0.95


def test_a_fractional_start_is_placed_between_frames() -> None:
    rate_hz = 16000
    rng = np.random.default_rng(RNG_SEED)
    for _ in range(3):
        at = 10000 + float(rng.uniform(0, 1))
        x = placed(3 * rate_hz, rate_hz, 0, at, phase=float(rng.uniform(0, 2 * math.pi)))
        found = one(scan(x, rate_hz, [search.template(marker.code(0), rate_hz)]))
        assert found.midpoint == pytest.approx(midpoint(at, rate_hz), abs=0.1)


def direct_rho(y: Floats, t: search.Complexes, p: int) -> float:
    window = y[p : p + len(t)]
    e = abs(np.sum(window * np.conj(t)))
    return float(math.sqrt(2) * e / (np.linalg.norm(t) * np.linalg.norm(window)))


def bandpassed(x: Floats, rate_hz: int) -> Floats:
    """`x` through the band-pass filter by direct convolution."""
    y: Floats = np.convolve(x, search.bandpass_taps(rate_hz), mode="same")
    return y


SEAM_RATE_HZ = 8000
SEAM_BLOCK = 10007
"""The length of the input's blocks in the seam tests, no multiple of a hop."""


def seams() -> list[int]:
    """Positions that are the first of a block: of the correlation and its
    window energy (twice), of the band-passed channel, and of the input.
    """
    length = marker.length(SEAM_RATE_HZ)
    hop = search.BLOCK - length + 1
    taps = len(search.bandpass_taps(SEAM_RATE_HZ))
    return [hop, 2 * hop, search.BLOCK - taps + 1, 3 * SEAM_BLOCK]


@pytest.mark.parametrize("seam", seams())
@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_rho_is_its_definition_across_every_seam(seam: int, offset: int) -> None:
    """A marker whose peak falls on, before or after a seam: its score is
    `rho` summed directly from its definition, and its midpoint is refined
    by the parabola through the direct sums at its neighbours, whichever
    block they were scored in.
    """
    rate_hz = SEAM_RATE_HZ
    rng = np.random.default_rng(RNG_SEED + seam + offset)
    t = search.template(marker.code(2), rate_hz)
    at = seam + offset
    total = 2 * search.BLOCK + 2 * len(t)
    x = 0.02 * rng.standard_normal(total) + placed(total, rate_hz, 2, at)
    y = bandpassed(x, rate_hz)
    found = one(scan(x, rate_hz, [t], SEAM_BLOCK))
    assert found.first == at
    before, peak, after = (direct_rho(y, t, p) for p in (at - 1, at, at + 1))
    assert found.score == pytest.approx(peak, abs=1e-9)
    shift = 0.5 * (before - after) / (before - 2 * peak + after)
    assert found.midpoint == pytest.approx(midpoint(at + shift, rate_hz), abs=1e-6)
    assert found.midpoint == pytest.approx(one(scan(x, rate_hz, [t])).midpoint, abs=1e-9)


def test_how_the_channel_is_handed_over_does_not_change_the_answer() -> None:
    rate_hz = SEAM_RATE_HZ
    rng = np.random.default_rng(RNG_SEED)
    length = marker.length(rate_hz)
    hop = search.BLOCK - length + 1
    total = 3 * hop
    x = 0.05 * rng.standard_normal(total)
    for shift, at in ((2, 1000.0), (5, hop - 3.5), (2, hop + 20000.25), (5, 2 * hop + 1.0)):
        x += placed(total, rate_hz, shift, at, level=float(rng.uniform(0.2, 0.6)))
    templates = [search.template(marker.code(s), rate_hz) for s in (2, 5)]
    whole = scan(x, rate_hz, templates)
    assert [len(f.instances) for f in whole] == [2, 2]
    for block in (hop - 1, 300):
        for a, b in zip(whole, scan(x, rate_hz, templates, block), strict=True):
            assert [i.first for i in b.instances] == [i.first for i in a.instances]
            assert b.best == pytest.approx(a.best, abs=1e-9)
            for i, j in zip(a.instances, b.instances, strict=True):
                assert j.score == pytest.approx(i.score, abs=1e-9)
                assert j.midpoint == pytest.approx(i.midpoint, abs=1e-9)


def test_the_quiet_rule_spans_the_channel() -> None:
    """A marker far below 10**-9 of the channel's largest energy scores 0,
    though everything louder comes many blocks after it.
    """
    rate_hz = SEAM_RATE_HZ
    rng = np.random.default_rng(RNG_SEED)
    t = search.template(marker.code(2), rate_hz)
    quiet = 3 * search.BLOCK
    loud = 2 * rate_hz
    faint = placed(quiet, rate_hz, 2, 100000, level=1e-7)
    sound = 0.05 * rng.standard_normal(loud) + placed(loud, rate_hz, 2, 3000)
    [alone] = scan(sound, rate_hz, [t])
    [result] = scan(np.concatenate([faint, sound]), rate_hz, [t], 65536)
    assert [i.first for i in result.instances] == [quiet + 3000]
    assert result.best == pytest.approx(alone.best, abs=1e-9)


MEMORY_RATE_HZ = 16000
MEMORY_PIECE = 1 << 16
MEMORY_AT = 300000


def generated(total: int, marker_wave: Floats) -> search.Blocks:
    """A channel of `total` frames of seeded noise with `marker_wave` added at
    `MEMORY_AT`, generated one piece at a time on each pass.
    """

    def blocks() -> Iterator[Floats]:
        rng = np.random.default_rng(RNG_SEED)
        for start in range(0, total, MEMORY_PIECE):
            piece = 0.01 * rng.standard_normal(min(MEMORY_PIECE, total - start))
            low = max(MEMORY_AT, start)
            high = min(MEMORY_AT + len(marker_wave), start + len(piece))
            if low < high:
                piece[low - start : high - start] += marker_wave[low - MEMORY_AT : high - MEMORY_AT]
            yield piece

    return blocks


def test_memory_does_not_grow_with_the_capture() -> None:
    rate_hz = MEMORY_RATE_HZ
    member = marker.codes(0)
    templates = [search.template(marker.code(s), rate_hz) for s in member]
    marker_wave = placed(marker.length(rate_hz) + 1, rate_hz, member[0], 0.0)
    peaks = []
    for total in (1 << 20, 1 << 22):
        tracemalloc.start()
        try:
            found = search.search(generated(total, marker_wave), rate_hz, templates)
            peaks.append(tracemalloc.get_traced_memory()[1])
        finally:
            tracemalloc.stop()
        assert [i.first for i in found[0].instances] == [MEMORY_AT]
        assert found[1].instances == ()
    short, long = peaks
    assert long <= 1.1 * short, peaks


def test_the_band_pass_passes_the_marker_and_stops_hum_and_hiss() -> None:
    for rate_hz in (8000, 16000, 48000):
        h = search.bandpass_taps(rate_hz)
        assert len(h) % 2 == 1
        assert len(h) in (math.floor(0.016 * rate_hz + 0.5), math.floor(0.016 * rate_hz + 0.5) + 1)

        def gain(hz: float, h: Floats = h, rate_hz: int = rate_hz) -> float:
            k = np.arange(len(h)) - len(h) // 2
            return float(abs(np.sum(h * np.exp(-2j * np.pi * hz * k / rate_hz))))

        for hz in (1000, 2000, 3000):
            assert gain(hz) == pytest.approx(1.0, abs=0.02), (rate_hz, hz)
        for hz in (50, 100):
            assert gain(hz) < 0.01, (rate_hz, hz)
        if rate_hz > 8000:
            assert gain(6000) < 0.01, rate_hz


def band_energy(x: Floats, rate_hz: int, at: int, length: int) -> float:
    y = bandpassed(x, rate_hz)[at : at + length]
    return float(np.sum(y * y))


def test_the_score_follows_the_share_of_energy_in_noise() -> None:
    """In noise, `rho` is the clean score times the square root of the
    marker's share of the band-passed energy over its length; the scene's
    levels put one marker above the threshold and one below.
    """
    rate_hz = 16000
    rng = np.random.default_rng(RNG_SEED)
    at = 20000
    total = 3 * rate_hz
    noise = rng.standard_normal(total)
    t = search.template(marker.code(4), rate_hz)
    clean = one(scan(placed(total, rate_hz, 4, at), rate_hz, [t])).score
    seen = []
    for level in (1.0, 0.05):
        m = placed(total, rate_hz, 4, at, level=level)
        share = band_energy(m, rate_hz, at, len(t)) / (
            band_energy(m, rate_hz, at, len(t)) + band_energy(noise, rate_hz, at, len(t))
        )
        predicted = clean * math.sqrt(share)
        [result] = scan(noise + m, rate_hz, [t])
        if predicted >= search.THRESHOLD:
            [instance] = result.instances
            assert instance.first == at
            assert instance.score == pytest.approx(predicted, abs=0.05)
        else:
            assert result.instances == ()
            assert result.best < search.THRESHOLD
        seen.append(predicted >= search.THRESHOLD)
    assert seen == [True, False]


def test_a_marker_at_the_level_of_noise_is_found_beside_a_louder_tone() -> None:
    rate_hz = 16000
    rng = np.random.default_rng(RNG_SEED)
    total = 3 * rate_hz
    at = 15000
    m = placed(total, rate_hz, 8, at)
    rms = math.sqrt(float(np.mean(m[at : at + marker.length(rate_hz)] ** 2)))
    noise = rms * rng.standard_normal(total)
    # Ten times the marker's power: 10 dB, inside the 12 dB the score allows.
    tone = (
        math.sqrt(10) * rms * math.sqrt(2) * np.sin(2 * np.pi * 1000 * np.arange(total) / rate_hz)
    )
    t = search.template(marker.code(8), rate_hz)
    assert one(scan(m + noise, rate_hz, [t])).first == at
    # The tone leaves the score's peak within a frame of the marker's.
    beside = one(scan(m + tone + 0.01 * noise, rate_hz, [t]))
    assert beside.midpoint == pytest.approx(midpoint(at, rate_hz), abs=1.0)
    assert beside.score >= search.THRESHOLD


def test_a_marker_twenty_db_under_noise_is_not_found_and_its_score_is_reported() -> None:
    rate_hz = 16000
    rng = np.random.default_rng(RNG_SEED)
    total = 3 * rate_hz
    at = 15000
    m = placed(total, rate_hz, 8, at)
    rms = math.sqrt(float(np.mean(m[at : at + marker.length(rate_hz)] ** 2)))
    [result] = scan(
        m + 10 * rms * rng.standard_normal(total),
        rate_hz,
        [search.template(marker.code(8), rate_hz)],
    )
    assert result.instances == ()
    assert 0 < result.best < search.THRESHOLD


def test_what_is_not_the_marker_yields_no_instance() -> None:
    rate_hz = 16000
    rng = np.random.default_rng(RNG_SEED)
    total = 20 * rate_hz
    n = np.arange(total)
    t = search.template(marker.code(0), rate_hz)
    scenes = {
        "noise": rng.standard_normal(total),
        "bursts": rng.standard_normal(total)
        * np.repeat(10 ** rng.uniform(-2, 0, total // 800), 800),
        "tones": sum(np.sin(2 * np.pi * hz * n / rate_hz) for hz in (440, 1000, 2000, 2100)),
        "another member": placed(total, rate_hz, 1, 5000)
        + placed(total, rate_hz, 3, 50000)
        + placed(total, rate_hz, 1021, 100000),
        "silence": np.zeros(total),
    }
    for name, x in scenes.items():
        [result] = scan(np.asarray(x, dtype=np.float64), rate_hz, [t])
        assert result.instances == (), name
        assert result.best < search.THRESHOLD, name


def test_three_members_overlapping_wholly_are_each_found() -> None:
    rate_hz = 16000
    rng = np.random.default_rng(RNG_SEED)
    total = 3 * rate_hz
    at = 12000
    shifts = (10, 301, 777)
    x = 1e-4 * rng.standard_normal(total)
    for s in shifts:
        x += placed(total, rate_hz, s, at, phase=float(rng.uniform(0, 2 * math.pi)))
    found = scan(x, rate_hz, [search.template(marker.code(s), rate_hz) for s in shifts])
    for s, result in zip(shifts, found, strict=True):
        [instance] = result.instances
        assert instance.first == at, s
        assert instance.score == pytest.approx(1 / math.sqrt(3), abs=0.05), s


@pytest.mark.parametrize(("ppm", "found"), [(200, True), (1000, False)])
def test_a_rate_offset_within_the_tolerance_is_found(ppm: int, found: bool) -> None:
    rate_hz = 16000
    at = 9000.0
    ratio = 1 + ppm * 1e-6
    x = placed(3 * rate_hz, rate_hz, 12, at, ratio=ratio)
    [result] = scan(x, rate_hz, [search.template(marker.code(12), rate_hz)])
    if found:
        [instance] = result.instances
        true_midpoint = at + marker.DURATION_MS / 1000 * rate_hz * ratio / 2
        assert instance.midpoint == pytest.approx(true_midpoint, abs=0.5)
    else:
        assert result.instances == ()


def test_two_instances_of_one_marker_are_both_found_in_capture_order() -> None:
    rate_hz = 8000
    total = 6 * rate_hz
    x = placed(total, rate_hz, 6, 30000, level=0.2) + placed(total, rate_hz, 6, 3000)
    [result] = scan(x, rate_hz, [search.template(marker.code(6), rate_hz)])
    assert [i.first for i in result.instances] == [3000, 30000]


def test_a_channel_shorter_than_a_marker_has_no_instance() -> None:
    rate_hz = 8000
    [result] = scan(np.ones(100), rate_hz, [search.template(marker.code(0), rate_hz)])
    assert result == search.Found((), 0.0)
