"""A finder that never returns is cut off after FINDER_BUDGET_SECONDS and recorded as an error, so it cannot hold a whole shard."""

from __future__ import annotations

import signal
import time
from pathlib import Path

import pytest

from py_ci_shared import corpus_drift

posix_only = pytest.mark.skipif(not hasattr(signal, "SIGALRM"), reason="the budget needs SIGALRM (POSIX)")


def _hangs() -> int:
    time.sleep(30)
    return 1


def _quick() -> int:
    return 2


@posix_only
def test_a_finder_past_its_budget_is_recorded_as_an_error_and_the_scan_goes_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(corpus_drift, "FINDER_BUDGET_SECONDS", 1)
    # without the budget the finder sleeps its full 30 s and records no error, so the error assertion is the check
    result = corpus_drift._scan_repo(tmp_path, [("hangs", _hangs), ("quick", _quick)])
    assert result["errors"] == {"hangs": "FinderTimeoutError"}
    assert result["counts"] == {"quick": 2}, "the finder after the stuck one still ran"


def test_a_finder_within_its_budget_keeps_its_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # negative control: the budget must not change the result of a finder that finishes
    monkeypatch.setattr(corpus_drift, "FINDER_BUDGET_SECONDS", 5)
    result = corpus_drift._scan_repo(tmp_path, [("quick", _quick)])
    assert result["counts"] == {"quick": 2} and result["errors"] == {}


@posix_only
def test_the_alarm_is_disarmed_after_a_finder_returns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(corpus_drift, "FINDER_BUDGET_SECONDS", 1)
    corpus_drift._scan_repo(tmp_path, [("quick", _quick)])
    assert signal.getitimer(signal.ITIMER_REAL)[0] == 0, "a leftover alarm would interrupt whatever runs next"
