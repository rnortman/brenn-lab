"""Aligning whole synthetic runs through the stand-in reader. Every expected
answer is a parameter of the scene, never a second search.
"""

import errno
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from brenn_lab.align import capture, report
from brenn_lab.data import Failure, JsonObject
from brenn_lab.marker import Carried
from brenn_lab.rundir import LAYOUT
from brenn_lab.testing import scenes
from brenn_lab.testing.scenes import Capture, Extra, Loss, Play

READER = os.path.abspath(os.environ["EXPORT_STAND_IN"])
HARNESS: JsonObject = {
    "repository": "brenn-lab",
    "commit": "c0ffee",
    "dirty": False,
    "source": "git",
}


def align(run: Path) -> dict[str, Any]:
    done = report.align(run, READER, False, HARNESS)
    assert not isinstance(done, Failure), done
    written: dict[str, Any] = json.loads((run / report.REPORT).read_text())
    assert written == json.loads(json.dumps(done.report))
    assert done.sha256 == hashlib.sha256((run / report.REPORT).read_bytes()).hexdigest()
    assert not (run / ".alignment.tmp").exists()
    assert not (run / ".alignment.json.tmp").exists()
    return written


def channel(
    done: dict[str, Any], role: str, play: int, name: str = "raw_mic_0", capture: str = "mic"
) -> dict[str, Any]:
    [pair] = [
        p
        for p in done["pairs"]
        if p["play"] == {"role": role, "play": play} and p["capture"]["role"] == capture
    ]
    found: list[dict[str, Any]] = [c for c in pair["channels"] if c["channel"] == name]
    [one] = found
    return one


def carried(built: scenes.Built, role: str, play: int) -> Carried:
    c = built.carried[(role, play)]
    assert c is not None
    return c


def test_one_play_one_capture(tmp_path: Path) -> None:
    play = Play("speaker", 1, 3, at=5000.25)
    built = scenes.build(tmp_path / "run", [play])
    done = align(built.run)
    got = channel(done, "speaker", 1)
    assert got["start"]["outcome"] == "found" and got["end"]["outcome"] == "found"
    assert got["start"]["instances"] == 1
    assert got["start"]["score"] > 0.9
    assert got["offset_frames"] == pytest.approx(5000.25, abs=0.1)
    assert got["drift_ppm"] == pytest.approx(0, abs=1)
    assert got["ratio_basis"] == "measured"
    assert got["drift_unmeasured"] is None
    c = carried(built, "speaker", 1)
    expected = scenes.midpoint(play, Capture(), c, "start")
    assert got["start"]["midpoint_frame"] == pytest.approx(expected, abs=0.1)
    assert done["plays"][0]["state"] == "marked"
    assert done["plays"][0]["marker"]["member"] == 3
    assert done["captures"][0]["exported_frames"] == Capture().frames
    assert done["captures"][0]["holes"] == []
    assert done["summary"] == {
        "plays": 1,
        "marked": 1,
        "located": 1,
        "captures": 1,
        "marker_failures": 0,
        "read_failures": 0,
    }
    assert done["clock_readings"] == "not_used"
    assert done["marker"]["threshold"] == 0.25
    assert done["harness"] == HARNESS
    assert done["reader"] == {"program": "frame-log", "pinned": False, "path": READER}
    assert done["results_read"] is True
    assert done["replaces"] is None
    assert done["failures"] == []
    assert report.ok(done)


def test_a_48_khz_source_in_a_16_khz_capture_with_drift(tmp_path: Path) -> None:
    play = Play(
        "speaker", 1, 9, at=3000.5, rate_hz=48000, drift_ppm=120.0, content_frames=5 * 48000
    )
    capture = Capture(frames=7 * 16000 + 8000)
    built = scenes.build(tmp_path / "run", [play], [capture])
    got = channel(align(built.run), "speaker", 1)
    assert got["ratio_basis"] == "measured"
    assert got["drift_ppm"] == pytest.approx(120.0, abs=2)
    assert got["ratio"] == pytest.approx(scenes.ratio(play, capture), rel=2e-6)
    assert got["offset_frames"] == pytest.approx(3000.5, abs=0.2)


