"""Unit tests for no_source_text_tests: an assert that probes the text of a SQL statement instead of the rows it returns."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from py_ci_shared.no_source_text_tests import assert_no_source_text_tests, find_source_text_tests


def _lines(tmp_path: Path, body: str) -> list[str]:
    p = tmp_path / "test_mod.py"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return [f.message for f in find_source_text_tests([p], min_files=1)]


@pytest.mark.parametrize(
    "assertion",
    [
        'assert "WHERE" in sql',
        'assert "SELECT" in sql',
        'assert "FROM" in sql',
        'assert "OR TRUE" not in sql',
        'assert "OR 1=1" not in sql',
        'assert "GROUP BY" in sql and "ORDER BY" in sql',
    ],
)
def test_sql_keyword_probe_is_found(tmp_path, assertion):
    body = f"""
        def test_query():
            sql = "SELECT * FROM orders WHERE tenant = 'acme'"
            {assertion}
    """
    assert len(_lines(tmp_path, body)) >= 1


def test_english_word_in_a_message_is_not_a_finding(tmp_path):
    body = """
        def test_message():
            msg = "the job is from the queue"
            assert "from" in msg
    """
    assert _lines(tmp_path, body) == []


def test_asserting_on_returned_rows_is_not_a_finding(tmp_path):
    body = """
        def test_rows():
            rows = [{"tenant": "acme", "id": 1}]
            assert [r["id"] for r in rows if r["tenant"] == "acme"] == [1]
    """
    assert _lines(tmp_path, body) == []


def test_assert_raises_on_a_probe_and_passes_on_row_checks(tmp_path):
    bad = tmp_path / "test_bad.py"
    bad.write_text('def test_q():\n    sql = "SELECT 1 WHERE x"\n    assert "WHERE" in sql\n', encoding="utf-8")
    with pytest.raises(AssertionError, match="source-text-test"):
        assert_no_source_text_tests([bad])
    good = tmp_path / "test_good.py"
    good.write_text("def test_q():\n    assert [1] == [1]\n", encoding="utf-8")
    assert_no_source_text_tests([good])
