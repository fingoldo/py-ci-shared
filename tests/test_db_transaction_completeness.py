"""Unit tests for the db-transaction-completeness check. Real files on disk, no mocking,
matching this package's convention."""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.db_transaction_completeness import (
    assert_no_new_incomplete_transaction,
    find_incomplete_transactions,
)


def _module(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


_MISSING_COMMIT = """
    def load_prev_hashes(db, cids):
        db.execute(SQL, (cids,))
        return db.fetchall()
"""

_COMMITS = """
    def fetch_column(db, sql):
        db.execute(sql)
        rows = db.fetchall()
        db.commit()
        return rows
"""

_ROLLS_BACK_ON_FAILURE = """
    def try_write(db, row):
        db.execute(INSERT_SQL, row)
        if bad(row):
            db.rollback()
            return
        db.commit()
"""

_NOT_A_HANDLE_PARAM = """
    def helper(items):
        db = build_fake()
        db.execute(SQL)
        return db.fetchall()
"""

_DELEGATED_VIA_CONTEXT_MANAGER = """
    def load(sql):
        with borrowed() as db:
            db.execute(sql)
            return db.fetchall()
"""

_PARAM_RESHADOWED_BY_CONTEXT_MANAGER = """
    def load(db, sql):
        with borrowed() as db:
            db.execute(sql)
            return db.fetchall()
"""

_TWO_HANDLES_ONE_MISSING = """
    def copy_row(conn, db):
        conn.execute(SELECT_SQL)
        row = conn.fetchone()
        db.execute(INSERT_SQL, row)
        db.commit()
"""


class TestFindIncompleteTransactions:
    def test_a_missing_commit_is_reported(self, tmp_path):
        path = _module(tmp_path, "bad.py", _MISSING_COMMIT)

        found = find_incomplete_transactions([path], tmp_path)

        assert len(found) == 1
        assert found[0].function == "load_prev_hashes"
        assert found[0].handle == "db"

    def test_a_real_commit_is_not_reported(self, tmp_path):
        path = _module(tmp_path, "good.py", _COMMITS)

        assert find_incomplete_transactions([path], tmp_path) == []

    def test_a_rollback_on_one_branch_and_commit_on_the_other_satisfies_it(self, tmp_path):
        path = _module(tmp_path, "good.py", _ROLLS_BACK_ON_FAILURE)

        assert find_incomplete_transactions([path], tmp_path) == []

    def test_a_local_variable_named_db_is_not_a_parameter(self, tmp_path):
        """The check is about the function's OWN parameter, not any name that happens to be `db`."""
        path = _module(tmp_path, "irrelevant.py", _NOT_A_HANDLE_PARAM)

        assert find_incomplete_transactions([path], tmp_path) == []

    def test_a_with_block_on_an_autocommitting_context_manager_is_exempt(self, tmp_path):
        path = _module(tmp_path, "ok.py", _DELEGATED_VIA_CONTEXT_MANAGER)

        assert find_incomplete_transactions([path], tmp_path, autocommitting_context_managers=frozenset({"borrowed"})) == []

    def test_the_exemption_actually_suppresses_a_would_be_finding(self, tmp_path):
        """Real teeth for the exemption: the same parameter name, reshadowed by the `with` block,
        is flagged without the exemption list and clean with it."""
        path = _module(tmp_path, "reshadowed.py", _PARAM_RESHADOWED_BY_CONTEXT_MANAGER)

        assert len(find_incomplete_transactions([path], tmp_path)) == 1
        assert find_incomplete_transactions([path], tmp_path, autocommitting_context_managers=frozenset({"borrowed"})) == []

    def test_two_handles_only_the_uncommitted_one_is_reported(self, tmp_path):
        path = _module(tmp_path, "half.py", _TWO_HANDLES_ONE_MISSING)

        found = find_incomplete_transactions([path], tmp_path)

        assert len(found) == 1
        assert found[0].handle == "conn"


class TestAssertNoNewIncompleteTransaction:
    def test_a_clean_tree_passes(self, tmp_path):
        _module(tmp_path, "good.py", _COMMITS)
        baseline = tmp_path / "baseline.json"
        baseline.write_text("{}", encoding="utf-8")

        assert_no_new_incomplete_transaction(files=[tmp_path / "good.py"], repo_root=tmp_path, baseline_path=baseline)

    def test_a_new_incomplete_transaction_fails(self, tmp_path):
        _module(tmp_path, "bad.py", _MISSING_COMMIT)
        baseline = tmp_path / "baseline.json"
        baseline.write_text("{}", encoding="utf-8")

        with pytest.raises(AssertionError, match="never commit"):
            assert_no_new_incomplete_transaction(files=[tmp_path / "bad.py"], repo_root=tmp_path, baseline_path=baseline)

    def test_a_baselined_incomplete_transaction_does_not_fail(self, tmp_path):
        path = _module(tmp_path, "bad.py", _MISSING_COMMIT)
        found = find_incomplete_transactions([path], tmp_path)
        baseline = tmp_path / "baseline.json"
        baseline.write_text(json.dumps({found[0].key: "known, tracked"}), encoding="utf-8")

        assert_no_new_incomplete_transaction(files=[path], repo_root=tmp_path, baseline_path=baseline)

    def test_a_stale_baseline_entry_fails_too(self, tmp_path):
        """The fixed function no longer needs its exemption -- the baseline must shrink, not just
        grow."""
        _module(tmp_path, "good.py", _COMMITS)
        baseline = tmp_path / "baseline.json"
        baseline.write_text(json.dumps({"good.py::fetch_column::db": "stale"}), encoding="utf-8")

        with pytest.raises(AssertionError, match="no longer describe"):
            assert_no_new_incomplete_transaction(files=[tmp_path / "good.py"], repo_root=tmp_path, baseline_path=baseline)
