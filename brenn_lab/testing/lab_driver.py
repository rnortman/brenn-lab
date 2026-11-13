"""What the tests that start the entry point, `//brenn_lab:lab`, as a program
share: the one-role lab configuration they write, the arguments they give it,
and the one document it prints.
"""

import json
import textwrap
from pathlib import Path
from typing import Any

from brenn_lab.testing.transcript_files import layout_of

MACHINE = "bench"


def lab_toml(program: str, card: str) -> str:
    """Machine `bench`, whose `pod_capture` is `program` as TOML, and role
    `mic`, a capture on `card` at 16 kHz, `s16_le`, with the six-name layout
    of the clean transcript.
    """
    return textwrap.dedent(f"""\
        schema = 1
        [machines.{MACHINE}.programs]
        pod_capture = {program}
        [roles.mic]
        kind = "capture"
        machine = "{MACHINE}"
        [roles.mic.device]
        card = {json.dumps(card)}
        rate_hz = 16000
        sample_format = "s16_le"
        layout = {json.dumps(layout_of("clean"))}
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
