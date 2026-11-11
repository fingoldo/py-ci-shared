"""A file no scanner can parse is a finding of the run, not a file every check skipped silently (audit 40-F5)."""

from __future__ import annotations

from pathlib import Path

from py_ci_shared.code_audit.registry import run_all_checked


def test_an_unparsable_file_is_reported_by_run_all_checked(tmp_path: Path) -> None:
    (tmp_path / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    findings, failed = run_all_checked(tmp_path, checks=["mutable_default"], parallel=False)
    assert failed == {}
    unparsable = [f for f in findings if f.check == "unparsable"]
    assert [(f.file, f.severity) for f in unparsable] == [("broken.py", "P2")]


def test_a_clean_tree_has_no_unparsable_finding(tmp_path: Path) -> None:
    (tmp_path / "ok.py").write_text("def ok():\n    return 1\n", encoding="utf-8")
    findings, _failed = run_all_checked(tmp_path, checks=["mutable_default"], parallel=False)
    assert not [f for f in findings if f.check == "unparsable"]


def test_an_excluded_directory_is_not_reported(tmp_path: Path) -> None:
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "bad.py").write_text("def bad(:\n", encoding="utf-8")
    findings, _failed = run_all_checked(tmp_path, checks=["mutable_default"], parallel=False)
    assert not [f for f in findings if f.check == "unparsable"]
