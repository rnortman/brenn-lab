"""The harness's own revision, read from the runner's build stamp."""

import json
import re
from dataclasses import dataclass

from brenn_lab.data import JsonObject

REPOSITORY = "brenn-lab"

_COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


@dataclass(frozen=True)
class Revision:
    """The commit of the tree the runner was built from, and whether a tracked
    file differed from it. `commit` and `dirty` are null when that is not
    known, and `reason` says why.
    """

    commit: str | None
    dirty: bool | None
    source: str
    reason: str | None = None

    def encode(self) -> JsonObject:
        block: JsonObject = {
            "repository": REPOSITORY,
            "commit": self.commit,
            "dirty": self.dirty,
            "source": self.source,
        }
        if self.reason is not None:
            block["reason"] = self.reason
        return block


def parse(text: str) -> Revision:
    """The revision a build stamp states. A stamp that is none of the forms
    the build writes raises `ValueError`.
    """
    try:
        value = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"the build stamp is not JSON: {e}") from e
    if not (
        isinstance(value, dict)
        and value.keys() == {"commit", "dirty"}
        and all(isinstance(v, str) for v in value.values())
    ):
        raise ValueError(f"the build stamp is not a commit and a dirty flag: {text!r}")
    commit, dirty = value["commit"], value["dirty"]
    if (commit, dirty) == ("", ""):
        return Revision(None, None, "unknown", "the build ran no workspace status command")
    if (commit, dirty) == ("unknown", "unknown"):
        return Revision(
            None,
            None,
            "unknown",
            "the workspace the runner was built from was not a git checkout, or git failed",
        )
    if _COMMIT.fullmatch(commit) and dirty in ("true", "false"):
        return Revision(commit, dirty == "true", "git")
    raise ValueError(f"the build stamp states no revision: {text!r}")
