"""What a run whose role raised on a script's command holds, for the runner's
tests with either kind of role.
"""

import json
from pathlib import Path
from typing import Any

from brenn_lab.testing.run_logs import runner_log_text

# The message of the error the tests' replacement of the ledger's event sink
# raises, which the role's own error carries as its cause.
SINK_MESSAGE = "the ledger refused the event"

# Where a role's raise points, in a run on the tests' machine.
RUNNER_LOG = {"machine": "bench", "path": "logs/runner.log", "in_run": True}


def check_role_raised(out: Path, results: Any, role: str, sent: set[str]) -> None:
    """The run in `out`, whose `results.json` is `results`, failed on `role`
    raising instead of replying to one of the commands in `sent`, the
    runner's `release` among them, and the role was released.
    """
    assert results["outcome"] == "failed"
    assert results["completed"] is False
    failure = results["failure"]
    assert failure["command"] in sent, failure
    assert failure["detail"].startswith("RuntimeError: ")
    assert failure == {
        "where": "role",
        "reason": "role_raised",
        "detail": failure["detail"],
        "role": role,
        "command": failure["command"],
        "key": None,
        "data": {},
        "log": RUNNER_LOG,
    }
    assert results["roles"][role]["failures"][0] == {
        "command": failure["command"],
        "reason": "role_raised",
        "detail": failure["detail"],
        "data": {},
        "log": RUNNER_LOG,
    }
    entries = [json.loads(t) for t in (out / "journal.jsonl").read_text().splitlines()]
    at_release = failure["command"] == "release"
    by = "runner" if at_release else "script"
    sent_lines = [e for e in entries if e.get("by") == by and e["command"] == failure["command"]]
    # The command's line, and no reply after it.
    assert "ok" not in sent_lines[-1]
    if at_release:
        assert sent_lines[-1] == entries[-1]
    else:
        assert [(e["command"], e["by"], "ok" in e) for e in entries[-2:]] == [
            ("release", "runner", False),
            ("release", "runner", True),
        ]
    log = runner_log_text(out)
    assert f"role {role} raised on {failure['command']}:" in log
    assert "Traceback" in log and SINK_MESSAGE in log
