"""Reading the runner's build stamp."""

import json

import pytest

from brenn_lab import buildinfo

COMMIT = "0123456789abcdef0123456789abcdef01234567"


def stamp(commit: str, dirty: str) -> str:
    return json.dumps({"commit": commit, "dirty": dirty})


@pytest.mark.parametrize(("dirty", "value"), [("true", True), ("false", False)])
def test_a_git_checkout(dirty: str, value: bool) -> None:
    revision = buildinfo.parse(stamp(COMMIT, dirty))
    assert revision == buildinfo.Revision(COMMIT, value, "git")
    assert revision.encode() == {
        "repository": "brenn-lab",
        "commit": COMMIT,
        "dirty": value,
        "source": "git",
    }


@pytest.mark.parametrize(("commit", "dirty"), [("unknown", "unknown"), ("", "")])
def test_an_unknown_revision_says_why(commit: str, dirty: str) -> None:
    revision = buildinfo.parse(stamp(commit, dirty))
    assert (revision.commit, revision.dirty, revision.source) == (None, None, "unknown")
    block = revision.encode()
    assert block["source"] == "unknown" and isinstance(block["reason"], str) and block["reason"]


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        json.dumps({"commit": COMMIT}),
        json.dumps({"commit": COMMIT, "dirty": "false", "extra": ""}),
        json.dumps({"commit": COMMIT, "dirty": False}),
        stamp(COMMIT[:39], "false"),
        stamp(COMMIT.upper(), "false"),
        stamp(COMMIT, "maybe"),
        stamp(COMMIT, "unknown"),
        stamp("unknown", "false"),
    ],
)
def test_anything_else_is_refused(text: str) -> None:
    with pytest.raises(ValueError):
        buildinfo.parse(text)
