"""Reading a run's logs, for the tests."""

import json
from pathlib import Path
from typing import Any


def runner_log(run: Path) -> list[Any]:
    """Every entry of `<run>/logs/runner.log`, each a JSON object with `at`
    and `text` and nothing else.
    """
    entries = [json.loads(line) for line in (run / "logs" / "runner.log").read_text().splitlines()]
    for entry in entries:
        assert entry.keys() == {"at", "text"}, entry
        assert isinstance(entry["text"], str)
    return entries


LOG_KEYS = {"role", "process", "pid", "stream", "file", "seq"}
LAB_STREAMS = [
    ("log", "logs/runner.log"),
    ("stdout", "logs/lab.stdout.log"),
    ("stderr", "logs/lab.stderr.log"),
]


def check_logs(run: Path, sink_client: bool, unlisted: frozenset[str] = frozenset()) -> list[Any]:
    """`<run>/results.json`'s `logs`, held to what the run directory holds:

    - the `lab` process's three logs come first;
    - the in-run pointers and `unlisted` are exactly the files under
      `<run>/logs/`; `unlisted` are the logs of a process whose start the
      run's event sink refused, which its role killed;
    - each role's entry carries the `seq` of the journal's `process.started`
      line of that role and process that names the same file and pid;
    - a `pulse-client` is listed, each with an integer pid and both its
      streams, iff `sink_client`.
    """
    results = json.loads((run / "results.json").read_text())
    logs = results["logs"]
    for entry in logs:
        assert entry.keys() == LOG_KEYS, entry
    lab = logs[:3]
    assert [(e["role"], e["process"], e["seq"]) for e in lab] == [(None, "lab", None)] * 3
    assert [(e["stream"], e["file"]["path"], e["file"]["in_run"]) for e in lab] == [
        (stream, path, True) for stream, path in LAB_STREAMS
    ]
    assert len({e["pid"] for e in lab}) == 1 and isinstance(lab[0]["pid"], int)

    listed = sorted(e["file"]["path"] for e in logs if e["file"]["in_run"])
    present = sorted(
        p.relative_to(run).as_posix() for p in (run / "logs").rglob("*") if p.is_file()
    )
    assert not unlisted & set(listed)
    assert sorted([*listed, *unlisted]) == present

    journal = {
        line["seq"]: line
        for line in map(json.loads, (run / "journal.jsonl").read_text().splitlines())
    }
    for entry in logs[3:]:
        started = journal[entry["seq"]]
        assert started["event"] == "process.started" and started["role"] == entry["role"], started
        body = started["data"]
        assert (body["process"], body["pid"]) == (entry["process"], entry["pid"])
        [named] = [log for log in body["logs"] if log["stream"] == entry["stream"]]
        assert named["location"]["machine"] == entry["file"]["machine"]
        if entry["file"]["in_run"]:
            assert (
                Path(named["location"]["path"]).resolve() == (run / entry["file"]["path"]).resolve()
            )
        else:
            assert named["location"]["path"] == entry["file"]["path"]

    clients = [e for e in logs if e["process"] == "pulse-client"]
    if sink_client:
        assert clients
        by_pid: dict[int, set[str]] = {}
        for e in clients:
            assert isinstance(e["pid"], int) and not isinstance(e["pid"], bool)
            assert (run / e["file"]["path"]).is_file()
            by_pid.setdefault(e["pid"], set()).add(e["stream"])
        assert all(s == {"stdout", "stderr"} for s in by_pid.values()), by_pid
    else:
        assert clients == []
    return list(logs)


def runner_log_text(run: Path) -> str:
    """The texts of every entry of `<run>/logs/runner.log`, one after another."""
    return "\n".join(entry["text"] for entry in runner_log(run))
