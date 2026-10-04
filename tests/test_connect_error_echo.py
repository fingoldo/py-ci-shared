"""connect_error_echo: a connect/DSN failure's text must not be printed, since libpq echoes the DSN in it.

Fixtures use a fake DSN only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from py_ci_shared import connect_error_echo as gate
from py_ci_shared.connect_error_echo import assert_no_connect_error_echo, find_connect_error_echo

NL = chr(10)


def _write(root: Path, rel: str, lines: list) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((NL.join(lines) + NL).encode("utf-8"))
    return path


def _handler(body: list, head: tuple = ("import psycopg2",), call: str = "conn = psycopg2.connect(dsn)", catch: str = "except Exception as e:") -> list:
    return [
        *head,
        "",
        "def probe(dsn):",
        "    try:",
        f"        {call}",
        f"    {catch}",
        *[f"        {line}" for line in body],
        "        return None",
        "    return conn",
    ]


def _found(tmp_path: Path, lines: list, **kwargs) -> list:
    _write(tmp_path, "probe.py", lines)
    return [f for f in find_connect_error_echo(tmp_path, use_git=False, **kwargs) if f.rule == gate.RULE]


def test_the_audit_probe_that_printed_the_dsn_is_reported(tmp_path: Path):
    """The real shape: `except Exception as e: print(e)` around psycopg2.connect printed the dashboard DSN three times."""
    found = _found(tmp_path, _handler(["print(e)"]))
    assert len(found) == 1
    assert found[0].line == 7 and "print(...)" in found[0].message and "probe" in found[0].message


def test_the_safe_probe_form_is_clean(tmp_path: Path):
    """dashboard/scripts/safe_db_probe.py: the type and SQLSTATE only, the new error raised outside the handler."""
    lines = [
        "import psycopg2",
        "",
        "class ProbeError(Exception):",
        "    pass",
        "",
        "def describe_error(exc):",
        "    sqlstate = getattr(exc, 'pgcode', None)",
        "    return f'{type(exc).__name__} sqlstate={sqlstate}'",
        "",
        "def run_probe(dsn, connect=None):",
        "    if connect is None:",
        "        connect = psycopg2.connect",
        "    conn = None",
        "    failure = None",
        "    try:",
        "        conn = connect(dsn)",
        "        return conn.cursor().fetchall()",
        "    except Exception as exc:",
        "        failure = describe_error(exc)",
        "    finally:",
        "        if conn is not None:",
        "            conn.close()",
        "    raise ProbeError(failure)",
    ]
    assert _found(tmp_path, lines) == []


@pytest.mark.parametrize(
    "body",
    [
        ["print(str(e))"],
        ["msg = repr(e)"],
        ["print(f'connect failed: {e}')"],
        ["print('connect failed: %s' % e)"],
        ["print('connect failed: {}'.format(e))"],
        ["log.error('connect failed: %s', e)"],
        ["log.warning('failed', exc_info=True)"],
        ["log.exception('connect failed')"],
        ["traceback.print_exc()"],
        ["raise RuntimeError(str(e))"],
        ["raise RuntimeError(e)"],
        ["sys.exit(f'cannot connect: {e.args[0]}')"],
        ["print(e.pgerror)"],
    ],
)
def test_every_echo_shape_is_reported(tmp_path: Path, body: list):
    """Each way the message reaches output: a sink call, a format, a traceback printer, a new exception carrying it."""
    found = _found(tmp_path, _handler(body, head=("import psycopg2", "import logging", "import sys", "import traceback", "log = logging.getLogger()")))
    assert len(found) == 1, body


@pytest.mark.parametrize(
    "body",
    [
        ["print(type(e).__name__)"],
        ["print(f'failed: {type(e).__name__} sqlstate={e.pgcode}')"],
        ["print('sqlstate', getattr(e, 'pgcode', None))"],
        ["raise RuntimeError('cannot connect') from e"],
        ["raise"],
        ["print(f'failed: {redact_secrets(str(e))[:120]}')"],
        ["log.warning('failed', exc_info=False)"],
        ["if isinstance(e, ValueError):", "    print('bad dsn')"],
    ],
)
def test_the_safe_forms_are_not_reported(tmp_path: Path, body: list):
    """The type name, the SQLSTATE, a chained raise and a scrubbing helper say nothing the DSN is in."""
    head = ("import psycopg2", "import logging", "from redaction import redact_secrets", "log = logging.getLogger()")
    assert _found(tmp_path, _handler(body, head=head)) == []


@pytest.mark.parametrize(
    ("head", "call"),
    [
        (("import psycopg2 as pg",), "conn = pg.connect(dsn)"),
        (("from psycopg2 import connect as open_db",), "conn = open_db(dsn)"),
        (("import psycopg2", "connect = psycopg2.connect"), "conn = connect(dsn)"),
        (("import psycopg",), "conn = psycopg.connect(dsn)"),
        (("import asyncpg",), "conn = asyncpg.connect(dsn)"),
        (("from sqlalchemy import create_engine",), "conn = create_engine(dsn)"),
        (("from .settings import get_dsn",), "conn = get_dsn()"),
        (("from show_top_jobs import dsn_or_none",), "conn = dsn_or_none()"),
    ],
)
def test_every_connect_spelling_is_followed(tmp_path: Path, head: tuple, call: str):
    """Import aliases, a re-bound name and a project's own DSN resolver all count as the connect call."""
    assert len(_found(tmp_path, _handler(["print(e)"], head=head, call=call))) == 1


