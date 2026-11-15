"""Assessing whole synthetic runs through the stand-in reader. Every expected
finding is a parameter of the scene.
"""

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from brenn_lab.align import capture
from brenn_lab.align import report as alignment
from brenn_lab.assess import report
from brenn_lab.data import Failure, JsonObject
from brenn_lab.marker import Carried
from brenn_lab.playback.output import OUTPUTS
from brenn_lab.testing import scenes
from brenn_lab.testing.scenes import (
    Account,
    Capture,
    Clip,
    Constant,
    Gap,
    Loss,
    Play,
    Silent,
)

READER = os.path.abspath(os.environ["EXPORT_STAND_IN"])
HARNESS: JsonObject = {"repository": "brenn-lab", "commit": "c0ffee", "dirty": False}
MARKED = Play("speaker", 1, 1, at=2000.0)
FAILURE: JsonObject = {"reason": "stream_died", "detail": "the stream died", "data": {}}


def assess(run: Path, reader: str = READER) -> dict[str, Any]:
    done = report.assess(run, reader, False, HARNESS)
    assert not isinstance(done, Failure), done
    written: dict[str, Any] = json.loads((run / report.REPORT).read_text())
    assert written == json.loads(json.dumps(done.report))
    sha = hashlib.sha256((run / alignment.REPORT).read_bytes()).hexdigest()
    assert written["alignment"]["sha256"] == sha
    assert not (run / capture.SCRATCH).exists()
    assert not (run / ".assessment.json.tmp").exists()
    assert written["complete"] == (written["not_assessed"] == [])
    if written["findings"]:
        assert written["answer"] == "findings"
    else:
        assert written["answer"] == ("incomplete" if written["not_assessed"] else "clean")
    for f in written["findings"]:
        assert list(f) == [
            "finding",
            "role",
            "capture",
            "play",
            "channel",
            "at_frame",
            "frames",
            "detail",
            "data",
        ]
    return written


def names(done: dict[str, Any]) -> list[str]:
    return [f["finding"] for f in done["findings"]]


def unexamined(done: dict[str, Any]) -> list[tuple[str, str]]:
    return [(n["what"], n["reason"]) for n in done["not_assessed"]]


def test_a_marked_play_located_with_no_loss_is_clean(tmp_path: Path) -> None:
    done = assess(scenes.build(tmp_path / "run", [MARKED]).run)
    assert done["answer"] == "clean"
    assert done["complete"] is True
    [c] = done["captures"]
    assert c["read"] is True and c["frames"] == Capture().frames and c["tail"] == "ended"
    assert c["loss"] == []
    assert c["recorder"] == {
        "ended": "stopped",
        "frames": Capture().frames,
        "gaps_reported": 0,
        "late": 0,
        "loss_free": True,
        "failure": None,
    }
    [m] = c["channels"]
    assert m["frames"] == Capture().frames
    assert m["silent"] is False and m["constant"] is False and m["limit_samples"] == 0
    [p] = done["plays"]
    assert p["record"]["ended"] == "completed" and p["record"]["underruns"] == 0
    marker = p["marker"]
    assert (marker["state"], marker["start"], marker["end"]) == ("marked", "expected", "expected")
    assert marker["located"] is True
    [where] = marker["found_in"]
    assert where["capture"] == {"role": "mic", "capture": 1}
    assert where["ratio_basis"] == "measured"
    assert where["offset_frames"] == pytest.approx(2000.0, abs=0.1)
    assert done["summary"] == {
        "captures": 1,
        "captures_read": 1,
        "channels": 1,
        "plays": 1,
        "marked": 1,
        "located": 1,
        "findings": 0,
        "not_assessed": 0,
    }


UNAVAILABLE: JsonObject = {
    "outcome": "unavailable",
    "why": "open",
    "detail": "cannot open /dev/bus/usb/001/002: permission denied",
    "kind": "xvf3800",
}
NOT_ATTEMPTED: JsonObject = {
    "outcome": "not_attempted",
    "why": "not_usb",
    "detail": "card 0's device is not on USB",
}


