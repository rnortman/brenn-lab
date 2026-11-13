"""What the tests that start the entry point, `//brenn_lab:lab`, as a program
share: the roles of the lab configuration they write, the arguments they give
it, and the one document it prints.
"""

import json
import textwrap
from pathlib import Path
from typing import Any

from brenn_lab.testing.transcript_files import layout_of

MACHINE = "bench"


def lab_toml(program: str, card: str, sample_clock: str | None = None) -> str:
    """Machine `bench`, whose `pod_capture` is `program` as TOML, and role
    `mic`, a capture on `card` at 16 kHz, `s16_le`, with the six-name layout
    of the clean transcript, stating `sample_clock` when one is given.
    """
    clock = "" if sample_clock is None else f"sample_clock = {json.dumps(sample_clock)}"
    return textwrap.dedent(f"""\
        schema = 1
        [machines.{MACHINE}.programs]
        pod_capture = {program}
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


def speaker_toml(card: str, source_dir: Path, cap_mb: int) -> str:
    """Role `speaker`, a playback on `card` through ALSA on machine `bench`,
    on sample clock `flex`, playing from `source_dir` under `cap_mb` with no
    quiet hours; its level control `PCM` 1, and `PCM` 0 held at 0 mB. Appended
    to `lab_toml`'s text, which holds the machine.
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
        quiet_hours = []
        [roles.speaker.device.level]
        element = "PCM"
        index = 1
        [[roles.speaker.device.held]]
        element = "PCM"
        index = 0
        volume_mb = 0
        """)


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
