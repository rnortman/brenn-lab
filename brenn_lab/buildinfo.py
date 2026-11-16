"""The revisions the runner's build stamp states: the harness's own, and the
revision of the tree that built the runner when that is another repository.
"""

import json
import re
from dataclasses import dataclass

from brenn_lab.data import JsonObject

REPOSITORY = "brenn-lab"

_COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


@dataclass(frozen=True)
class Revision:
    """The commit of `repository`'s tree the runner was built from, and
    whether a tracked file differed from it. `commit` and `dirty` are null
    when that is not known, and `reason` says why.
    """

    repository: str
    commit: str | None
    dirty: bool | None
    source: str
    reason: str | None = None

    def encode(self) -> JsonObject:
        block: JsonObject = {
            "repository": self.repository,
            "commit": self.commit,
            "dirty": self.dirty,
            "source": self.source,
        }
        if self.reason is not None:
            block["reason"] = self.reason
        return block


@dataclass(frozen=True)
class Stamp:
    """What a build stamp states: the harness's revision, and the builder's
    when a repository other than the harness built the runner.
    """

    harness: Revision
    builder: Revision | None


def parse(text: str) -> Stamp:
    """The revisions a build stamp states. A stamp that is none of the forms
    the build writes raises `ValueError`.
    """
    try:
        value = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"the build stamp is not JSON: {e}") from e
    if not (
        isinstance(value, dict)
        and value.keys() in ({"commit", "dirty"}, {"commit", "dirty", "builder"})
        and isinstance(value["commit"], str)
        and isinstance(value["dirty"], str)
    ):
        raise ValueError(f"the build stamp is not a commit and a dirty flag: {text!r}")
    harness = _harness(value["commit"], value["dirty"], text)
    if "builder" not in value:
        return Stamp(harness, None)
    return Stamp(harness, _builder(value["builder"], text))


def _harness(commit: str, dirty: str, text: str) -> Revision:
    if (commit, dirty) == ("", ""):
        return Revision(
            REPOSITORY, None, None, "unknown", "the build ran no workspace status command"
        )
    if (commit, dirty) == ("unknown", "unknown"):
        return Revision(
            REPOSITORY,
            None,
            None,
            "unknown",
            "the workspace the runner was built from was not a git checkout, or git failed",
        )
    found = _git(REPOSITORY, commit, dirty)
    if found is None:
        raise ValueError(f"the build stamp states no revision: {text!r}")
    return found


def _builder(value: object, text: str) -> Revision:
    if not (
        isinstance(value, dict)
        and value.keys() == {"repository", "commit", "dirty"}
        and all(isinstance(v, str) for v in value.values())
    ):
        raise ValueError(f"the build stamp's builder is not a repository and a revision: {text!r}")
    repository, commit, dirty = value["repository"], value["commit"], value["dirty"]
    if not _REPOSITORY.fullmatch(repository):
        raise ValueError(f"the build stamp's builder names no repository: {text!r}")
    if (commit, dirty) == ("unknown", "unknown"):
        return Revision(
            repository,
            None,
            None,
            "unknown",
            "the tree that built the runner was not a git checkout, or git failed",
        )
    found = _git(repository, commit, dirty)
    if found is None:
        raise ValueError(f"the build stamp's builder states no revision: {text!r}")
    return found


def _git(repository: str, commit: str, dirty: str) -> Revision | None:
    if _COMMIT.fullmatch(commit) and dirty in ("true", "false"):
        return Revision(repository, commit, dirty == "true", "git")
    return None
