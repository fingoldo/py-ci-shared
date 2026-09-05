"""The SQL-verification harness: what it reports, and what it exits with.

A fake connection is the RIGHT tool here and only here. The subject under test is the harness --
whether a failure is reported instead of raised, whether one bad statement poisons the rest of the
sweep, and which exit code a missing database produces. The thing a fake connection cannot prove --
that PostgreSQL accepts a statement -- is exactly what the harness exists to have a real server
answer, and is never asserted here.
"""

from __future__ import annotations

import sys
import types

from py_ci_shared import sql_verify as sv


class _Cursor:
    def __init__(self, conn, rows, raises):
        self._conn, self._rows, self._raises = conn, rows, raises

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self._conn.executed.append((sql, params))
        if self._raises is not None:
            raise self._raises

    def fetchall(self):
        return self._rows


class _Conn:
    """Minimal psycopg2-shaped connection: enough to drive the harness, nothing more."""

    def __init__(self, rows=(), raises=None):
        self.rows, self.raises = list(rows), raises
        self.executed, self.commits, self.rollbacks = [], 0, 0

    def cursor(self):
        return _Cursor(self, self.rows, self.raises)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestDsnFromEnv:
    def test_the_first_non_empty_variable_wins(self, monkeypatch):
        monkeypatch.setenv("A_DSN", "   ")
        monkeypatch.setenv("B_DSN", "postgresql://h/db")
        assert sv.dsn_from_env(("A_DSN", "B_DSN")) == "postgresql://h/db"

    def test_no_variable_set_is_none_not_an_exception(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert sv.dsn_from_env(("NOT_SET_ANYWHERE",)) is None

    def test_a_sqlalchemy_driver_suffix_is_stripped(self, monkeypatch):
        """The point is reusing the APPLICATION's own variable, and psycopg2 cannot parse its shape."""
        monkeypatch.setenv("APP_DSN", "postgresql+asyncpg://u:p@h:5432/db")
        assert sv.dsn_from_env(("APP_DSN",)) == "postgresql://u:p@h:5432/db"
        monkeypatch.setenv("APP_DSN", "postgresql+psycopg://u@h/db")
        assert sv.dsn_from_env(("APP_DSN",)) == "postgresql://u@h/db"


class TestCheck:
    def test_a_statement_that_runs_is_reported_ok(self, capsys):
        conn = _Conn(rows=[(1, "x")])
        assert sv.check(conn, "listing", "SELECT 1", {"a": 2}) is True
        assert conn.executed == [("SELECT 1", {"a": 2})]
        out = capsys.readouterr().out
        assert out.startswith("OK   listing: 1 row(s)")

    def test_a_failing_statement_is_reported_not_raised(self, capsys):
        conn = _Conn(raises=RuntimeError("syntax error at or near \"FROM\""))
        assert sv.check(conn, "listing", "SELECT ,FROM t") is False
        assert "FAIL listing: RuntimeError: syntax error" in capsys.readouterr().out

    def test_a_failure_rolls_back_so_the_rest_of_the_sweep_still_runs(self):
        """Without this, one bad statement aborts the transaction and every later check fails with
        "current transaction is aborted" -- blaming statements that are perfectly fine."""
        conn = _Conn(raises=RuntimeError("boom"))
        sv.check(conn, "bad", "SELECT 1")
        assert conn.rollbacks == 1 and conn.commits == 0

    def test_no_params_passes_an_empty_mapping_not_none(self):
        conn = _Conn()
        sv.check(conn, "l", "SELECT 1")
        assert conn.executed == [("SELECT 1", {})]

    def test_an_empty_result_is_still_a_pass(self, capsys):
        assert sv.check(_Conn(rows=[]), "l", "SELECT 1 WHERE false") is True
        assert "OK   l: 0 row(s), first=None" in capsys.readouterr().out


class TestCheckLoader:
    def test_a_loader_that_returns_rows_is_ok(self, capsys):
        assert sv.check_loader(None, "load_x", lambda: [{"a": 1}]) is True
        assert "OK   load_x: 1 row(s)" in capsys.readouterr().out

    def test_a_loader_that_raises_is_a_failure(self, capsys):
        def _boom():
            raise ValueError("no such column")

        assert sv.check_loader(None, "load_x", _boom) is False
        assert "FAIL load_x: ValueError: no such column" in capsys.readouterr().out

    def test_a_loader_that_swallowed_its_error_is_a_failure_too(self, capsys):
        """A result carrying `.failed` returned instead of raising is the exact shape that would
        otherwise be reported OK -- the loader caught the error, so the harness must not."""

        class _Rows(list):
            failed = True

        assert sv.check_loader(None, "load_x", lambda: _Rows([{"a": 1}])) is False
        assert "flagged-failed" in capsys.readouterr().out

    def test_plain_tuple_rows_are_summarised_too(self, capsys):
        assert sv.check_loader(None, "load_x", lambda: [(1, 2)]) is True
        assert "first=('1', '2')" in capsys.readouterr().out


def _fake_psycopg2(monkeypatch, conn=None, connect_error=None):
    calls = []

    def _connect(dsn, **kwargs):
        calls.append((dsn, kwargs))
        if connect_error is not None:
            raise connect_error
        return conn if conn is not None else _Conn()

    monkeypatch.setitem(sys.modules, "psycopg2", types.SimpleNamespace(connect=_connect))
    return calls


class TestRunChecks:
    def test_all_passing_checks_exit_zero(self, monkeypatch, capsys):
        _fake_psycopg2(monkeypatch)
        rc = sv.run_checks(lambda conn: True, dsn="postgresql://h/db", argv=[])
        assert rc == 0
        assert "ALL OK" in capsys.readouterr().out

    def test_one_failing_check_exits_one(self, monkeypatch, capsys):
        _fake_psycopg2(monkeypatch)
        rc = sv.run_checks(lambda conn: False, dsn="postgresql://h/db", argv=[])
        assert rc == 1
        assert "SOMETHING FAILED" in capsys.readouterr().out

    def test_every_check_runs_even_after_one_fails(self, monkeypatch):
        """A failing group must not hide the groups after it -- the sweep's value is the full list."""
        _fake_psycopg2(monkeypatch)
        seen = []

        def _mk(name, ok):
            def _run(conn):
                seen.append(name)
                return ok

            return _run

        rc = sv.run_checks([_mk("a", False), _mk("b", True), _mk("c", True)], dsn="postgresql://h/db", argv=[])
        assert seen == ["a", "b", "c"] and rc == 1

    def test_no_database_configured_is_skipped_not_failed(self, monkeypatch, capsys):
        _fake_psycopg2(monkeypatch)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        rc = sv.run_checks(lambda conn: True, env_names=("NOT_SET",), argv=[])
        assert rc == sv.SKIPPED, "distinct from 1, so nobody reads 'not verified' as 'verified clean'"
        assert "SKIPPED" in capsys.readouterr().out

    def test_skip_without_db_turns_that_into_a_pass_for_a_pre_push_hook(self, monkeypatch):
        _fake_psycopg2(monkeypatch)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert sv.run_checks(lambda conn: True, env_names=("NOT_SET",), argv=["--skip-without-db"]) == 0

    def test_an_unreachable_server_is_a_skip_not_a_failed_statement(self, monkeypatch, capsys):
        _fake_psycopg2(monkeypatch, connect_error=OSError("connection refused"))
        assert sv.run_checks(lambda conn: True, dsn="postgresql://h/db", argv=[]) == sv.SKIPPED
        assert "unreachable" in capsys.readouterr().out
        assert sv.run_checks(lambda conn: True, dsn="postgresql://h/db", argv=["--skip-without-db"]) == 0

    def test_the_checks_never_run_when_there_is_no_database(self, monkeypatch):
        _fake_psycopg2(monkeypatch, connect_error=OSError("refused"))

        def _never(conn):
            raise AssertionError("a check ran without a usable connection")

        assert sv.run_checks(_never, dsn="postgresql://h/db", argv=[]) == sv.SKIPPED

    def test_the_reachability_probe_is_bounded_by_a_timeout(self, monkeypatch):
        """A pre-push hook that hangs on an unreachable host is a pre-push hook people disable."""
        calls = _fake_psycopg2(monkeypatch)
        sv.run_checks(lambda conn: True, dsn="postgresql://h/db", argv=[], connect_timeout=3)
        assert calls[0][1] == {"connect_timeout": 3}

    def test_sys_argv_is_read_when_argv_is_not_given(self, monkeypatch):
        _fake_psycopg2(monkeypatch)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setattr(sys, "argv", ["verify", "--skip-without-db"])
        assert sv.run_checks(lambda conn: True, env_names=("NOT_SET",)) == 0


def test_a_real_statement_would_reach_the_server(monkeypatch):
    """The end-to-end shape: `check` inside `run_checks` executes against the connection it opened."""
    conn = _Conn(rows=[(1,)])
    _fake_psycopg2(monkeypatch, conn=conn)
    rc = sv.run_checks(lambda c: sv.check(c, "listing", "SELECT 1"), dsn="postgresql://h/db", argv=[])
    assert rc == 0
    assert conn.executed == [("SELECT 1", {})]
