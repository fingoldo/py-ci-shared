"""The raw write gate judges production code, not test fixtures (audit 50-F3)."""

from __future__ import annotations

from pathlib import Path

from py_ci_shared.raw_write_row_count import find_raw_write_row_count

BODY = b"def purge(conn):\n    conn.execute('DELETE FROM visits WHERE id = 1')\n"


def test_a_raw_delete_under_tests_is_not_judged(tmp_path: Path) -> None:
    """Negative control (50-F3): before the change the test file was reported; the same body in src still is."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "tests" / "test_fixture.py").write_bytes(BODY)
    (tmp_path / "src" / "store.py").write_bytes(BODY)
    found = find_raw_write_row_count(tmp_path, use_git=False)
    assert [f.path for f in found] == ["src/store.py"]
