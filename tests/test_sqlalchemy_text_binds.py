"""Unit tests for the shared `:name::type` bind-parameter check."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.sqlalchemy_text_binds import assert_no_colon_cast_binds, colon_cast_binds


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