@pytest.mark.parametrize(
    ("state", "stated"),
    [
        (scenes.READ_STATE, {"outcome": "read", "why": None, "detail": None}),
        (UNAVAILABLE, {k: UNAVAILABLE[k] for k in ("outcome", "why", "detail")}),
        (NOT_ATTEMPTED, NOT_ATTEMPTED),
    ],
    ids=["read", "unavailable", "not_attempted"],
)
def test_the_device_state_is_stated_and_is_no_finding(
    tmp_path: Path, state: JsonObject, stated: JsonObject
) -> None:
    c = Capture(device_state=dict(state))
    done = assess(scenes.build(tmp_path / "run", [MARKED], [c]).run)
    assert done["answer"] == "clean"
    [captured] = done["captures"]
    assert captured["device_state"] == stated
    keys = list(captured)
    assert keys[keys.index("recorder") + 1] == "device_state"


def test_a_capture_that_could_not_be_read_states_its_device_state(tmp_path: Path) -> None:
    c = Capture(refuse="error: no", device_state=dict(UNAVAILABLE))
    done = assess(scenes.build(tmp_path / "run", [], [c]).run)
    assert names(done) == ["capture_unreadable"]
    [captured] = done["captures"]
    assert captured["read"] is False
    assert captured["device_state"] == {k: UNAVAILABLE[k] for k in ("outcome", "why", "detail")}


@pytest.mark.parametrize(
    "state",
    [
        None,
        "read",
        {"why": "open", "detail": "x"},
        {"outcome": "partly"},
        {"outcome": "unavailable", "detail": "x"},
        {"outcome": "not_attempted", "why": "not_usb", "detail": 1},
    ],
    ids=["absent", "not_an_object", "no_outcome", "unknown_outcome", "no_why", "detail_int"],
)
def test_a_capture_entry_without_a_device_state_is_unreadable_and_still_aligned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: object
) -> None:
    built = scenes.build(tmp_path / "run", [MARKED])
    path = built.run / "manifest.json"
    manifest = json.loads(path.read_text())
    entry = manifest["roles"]["mic"]["captures"][0]
    if state is None:
        del entry["device_state"]
    else:
        entry["device_state"] = state
    path.write_text(json.dumps(manifest))
    before = listing(built.run)
    exports: list[Path] = []
    monkeypatch.setattr(capture, "read", lambda log, *a: exports.append(log))
    refused = report.assess(built.run, READER, False, HARNESS)
    assert isinstance(refused, Failure) and refused.reason == "unreadable"
    assert "device_state" in refused.detail and "manifest" in refused.detail, refused.detail
    assert listing(built.run) == before
    assert exports == []
    monkeypatch.undo()
    assert not isinstance(alignment.align(built.run, READER, False, HARNESS), Failure)


def test_a_run_with_no_playback_is_clean(tmp_path: Path) -> None:
    done = assess(scenes.build(tmp_path / "run", []).run)
    assert done["answer"] == "clean"
    assert done["plays"] == []


@pytest.mark.parametrize(
    ("content", "finding", "data", "place"),
    [
        (Silent(), "channel_silent", {}, (None, None)),
        (Constant(100), "channel_constant", {"value": 100}, (None, None)),
        (
            Clip(1000, 5),
            "channel_at_limit",
            {"limit_samples": 5, "limit_spans": 1, "longest_limit_span": 5},
            (1000, 5),
        ),
    ],
)
def test_a_channel_finding(
    tmp_path: Path, content: Any, finding: str, data: JsonObject, place: tuple[Any, Any]
) -> None:
    c = Capture(layout=("a", "b"), content=(None, content))
    done = assess(scenes.build(tmp_path / "run", [], [c]).run)
    [f] = done["findings"]
    assert (f["finding"], f["channel"], f["data"]) == (finding, "b", data)
    assert (f["at_frame"], f["frames"]) == place