def test_channels_of_one_capture_delayed_differently_are_located_apart(tmp_path: Path) -> None:
    play = Play("speaker", 1, 2, at=4000.0)
    layout = ("raw_mic_0", "raw_mic_1", "raw_mic_2")
    delays = (0.0, 3.0, -1.5)
    capture = Capture(layout=layout, delays=delays)
    done = align(scenes.build(tmp_path / "run", [play], [capture]).run)
    for name, delay in zip(layout, delays, strict=True):
        got = channel(done, "speaker", 1, name)
        assert got["start"]["outcome"] == "found" and got["end"]["outcome"] == "found", got
        assert got["offset_frames"] == pytest.approx(play.at + delay, abs=0.1)


def test_delays_not_one_per_channel_are_refused(tmp_path: Path) -> None:
    capture = Capture(layout=("raw_mic_0", "raw_mic_1"), delays=(1.0,))
    with pytest.raises(ValueError):
        scenes.build(tmp_path / "run", [Play("speaker", 1, 2, at=4000.0)], [capture])


TWO_ROLES = [
    Play("left", 1, 4, at=4000.0, content_frames=48000),
    Play("right", 1, 5, at=8000.0, level=0.316, drift_ppm=150.0, content_frames=48000),
]


def test_two_plays_on_two_roles_overlapping_are_each_attributed(tmp_path: Path) -> None:
    capture = Capture(frames=7 * 16000)
    built = scenes.build(tmp_path / "run", TWO_ROLES, [capture])
    done = align(built.run)
    for play in TWO_ROLES:
        got = channel(done, play.role, play.play)
        assert got["start"]["outcome"] == "found", got
        assert got["end"]["outcome"] == "found", got
        assert got["offset_frames"] == pytest.approx(play.at, abs=0.5)
        assert got["drift_ppm"] == pytest.approx(play.drift_ppm, abs=5)
    assert done["summary"]["located"] == 2

    # The clock readings in the manifest are swapped between the two plays:
    # nothing in the report moves.
    manifest = json.loads((built.run / "manifest.json").read_text())
    left = manifest["roles"]["left"]["sources"][0]
    right = manifest["roles"]["right"]["sources"][0]
    left["first"], right["first"] = right["first"], left["first"]
    (built.run / "manifest.json").write_text(json.dumps(manifest))
    again = align(built.run)
    for key in ("written", "replaces"):
        del done[key], again[key]
    assert again == done


def test_a_play_of_an_unknown_output_kind_is_aligned_as_any_other(tmp_path: Path) -> None:
    def aligned(name: str, output: str) -> dict[str, Any]:
        play = Play("speaker", 1, 3, at=5000.0, ended="stopped", stopped_by="runner", output=output)
        done = align(scenes.build(tmp_path / name, [play]).run)
        for key in ("written", "replaces"):
            del done[key]
        return done

    known = aligned("known", "alsa")
    assert channel(known, "speaker", 1)["start"]["outcome"] == "found"
    assert aligned("unknown", "invented") == known


def twice(member_b: int = 2, role_b: str = "speaker", **b: Any) -> list[Play]:
    """The same marked file, member 2, played twice; the second's settings in `b`."""
    return [
        Play("speaker", 1, 2, at=2000.0),
        Play(role_b, 2, member_b, **{"at": 40000.0, **b}),
    ]


def outcomes(done: dict[str, Any], role: str, play: int) -> tuple[str, str]:
    got = channel(done, role, play)
    return got["start"]["outcome"], got["end"]["outcome"]


def test_the_same_file_twice_on_one_role_is_attributed_in_order(tmp_path: Path) -> None:
    built = scenes.build(tmp_path / "run", twice())
    done = align(built.run)
    for play in twice():
        got = channel(done, play.role, play.play)
        assert got["offset_frames"] == pytest.approx(play.at, abs=0.1)
        assert got["start"]["instances"] == 2


def test_the_same_file_twice_with_one_in_the_capture_is_a_count_mismatch(tmp_path: Path) -> None:
    built = scenes.build(tmp_path / "run", twice(at=200000.0))
    done = align(built.run)
    for n in (1, 2):
        got = channel(done, "speaker", n)
        assert got["start"]["outcome"] == "ambiguous"
        assert got["start"]["reason"] == "count_mismatch"
        assert got["offset_frames"] is None
    assert not report.ok(done)


