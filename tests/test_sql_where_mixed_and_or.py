"""sql_where_mixed_and_or: a WHERE whose own parenthesis level mixes AND and OR (glossum N-9 wrote one verdict onto every cognate)."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.sql_where_mixed_and_or import (
    RULE,
    assert_sql_where_mixed_and_or,
    find_sql_where_mixed_and_or,
)

BOM = b"\xef\xbb\xbf"
NL = chr(10)


def _write(root: Path, rel: str, lines: list, *, bom: bool = False) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((BOM if bom else b"") + (NL.join(lines) + NL).encode("utf-8"))
    return path


def _find(tmp_path: Path, lines: list, **kwargs) -> list:
    _write(tmp_path, "mod.py", lines)
    return find_sql_where_mixed_and_or(tmp_path, use_git=False, **kwargs)


def test_the_glossum_incident_is_reported_at_its_string_line(tmp_path):
    found = _find(
        tmp_path,
        [
            'UPDATE_SQL = """',  # 1
            "UPDATE verdicts SET status = :s",  # 2
            "WHERE lang = :l AND text = :t AND sense IN (1, 2) OR sense IN (3, 4)",  # 3
            '"""',  # 4
        ],
    )
    assert [(f.path, f.line, f.rule) for f in found] == [("mod.py", 1, RULE)]
    assert "sense IN (3, 4)" in found[0].message


def test_a_bracketed_or_arm_is_clean(tmp_path):
    assert (
        _find(
            tmp_path,
            [
                'UPDATE_SQL = """',
                "UPDATE verdicts SET status = :s",
                "WHERE lang = :l AND text = :t AND (sense IN (1, 2) OR sense IN (3, 4))",
                '"""',
            ],
        )
        == []
    )


def test_between_and_and_plain_and_or_only_levels_are_clean(tmp_path):
    assert (
        _find(
            tmp_path,
            [
                'SIMPLE = "SELECT 1 FROM t WHERE a = 1 AND b BETWEEN 2 AND 3"',
                'ONLY_OR = "SELECT 1 FROM t WHERE a IS NULL OR a = 1"',
            ],
        )
        == []
    )


def test_a_case_expression_inside_the_where_is_not_a_level_mix(tmp_path):
    assert _find(tmp_path, ['Q = "SELECT 1 FROM t WHERE CASE WHEN a = 1 AND b = 2 THEN 1 ELSE 0 END = 1 AND c = 3"']) == []


def test_an_or_only_in_a_sql_comment_is_not_counted(tmp_path):
    assert (
        _find(
            tmp_path,
            [
                'Q = """',
                "SELECT 1 FROM t",
                "-- this comment says or and",
                "WHERE a = 1 AND b = 2 /* or */",
                '"""',
            ],
        )
        == []
    )


def test_a_docstring_that_describes_a_bad_where_is_skipped(tmp_path):
    assert (
        _find(
            tmp_path,
            [
                "def f():",
                '    """Example: SELECT 1 FROM t WHERE a = 1 AND b = 2 OR c = 3 (documented, not run)."""',
                "    return 1",
            ],
        )
        == []
    )


def test_a_plpgsql_dollar_body_is_skipped_but_the_statement_outside_it_is_checked(tmp_path):
    body = [
        'DDL = """',
        "CREATE FUNCTION f() RETURNS void AS $$",
        "BEGIN UPDATE t SET x = 1 WHERE a = 1 AND b = 2 OR c = 3; END;",
        "$$ LANGUAGE plpgsql;",
        '"""',
    ]
    assert _find(tmp_path, body) == []
    outside = ['DDL = """', "CREATE FUNCTION f() RETURNS void AS $$ BEGIN END; $$ LANGUAGE plpgsql;", "UPDATE t SET x = 1 WHERE a = 1 AND b = 2 OR c = 3;", '"""']
    assert [f.line for f in _find(tmp_path / "second", outside)] == [1]


def test_an_fstring_placeholder_does_not_hide_the_mix(tmp_path):
    found = _find(tmp_path, ["def q(lang):", '    return f"SELECT 1 FROM t WHERE lang = {lang} AND a = 1 OR a = 2"'])
    assert [(f.line, f.rule) for f in found] == [(2, RULE)]


def test_a_subquery_level_is_judged_on_its_own_parentheses(tmp_path):
    assert _find(tmp_path, ['Q = "SELECT 1 FROM t WHERE a = 1 AND b IN (SELECT x FROM u WHERE p = 1 OR q = 2) ORDER BY 1"']) == []
    found = _find(tmp_path, ['Q = "SELECT 1 FROM t WHERE a IN (SELECT x FROM u WHERE p = 1 AND q = 2 OR r = 3)"'])
    assert [f.line for f in found] == [1]


def test_order_by_ends_the_level_so_a_later_or_is_not_counted(tmp_path):
    assert _find(tmp_path, ['Q = "SELECT 1 FROM t WHERE a = 1 AND b = 2 ORDER BY c OR d"']) == []


def test_a_second_statement_is_judged_on_its_own(tmp_path):
    assert _find(tmp_path, ['Q = "UPDATE t SET x = 1 WHERE a = 1 AND b = 2; SELECT 1 FROM u WHERE p = 1 OR q = 2"']) == []


def test_a_marker_with_a_reason_accepts_the_statement(tmp_path):
    assert (
        _find(
            tmp_path,
            [
                "# sql-precedence-ok: the OR arm is deliberate and covered by test_x",
                'Q = "SELECT 1 FROM t WHERE a = 1 AND b = 2 OR c = 3"',
            ],
        )
        == []
    )


def test_a_marker_without_a_reason_is_itself_a_finding(tmp_path):
    found = _find(tmp_path, ["# sql-precedence-ok:", 'Q = "SELECT 1 FROM t WHERE a = 1 AND b = 2 OR c = 3"'])
    assert [f.line for f in found] == [2]
    assert "marker needs a reason" in found[0].message


def test_a_bom_prefixed_violation_is_reported_like_the_plain_one(tmp_path):
    _write(tmp_path, "mod.py", ['Q = "SELECT 1 FROM t WHERE a = 1 AND b = 2 OR c = 3"'], bom=True)
    assert [f.line for f in find_sql_where_mixed_and_or(tmp_path, use_git=False)] == [1]


def test_an_empty_tree_fails_the_floor(tmp_path):
    with pytest.raises(EmptyScanError):
        assert_sql_where_mixed_and_or(tmp_path, use_git=False)


def test_an_unparsable_file_fails_the_gate(tmp_path):
    _write(tmp_path, "broken.py", ["def ("])
    _write(tmp_path, "ok.py", ["x = 1"])
    with pytest.raises(UnparsedFilesError):
        assert_sql_where_mixed_and_or(tmp_path, use_git=False, min_files=1)