@pytest.mark.parametrize(
    ("basis", "frames", "estimated"),
    [("measured", 500, None), ("clock_estimate", None, 500), ("unknown", None, None)],
)
def test_a_loss_both_witnesses_state(
    tmp_path: Path, basis: str, frames: int | None, estimated: int | None
) -> None:
    c = Capture(losses=(Loss(20000, 500, basis),))
    done = assess(scenes.build(tmp_path / "run", [], [c]).run)
    assert names(done) == ["loss"]
    entry = {
        "at_frame": 20000,
        "frames": frames,
        "basis": basis,
        "estimated_frames": estimated,
        "cause": "overrun",
        "witnesses": ["reader", "recorder"],
    }
    assert done["captures"][0]["loss"] == [entry]
    assert done["findings"][0]["data"] == entry
    assert (done["findings"][0]["at_frame"], done["findings"][0]["frames"]) == (20000, frames)


HOLE = (Loss(20000, 500, "measured"),)


@pytest.mark.parametrize(
    ("losses", "account", "tail", "expected"),
    [
        (HOLE, Account("stopped", 64000, 0, ()), "ended", ["loss", "hole_without_gap"]),
        ((), Account("stopped", 64000, 1, (Gap(100, 50),)), "ended", ["loss", "gap_without_hole"]),
        (HOLE, Account("stopped", 64000, 1, (Gap(20000, 400),)), "ended", ["loss", "length"]),
        (HOLE, Account("stopped", 64000, 1, (Gap(20000, None),)), "ended", ["loss", "length"]),
        ((), Account("stopped", 64000, 2, ()), "ended", ["gaps_reported"]),
        ((), Account("stopped", 63000, 0, ()), "ended", ["frames"]),
        ((), None, "unterminated", ["log_not_ended", "end"]),
    ],
)
def test_the_witnesses_disagree(
    tmp_path: Path,
    losses: tuple[Loss, ...],
    account: Account | None,
    tail: str,
    expected: list[str],
) -> None:
    c = Capture(losses=losses, account=account, tail=tail)
    done = assess(scenes.build(tmp_path / "run", [], [c]).run)
    got = [
        f["data"]["difference"] if f["finding"] == "loss_witnesses_disagree" else f["finding"]
        for f in done["findings"]
    ]
    assert got == expected


def test_a_failed_capture(tmp_path: Path) -> None:
    c = Capture(account=Account("failed", None, None, (), failure=FAILURE))
    done = assess(scenes.build(tmp_path / "run", [], [c]).run)
    assert names(done) == ["capture_failed"]
    assert done["findings"][0]["data"] == FAILURE


@pytest.mark.parametrize(("status", "reason"), [(1, "export_refused"), (3, "reader_contract")])
def test_an_unreadable_capture(tmp_path: Path, status: int, reason: str) -> None:
    c = Capture(refuse="error: no", status=status)
    done = assess(scenes.build(tmp_path / "run", [MARKED], [c]).run)
    assert names(done) == ["capture_unreadable"]
    assert done["findings"][0]["data"] == {"reason": reason}
    assert unexamined(done) == [("markers", "no_capture_searched"), ("capture", reason)]
    captured = done["captures"][0]
    assert captured["read"] is False and captured["failure"]["reason"] == reason
    assert (captured["frames"], captured["tail"], captured["channels"]) == (None, None, None)


@pytest.mark.parametrize("reason", ["missing", "capture_differs"])
def test_a_capture_gone_or_not_as_granted_is_unreadable(tmp_path: Path, reason: str) -> None:
    run = scenes.build(tmp_path / "run", [MARKED]).run
    if reason == "missing":
        (run / "captures" / "mic" / "0001.framelog").unlink()
    else:
        manifest = json.loads((run / "manifest.json").read_text())
        # The export is at the scene's 16 kHz; the grant now says otherwise.
        manifest["roles"]["mic"]["captures"][0]["granted"]["rate_hz"] = 8000
        (run / "manifest.json").write_text(json.dumps(manifest))
    done = assess(run)
    assert names(done) == ["capture_unreadable"]
    assert done["findings"][0]["data"] == {"reason": reason}
    assert unexamined(done) == [("markers", "no_capture_searched"), ("capture", reason)]
    assert done["captures"][0]["read"] is False