def test_a_second_play_cut_before_its_end_marker(tmp_path: Path) -> None:
    probe = scenes.build(tmp_path / "probe", twice())
    end = carried(probe, "speaker", 2).end
    built = scenes.build(tmp_path / "run", twice(handed=end.frame))
    done = align(built.run)
    assert outcomes(done, "speaker", 1) == ("found", "found")
    assert outcomes(done, "speaker", 2) == ("found", "not_tried")
    assert channel(done, "speaker", 2)["end"]["reason"] == "not_handed"
    assert channel(done, "speaker", 2)["offset_frames"] == pytest.approx(40000.0, abs=0.1)
    assert channel(done, "speaker", 2)["ratio_basis"] == "nominal"

    # Without the results nothing says the second end marker was not handed.
    (built.run / "results.json").unlink()
    done = align(built.run)
    assert done["results_read"] is False
    assert outcomes(done, "speaker", 1) == ("found", "ambiguous")
    assert outcomes(done, "speaker", 2) == ("found", "ambiguous")
    assert channel(done, "speaker", 2)["end"]["reason"] == "count_mismatch"


def test_the_same_member_on_two_roles_is_shared_across_roles(tmp_path: Path) -> None:
    built = scenes.build(tmp_path / "run", twice(role_b="other"))
    done = align(built.run)
    for role, n in (("speaker", 1), ("other", 2)):
        got = channel(done, role, n)
        assert got["start"]["outcome"] == "ambiguous"
        assert got["start"]["reason"] == "shared_across_roles"


def test_an_extra_instance_exceeds_the_plays(tmp_path: Path) -> None:
    plays = [Play("speaker", 1, 6, at=2000.0)]
    built = scenes.build(tmp_path / "run", plays, extras=[Extra(6, "start", at=40000.0)])
    got = channel(align(built.run), "speaker", 1)
    assert got["start"]["outcome"] == "ambiguous"
    assert got["start"]["reason"] == "instances_exceed_plays"
    assert got["start"]["instances"] == 2
    assert got["end"]["outcome"] == "found"
    assert got["offset_frames"] is None


def test_a_capture_with_no_marker_in_it(tmp_path: Path) -> None:
    built = scenes.build(tmp_path / "run", [Play("speaker", 1, 1, at=500000.0)])
    done = align(built.run)
    got = channel(done, "speaker", 1)
    for which in ("start", "end"):
        assert got[which]["outcome"] == "not_found"
        assert got[which]["reason"] == "below_threshold"
        assert got[which]["midpoint_frame"] is None
        assert 0 < got[which]["score"] < 0.25
    assert got["offset_frames"] is None
    assert got["ratio"] is None
    assert done["summary"]["marker_failures"] == 2
    assert {f["marker"] for f in done["failures"]} == {"start", "end"}
    assert done["failures"][0]["capture"] == {"role": "mic", "capture": 1}
    assert not report.ok(done)


def test_a_hole_between_the_markers_leaves_drift_unmeasured(tmp_path: Path) -> None:
    play = Play("speaker", 1, 1, at=2000.0, content_frames=32000)
    capture = Capture(frames=80000, losses=(Loss(30000, 500, "clock_estimate"),))
    built = scenes.build(tmp_path / "run", [play], [capture])
    done = align(built.run)
    got = channel(done, "speaker", 1)
    assert got["drift_unmeasured"] == "capture_hole"
    assert got["ratio"] is None and got["drift_ppm"] is None
    assert got["ratio_basis"] == "nominal"
    c = carried(built, "speaker", 1)
    for which in ("start", "end"):
        expected = scenes.midpoint(play, capture, c, which)
        assert got[which]["midpoint_frame"] == pytest.approx(expected, abs=0.1)
    assert got["offset_frames"] == pytest.approx(2000.0, abs=0.1)
    assert done["captures"][0]["holes"] == [
        {"at_frame": 30000, "filled_frames": 500, "basis": "clock_estimate"}
    ]


def test_a_marker_in_a_hole(tmp_path: Path) -> None:
    play = Play("speaker", 1, 1, at=2000.0)
    capture = Capture(losses=(Loss(2000 + 8000 + 4000, 100, "measured"),))
    built = scenes.build(tmp_path / "run", [play], [capture])
    got = channel(align(built.run), "speaker", 1)
    assert got["start"]["outcome"] == "not_found"
    assert got["start"]["reason"] == "in_hole"
    assert got["end"]["outcome"] == "found"


