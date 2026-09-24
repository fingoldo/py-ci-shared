"""Unit tests for the shared `:name::type` bind-parameter check."""

from __future__ import annotations

import pytest

from py_ci_shared.sqlalchemy_text_binds import assert_no_colon_cast_binds, colon_cast_binds, find_colon_cast_binds


def test_a_bind_followed_by_a_cast_is_found():
    assert [m for _, m in colon_cast_binds("SELECT :ids::uuid[] FROM unnest(:names::text[])")] == [":ids::uuid[]", ":names::text[]"]


@pytest.mark.parametrize(
    "safe",
    ["SELECT CAST(:ids AS uuid[]) AS x", "SELECT col::text FROM t", "SELECT :ids AS x", "SELECT a::b::c FROM t", "# :ids::uuid in prose"],
    ids=["cast-form", "column-cast", "plain-bind", "cast-chain", "comment"],
)
def test_the_safe_forms_are_not_flagged(safe):
    assert colon_cast_binds(safe) == []


def test_the_assertion_reports_file_and_line(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "db.py").write_text('SQL = "x"\nQ = text("INSERT INTO t SELECT * FROM unnest(:a::int[])")\n', encoding="utf-8")

    with pytest.raises(pytest.fail.Exception, match=r"pkg/db\.py:2: :a::int\[\]"):
        assert_no_colon_cast_binds(tmp_path, roots=("pkg",))


def test_a_scan_that_lost_its_subject_fails(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "ok.py").write_text("x = 1\n", encoding="utf-8")

    with pytest.raises(pytest.fail.Exception, match="lost its subject"):
        assert_no_colon_cast_binds(tmp_path, roots=("pkg",), min_files=2)


def test_a_word_before_the_colon_is_not_a_bind():
    assert colon_cast_binds("SELECT x:a::int") == []
    assert colon_cast_binds("SELECT (:a::int)") == [(1, ":a::int")]


def test_an_indented_comment_line_and_a_sql_comment_are_skipped():
    assert colon_cast_binds("    # :a::text in prose") == []
    assert colon_cast_binds("SELECT 1 -- was :a::text") == []
    assert colon_cast_binds("  SELECT :a::text") == [(1, ":a::text")]


def test_a_quoted_type_is_a_cast():
    assert colon_cast_binds('SELECT :a::"MyEnum"') == [(1, ':a::"MyEnum"')]


def test_in_python_only_string_literals_are_scanned(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "m.py").write_text(
        '"""Docstring: SELECT :a::text is the bug."""\n'
        "y = 1  # was :a::int, a trailing comment\n"
        "# Q = text('SELECT :b::int')\n"
        'Q = text("""\n  SELECT 1,\n  :c::int[]\n""")\n',
        encoding="utf-8",
    )
    problems, scanned = find_colon_cast_binds(tmp_path, ("pkg",))
    assert scanned == 1 and problems == ["pkg/m.py:6: :c::int[]"]


def test_pycache_and_other_suffixes_are_not_scanned_by_default(tmp_path):
    pkg = tmp_path / "pkg"
    (pkg / "__pycache__").mkdir(parents=True)
    (pkg / "__pycache__" / "m.py").write_text('Q = "SELECT :a::int"\n', encoding="utf-8")
    (pkg / "q.sql").write_text("SELECT :a::int\n", encoding="utf-8")
    (pkg / "n.txt").write_text("SELECT :a::int\n", encoding="utf-8")
    (pkg / "ok.py").write_text("x = 1\n", encoding="utf-8")
    assert find_colon_cast_binds(tmp_path, ("pkg",)) == ([], 1)
    problems, scanned = find_colon_cast_binds(tmp_path, ("pkg",), suffixes=(".py", ".sql"))
    assert problems == ["pkg/q.sql:1: :a::int"] and scanned == 2


def test_a_missing_root_and_an_unparsable_file_fail(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "ok.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match="nope: corpus root does not exist"):
        assert_no_colon_cast_binds(tmp_path, roots=("pkg", "nope"))
    (tmp_path / "pkg" / "bad.py").write_text('Q = text("SELECT :a::int"\n', encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match=r"bad\.py:1: unparsable"):
        assert_no_colon_cast_binds(tmp_path, roots=("pkg",))