@pytest.mark.parametrize("frames", [500, None])
def test_a_loss_only_the_recorder_states(tmp_path: Path, frames: int | None) -> None:
    # The reader never read the log: no basis, no estimate, no disagreement.
    account = Account("stopped", 64000, 1, (Gap(20000, frames),))
    c = Capture(refuse="error: no", status=1, account=account)
    done = assess(scenes.build(tmp_path / "run", [], [c]).run)
    assert names(done) == ["capture_unreadable", "loss"]
    entry = {
        "at_frame": 20000,
        "frames": frames,
        "basis": None,
        "estimated_frames": None,
        "cause": "overrun",
        "witnesses": ["recorder"],
    }
    assert done["captures"][0]["loss"] == [entry]
    assert done["findings"][1]["data"] == entry
    assert unexamined(done) == [("capture", "export_refused")]


def test_a_capture_the_reader_was_never_given(tmp_path: Path) -> None:
    usage = Capture(refuse="usage", status=2)
    elsewhere = Capture(role="far", in_run=False)
    run = scenes.build(tmp_path / "run", [], [usage, elsewhere]).run
    done = assess(run)
    assert done["answer"] == "incomplete"
    assert unexamined(done) == [("capture", "reader_usage"), ("capture", "not_in_run")]
    missing = assess(run, str(tmp_path / "no-reader"))
    assert unexamined(missing) == [("capture", "reader_missing"), ("capture", "not_in_run")]


@pytest.mark.parametrize("losses", [(), (Loss(0, 1000, "measured"),)])
def test_an_empty_capture(tmp_path: Path, losses: tuple[Loss, ...]) -> None:
    c = Capture(frames=1000 if losses else 0, losses=losses)
    done = assess(scenes.build(tmp_path / "run", [], [c]).run)
    assert names(done) == ["capture_empty"] + (["loss"] if losses else [])
    [m] = done["captures"][0]["channels"]
    assert m["frames"] == 0 and m["silent"] is None and m["rms_mb"] is None


def test_roles_with_nothing_recorded_or_of_an_unknown_kind(tmp_path: Path) -> None:
    roles: JsonObject = {"mic2": {"kind": "capture", "captures": []}, "arm": {"kind": "motion"}}
    done = assess(scenes.build(tmp_path / "run", [], roles=roles).run)
    assert names(done) == ["role_recorded_nothing"]
    assert done["findings"][0]["role"] == "mic2"
    assert unexamined(done) == [("role", "unknown_kind")]


def test_a_capture_with_no_record(tmp_path: Path) -> None:
    done = assess(scenes.build(tmp_path / "run", [], [Capture(recorded=False)]).run)
    assert done["answer"] == "incomplete"
    assert unexamined(done) == [("recorder_account", "no_record")]
    assert done["captures"][0]["recorder"] is None


@pytest.mark.parametrize(
    ("ending", "expected"),
    [
        ({"ended": "failed", "failure": FAILURE}, ["play_failed"]),
        ({"ended": "stopped", "stopped_by": "runner", "unplayed": 40}, ["play_cut_by_runner"]),
        ({"underruns": (100,)}, ["play_underrun"]),
        ({"restored": False}, ["controls_not_restored"]),
        ({"ended": "stopped", "stopped_by": "script", "handed": 8000}, []),
    ],
)
def test_a_play_ending(tmp_path: Path, ending: dict[str, Any], expected: list[str]) -> None:
    play = Play("speaker", 1, None, at=2000.0, **ending)
    done = assess(scenes.build(tmp_path / "run", [play]).run)
    assert names(done) == expected
    assert done["plays"][0]["marker"]["state"] == "no_marker"
    if expected == ["play_underrun"]:
        assert done["findings"][0]["at_frame"] == 100
        assert done["plays"][0]["record"]["as_sent"] is False


END_NOT_REPORTED: JsonObject = {
    "reason": "end_not_reported",
    "detail": "no end reached the run",
    "data": {},
}


