"""The run directory, layout 1.

<run>/.gitignore          `*`: created first, so git ignores the whole run
<run>/manifest.json       rewritten whole, by a temporary file and a rename
<run>/journal.jsonl       every command, reply and event, appended
<run>/results.json        written last, by a temporary file and a rename
<run>/script/<file>       the script, the lab configuration and the notes,
<run>/lab/<file>          byte for byte as they were read
<run>/notes/<file>
<run>/<section>/<role>/   each role's data, under its kind's section
                          (`captures/<role>/`: what a capture role recorded)
<run>/logs/runner.log     the runner's own log
<run>/logs/<role>/        the output of the programs each role calls
<run>/alignment.json      written by `lab align`, by `lab assess` and by the
                          run's closing assessment, by a temporary file and a
                          rename
<run>/assessment.json     written by `lab assess` and by the run's closing
                          assessment, after `alignment.json`, by a temporary
                          file and a rename
"""

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import IO

from brenn_lab.data import JsonObject, to_text

LAYOUT = 1
GITIGNORE = "*\n"


def replace_json(run: Path, name: str, value: JsonObject) -> str:
    """Write `value` as `<run>/<name>` whole, in UTF-8: to `.<name>.tmp`,
    synced, then renamed over it; the SHA-256 of the bytes written. Whatever
    stands under the temporary name is unlinked first, a link and never its
    target, and the temporary file is then created anew; a link that appears
    under the name in between makes this raise, and no link is followed.
    """
    data = (to_text(value) + "\n").encode()
    temporary = run / f".{name}.tmp"
    temporary.unlink(missing_ok=True)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    temporary.replace(run / name)
    return hashlib.sha256(data).hexdigest()


def replace_report(run: Path, name: str, report: JsonObject) -> str:
    """Write the report `report` as `<run>/<name>` by `replace_json`, its
    `replaces` naming the report it replaces by harness, time written and
    SHA-256; the SHA-256 of what was written.
    """
    path = run / name
    if path.exists():
        previous = path.read_bytes()
        try:
            old = json.loads(previous)
            harness = old.get("harness") if isinstance(old, dict) else None
            written = old.get("written") if isinstance(old, dict) else None
        except json.JSONDecodeError, UnicodeDecodeError:
            harness = written = None
        report["replaces"] = {
            "harness": harness,
            "written": written,
            "sha256": hashlib.sha256(previous).hexdigest(),
        }
    return replace_json(run, name, report)


def resolve(run: Path, path: str) -> Path | None:
    """The file that `path`, relative to the run, names, if it lies inside the
    run; the inverse of `RunDir.in_run`.
    """
    if path == "" or Path(path).is_absolute():
        return None
    root = run.resolve()
    try:
        resolved = (root / path).resolve()
    except ValueError:
        return None
    if resolved == root or not resolved.is_relative_to(root):
        return None
    return resolved


class RunDir:
    """A run directory this process created, and the only place it writes."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._journal: IO[str] | None = None
        self._log: IO[str] | None = None

    @classmethod
    def create(cls, path: Path) -> RunDir:
        """Create the directory, which must not exist, and its `.gitignore`."""
        path.mkdir()
        (path / ".gitignore").write_text(GITIGNORE)
        return cls(path)

    def keep(self, section: str, name: str, data: bytes) -> str:
        """Write `data` as `<section>/<name>`; its path in the run."""
        if Path(name).name != name or name in ("", ".", ".."):
            raise ValueError(f"{name!r} is not a file name")
        (self.path / section).mkdir()
        (self.path / section / name).write_bytes(data)
        return f"{section}/{name}"

    def data(self, section: str, role: str) -> Path:
        """Where `role` writes its data, in its kind's section."""
        return self.path / section / role

    def logs(self, role: str) -> Path:
        return self.path / "logs" / role

    def in_run(self, path: Path) -> str | None:
        """`path` relative to the run, if it lies inside it."""
        if not path.is_absolute():
            return None
        resolved = path.resolve()
        root = self.path.resolve()
        if resolved == root or not resolved.is_relative_to(root):
            return None
        return resolved.relative_to(root).as_posix()

    def write_manifest(self, manifest: JsonObject) -> None:
        self._replace("manifest.json", manifest)

    def write_results(self, results: JsonObject) -> None:
        self._replace("results.json", results)

    def _replace(self, name: str, value: JsonObject) -> None:
        replace_json(self.path, name, value)

    def open_journal(self) -> None:
        self._journal = (self.path / "journal.jsonl").open("x", buffering=1)

    def journal(self, line: JsonObject) -> None:
        """Append one line to the journal, flushed before this returns."""
        assert self._journal is not None
        with self._lock:
            self._journal.write(to_text(line) + "\n")
            self._journal.flush()

    def note(self, text: str) -> None:
        """Append to `logs/runner.log`."""
        with self._lock:
            if self._log is None:
                (self.path / "logs").mkdir(exist_ok=True)
                self._log = (self.path / "logs" / "runner.log").open("a", buffering=1)
            self._log.write(text.rstrip("\n") + "\n")
            self._log.flush()

    def close(self) -> None:
        with self._lock:
            for f in (self._journal, self._log):
                if f is not None:
                    f.close()
            self._journal = None
            self._log = None