def test_a_try_without_a_connect_call_is_out_of_scope(tmp_path: Path):
    """print(e) after json.loads says nothing about a DSN; the gate is not a general print(e) ban."""
    assert _found(tmp_path, _handler(["print(e)"], head=("import json",), call="conn = json.loads(dsn)")) == []


def test_sqlite_connect_is_not_a_database_dsn(tmp_path: Path):
    """Bare `connect` is not in the default callees: sqlite3.connect takes a path, not a password."""
    assert _found(tmp_path, _handler(["print(e)"], head=("import sqlite3",), call="conn = sqlite3.connect(dsn)")) == []


def test_callees_are_configurable(tmp_path: Path):
    """A project's own opener joins the list by name."""
    lines = _handler(["print(e)"], head=("from mydb import open_session",), call="conn = open_session(dsn)")
    assert _found(tmp_path, lines) == []
    assert len(_found(tmp_path, lines, callees={"open_session"})) == 1


def test_a_handler_without_a_name_still_reports_a_traceback_printer(tmp_path: Path):
    """`except Exception: log.exception(...)` names no variable, but the traceback still carries the message."""
    found = _found(
        tmp_path, _handler(["log.exception('db down')"], head=("import psycopg2", "import logging", "log = logging.getLogger()"), catch="except Exception:")
    )
    assert len(found) == 1


def test_one_line_is_one_finding(tmp_path: Path):
    """print(f'{e}') is one echo, not an f-string plus a print."""
    assert len(_found(tmp_path, _handler(["print(f'x {e} {str(e)}')"]))) == 1


def test_the_marker_accepts_a_handler(tmp_path: Path):
    """A reviewed handler (a DSN known not to hold a password, say) can be accepted on the except line."""
    lines = _handler(["print(e)"], catch="except Exception as e:  # dsn-echo-ok: local socket DSN, no password")
    assert _found(tmp_path, lines) == []


def test_tests_are_skipped_unless_asked_for(tmp_path: Path):
    _write(tmp_path, "tests/test_probe.py", _handler(["print(e)"]))
    _write(tmp_path, "app.py", ["X = 1"])
    assert find_connect_error_echo(tmp_path, use_git=False) == []
    assert len(find_connect_error_echo(tmp_path, use_git=False, include_tests=True)) == 1


def test_the_assertion_names_the_site(tmp_path: Path):
    _write(tmp_path, "probe.py", _handler(["print(e)"]))
    with pytest.raises(pytest.fail.Exception, match=r"probe\.py:7"):
        assert_no_connect_error_echo(tmp_path, use_git=False)


def test_an_unparsable_file_fails_unless_allowed(tmp_path: Path):
    _write(tmp_path, "ok.py", ["X = 1"])
    _write(tmp_path, "broken.py", ["def f(:"])
    with pytest.raises(pytest.fail.Exception, match=r"broken\.py"):
        assert_no_connect_error_echo(tmp_path, use_git=False)
    assert_no_connect_error_echo(tmp_path, use_git=False, allow_unparsed=True)


def test_an_empty_corpus_fails_the_floor(tmp_path: Path):
    with pytest.raises(pytest.fail.Exception, match="parsed"):
        assert_no_connect_error_echo(tmp_path, use_git=False)


def test_a_baselined_finding_passes_and_a_new_one_fails(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")  # seeding a baseline is growth
    src = tmp_path / "src"
    _write(src, "probe.py", _handler(["print(e)"]))
    baseline = tmp_path / "baseline.json"
    with pytest.raises(pytest.skip.Exception):
        assert_no_connect_error_echo(src, use_git=False, baseline_path=baseline, refresh=True, request=None)
    assert json.loads(baseline.read_text(encoding="utf-8"))["entries"]
    monkeypatch.delenv("PY_CI_SHARED_REFRESH_ALLOW_GROW")
    assert_no_connect_error_echo(src, use_git=False, baseline_path=baseline, refresh=False)
    _write(src, "probe2.py", _handler(["print(e)"]))
    with pytest.raises(pytest.fail.Exception, match=r"probe2\.py"):
        assert_no_connect_error_echo(src, use_git=False, baseline_path=baseline, refresh=False)


def test_teeth_the_connect_condition_is_what_finds_the_probe(tmp_path: Path, monkeypatch):
    """Revert the core condition (the try body opens a connection): the motivating probe must then go unreported."""
    lines = _handler(["print(e)"])
    assert len(_found(tmp_path, lines)) == 1
    calls = []

    def never(self, body):
        calls.append(body)
        return False

    monkeypatch.setattr(gate._Callees, "in_body", never)
    assert _found(tmp_path, lines) == []
    assert calls, "the substitution was not applied: in_body was never consulted"


def test_teeth_the_sink_condition_is_what_finds_the_probe(tmp_path: Path, monkeypatch):
    """Revert the sink list to nothing: print(e) must then go unreported, so the positive test depends on it."""
    lines = _handler(["print(e)"])
    assert _found(tmp_path, lines, sinks=()) == []
    assert len(_found(tmp_path, lines, sinks={"print"})) == 1