def test_a_run_whose_ends_were_not_reported_is_assessed(tmp_path: Path) -> None:
    """A capture and a play the runner gave `end_not_reported`, the play's
    `restored` null: the capture is measured, each failure is a finding,
    and no finding says the controls were not put back.
    """
    play = Play("speaker", 1, 1, at=2000.0, ended="failed", restored=None, failure=END_NOT_REPORTED)
    c = Capture(account=Account("failed", None, None, (), failure=END_NOT_REPORTED))
    done = assess(scenes.build(tmp_path / "run", [play], [c], outcome="failed").run)
    assert sorted(names(done)) == ["capture_failed", "play_failed", "run_not_completed"]
    assert all(
        f["data"] == END_NOT_REPORTED
        for f in done["findings"]
        if f["finding"] in ("capture_failed", "play_failed")
    )
    assert unexamined(done) == []
    assert done["captures"][0]["read"] is True
    assert done["plays"][0]["record"]["restored"] is None
    assert done["plays"][0]["marker"]["state"] == "marked"
    assert done["schema"] == 2


def test_a_timed_play_s_start_is_copied_and_judged_by_nothing(tmp_path: Path) -> None:
    asked: JsonObject = {
        "machine": "bench",
        "monotonic": {"clock": "CLOCK_MONOTONIC", "us": 5_000_000},
        "wall": {"clock": "CLOCK_REALTIME", "us": 1_700_000_000_000_000},
    }
    timed = Play("speaker", 1, None, at=2000.0, asked_start=asked, started_after_us=900_000)
    done = assess(scenes.build(tmp_path / "run", [timed]).run)
    assert done["plays"][0]["record"]["asked_start"] == asked
    assert done["plays"][0]["record"]["started_after_us"] == 900_000
    assert names(done) == []
    assert done["clock_readings"] == "not_used"
    late = Play(
        "speaker",
        1,
        None,
        at=2000.0,
        ended="failed",
        failure={"reason": "start_passed", "detail": "late", "data": {"late_us": 20_001}},
        handed=0,
        asked_start=asked,
    )
    failed = assess(scenes.build(tmp_path / "late", [late]).run)
    assert names(failed) == ["play_failed"]
    assert failed["plays"][0]["record"]["asked_start"] == asked
    assert failed["plays"][0]["record"]["started_after_us"] is None
    untimed = assess(scenes.build(tmp_path / "untimed", [Play("speaker", 1, None, at=2000.0)]).run)
    assert untimed["plays"][0]["record"]["asked_start"] is None
    assert untimed["plays"][0]["record"]["started_after_us"] is None


def test_a_missing_kept_copy(tmp_path: Path) -> None:
    built = scenes.build(tmp_path / "run", [MARKED])
    (built.run / "sources" / "speaker" / "0001.wav").unlink()
    done = assess(built.run)
    assert names(done) == ["kept_copy_unusable"]
    assert done["findings"][0]["data"] == {"reason": "copy_missing"}
    assert unexamined(done) == [("markers", "copy_unusable")]
    assert done["complete"] is False


def test_a_marked_play_not_located(tmp_path: Path) -> None:
    done = assess(scenes.build(tmp_path / "run", [Play("speaker", 1, 1, at=500000.0)]).run)
    assert names(done) == ["play_not_located"]
    data = done["findings"][0]["data"]
    assert (data["start"], data["end"], data["pairs_tried"]) == ("expected", "expected", 1)
    assert data["outcomes"] == {"not_found/below_threshold": 2}


def test_a_run_that_did_not_complete(tmp_path: Path) -> None:
    failure: JsonObject = {"where": "role", "reason": "over_cap", "role": "speaker"}
    run = scenes.build(tmp_path / "run", [], outcome="failed", failure=failure).run
    done = assess(run)
    assert names(done) == ["run_not_completed"]
    assert done["findings"][0]["data"] == {
        "outcome": "failed",
        "where": "role",
        "reason": "over_cap",
    }
    assert done["run"] == {"outcome": "failed", "failure": failure}


def carried(tmp_path: Path) -> Carried:
    found = scenes.build(tmp_path / "probe", [MARKED]).carried[("speaker", 1)]
    assert found is not None
    return found


