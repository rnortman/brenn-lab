"""What the tests that start the entry point, `//brenn_lab:lab`, as a program
share: the roles of the lab configuration they write, the arguments they give
it, and the one document it prints.
"""

import json
import textwrap
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from brenn_lab.testing.transcript_files import layout_of

MACHINE = "bench"
HELD_MB = 0
"""The volume `speaker_toml` holds its held control at."""


def lab_toml(
    program: str, card: str, sample_clock: str | None = None, pulse_client: str | None = None
) -> str:
    """Machine `bench`, whose `pod_capture` is `program` as TOML, and whose
    `pulse_client` is that one when given, and role `mic`, a capture on
    `card` at 16 kHz, `s16_le`, with the six-name layout of the clean
    transcript, stating `sample_clock` when one is given.
    """
    clock = "" if sample_clock is None else f"sample_clock = {json.dumps(sample_clock)}"
    client = "" if pulse_client is None else f"pulse_client = {pulse_client}"
    return textwrap.dedent(f"""\
        schema = 1
        [machines.{MACHINE}.programs]
        pod_capture = {program}
        {client}
        [roles.mic]
        kind = "capture"
        machine = "{MACHINE}"
        {clock}
        [roles.mic.device]
        card = {json.dumps(card)}
        rate_hz = 16000
        sample_format = "s16_le"
        layout = {json.dumps(layout_of("clean"))}
        """)


def speaker_toml(
    card: str, source_dir: Path, cap_mb: int, quiet_hours: Sequence[tuple[str, str]] = ()
) -> str:
    """Role `speaker`, a playback on `card` through ALSA on machine `bench`,
    on sample clock `flex`, playing from `source_dir` under `cap_mb`, quiet
    in each `("HH:MM", "HH:MM")` span of `quiet_hours`; its level control
    `PCM` 1, and `PCM` 0 held at HELD_MB. Appended to `lab_toml`'s text, which
    holds the machine.
    """
    return textwrap.dedent(f"""\
        [roles.speaker]
        kind = "playback"
        machine = "{MACHINE}"
        sample_clock = "flex"
        [roles.speaker.device]
        output = "alsa"
        card = {json.dumps(card)}
        source_dir = {json.dumps(str(source_dir))}
        cap_mb = {cap_mb}
        quiet_hours = {_spans(quiet_hours)}
        [roles.speaker.device.level]
        element = "PCM"
        index = 1
        [[roles.speaker.device.held]]
        element = "PCM"
        index = 0
        volume_mb = {HELD_MB}
        """)


def talker_toml(
    sink: str,
    source_dir: Path,
    cap_mb: int,
    quiet_hours: Sequence[tuple[str, str]] = (),
    role: str = "talker",
) -> str:
    """Role `role`, a playback on the sound server's sink `sink` on machine
    `bench`, playing from `source_dir` under `cap_mb`, quiet in each
    `("HH:MM", "HH:MM")` span of `quiet_hours`. It states no sample clock: a
    sink refuses one. Appended to `lab_toml`'s text given a `pulse_client`.
    """
    return textwrap.dedent(f"""\
        [roles.{role}]
        kind = "playback"
        machine = "{MACHINE}"
        [roles.{role}.device]
        output = "pulse"
        sink = {json.dumps(sink)}
        source_dir = {json.dumps(str(source_dir))}
        cap_mb = {cap_mb}
        quiet_hours = {_spans(quiet_hours)}
        """)


def _spans(quiet_hours: Sequence[tuple[str, str]]) -> str:
    """`quiet_hours` as a TOML array of two-string arrays."""
    return json.dumps([list(span) for span in quiet_hours])


def dry_args(lab: Path, script: Path) -> list[str]:
    """The `lab` arguments that dry-run `script` on machine `bench`."""
    return ["dry-run", "--lab", str(lab), "--machine", MACHINE, "--script", str(script)]


def run_args(lab: Path, script: Path, out: Path) -> list[str]:
    """The `lab` arguments that run `script` on machine `bench` into `out`."""
    return [
        "run",
        "--lab",
        str(lab),
        "--machine",
        MACHINE,
        "--script",
        str(script),
        "--out",
        str(out),
    ]


def document(stdout: str) -> Any:
    """The one JSON document on stdout, which holds nothing else."""
    assert stdout.endswith("\n") and stdout.count("\n") == 1, stdout
    return json.loads(stdout)
