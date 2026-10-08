"""Tests for py_ci_shared.sql_verifier_coverage: what counts as a SQL constant, module naming, and the fail-closed paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared.sql_verifier_coverage import assert_verifier_covers_statements, sql_constants, verifier_lists

_REASON = "a reason that is long enough to count as documentation of why"


def _write(root: Path, rel: str, body: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_prose_starting_with_a_keyword_prefix_is_not_sql(tmp_path: Path):
    _write(tmp_path, "m.py", 'MSG = "Withdrawal failed"\nNOTE = "Selection of rows"\nQ = "WITH x AS (SELECT 1) SELECT * FROM x"\nU = "update t set a = 1"\n')
    assert sql_constants(tmp_path, exclude_top_dirs=()) == {"m.Q", "m.U"}


def test_comment_led_and_parenthesised_sql_is_sql(tmp_path: Path):
    _write(tmp_path, "m.py", 'A = """-- the listing\n/* hot path */\nSELECT 1"""\nB = "(SELECT 1) UNION (SELECT 2)"\nC = "-- only a comment"\n')
    assert sql_constants(tmp_path, exclude_top_dirs=()) == {"m.A", "m.B"}


def test_chained_targets_define_every_name(tmp_path: Path):
    _write(tmp_path, "m.py", 'A = B = "SELECT 1"\n')
    assert sql_constants(tmp_path, exclude_top_dirs=()) == {"m.A", "m.B"}


def test_a_package_init_constant_is_keyed_by_the_package(tmp_path: Path):
    _write(tmp_path, "pkg/__init__.py", 'Q = "SELECT 1"\n')
    _write(tmp_path, "pkg/sub.py", 'R = "SELECT 2"\n')
    _write(tmp_path, "__init__.py", 'TOP = "SELECT 3"\n')
    assert sql_constants(tmp_path, exclude_top_dirs=()) == {"pkg.Q", "pkg.sub.R", "__init__.TOP"}


def test_bom_files_are_read_on_both_sides(tmp_path: Path):
    (tmp_path / "m.py").write_bytes(b'\xef\xbb\xbfQ = "SELECT 1"\n')
    verifier = tmp_path / "scripts" / "verify.py"
    verifier.parent.mkdir()
    verifier.write_bytes(b'\xef\xbb\xbfSTATEMENTS = [("m", ("Q",))]\nEXCLUDED = {}\n')
    assert sql_constants(tmp_path, exclude_top_dirs={"scripts"}) == {"m.Q"}
    assert verifier_lists(verifier) == ({"m.Q"}, {})
    assert_verifier_covers_statements(tmp_path, verifier, exclude_top_dirs={"scripts"})


def test_an_unparsable_file_fails_instead_of_being_skipped(tmp_path: Path):
    _write(tmp_path, "m.py", 'Q = "SELECT 1"\n')
    _write(tmp_path, "broken.py", 'HIDDEN = "SELECT 2"\ndef (:\n')
    verifier = _write(tmp_path, "scripts/verify.py", f'STATEMENTS = [("m", ("Q",))]\nEXCLUDED = {{}}\n')
    with pytest.raises(AssertionError, match=r"broken\.py"):
        sql_constants(tmp_path, exclude_top_dirs={"scripts"})
    assert sql_constants(tmp_path, exclude_top_dirs={"scripts"}, allow_unparsed=True) == {"m.Q"}
    with pytest.raises(pytest.fail.Exception, match="could not be read or parsed"):
        assert_verifier_covers_statements(tmp_path, verifier, exclude_top_dirs={"scripts"})
    (tmp_path / "broken.py").unlink()
    assert_verifier_covers_statements(tmp_path, verifier, exclude_top_dirs={"scripts"})


def test_the_both_directions_contract(tmp_path: Path):
    _write(tmp_path, "m.py", 'Q = "SELECT 1"\nR = "SELECT 2"\n')
    verifier = _write(tmp_path, "scripts/verify.py", f'STATEMENTS = [("m", ("Q", "GONE"))]\nEXCLUDED = {{"m.R": "{_REASON}"}}\n')
    with pytest.raises(pytest.fail.Exception, match=r"m\.GONE"):
        assert_verifier_covers_statements(tmp_path, verifier, exclude_top_dirs={"scripts"})
    _write(tmp_path, "scripts/verify.py", 'STATEMENTS = [("m", ("Q",))]\nEXCLUDED = {"m.R": "short"}\n')
    with pytest.raises(pytest.fail.Exception, match="without a real reason"):
        assert_verifier_covers_statements(tmp_path, verifier, exclude_top_dirs={"scripts"})


class TestAStatementThatIsNotOneStringLiteral:
    """A constant built with `+`, one led by a session setting, and one led by REFRESH were invisible: the verifier lists could omit them and the gate stayed green (audit 2026-10-03 SQL-26)."""

    def test_a_chain_of_literals_is_one_statement(self, tmp_path: Path):
        _write(tmp_path, "m.py", 'Q = "WITH x AS (SELECT 1) " + "SELECT * FROM x"\n')
        assert sql_constants(tmp_path, exclude_top_dirs=()) == {"m.Q"}

    def test_a_module_level_name_in_the_chain_is_resolved(self, tmp_path: Path):
        _write(tmp_path, "m.py", 'CTE = "WITH x AS (SELECT 1) "\nQ = CTE + "SELECT * FROM x"\nFRAGMENT = CTE\n')
        assert sql_constants(tmp_path, exclude_top_dirs=()) == {"m.CTE", "m.Q", "m.FRAGMENT"}  # the fragment starts WITH, so it is a constant of its own

    def test_a_name_the_module_does_not_bind_leaves_the_statement_seen_by_its_literal(self, tmp_path: Path):
        """`from other import CTE` cannot be resolved from one file; the statement still exists and must be listed or excluded."""
        _write(tmp_path, "m.py", 'from other import CTE\nQ = CTE + "SELECT * FROM x"\n')
        assert sql_constants(tmp_path, exclude_top_dirs=()) == {"m.Q"}

    def test_a_chain_that_is_not_sql_is_not_a_statement(self, tmp_path: Path):
        _write(tmp_path, "m.py", 'MSG = "Selection of " + "rows"\nFROM_X = "FROM x" + " WHERE a"\n')
        assert sql_constants(tmp_path, exclude_top_dirs=()) == set()

    def test_a_refresh_is_a_statement(self, tmp_path: Path):
        _write(tmp_path, "m.py", 'R = "REFRESH MATERIALIZED VIEW CONCURRENTLY v"\n')
        assert sql_constants(tmp_path, exclude_top_dirs=()) == {"m.R"}

    def test_a_comment_after_the_opening_parenthesis_does_not_hide_the_statement(self, tmp_path: Path):
        _write(tmp_path, "m.py", 'P = """(-- the pending jobs\nSELECT 1)"""\nNOT = "(-- only a comment)"\n')
        assert sql_constants(tmp_path, exclude_top_dirs=()) == {"m.P"}

    def test_a_session_setting_in_front_is_not_what_is_sent(self, tmp_path: Path):
        _write(
            tmp_path,
            "m.py",
            'U = "SET LOCAL TimeZone = \'UTC\'; INSERT INTO t (a) VALUES (%s)"\nS = "SET statement_timeout = 0; SELECT 1"\nONLY = "SET LOCAL x = 1"\n',
        )
        assert sql_constants(tmp_path, exclude_top_dirs=()) == {"m.U", "m.S"}

    def test_a_built_statement_missing_from_the_list_now_fails_the_gate(self, tmp_path: Path):
        _write(tmp_path, "m.py", 'CTE = "WITH x AS (SELECT 1) "\nQ = CTE + "SELECT * FROM x"\n')
        verifier = _write(tmp_path, "scripts/verify.py", "STATEMENTS = []\nEXCLUDED = {}\n")
        with pytest.raises(pytest.fail.Exception, match=r"m\.Q"):
            assert_verifier_covers_statements(tmp_path, verifier, exclude_top_dirs={"scripts"})