def test_a_marked_play_the_script_cut_before_its_end_marker(tmp_path: Path) -> None:
    probe = carried(tmp_path)
    play = Play("speaker", 1, 1, at=2000.0, ended="stopped", handed=probe.end.frame)
    done = assess(scenes.build(tmp_path / "run", [play]).run)
    assert done["answer"] == "clean"
    marker = done["plays"][0]["marker"]
    assert (marker["start"], marker["end"], marker["located"]) == ("expected", "not_handed", False)
    [where] = marker["found_in"]
    assert where["ratio_basis"] == "nominal" and where["drift_ppm"] is None

    far = Play("speaker", 1, 1, at=500000.0, ended="stopped", handed=probe.end.frame)
    done = assess(scenes.build(tmp_path / "far", [far]).run)
    assert names(done) == ["play_not_located"]


@pytest.mark.parametrize("unplayed", [True, False])
def test_a_marked_play_cut_with_its_end_marker_discarded(tmp_path: Path, unplayed: bool) -> None:
    probe = carried(tmp_path)
    frames = probe.end.frame + probe.end.frames
    play = Play(
        "speaker",
        1,
        1,
        at=2000.0,
        ended="stopped",
        stopped_by="script",
        sounded=probe.end.frame,
        unplayed=frames - probe.end.frame if unplayed else None,
    )
    done = assess(scenes.build(tmp_path / "run", [play]).run)
    assert done["answer"] == "clean"
    marker = done["plays"][0]["marker"]
    # With no delay reading the record cannot say that even the start sounded.
    assert (marker["start"], marker["end"]) == ("expected" if unplayed else "unknown", "unknown")
    assert len(marker["found_in"]) == 1


def cut_by_runner(probe: Carried, output: str, ended: str = "stopped") -> Play:
    """A marked play of `output`'s kind whose results record says every frame
    it handed sounded, so that a reader taking the figure calls both markers
    `expected`.
    """
    frames = probe.end.frame + probe.end.frames
    return Play(
        "speaker",
        1,
        1,
        at=2000.0,
        ended=ended,
        stopped_by="runner" if ended == "stopped" else None,
        handed=frames,
        unplayed=0,
        output=output,
    )


def test_a_play_of_an_unknown_output_kind_does_not_read_its_unplayed_frames(
    tmp_path: Path,
) -> None:
    play = cut_by_runner(carried(tmp_path), "invented")
    done = assess(scenes.build(tmp_path / "run", [play]).run)
    assert done["answer"] != "clean"
    [p] = done["plays"]
    assert p["record"]["unplayed_frames"] is None
    assert (p["marker"]["start"], p["marker"]["end"]) == ("unknown", "unknown")
    [cut] = [f for f in done["findings"] if f["finding"] == "play_cut_by_runner"]
    assert cut["data"]["unplayed_frames"] is None
    [entry] = [n for n in done["not_assessed"] if n["what"] == "play_account"]
    assert (entry["reason"], entry["role"], entry["play"]) == ("unknown_output_kind", "speaker", 1)
    assert "'invented'" in entry["detail"] and "speaker" in entry["detail"]


def test_a_completed_play_of_an_unknown_output_kind_is_still_listed(tmp_path: Path) -> None:
    play = cut_by_runner(carried(tmp_path), "invented", ended="completed")
    done = assess(scenes.build(tmp_path / "run", [play]).run)
    assert done["answer"] == "incomplete"
    marker = done["plays"][0]["marker"]
    assert (marker["start"], marker["end"]) == ("expected", "expected")
    assert unexamined(done) == [("play_account", "unknown_output_kind")]


def test_a_play_of_a_known_output_kind_reads_its_unplayed_frames(tmp_path: Path) -> None:
    probe = carried(tmp_path)
    for kind in sorted(OUTPUTS):
        play = cut_by_runner(probe, kind)
        done = assess(scenes.build(tmp_path / kind, [play]).run)
        assert names(done) == ["play_cut_by_runner"]
        assert unexamined(done) == []
        [p] = done["plays"]
        assert p["record"]["unplayed_frames"] == 0
        assert (p["marker"]["start"], p["marker"]["end"]) == ("expected", "expected")


