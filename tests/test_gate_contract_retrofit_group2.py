"""Regression tests for the group-2 gate retrofit: an unparsable file is a finding unless ``allow_unparsed``, and a
floor counts real files. One case per retrofitted entry family, on a corpus with one unparsable file."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared.disposition_test_references import find_missing_test_references
from py_ci_shared.edge_function_hygiene import find_edge_function_problems
from py_ci_shared.fail_message_quality import fail_message_problems
from py_ci_shared.lf_file_writes import assert_no_crlf_writes
from py_ci_shared.sqlalchemy_text_binds import find_colon_cast_binds

BROKEN = "def broken(:\n"


def _pkg(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "ok.py").write_text("x = 1\n", encoding="utf-8")
    (pkg / "bad.py").write_text(BROKEN, encoding="utf-8")
    return tmp_path


def test_lf_file_writes_fails_on_unparsable_file_unless_allowed(tmp_path):
    _pkg(tmp_path)
    with pytest.raises(pytest.fail.Exception, match=r"bad\.py"):
        assert_no_crlf_writes(tmp_path, use_git=False)
    assert_no_crlf_writes(tmp_path, use_git=False, allow_unparsed=True)


def test_sqlalchemy_colon_cast_reports_unparsable_file_unless_allowed(tmp_path):
    root = _pkg(tmp_path)
    problems, scanned = find_colon_cast_binds(root, ["pkg"])
    assert any("bad.py" in p for p in problems)
    problems, scanned = find_colon_cast_binds(root, ["pkg"], allow_unparsed=True)
    assert not problems
    assert scanned == 1


def test_fail_message_quality_reports_unparsable_file_unless_allowed(tmp_path):
    root = _pkg(tmp_path)
    files = sorted((root / "pkg").glob("*.py"))
    _audited, bad = fail_message_problems(files, root=root)
    assert any("bad.py" in p for p in bad)
    _audited, bad = fail_message_problems(files, root=root, allow_unparsed=True)
    assert not bad


def test_disposition_references_reports_unparsable_test_unless_allowed(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_bad.py").write_text(BROKEN, encoding="utf-8")
    audit = tmp_path / "audit.md"
    audit.write_text("no references here\n", encoding="utf-8")
    assert any("cannot be parsed" in p for p in find_missing_test_references([audit], tmp_path))
    assert not find_missing_test_references([audit], tmp_path, allow_unparsed=True)


def test_edge_function_floor_counts_real_sources(tmp_path):
    (tmp_path / "fn").mkdir()
    (tmp_path / "fn" / "index.ts").write_text("export const x = 1;\n", encoding="utf-8")
    problems = find_edge_function_problems(tmp_path, min_files=2)
    assert any("expected at least 2" in p for p in problems)
    assert not any("expected at least" in p for p in find_edge_function_problems(tmp_path, min_files=1))