def test_plays_with_no_marker_or_no_record_are_not_tried(tmp_path: Path) -> None:
    plays = [Play("speaker", 1, None, at=2000.0), Play("speaker", 2, 1, at=30000.0, record=False)]
    done = align(scenes.build(tmp_path / "run", plays).run)
    assert [p["state"] for p in done["plays"]] == ["no_marker", "no_marker_record"]
    assert [p["marker"] for p in done["plays"]] == [None, None]
    for pair in done["pairs"]:
        assert pair["tried"] is False
        assert pair["not_tried"] == "play_not_marked"
        assert pair["channels"] == []
    assert done["summary"]["marked"] == 0
    assert report.ok(done)


def test_a_play_cut_before_its_end_marker_is_not_handed(tmp_path: Path) -> None:
    probe = scenes.build(tmp_path / "probe", [Play("speaker", 1, 1, at=2000.0)])
    end = carried(probe, "speaker", 1).end
    built = scenes.build(
        tmp_path / "run", [Play("speaker", 1, 1, at=2000.0, handed=end.frame + end.frames - 1)]
    )
    done = align(built.run)
    got = channel(done, "speaker", 1)
    assert got["end"] == {
        "outcome": "not_tried",
        "reason": "not_handed",
        "score": None,
        "midpoint_frame": None,
        "instances": None,
    }
    assert got["start"]["outcome"] == "found"
    assert done["summary"]["located"] == 0
    assert done["summary"]["marker_failures"] == 0
    assert not report.ok(done)


def test_an_underrun_between_the_markers_leaves_drift_unmeasured(tmp_path: Path) -> None:
    built = scenes.build(tmp_path / "run", [Play("speaker", 1, 1, at=2000.0, underruns=(20000,))])
    got = channel(align(built.run), "speaker", 1)
    assert (got["start"]["outcome"], got["end"]["outcome"]) == ("found", "found")
    assert got["drift_unmeasured"] == "underrun"
    assert got["ratio"] is None


def test_an_altered_kept_copy_differs(tmp_path: Path) -> None:
    built = scenes.build(tmp_path / "run", [Play("speaker", 1, 1, at=2000.0)])
    copy = built.run / "sources" / "speaker" / "0001.wav"
    data = bytearray(copy.read_bytes())
    # The high byte of a sample inside the end marker.
    data[-2 * 6000 + 1] ^= 0x40
    copy.write_bytes(bytes(data))
    done = align(built.run)
    assert done["plays"][0]["state"] == "failed"
    assert done["plays"][0]["reason"] == "copy_differs"
    assert done["pairs"][0]["not_tried"] == "play_not_marked"
    assert done["failures"][0]["kind"] == "play"
    assert done["summary"]["read_failures"] == 1
    assert not report.ok(done)


def test_a_missing_kept_copy(tmp_path: Path) -> None:
    built = scenes.build(tmp_path / "run", [Play("speaker", 1, 1, at=2000.0)])
    (built.run / "sources" / "speaker" / "0001.wav").unlink()
    done = align(built.run)
    assert done["plays"][0]["reason"] == "copy_missing"


@pytest.mark.parametrize("path", ["../elsewhere.wav", "/abs/0001.wav", ""])
def test_a_kept_copy_or_capture_whose_path_leaves_the_run(tmp_path: Path, path: str) -> None:
    built = scenes.build(tmp_path / "run", [Play("speaker", 1, 1, at=2000.0)])
    manifest = json.loads((built.run / "manifest.json").read_text())
    manifest["roles"]["speaker"]["sources"][0]["file"]["path"] = path
    manifest["roles"]["mic"]["captures"][0]["file"]["path"] = path
    (built.run / "manifest.json").write_text(json.dumps(manifest))
    done = align(built.run)
    play, capture = done["plays"][0], done["captures"][0]
    assert (play["reason"], capture["failure"]["reason"]) == ("copy_missing", "not_in_run")
    for detail in (play["detail"], capture["failure"]["detail"]):
        assert detail.endswith("does not lie inside the run"), detail


def test_an_unknown_definition(tmp_path: Path) -> None:
    built = scenes.build(tmp_path / "run", [Play("speaker", 1, 1, at=2000.0)])
    manifest = json.loads((built.run / "manifest.json").read_text())
    manifest["roles"]["speaker"]["sources"][0]["source"]["marker"] = {"definition": "chirp-9"}
    (built.run / "manifest.json").write_text(json.dumps(manifest))
    done = align(built.run)
    assert done["plays"][0]["state"] == "failed"
    assert done["plays"][0]["reason"] == "unknown_definition"