@pytest.mark.parametrize("kind", ["alsa", "pulse", "invented"])
@pytest.mark.parametrize("sounded", ["both", "start", "neither"])
def test_a_completed_play_with_an_underrun_by_output_kind(
    tmp_path: Path, kind: str, sounded: str
) -> None:
    probe = carried(tmp_path)
    left = {"both": None, "start": probe.end.frame, "neither": 0}[sounded]
    play = Play(
        "speaker",
        1,
        1,
        at=2000.0,
        underruns=(probe.start.frame + probe.start.frames + 100,),
        sounded=left,
        output=kind,
    )
    done = assess(scenes.build(tmp_path / "run", [play]).run)
    assert done["answer"] != "clean"
    assert "play_underrun" in names(done)
    marker = done["plays"][0]["marker"]
    plays_on = kind == "alsa"
    word = "expected" if plays_on else "unknown"
    assert (marker["start"], marker["end"]) == (word, word)
    assert len(marker["found_in"]) == (sounded == "both" or (sounded == "start" and not plays_on))
    assert ("play_not_located" in names(done)) == (plays_on and sounded != "both")
    account = [n for n in done["not_assessed"] if n["what"] == "play_account"]
    assert len(account) == (kind == "invented")


def test_a_source_entry_without_its_output_kind_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = scenes.build(tmp_path / "run", [Play("speaker", 1, 1, at=2000.0, output=None)])
    before = listing(built.run)
    exports: list[Path] = []
    monkeypatch.setattr(capture, "read", lambda log, *a: exports.append(log))
    refused = report.assess(built.run, READER, False, HARNESS)
    assert isinstance(refused, Failure) and refused.reason == "unreadable"
    assert "output" in refused.detail and "manifest" in refused.detail, refused.detail
    assert listing(built.run) == before
    assert exports == []


def test_two_overlapping_marked_plays_on_two_roles_are_both_located(tmp_path: Path) -> None:
    plays = [
        Play("speaker", 1, 4, at=4000.0, content_frames=48000),
        Play("talker", 1, 5, at=8000.0, level=0.316, drift_ppm=150.0, content_frames=48000),
    ]
    done = assess(scenes.build(tmp_path / "run", plays, [Capture(frames=7 * 16000)]).run)
    assert done["answer"] == "clean"
    assert [(p["role"], p["play"]) for p in done["plays"]] == [("speaker", 1), ("talker", 1)]
    for p, play in zip(done["plays"], plays, strict=True):
        marker = p["marker"]
        assert (marker["start"], marker["end"], marker["located"]) == ("expected", "expected", True)
        [where] = marker["found_in"]
        assert where["offset_frames"] == pytest.approx(play.at, abs=0.5)
    assert (done["summary"]["marked"], done["summary"]["located"]) == (2, 2)


def test_plays_whose_markers_were_not_examined(tmp_path: Path) -> None:
    plays = [MARKED, Play("speaker", 2, 2, at=30000.0, record=False)]
    built = scenes.build(tmp_path / "run", plays, [Capture(refuse="usage", status=2)])
    manifest = json.loads((built.run / "manifest.json").read_text())
    manifest["roles"]["other"] = json.loads(json.dumps(manifest["roles"]["speaker"]))
    manifest["roles"]["other"]["sources"] = manifest["roles"]["other"]["sources"][:1]
    manifest["roles"]["other"]["sources"][0]["source"]["marker"]["definition"] = "other"
    (built.run / "manifest.json").write_text(json.dumps(manifest))
    done = assess(built.run)
    assert done["answer"] == "incomplete"
    assert unexamined(done) == [
        ("markers", "no_capture_searched"),
        ("markers", "no_marker_record"),
        ("capture", "reader_usage"),
        ("markers", "unknown_definition"),
    ]


