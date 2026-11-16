"""Reading the runner's build stamp."""

import json

import pytest

from brenn_lab import buildinfo

COMMIT = "0123456789abcdef0123456789abcdef01234567"
OTHER = "89abcdef0123456789abcdef0123456789abcdef"


def stamp(commit: str, dirty: str) -> str:
    return json.dumps({"commit": commit, "dirty": dirty})


def with_builder(builder: object) -> str:
    return json.dumps({"commit": COMMIT, "dirty": "false", "builder": builder})


def built_by(repository: str, commit: str, dirty: str) -> str:
    return with_builder({"repository": repository, "commit": commit, "dirty": dirty})


@pytest.mark.parametrize(("dirty", "value"), [("true", True), ("false", False)])
def test_a_git_checkout(dirty: str, value: bool) -> None:
    parsed = buildinfo.parse(stamp(COMMIT, dirty))
    assert parsed == buildinfo.Stamp(buildinfo.Revision("brenn-lab", COMMIT, value, "git"), None)
    assert parsed.harness.encode() == {
        "repository": "brenn-lab",
        "commit": COMMIT,
        "dirty": value,
        "source": "git",
    }


@pytest.mark.parametrize(("commit", "dirty"), [("unknown", "unknown"), ("", "")])
def test_an_unknown_revision_says_why(commit: str, dirty: str) -> None:
    parsed = buildinfo.parse(stamp(commit, dirty))
    revision = parsed.harness
    assert (revision.commit, revision.dirty, revision.source) == (None, None, "unknown")
    block = revision.encode()
    assert block["source"] == "unknown" and isinstance(block["reason"], str) and block["reason"]
    assert parsed.builder is None


@pytest.mark.parametrize(("dirty", "value"), [("true", True), ("false", False)])
def test_a_stamp_with_a_builder_states_both_revisions(dirty: str, value: bool) -> None:
    parsed = buildinfo.parse(built_by("brenn-pod-experiments", OTHER, dirty))
    assert parsed.harness == buildinfo.Revision("brenn-lab", COMMIT, False, "git")
    assert parsed.builder == buildinfo.Revision("brenn-pod-experiments", OTHER, value, "git")
    assert parsed.builder is not None
    assert parsed.builder.encode() == {
        "repository": "brenn-pod-experiments",
        "commit": OTHER,
        "dirty": value,
        "source": "git",
    }


def test_a_builder_that_was_not_a_git_checkout_says_why() -> None:
    builder = buildinfo.parse(built_by("brenn-pod-experiments", "unknown", "unknown")).builder
    assert builder is not None
    block = builder.encode()
    assert (block["repository"], block["commit"], block["dirty"], block["source"]) == (
        "brenn-pod-experiments",
        None,
        None,
        "unknown",
    )
    assert isinstance(block["reason"], str) and block["reason"]


def test_a_builder_beside_an_unknown_harness() -> None:
    text = json.dumps(
        {
            "commit": "unknown",
            "dirty": "unknown",
            "builder": {"repository": "x", "commit": OTHER, "dirty": "true"},
        }
    )
    parsed = buildinfo.parse(text)
    assert parsed.harness.source == "unknown"
    assert parsed.builder == buildinfo.Revision("x", OTHER, True, "git")


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


@pytest.mark.parametrize(
    "text",
    [
        with_builder(None),
        with_builder("brenn-pod-experiments"),
        with_builder({"repository": "r", "commit": OTHER}),
        with_builder({"repository": "r", "commit": OTHER, "dirty": "false", "extra": ""}),
        with_builder({"repository": "r", "commit": OTHER, "dirty": False}),
        built_by("", OTHER, "false"),
        built_by("-leading-dash", OTHER, "false"),
        built_by("has space", OTHER, "false"),
        built_by('quote"d', OTHER, "false"),
        built_by("slash/ed", OTHER, "false"),
        built_by("r", "", ""),
        built_by("r", OTHER[:39], "false"),
        built_by("r", OTHER, "maybe"),
        built_by("r", OTHER, "unknown"),
        built_by("r", "unknown", "false"),
    ],
)
def test_a_malformed_builder_is_refused(text: str) -> None:
    with pytest.raises(ValueError):
        buildinfo.parse(text)