def test_a_marker_record_with_keys_this_harness_does_not_know_is_read(tmp_path: Path) -> None:
    built = scenes.build(tmp_path / "run", [Play("speaker", 1, 1, at=2000.0)])
    manifest = json.loads((built.run / "manifest.json").read_text())
    record = manifest["roles"]["speaker"]["sources"][0]["source"]["marker"]
    record["level_dbfs"] = -6.0
    record["start"]["note"] = "a later key"
    (built.run / "manifest.json").write_text(json.dumps(manifest))
    done = align(built.run)
    assert done["plays"][0]["state"] == "marked"
    assert done["summary"]["located"] == 1


def test_a_marker_record_missing_a_field_refuses_the_run(tmp_path: Path) -> None:
    built = scenes.build(tmp_path / "run", [Play("speaker", 1, 1, at=2000.0)])
    manifest = json.loads((built.run / "manifest.json").read_text())
    del manifest["roles"]["speaker"]["sources"][0]["source"]["marker"]["end"]["frames"]
    (built.run / "manifest.json").write_text(json.dumps(manifest))
    refused = report.align(built.run, READER, False, HARNESS)
    assert isinstance(refused, Failure)
    assert refused.reason == "unreadable"
    assert "end" in refused.detail and "frames" in refused.detail, refused.detail
    assert not (built.run / report.REPORT).exists()


def test_a_capture_only_run_whose_results_have_no_sources(tmp_path: Path) -> None:
    built = scenes.build(tmp_path / "run", [])
    results = json.loads((built.run / "results.json").read_text())
    del results["sources"]
    (built.run / "results.json").write_text(json.dumps(results))
    done = align(built.run)
    assert done["plays"] == []
    assert done["captures"][0]["exported_frames"] == Capture().frames
    assert done["captures"][0]["failure"] is None
    assert report.ok(done)


def test_a_role_name_never_names_a_file(tmp_path: Path) -> None:
    """A capture role whose name climbs out of the run neither writes nor
    removes anything where its name points.
    """
    built = scenes.build(tmp_path / "run", [Play("speaker", 1, 1, at=2000.0)])
    manifest = json.loads((built.run / "manifest.json").read_text())
    manifest["roles"]["../../take"] = manifest["roles"].pop("mic")
    (built.run / "manifest.json").write_text(json.dumps(manifest))
    planted = tmp_path / "take-0001.wav"
    planted.write_bytes(b"a recording")
    done = align(built.run)
    assert planted.read_bytes() == b"a recording"
    assert done["captures"][0]["exported_frames"] == Capture().frames
    assert done["summary"]["located"] == 1


def test_a_refused_export_is_a_read_failure(tmp_path: Path) -> None:
    captures = [Capture(), Capture(role="far", refuse="error: overlapping segments")]
    built = scenes.build(tmp_path / "run", [Play("speaker", 1, 1, at=2000.0)], captures)
    done = align(built.run)
    far = done["captures"][1]
    assert far["failure"] == {"reason": "export_refused", "detail": "error: overlapping segments"}
    assert far["exported_frames"] is None
    assert channel(done, "speaker", 1)["start"]["outcome"] == "found"
    [pair] = [p for p in done["pairs"] if p["capture"]["role"] == "far"]
    assert pair["not_tried"] == "capture_failed"
    assert done["summary"]["located"] == 1
    assert done["summary"]["read_failures"] == 1
    assert not report.ok(done)


@pytest.mark.parametrize("member", [1, None])
def test_each_export_is_gone_before_the_next_capture_is_exported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, member: int | None
) -> None:
    """With a marked play the first capture is searched; with none it is read
    and not searched. Either way its export is removed before the second
    capture's is written.
    """
    captures = [Capture(), Capture(role="far")]
    built = scenes.build(tmp_path / "run", [Play("speaker", 1, member, at=2000.0)], captures)
    real = capture.read
    seen: list[list[str]] = []

    def watched(log: Path, out: Path, *args: Any) -> capture.Exported | Failure:
        seen.append(sorted(p.name for p in out.parent.iterdir()))
        return real(log, out, *args)

    monkeypatch.setattr(capture, "read", watched)
    done = align(built.run)
    assert seen == [[], []]
    assert [c["exported_frames"] for c in done["captures"]] == [Capture().frames] * 2
    assert done["summary"]["located"] == (0 if member is None else 1)


