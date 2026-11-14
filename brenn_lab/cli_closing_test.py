"""The run's closing assessment as the entry point makes it, in this process:
what the pointer's `usability` says when the assessment is interrupted by a
signal, raises, or is not made, and the SIGTERM handler it leaves behind.
"""

import os
import signal
import time
from collections.abc import Iterator
from types import FrameType

import pytest

from brenn_lab.cli import closing_assessment
from brenn_lab.data import JsonObject


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


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT], ids=["sigterm", "sigint"])
@pytest.mark.usefixtures("sigterm_handler")
def test_a_signal_during_the_assessment(signum: signal.Signals) -> None:
    usability = closing_assessment("completed", lambda: signalled(signum))
    assert usability.keys() == {"answer", "reason", "detail"}
    assert (usability["answer"], usability["reason"]) == ("not_assessed", "assessment_interrupted")
    assert signal.getsignal(signal.SIGTERM) is _found_before


@pytest.mark.usefixtures("sigterm_handler")
def test_an_assessment_that_raises(capsys: pytest.CaptureFixture[str]) -> None:
    def raises() -> JsonObject:
        raise ValueError("no such thing")

    usability = closing_assessment("failed", raises)
    assert usability == {
        "answer": "not_assessed",
        "reason": "raised",
        "detail": "ValueError: no such thing",
    }
    assert "Traceback" in capsys.readouterr().err
    assert signal.getsignal(signal.SIGTERM) is _found_before


@pytest.mark.usefixtures("sigterm_handler")
def test_an_assessment_that_returns() -> None:
    def assessed() -> JsonObject:
        assert signal.getsignal(signal.SIGTERM) is signal.default_int_handler
        return {"answer": "clean", "complete": True, "assessment": "a"}

    assert closing_assessment("completed", assessed) == {
        "answer": "clean",
        "complete": True,
        "assessment": "a",
    }
    assert signal.getsignal(signal.SIGTERM) is _found_before


def test_an_interrupted_run_is_not_assessed() -> None:
    def never() -> JsonObject:
        raise AssertionError("an interrupted run was assessed")

    usability = closing_assessment("interrupted", never)
    assert (usability["answer"], usability["reason"]) == ("not_assessed", "interrupted")
