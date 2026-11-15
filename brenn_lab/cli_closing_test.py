"""The run's closing assessment as the entry point makes it, in this process:
what the pointer's `usability` says when the assessment is interrupted by a
signal, raises, or is not made, and the SIGTERM handler it leaves behind.
"""

import os
import signal
import time
from collections.abc import Iterator
from pathlib import Path
from types import FrameType

import pytest

from brenn_lab.cli import closing_assessment
from brenn_lab.data import JsonObject
from brenn_lab.rundir import RunDir
from brenn_lab.testing.run_logs import runner_log

MACHINE = "bench"


def _found_before(signum: int, frame: FrameType | None) -> None:
    raise AssertionError("the handler found before ran during the assessment")


@pytest.fixture
def sigterm_handler() -> Iterator[None]:
    """A SIGTERM handler of the test's own, standing before the assessment."""
    previous = signal.signal(signal.SIGTERM, _found_before)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def signalled(signum: signal.Signals) -> JsonObject:
    os.kill(os.getpid(), signum)
    time.sleep(10)
    raise AssertionError("the signal did not end the assessment")


@pytest.fixture
def run(tmp_path: Path) -> Path:
    """A run directory with its `runner.log` and first entry."""
    return RunDir.create(tmp_path / "run", MACHINE).path


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT], ids=["sigterm", "sigint"])
@pytest.mark.usefixtures("sigterm_handler")
def test_a_signal_during_the_assessment(signum: signal.Signals, run: Path) -> None:
    usability = closing_assessment("completed", lambda: signalled(signum), run, MACHINE)
    assert usability.keys() == {"answer", "reason", "detail"}
    assert (usability["answer"], usability["reason"]) == ("not_assessed", "assessment_interrupted")
    assert signal.getsignal(signal.SIGTERM) is _found_before


@pytest.mark.usefixtures("sigterm_handler")
def test_an_assessment_that_raises(capsys: pytest.CaptureFixture[str], run: Path) -> None:
    def raises() -> JsonObject:
        raise ValueError("no such thing")

    usability = closing_assessment("failed", raises, run, MACHINE)
    assert usability == {
        "answer": "not_assessed",
        "reason": "raised",
        "detail": "ValueError: no such thing",
    }
    assert "Traceback" in capsys.readouterr().err
    assert signal.getsignal(signal.SIGTERM) is _found_before
    started, raised = runner_log(run)
    assert started["text"] == "run started"
    assert raised["text"].startswith("the closing assessment raised:\nTraceback")
    assert "ValueError: no such thing" in raised["text"]
    assert raised["at"]["machine"] == MACHINE


@pytest.mark.usefixtures("sigterm_handler")
def test_an_assessment_that_raises_where_its_note_cannot_be_written(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    def raises() -> JsonObject:
        raise ValueError("no such thing")

    usability = closing_assessment("failed", raises, tmp_path / "no_run", MACHINE)
    assert usability["reason"] == "raised"
    err = capsys.readouterr().err
    assert "ValueError: no such thing" in err and "FileNotFoundError" in err


@pytest.mark.usefixtures("sigterm_handler")
def test_an_assessment_that_returns(run: Path) -> None:
    def assessed() -> JsonObject:
        assert signal.getsignal(signal.SIGTERM) is signal.default_int_handler
        return {"answer": "clean", "complete": True, "assessment": "a"}

    assert closing_assessment("completed", assessed, run, MACHINE) == {
        "answer": "clean",
        "complete": True,
        "assessment": "a",
    }
    assert signal.getsignal(signal.SIGTERM) is _found_before


def test_an_interrupted_run_is_not_assessed(run: Path) -> None:
    def never() -> JsonObject:
        raise AssertionError("an interrupted run was assessed")

    usability = closing_assessment("interrupted", never, run, MACHINE)
    assert (usability["answer"], usability["reason"]) == ("not_assessed", "interrupted")