def test_every_channel_is_searched_and_reported_by_name(tmp_path: Path) -> None:
    capture = Capture(layout=("raw_mic_0", "asr_beam"), gains=(1.0, 0.0))
    built = scenes.build(tmp_path / "run", [Play("speaker", 1, 1, at=2000.0)], [capture])
    done = align(built.run)
    assert channel(done, "speaker", 1)["start"]["outcome"] == "found"
    assert channel(done, "speaker", 1, "asr_beam")["start"]["outcome"] == "not_found"
    assert done["summary"]["located"] == 1
    assert {f["channel"] for f in done["failures"]} == {"asr_beam"}
    assert report.ok(done)


def test_a_rerun_names_the_report_it_replaces_and_changes_nothing_else(tmp_path: Path) -> None:
    built = scenes.build(tmp_path / "run", [Play("speaker", 1, 1, at=2000.0)])
    before = {n: (built.run / n).read_bytes() for n in ("manifest.json", "results.json")}
    first = align(built.run)
    first_bytes = (built.run / report.REPORT).read_bytes()
    second = align(built.run)
    assert second["replaces"]["harness"] == first["harness"]
    assert second["replaces"]["written"] == first["written"]
    assert len(second["replaces"]["sha256"]) == 64
    assert second["replaces"]["sha256"] == hashlib.sha256(first_bytes).hexdigest()
    for name, data in before.items():
        assert (built.run / name).read_bytes() == data

    (built.run / report.REPORT).write_text("not json")
    third = align(built.run)
    assert third["replaces"]["harness"] is None
    assert third["replaces"]["written"] is None


class Watching:
    """An observer that keeps what it is shown, and refuses with `refuse`."""

    def __init__(self, refuse: Failure | None = None) -> None:
        self.refuse = refuse
        self.begun: list[tuple[JsonObject, JsonObject | None]] = []
        self.captures: list[report.CaptureRead] = []
        self.existed: list[bool] = []

    def begin(self, manifest: JsonObject, results: JsonObject | None) -> Failure | None:
        self.begun.append((manifest, results))
        return self.refuse

    def capture(self, read: report.CaptureRead) -> None:
        self.captures.append(read)
        self.existed.append(isinstance(read.read, capture.Exported) and read.read.path.is_file())


@pytest.mark.parametrize("results", [True, False])
def test_an_observer_is_shown_the_run_and_every_capture_while_its_export_stands(
    tmp_path: Path, results: bool
) -> None:
    captures = [
        Capture(layout=("raw_mic_0", "asr_beam")),
        Capture(capture=2, refuse="error: not a frame log"),
    ]
    built = scenes.build(
        tmp_path / "run", [Play("speaker", 1, 1, at=2000.0)], captures, results=results
    )
    watching = Watching()
    done = report.align(built.run, READER, False, HARNESS, watching)
    assert isinstance(done, report.Aligned), done
    manifest = json.loads((built.run / "manifest.json").read_text())
    expected = json.loads((built.run / "results.json").read_text()) if results else None
    assert watching.begun == [(manifest, expected)]
    assert [(r.role, r.capture) for r in watching.captures] == [("mic", 1), ("mic", 2)]
    first, second = watching.captures
    assert (first.rate_hz, first.sample_format, first.layout) == (
        16000,
        "s16_le",
        ("raw_mic_0", "asr_beam"),
    )
    assert first.file == manifest["roles"]["mic"]["captures"][0]["file"]
    assert isinstance(first.read, capture.Exported)
    assert isinstance(second.read, Failure) and second.read.reason == "export_refused"
    assert watching.existed == [True, False]
    summary = done.report["summary"]
    assert isinstance(summary, dict) and summary["located"] == 1
    assert not (built.run / ".alignment.tmp").exists()


def test_an_observer_that_refuses_at_the_beginning_stops_the_run_untouched(
    tmp_path: Path,
) -> None:
    built = scenes.build(tmp_path / "run", [Play("speaker", 1, 1, at=2000.0)])
    (built.run / capture.SCRATCH).mkdir()
    (built.run / capture.SCRATCH / report.EXPORT).write_bytes(b"left by a killed align")
    before = listing(built.run)
    watching = Watching(Failure("unreadable", "results.json: sources[0] has no 'restored'"))
    refused = report.align(built.run, READER, False, HARNESS, watching)
    assert refused == watching.refuse
    assert len(watching.begun) == 1
    assert watching.captures == []
    assert listing(built.run) == before


