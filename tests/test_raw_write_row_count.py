"""Tests for py_ci_shared.raw_write_row_count: a raw UPDATE or DELETE whose result the function never reads."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.raw_write_row_count import assert_raw_write_row_count, find_raw_write_row_count

BOM = b"\xef\xbb\xbf"


def _corpus(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    return tmp_path


def test_reports_the_seeded_violation(tmp_path):
    body = b"def purge(conn):\n    conn.execute('DELETE FROM visits WHERE id = 1')\n"
    found = find_raw_write_row_count(_corpus(tmp_path, {"m.py": body}), use_git=False)
    assert [(f.path, f.line, f.rule) for f in found] == [("m.py", 2, "raw-write-row-count")]
    assert "raw DELETE runs without a row-count check" in found[0].message


def test_negative_control_the_nearest_correct_code_is_not_reported(tmp_path):
    body = b"def purge(conn):\n    result = conn.execute('DELETE FROM visits WHERE id = 1')\n    return result.rowcount\n"
    assert find_raw_write_row_count(_corpus(tmp_path, {"m.py": body}), use_git=False) == []


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    plain = b"def purge(conn):\n    conn.execute('UPDATE t SET a = 1')\n"
    plain_found = find_raw_write_row_count(_corpus(tmp_path / "a", {"m.py": plain}), use_git=False)
    bom_found = find_raw_write_row_count(_corpus(tmp_path / "b", {"m.py": BOM + plain}), use_git=False)
    assert [(f.line, f.rule) for f in bom_found] == [(f.line, f.rule) for f in plain_found] != []


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    root = _corpus(tmp_path, {"m.py": b"x = 1\n", "broken.py": b"def broken(:\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken\.py"):
        find_raw_write_row_count(root, use_git=False)
    assert find_raw_write_row_count(root, allow_unparsed=True, use_git=False) == []


def test_an_empty_corpus_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        find_raw_write_row_count(tmp_path, use_git=False)


def test_a_migration_is_not_judged(tmp_path):
    """Migrations run bulk statements once on purpose; the gate excludes them by path."""
    body = b"def upgrade(op):\n    op.execute('DELETE FROM old_rows')\n"
    root = _corpus(tmp_path, {"alembic/versions/r1.py": body, "app.py": b"x = 1\n"})
    assert find_raw_write_row_count(root, use_git=False) == []


def test_the_assert_fails_on_a_finding_and_names_the_fix(tmp_path):
    root = _corpus(tmp_path, {"m.py": b"def purge(conn):\n    conn.execute('DELETE FROM visits')\n"})
    with pytest.raises(AssertionError, match="raw-write-row-count finding"):
        assert_raw_write_row_count(root, use_git=False)


def test_report_only_prints_the_finding_and_never_fails(tmp_path, capsys):
    root = _corpus(tmp_path, {"m.py": b"def purge(conn):\n    conn.execute('DELETE FROM visits')\n"})
    assert_raw_write_row_count(root, use_git=False, report_only=True)
    assert "raw-write-row-count" in capsys.readouterr().err


def test_a_batch_script_is_not_judged(tmp_path):
    body = b"def run(conn):\n    conn.execute('DELETE FROM llm.collocation_pmi')\n"
    assert find_raw_write_row_count(_corpus(tmp_path, {"scripts/batch_collocation_pmi.py": body}), use_git=False) == []
