"""A stand-in for brenn-pod's recorder, for tests: it replays a transcript.

It takes the recorder's command line. `--card <name>` selects the transcript
`transcripts/recorded/<name>.jsonl` or `transcripts/authored/<name>.jsonl`, a
file of lines a recorder printed; the stand-in prints them and builds none of
its own. It creates `--out` with a few bytes nothing reads.

It prints every line at once except a final `capture_stopped`, which it prints
when it receives SIGTERM or SIGINT, then exits 0 after `capture_stopped` and 1
after `capture_failed`. With neither as the last line it waits for the signal
and exits 1, printing nothing more. `authored/<name>.replay.toml` may change
when it prints and how it exits:

- `held = <n>`: the last `n` lines are printed on the signal, the rest at once;
- `unasked_exit = <status>`: exit with this status once the lines are printed,
  without waiting for a signal.

A command line it cannot use is refused as the recorder refuses one: exit
status 2, nothing on stdout, no file created, the reason and a usage line on
stderr.
"""

import json
import signal
import sys
import tomllib
from pathlib import Path

from brenn_lab.testing import transcript_files

FLAGS = ("--card", "--rate", "--format", "--layout", "--pod-id", "--out")
USAGE = (
    "usage: stand-in --card <TRANSCRIPT> --rate <HZ> --format <FORMAT> "
    "--layout <a,b,c,...> --pod-id <NAME> --out <PATH>"
)
REPLAY_KEYS = frozenset({"held", "unasked_exit"})
STOP_SIGNALS = {signal.SIGTERM, signal.SIGINT}


class Refused(Exception):
    """A command line the stand-in cannot use."""


def parse_args(argv: list[str]) -> dict[str, str]:
    args: dict[str, str] = {}
    i = 0
    while i < len(argv):
        flag = argv[i]
        if flag not in FLAGS:
            raise Refused(f"unknown argument {flag!r}")
        if flag in args:
            raise Refused(f"{flag} given twice")
        if i + 1 >= len(argv):
            raise Refused(f"{flag} needs a value")
        args[flag] = argv[i + 1]
        i += 2
    missing = [f for f in FLAGS if f not in args]
    if missing:
        raise Refused(f"missing {', '.join(missing)}")
    return args


def find_transcript(name: str) -> Path:
    path = transcript_files.find(name)
    if path is None:
        raise Refused(f"--card {name!r}: no transcript of that name")
    return path


def read_replay(transcript: Path) -> dict[str, int]:
    path = transcript.with_name(transcript.name.removesuffix(".jsonl") + ".replay.toml")
    if not path.exists():
        return {}
    replay = tomllib.loads(path.read_text())
    unknown = replay.keys() - REPLAY_KEYS
    if unknown:
        raise Refused(f"{path.name}: unknown keys {sorted(unknown)}")
    for key, value in replay.items():
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise Refused(f"{path.name}: {key} must be a non-negative integer")
    return replay


def event_of(line: bytes) -> str | None:
    try:
        value = json.loads(line)
    except ValueError:
        return None
    event = value.get("event") if isinstance(value, dict) else None
    return event if isinstance(event, str) else None


def main(argv: list[str]) -> int:
    signal.pthread_sigmask(signal.SIG_BLOCK, STOP_SIGNALS)
    try:
        args = parse_args(argv)
        transcript = find_transcript(args["--card"])
        replay = read_replay(transcript)
        out = Path(args["--out"])
        if out.exists() or out.is_symlink():
            raise Refused(f"--out {str(out)!r} exists")
    except Refused as e:
        sys.stderr.write(f"stand-in: {e}\n{USAGE}\n")
        return 2
    out.write_bytes(b"stand-in: not a frame log\n")

    lines = transcript.read_bytes().splitlines(keepends=True)
    last = event_of(lines[-1]) if lines else None
    held = replay.get("held", 1 if last == "capture_stopped" else 0)
    if held > len(lines):
        sys.stderr.write(f"stand-in: held = {held} is more than the transcript's lines\n")
        return 2
    now, later = lines[: len(lines) - held], lines[len(lines) - held :]

    stdout = sys.stdout.buffer
    for line in now:
        stdout.write(line)
        stdout.flush()
    if "unasked_exit" in replay:
        return replay["unasked_exit"]
    if not later and last in ("capture_stopped", "capture_failed"):
        return 0 if last == "capture_stopped" else 1
    signal.sigwait(STOP_SIGNALS)
    for line in later:
        stdout.write(line)
        stdout.flush()
    return 0 if later and last == "capture_stopped" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
