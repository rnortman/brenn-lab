"""Resolving a path the manifest gives, relative to the run, to a file in it,
and writing a JSON file whole into the run.
"""

import hashlib
import json
from pathlib import Path

import pytest

from brenn_lab import rundir
from brenn_lab.data import JsonObject


@pytest.fixture
def run(tmp_path: Path) -> Path:
    run = tmp_path / "run"
    (run / "captures" / "mic").mkdir(parents=True)
    (run / "captures" / "mic" / "0001.framelog").write_bytes(b"log")
    (tmp_path / "outside.framelog").write_bytes(b"not the run's")
    return run


def test_a_file_in_the_run_resolves_to_itself(run: Path) -> None:
    got = rundir.resolve(run, "captures/mic/0001.framelog")
    assert got == (run / "captures" / "mic" / "0001.framelog").resolve()


def test_a_symlink_in_the_run_to_a_file_outside_it_does_not_resolve(run: Path) -> None:
    (run / "captures" / "mic" / "link").symlink_to(run.parent / "outside.framelog")
    assert rundir.resolve(run, "captures/mic/link") is None


def test_a_symlink_in_the_run_to_a_file_inside_it_resolves_to_the_target(run: Path) -> None:
    (run / "captures" / "mic" / "link").symlink_to("0001.framelog")
    got = rundir.resolve(run, "captures/mic/link")
    assert got == (run / "captures" / "mic" / "0001.framelog").resolve()


@pytest.mark.parametrize("path", [".", "captures/..", "captures/mic/../.."])
def test_a_path_that_names_the_run_itself_does_not_resolve(run: Path, path: str) -> None:
    assert rundir.resolve(run, path) is None


@pytest.mark.parametrize("path", ["", "../outside.framelog", "/abs/0001.framelog"])
def test_a_path_that_leaves_the_run_lexically_does_not_resolve(run: Path, path: str) -> None:
    assert rundir.resolve(run, path) is None


def written(run: Path, name: str) -> object:
    """`<run>/<name>` as JSON, after asserting no temporary name remains."""
    assert not (run / f".{name}.tmp").is_symlink()
    assert not (run / f".{name}.tmp").exists()
    return json.loads((run / name).read_text())


def test_replace_json_does_not_write_through_a_link_under_its_temporary_name(run: Path) -> None:
    outside = run.parent / "outside.framelog"
    (run / ".report.json.tmp").symlink_to(outside)
    rundir.replace_json(run, "report.json", {"a": 1})
    assert outside.read_bytes() == b"not the run's"
    assert written(run, "report.json") == {"a": 1}


def test_replace_json_does_not_create_the_target_of_a_dangling_link(run: Path) -> None:
    outside = run.parent / "nowhere.json"
    (run / ".report.json.tmp").symlink_to(outside)
    rundir.replace_json(run, "report.json", {"a": 1})
    assert not outside.exists()
    assert written(run, "report.json") == {"a": 1}


def test_replace_json_replaces_a_stale_temporary_file(run: Path) -> None:
    (run / ".report.json.tmp").write_text("left by a killed writer, and longer than the value")
    rundir.replace_json(run, "report.json", {"a": 1})
    assert written(run, "report.json") == {"a": 1}


def test_replace_json_replaces_a_link_at_the_final_name_and_not_its_target(run: Path) -> None:
    outside = run.parent / "outside.framelog"
    (run / "report.json").symlink_to(outside)
    rundir.replace_json(run, "report.json", {"a": 1})
    assert outside.read_bytes() == b"not the run's"
    assert not (run / "report.json").is_symlink()
    assert written(run, "report.json") == {"a": 1}


def test_replace_json_does_not_follow_a_link_that_appears_after_the_unlink(
    run: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = run.parent / "outside.framelog"
    temporary = run / ".report.json.tmp"
    unlink = Path.unlink

    def unlink_then_plant(self: Path, missing_ok: bool = False) -> None:
        unlink(self, missing_ok=missing_ok)
        if self == temporary:
            temporary.symlink_to(outside)

    monkeypatch.setattr(Path, "unlink", unlink_then_plant)
    with pytest.raises(FileExistsError):
        rundir.replace_json(run, "report.json", {"a": 1})
    assert outside.read_bytes() == b"not the run's"
    assert not (run / "report.json").exists()


def test_replace_report_returns_the_hash_of_what_it_wrote_and_names_what_it_replaced(
    run: Path,
) -> None:
    first = rundir.replace_report(run, "report.json", {"harness": "h1", "written": "t1"})
    first_bytes = (run / "report.json").read_bytes()
    assert first == hashlib.sha256(first_bytes).hexdigest()
    report: JsonObject = {"harness": "h2", "written": "t2", "name": "é"}
    second = rundir.replace_report(run, "report.json", report)
    assert second == hashlib.sha256((run / "report.json").read_bytes()).hexdigest()
    assert written(run, "report.json") == {
        **report,
        "replaces": {"harness": "h1", "written": "t1", "sha256": first},
    }


def test_writing_the_manifest_twice_leaves_one_file_and_no_temporary(tmp_path: Path) -> None:
    made = rundir.RunDir.create(tmp_path / "run")
    made.write_manifest({"n": 1})
    made.write_manifest({"n": 2})
    assert written(made.path, "manifest.json") == {"n": 2}
    assert sorted(p.name for p in made.path.iterdir()) == [".gitignore", "manifest.json"]
