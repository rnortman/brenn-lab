"""The stand-in sound server run as a program, for tests of the sink binding.

`StandInServer` starts `pulse-stand-in` with the sinks a test describes and
listens at `<runtime>/pulse/native`, where the binding looks when
`XDG_RUNTIME_DIR` is `runtime`. `runtime` is a fresh directory under `/tmp`:
a Unix socket's path is at most 108 bytes, and Bazel's test directories are
longer.
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import IO

from brenn_lab import pinned
from brenn_lab.data import JsonObject, JsonValue

STAND_IN = "_main/brenn_lab/playback/pulse_client/pulse-stand-in"


def sink(name: str, description: str | None = None, **fields: JsonValue) -> JsonObject:
    """A stand-in sink: 48 kHz, two channels, `s32le`, decibel volume at 0 dB,
    unless `fields` say otherwise.
    """
    described: JsonObject = {
        "name": name,
        "description": description,
        "rate_hz": 48000,
        "channels": 2,
        "format": "s32le",
    }
    described.update(fields)
    return described


class StandInServer:
    """A running stand-in serving `sinks`, playing streams at `speed` times
    real time.
    """

    def __init__(self, sinks: list[JsonObject], speed: float = 1.0) -> None:
        self.runtime = Path(tempfile.mkdtemp(prefix="pulse-", dir="/tmp"))
        (self.runtime / "pulse").mkdir()
        self.frames_path = self.runtime / "frames"
        program = pinned.locate(pinned.runner_runfiles(), STAND_IN)
        self._process = subprocess.Popen(
            [
                str(program),
                "--socket",
                str(self.runtime / "pulse" / "native"),
                "--sinks",
                json.dumps(sinks),
                "--speed",
                str(speed),
                "--frames",
                str(self.frames_path),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        assert self._process.stdin is not None and self._process.stdout is not None
        self._stdin: IO[str] = self._process.stdin
        self._stdout: IO[str] = self._process.stdout
        ready = self._stdout.readline()
        if not ready or json.loads(ready) != {"ready": True}:
            self.close()
            raise RuntimeError(f"the stand-in did not start: {ready!r}")

    def instruct(self, op: str, **fields: JsonValue) -> JsonObject:
        """Send one instruction and return its answer. Raises when the
        stand-in refuses it.
        """
        self._stdin.write(json.dumps({"op": op, **fields}) + "\n")
        self._stdin.flush()
        line = self._stdout.readline()
        if not line:
            raise RuntimeError(f"the stand-in exited during {op!r}")
        answer = json.loads(line)
        assert isinstance(answer, dict)
        if answer.get("ok") is not True:
            raise RuntimeError(f"the stand-in refused {op!r}: {answer}")
        return answer

    def state(self) -> JsonObject:
        """The sinks' volumes and mute, the open streams and the streams made."""
        return self.instruct("state")

    def sink_state(self, name: str) -> JsonObject:
        """One sink's volumes and mute as the stand-in holds them."""
        sinks = self.state()["sinks"]
        assert isinstance(sinks, list)
        for s in sinks:
            assert isinstance(s, dict)
            if s["name"] == name:
                return s
        raise KeyError(name)

    def streams(self) -> list[JsonObject]:
        """The open streams as the stand-in holds them."""
        streams = self.state()["streams"]
        assert isinstance(streams, list)
        return [s for s in streams if isinstance(s, dict)]

    def frames(self) -> bytes:
        """Every frame the stand-in was handed, in order."""
        return self.frames_path.read_bytes() if self.frames_path.exists() else b""

    def close(self) -> None:
        """Stop the stand-in and remove its directory."""
        try:
            self._stdin.close()
        except OSError:
            pass
        try:
            self._process.wait(5)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()
        self._stdout.close()
        shutil.rmtree(self.runtime, ignore_errors=True)
