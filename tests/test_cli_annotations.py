"""49-T1, 49-T8, 49-T9: GitHub annotations carry the file and line; a budget ``fail`` is annotated; the run ends with a verdict."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared import cli
from py_ci_shared._core.runner import ERROR, FAILED, PASSED, GateResult


def _failed(message: str, name: str = "swallowed_exceptions") -> GateResult:
    return GateResult(name=name, status=FAILED, message=message, seconds=0.1, budget_s=60.0)


def test_annotation_carries_file_and_line_of_each_finding() -> None:
    message = "swallowed-exceptions:\n  log at WARNING or above:\n      src/a.py:4: [swallowed-exception] `except Exception` swallows"
    commands = cli._error_annotations(_failed(message))
    assert commands == ["::error file=src/a.py,line=4,title=py-ci-shared swallowed_exceptions::[swallowed-exception] `except Exception` swallows"]


def test_windows_path_and_special_characters_are_escaped() -> None:
    message = "      C:\\repo\\a,b.py:7: 100% wrong\nsecond line"
    (command,) = cli._error_annotations(_failed(message))
    assert command == "::error file=C%3A\\repo\\a%2Cb.py,line=7,title=py-ci-shared swallowed_exceptions::100%25 wrong"


def test_more_than_ten_findings_are_capped_with_one_summary() -> None:
    message = "\n".join(f"src/m{i}.py:{i + 1}: [rule] x" for i in range(12))
    commands = cli._error_annotations(_failed(message))
    assert len(commands) == 11
    assert commands[-1] == "::error title=py-ci-shared swallowed_exceptions FAIL::2 more finding(s) in the log"


def test_gate_without_a_location_keeps_the_first_line() -> None:
    commands = cli._error_annotations(_failed("naive_utcnow: deprecated\n  detail", name="naive_utcnow"))
    assert commands == ["::error title=py-ci-shared naive_utcnow FAIL::naive_utcnow: deprecated"]


def test_budget_fail_is_annotated_and_written_to_the_job_summary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    cli._announce_over_budget(["naive_utcnow took 5.4s, over its 0.1s budget."], "fail")
    out = capsys.readouterr().out
    assert "::error title=py-ci-shared gate over budget::naive_utcnow took 5.4s" in out
    assert "### Gates over budget" in summary.read_text(encoding="utf-8")


def test_run_ends_with_a_verdict_word(capsys: pytest.CaptureFixture[str]) -> None:
    passed = GateResult(name="a", status=PASSED, message="", seconds=0.1, budget_s=60.0)
    assert cli._report([passed], "warn") == 0
    assert capsys.readouterr().out.rstrip().endswith("1 passed, 0 failed, 0 skipped, 0 error -> OK")
    assert cli._report([_failed("src/a.py:1: x")], "warn") == 1
    assert capsys.readouterr().out.rstrip().endswith("0 passed, 1 failed, 0 skipped, 0 error -> FAILED (exit 1)")
    errored = GateResult(name="b", status=ERROR, message="cannot run", seconds=0.1, budget_s=60.0)
    assert cli._report([errored], "warn") == 2
    assert capsys.readouterr().out.rstrip().endswith("-> ERROR (exit 2: a gate could not run)")