def test_a_leftover_scratch_directory_is_cleared(tmp_path: Path) -> None:
    built = scenes.build(tmp_path / "run", [Play("speaker", 1, 1, at=2000.0)])
    (built.run / ".alignment.tmp").mkdir()
    (built.run / ".alignment.tmp" / report.EXPORT).write_bytes(b"left by a killed align")
    align(built.run)


def test_a_link_planted_under_the_reports_temporary_name_is_not_written_through(
    tmp_path: Path,
) -> None:
    built = scenes.build(tmp_path / "run", [Play("speaker", 1, 1, at=2000.0)])
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"not the run's")
    (built.run / ".alignment.json.tmp").symlink_to(outside)
    done = align(built.run)
    assert outside.read_bytes() == b"not the run's"
    assert not (built.run / report.REPORT).is_symlink()
    assert done["summary"]["located"] == 1


def test_a_link_planted_under_the_scratch_name_is_not_written_through(tmp_path: Path) -> None:
    built = scenes.build(tmp_path / "run", [Play("speaker", 1, 1, at=2000.0)])
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "kept").write_bytes(b"not the run's")
    (built.run / ".alignment.tmp").symlink_to(outside, target_is_directory=True)
    done = align(built.run)
    assert done["summary"]["located"] == 1
    assert [p.name for p in outside.iterdir()] == ["kept"]
    assert (outside / "kept").read_bytes() == b"not the run's"


def test_a_leftover_that_cannot_be_cleared_is_refused_naming_it(tmp_path: Path) -> None:
    built = scenes.build(tmp_path / "run", [Play("speaker", 1, 1, at=2000.0)])
    held = built.run / capture.SCRATCH / "held"
    held.mkdir(parents=True)
    (held / report.EXPORT).write_bytes(b"left by another user's align")
    held.chmod(0o555)
    try:
        assert not os.access(held, os.W_OK), "the test's process can write a 0o555 directory"
        refused = report.align(built.run, READER, False, HARNESS)
    finally:
        held.chmod(0o755)
    assert isinstance(refused, Failure)
    assert refused.reason == "not_writable"
    assert refused.detail == (
        f"{built.run} cannot be written: the leftover {held / report.EXPORT} cannot be cleared: "
        f"{os.strerror(errno.EACCES)}"
    )
    assert not (built.run / report.REPORT).exists()


def listing(run: Path) -> dict[str, bytes | None]:
    return {
        p.relative_to(run).as_posix(): p.read_bytes() if p.is_file() else None
        for p in sorted(run.rglob("*"))
    }


@pytest.mark.parametrize("previous", [False, True])
def test_a_run_that_cannot_be_written_is_refused_and_unchanged(
    tmp_path: Path, previous: bool
) -> None:
    built = scenes.build(tmp_path / "run", [Play("speaker", 1, 1, at=2000.0)])
    if previous:
        align(built.run)
    before = listing(built.run)
    built.run.chmod(0o555)
    try:
        assert not os.access(built.run, os.W_OK), "the test's process can write a 0o555 directory"
        refused = report.align(built.run, READER, False, HARNESS)
    finally:
        built.run.chmod(0o755)
    assert isinstance(refused, Failure)
    assert refused.reason == "not_writable"
    assert refused.detail is not None and str(built.run) in refused.detail
    assert listing(built.run) == before
    assert (built.run / report.REPORT).exists() == previous


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        ("absent", "unreadable"),
        ("no_manifest", "unreadable"),
        ("not_json", "unreadable"),
        ("layout_1", "layout"),
        ("layout_2", "layout"),
        ("layout_3", "layout"),
        ("layout_4", "layout"),
        ("malformed", "unreadable"),
    ],
)
def test_a_run_that_cannot_be_read_is_refused(tmp_path: Path, setup: str, reason: str) -> None:
    run = tmp_path / "run"
    if setup != "absent":
        run.mkdir()
    if setup == "not_json":
        (run / "manifest.json").write_text("{")
    elif setup.startswith("layout_"):
        layout = int(setup.removeprefix("layout_"))
        (run / "manifest.json").write_text(json.dumps({"layout": layout, "roles": {}}))
    elif setup == "malformed":
        (run / "manifest.json").write_text(json.dumps({"layout": LAYOUT, "roles": {"r": {}}}))
    refused = report.align(run, READER, True, HARNESS)
    assert isinstance(refused, Failure)
    assert refused.reason == reason
    if run.exists():
        assert not (run / report.REPORT).exists()