def test_a_run_with_no_results(tmp_path: Path) -> None:
    built = scenes.build(tmp_path / "run", [MARKED], [Capture(losses=HOLE)], results=False)
    done = assess(built.run)
    assert names(done) == ["run_not_completed", "loss"]
    assert done["findings"][0]["data"] == {"outcome": None, "reason": "no_results"}
    assert unexamined(done) == [("results", "no_results")]
    assert done["captures"][0]["loss"][0]["witnesses"] == ["reader"]
    assert done["plays"][0]["record"] is None
    assert done["plays"][0]["marker"]["located"] is True


def test_results_without_sources(tmp_path: Path) -> None:
    built = scenes.build(tmp_path / "run", [])
    results = json.loads((built.run / "results.json").read_text())
    del results["sources"]
    (built.run / "results.json").write_text(json.dumps(results))
    assert assess(built.run)["answer"] == "clean"


def listing(run: Path) -> dict[str, bytes | None]:
    return {
        p.relative_to(run).as_posix(): p.read_bytes() if p.is_file() else None
        for p in sorted(run.rglob("*"))
    }


def test_a_rerun_names_what_it_replaces_and_only_the_reports_are_new(tmp_path: Path) -> None:
    built = scenes.build(tmp_path / "run", [MARKED])
    before = listing(built.run)
    first = assess(built.run)
    assert first["replaces"] is None
    assert set(listing(built.run)) - set(before) == {alignment.REPORT, report.REPORT}
    previous = (built.run / report.REPORT).read_bytes()
    second = assess(built.run)
    assert second["replaces"]["sha256"] == hashlib.sha256(previous).hexdigest()
    assert second["replaces"]["written"] == first["written"]


def damage(results: dict[str, Any], how: str) -> None:
    if how == "frames_handed":
        results["sources"][0]["frames_handed"] = "many"
    elif how == "gaps":
        results["captures"][0]["gaps"] = {}
    elif how == "restored":
        del results["sources"][0]["restored"]


@pytest.mark.parametrize(
    ("how", "named"),
    [
        ("not_json", "not JSON"),
        ("frames_handed", "frames_handed"),
        ("gaps", "gaps"),
        ("restored", "restored"),
    ],
)
def test_a_damaged_results_file_is_refused_with_nothing_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, how: str, named: str
) -> None:
    built = scenes.build(tmp_path / "run", [MARKED])
    aligned = alignment.align(built.run, READER, False, HARNESS)
    assert not isinstance(aligned, Failure)
    path = built.run / "results.json"
    if how == "not_json":
        path.write_text("{")
    else:
        results = json.loads(path.read_text())
        damage(results, how)
        path.write_text(json.dumps(results))
    before = listing(built.run)
    exports: list[Path] = []
    monkeypatch.setattr(capture, "read", lambda log, *a: exports.append(log))
    refused = report.assess(built.run, READER, False, HARNESS)
    assert isinstance(refused, Failure)
    assert refused.reason == "unreadable"
    assert named in refused.detail and "results" in refused.detail, refused.detail
    assert listing(built.run) == before
    assert exports == []
    monkeypatch.undo()
    if how in ("gaps", "restored"):
        assert not isinstance(alignment.align(built.run, READER, False, HARNESS), Failure)


def test_a_missing_manifest_or_another_layout_is_refused(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    refused = report.assess(run, READER, False, HARNESS)
    assert isinstance(refused, Failure) and refused.reason == "unreadable"
    for layout in (1, 2, 3):
        (run / "manifest.json").write_text(json.dumps({"layout": layout, "roles": {}}))
        refused = report.assess(run, READER, False, HARNESS)
        assert isinstance(refused, Failure) and refused.reason == "layout"
    assert listing(run) == {"manifest.json": (run / "manifest.json").read_bytes()}


def test_a_run_that_cannot_be_written_is_refused(tmp_path: Path) -> None:
    built = scenes.build(tmp_path / "run", [MARKED])
    before = listing(built.run)
    built.run.chmod(0o555)
    try:
        assert not os.access(built.run, os.W_OK), "the test's process can write a 0o555 directory"
        refused = report.assess(built.run, READER, False, HARNESS)
    finally:
        built.run.chmod(0o755)
    assert isinstance(refused, Failure) and refused.reason == "not_writable"
    assert listing(built.run) == before
