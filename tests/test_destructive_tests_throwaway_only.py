"""Tests for py_ci_shared.destructive_tests_throwaway_only: a test that writes SQL reaches only a throwaway server."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from py_ci_shared._core import CorpusError, EmptyScanError, UnparsedFilesError
from py_ci_shared.destructive_tests_throwaway_only import (
    RULE_NEVER_RUN,
    RULE_NO_ACCESSOR,
    RULE_PRODUCTION_DSN,
    RULE_RUNNER,
    RULE_STALE_ALLOW,
    assert_destructive_tests_are_throwaway_only,
    find_destructive_tests_throwaway_only,
)

BOM = b"\xef\xbb\xbf"
EMBEDDED = "python -m py_ci_shared.embedded_postgres run --env X_PG_DSN -- python -m pytest"

DELETING_ON_PRODUCTION = """\
import os

import psycopg2


def test_cleanup():
    connection = psycopg2.connect(os.environ["DATABASE_URL"])
    connection.cursor().execute("DELETE FROM ledger WHERE tag = %s", ("~01test",))
"""
DELETING_ON_THROWAWAY = """\
import psycopg2
from tests._live_dsn import throwaway_dsn


def test_cleanup():
    connection = psycopg2.connect(throwaway_dsn())
    connection.cursor().execute("DELETE FROM ledger WHERE tag = %s", ("~01test",))
"""


def _hook(hook_id: str, entry: str, *, extra: str = "") -> str:
    return f"""\
repos:
  - repo: local
    hooks:
      - id: {hook_id}
        name: {hook_id}
        entry: {json.dumps(entry)}
        language: system
        pass_filenames: false{extra}
"""


def _repo(tmp_path: Path, files: "dict[str, str | bytes]") -> Path:
    for rel, data in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return tmp_path


def _find(root: Path, *, tests: "str | list[str]" = "tests", configs: "list[str] | None" = None, **kwargs):
    roots = [root / t for t in ([tests] if isinstance(tests, str) else tests)]
    kwargs.setdefault("use_git", False)
    return find_destructive_tests_throwaway_only(
        roots, hook_config_paths=[root / c for c in (configs or [".pre-commit-config.yaml"])], repo_root=root, **kwargs
    )


def _rules(findings) -> "list[str]":
    return [f.rule for f in findings]


def test_the_seeded_incident_is_reported_with_its_path_line_and_rule(tmp_path):
    root = _repo(
        tmp_path,
        {"tests/test_ledger.py": DELETING_ON_PRODUCTION, ".pre-commit-config.yaml": _hook("live", "python -m pytest tests/test_ledger.py")},
    )
    found = _find(root)
    assert {(f.path, f.line, f.rule) for f in found} == {("tests/test_ledger.py", 7, RULE_PRODUCTION_DSN), ("tests/test_ledger.py", 8, RULE_RUNNER)}
    production = next(f for f in found if f.rule == RULE_PRODUCTION_DSN)
    assert (production.path, production.line) == ("tests/test_ledger.py", 7)
    assert "DATABASE_URL" in production.message and "throwaway_dsn" in production.message
    runner = next(f for f in found if f.rule == RULE_RUNNER)
    assert (runner.path, runner.line) == ("tests/test_ledger.py", 8)
    assert ".pre-commit-config.yaml::live" in runner.message


def test_negative_control_the_same_test_through_the_accessor_in_a_throwaway_hook_is_clean(tmp_path):
    root = _repo(
        tmp_path,
        {"tests/test_ledger.py": DELETING_ON_THROWAWAY, ".pre-commit-config.yaml": _hook("t", f"{EMBEDDED} tests/test_ledger.py")},
    )
    assert _find(root, min_destructive_files=1) == []


def test_the_assert_entry_raises_on_the_incident_and_passes_the_control(tmp_path):
    bad = _repo(
        tmp_path / "bad", {"tests/test_ledger.py": DELETING_ON_PRODUCTION, ".pre-commit-config.yaml": _hook("live", "python -m pytest tests/test_ledger.py")}
    )
    with pytest.raises(AssertionError, match="destructive-test-production-dsn"):
        assert_destructive_tests_are_throwaway_only(bad / "tests", hook_config_paths=[bad / ".pre-commit-config.yaml"], repo_root=bad, use_git=False)
    good = _repo(tmp_path / "good", {"tests/test_ledger.py": DELETING_ON_THROWAWAY, ".pre-commit-config.yaml": _hook("t", f"{EMBEDDED} tests/test_ledger.py")})
    assert_destructive_tests_are_throwaway_only(good / "tests", hook_config_paths=[good / ".pre-commit-config.yaml"], repo_root=good, use_git=False)


def test_a_file_no_hook_names_never_runs(tmp_path):
    root = _repo(tmp_path, {"tests/test_ledger.py": DELETING_ON_THROWAWAY, ".pre-commit-config.yaml": _hook("t", f"{EMBEDDED} tests/test_other.py")})
    found = _find(root)
    assert _rules(found) == [RULE_NEVER_RUN]
    assert found[0].path == "tests/test_ledger.py"


def test_every_entry_that_names_the_file_must_be_a_throwaway_one(tmp_path):
    config = """\
repos:
  - repo: local
    hooks:
      - id: throwaway
        name: throwaway
        entry: python -m py_ci_shared.embedded_postgres run --env X_PG_DSN -- python -m pytest tests/test_ledger.py
        language: system
      - id: live
        name: live
        entry: python -m pytest tests/test_ledger.py
        language: system
"""
    root = _repo(tmp_path, {"tests/test_ledger.py": DELETING_ON_THROWAWAY, ".pre-commit-config.yaml": config})
    found = _find(root)
    assert _rules(found) == [RULE_RUNNER]
    assert "::live" in found[0].message and "::throwaway" not in found[0].message


def test_the_harness_must_precede_the_pytest_it_wraps_in_the_same_invocation(tmp_path):
    entry = 'bash -c "python -m pytest tests/test_a.py && python -m py_ci_shared.embedded_postgres run --env X_PG_DSN -- python -m pytest tests/test_b.py"'
    root = _repo(
        tmp_path,
        {"tests/test_a.py": DELETING_ON_THROWAWAY, "tests/test_b.py": DELETING_ON_THROWAWAY, ".pre-commit-config.yaml": _hook("two", entry)},
    )
    found = _find(root)
    assert [(f.path, f.rule) for f in found] == [("tests/test_a.py", RULE_RUNNER)]


@pytest.mark.parametrize("accessor", ["live_dsn", "dsn_is_configured"])
def test_a_production_accessor_is_a_finding_even_beside_the_throwaway_one(tmp_path, accessor):
    source = f"""\
import psycopg2
from tests._live_dsn import {accessor}, throwaway_dsn


def test_x():
    {accessor}()
    psycopg2.connect(throwaway_dsn()).cursor().execute("INSERT INTO t VALUES (1)")
"""
    root = _repo(tmp_path, {"tests/test_x.py": source, ".pre-commit-config.yaml": _hook("t", f"{EMBEDDED} tests/test_x.py")})
    found = _find(root)
    assert [(f.rule, f.line) for f in found] == [(RULE_PRODUCTION_DSN, 6)]
    assert accessor in found[0].message


@pytest.mark.parametrize(
    "read",
    [
        'os.environ.get("DATABASE_JOBSTRACKER_URL")',
        'os.getenv("DATABASE_URL")',
        'os.environ["SUPABASE_DB_URL"]',
        'dotenv_values(".env")["DATABASE_URL"]',
        'environ.get("POSTGRES_URL")',
    ],
)
def test_reading_a_database_environment_variable_is_a_finding(tmp_path, read):
    source = f"""\
import os
from os import environ
import psycopg2


def test_x():
    dsn = {read}
    psycopg2.connect(dsn).cursor().execute("UPDATE t SET a = 1")
"""
    root = _repo(tmp_path, {"tests/test_x.py": source, ".pre-commit-config.yaml": _hook("t", f"{EMBEDDED} tests/test_x.py")})
    assert _rules(_find(root)) == [RULE_PRODUCTION_DSN]


def test_setting_or_storing_a_database_variable_is_not_reading_it(tmp_path):
    source = """\
import os
import psycopg2
from tests._live_dsn import throwaway_dsn


def test_x(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "x")
    os.environ["DATABASE_URL"] = "y"
    psycopg2.connect(throwaway_dsn()).cursor().execute("INSERT INTO t VALUES (1)")
"""
    root = _repo(tmp_path, {"tests/test_x.py": source, ".pre-commit-config.yaml": _hook("t", f"{EMBEDDED} tests/test_x.py")})
    assert _find(root, min_destructive_files=1) == []


def test_a_database_file_that_never_asks_for_the_throwaway_dsn_is_reported(tmp_path):
    source = """\
import pytest

pytestmark = pytest.mark.real_database


def test_x(conn):
    conn.cursor().execute("TRUNCATE TABLE t")
"""
    root = _repo(tmp_path, {"tests/test_x.py": source, ".pre-commit-config.yaml": _hook("t", f"{EMBEDDED} tests/test_x.py")})
    found = _find(root)
    assert [(f.rule, f.line) for f in found] == [(RULE_NO_ACCESSOR, 7)]


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO t VALUES (1)",
        "insert  into t values (1)",
        "UPDATE schema.t SET a = 1",
        "DELETE FROM t",
        "TRUNCATE TABLE t",
        "TRUNCATE t",
        "TRUNCATE t, u CASCADE",
        "DROP TABLE t",
        "DROP SCHEMA s CASCADE",
        "ALTER TABLE t ADD COLUMN c int",
        "CREATE TABLE t (a int)",
        "CREATE UNIQUE INDEX i ON t (a)",
        "CREATE TEMP TABLE t (a int)",
    ],
)
def test_every_write_verb_makes_a_connected_file_destructive(tmp_path, statement):
    source = f'import psycopg2\n\n\ndef test_x():\n    psycopg2.connect("host=127.0.0.1").cursor().execute("{statement}")\n'
    root = _repo(tmp_path, {"tests/test_x.py": source, ".pre-commit-config.yaml": _hook("t", "python -m pytest tests/test_x.py")})
    assert sorted(_rules(_find(root, min_destructive_files=1))) == sorted([RULE_NO_ACCESSOR, RULE_RUNNER])


@pytest.mark.parametrize(
    "body",
    [
        '    cursor.execute("SELECT 1 FROM t")',
        '    cursor.execute("SELECT * FROM t WHERE note = %s", ("please update the set",))',
        '    """INSERT INTO t is only the docstring"""',
        "    # DELETE FROM t in a comment, never executed",
        '    cursor.execute("TRUNCATE the value to 10 characters")',
    ],
)
def test_reads_prose_comments_and_docstrings_are_not_write_verbs(tmp_path, body):
    source = f"import psycopg2\n\n\ndef test_x(cursor):\n{body}\n    psycopg2.connect('host=127.0.0.1')\n"
    root = _repo(tmp_path, {"tests/test_x.py": source, ".pre-commit-config.yaml": _hook("t", "python -m pytest tests/test_x.py")})
    assert _find(root) == []


def test_a_docstring_holding_a_write_statement_is_not_a_write(tmp_path):
    source = 'import psycopg2\n\n\ndef test_x():\n    """DELETE FROM t WHERE x"""\n    psycopg2.connect("host=127.0.0.1")\n'
    root = _repo(tmp_path, {"tests/test_x.py": source, ".pre-commit-config.yaml": _hook("t", "python -m pytest tests/test_x.py")})
    assert _find(root) == []


def test_a_fake_cursor_unit_test_with_write_text_but_no_database_is_not_destructive(tmp_path):
    source = """\
def test_it_asks_for_the_delete(conn):
    run(conn)
    assert "DELETE FROM ledger" in conn.cursor().execute.call_args.args[0]
"""
    root = _repo(tmp_path, {"tests/test_x.py": source, ".pre-commit-config.yaml": _hook("t", "python -m pytest tests/test_x.py")})
    assert _find(root, min_destructive_files=0) == []


def test_an_fstring_with_a_constant_write_verb_counts_and_a_split_literal_too(tmp_path):
    source = """\
import psycopg2


def test_x(table):
    cursor = psycopg2.connect("host=127.0.0.1").cursor()
    cursor.execute(f"DELETE FROM {table} WHERE a = 1")
    cursor.execute("INSERT " "INTO t VALUES (1)")
"""
    root = _repo(tmp_path, {"tests/test_x.py": source, ".pre-commit-config.yaml": _hook("t", "python -m pytest tests/test_x.py")})
    found = _find(root)
    assert {f.line for f in found} == {6}
    assert RULE_RUNNER in _rules(found)


def test_sqlite_in_memory_is_its_own_throwaway_and_is_not_a_server_connection(tmp_path):
    source = 'import sqlite3\n\n\ndef test_x():\n    sqlite3.connect(":memory:").execute("DELETE FROM t")\n'
    root = _repo(tmp_path, {"tests/test_x.py": source, ".pre-commit-config.yaml": _hook("t", "python -m pytest tests/test_x.py")})
    assert _find(root) == []


def test_an_aliased_connect_import_is_still_a_connection(tmp_path):
    source = 'from psycopg2 import connect as dial\n\n\ndef test_x():\n    dial("host=127.0.0.1").cursor().execute("DELETE FROM t")\n'
    root = _repo(tmp_path, {"tests/test_x.py": source, ".pre-commit-config.yaml": _hook("t", "python -m pytest tests/test_x.py")})
    assert RULE_NO_ACCESSOR in _rules(_find(root))


def test_the_database_markers_are_configurable_and_default_to_real_database_and_integration(tmp_path):
    marked = "import pytest\n\n\n@pytest.mark.{mark}\ndef test_x(conn):\n    conn.cursor().execute('DELETE FROM t')\n"
    files = {f"tests/test_{m}.py": marked.format(mark=m) for m in ("real_database", "integration", "slow_db", "unit")}
    root = _repo(tmp_path, {**files, ".pre-commit-config.yaml": _hook("t", "python -m pytest tests -q")})
    assert {f.path for f in _find(root)} == {"tests/test_real_database.py", "tests/test_integration.py"}
    assert {f.path for f in _find(root, db_markers=("slow_db",))} == {"tests/test_slow_db.py"}


def test_a_fixture_request_counts_as_the_configured_accessor(tmp_path):
    source = "import pytest\n\npytestmark = pytest.mark.real_database\n\n\ndef test_x(pg_cursor):\n    pg_cursor.execute('DELETE FROM t')\n"
    root = _repo(tmp_path, {"tests/test_x.py": source, ".pre-commit-config.yaml": _hook("t", f"{EMBEDDED} tests/test_x.py")})
    assert _rules(_find(root)) == [RULE_NO_ACCESSOR]
    assert _find(root, throwaway_accessors=("throwaway_dsn", "pg_cursor")) == []


def test_usefixtures_names_the_accessor_too(tmp_path):
    source = "import pytest\n\n\n@pytest.mark.usefixtures('pg_conn')\ndef test_x():\n    run('DELETE FROM t')\n"
    root = _repo(tmp_path, {"tests/test_x.py": source, ".pre-commit-config.yaml": _hook("t", f"{EMBEDDED} tests/test_x.py")})
    assert _find(root, throwaway_accessors=("pg_conn",)) == []


# ---------------------------------------------------------------------------------------------------------------------
# monorepo layout


def test_test_roots_in_several_packages_and_a_config_at_the_repository_root(tmp_path):
    entry = "bash -c \"cd 'pkg_b' && " + EMBEDDED + ' tests/test_ledger.py"'
    root = _repo(
        tmp_path,
        {
            "pkg_a/tests/test_ledger.py": DELETING_ON_THROWAWAY,
            "pkg_b/tests/test_ledger.py": DELETING_ON_THROWAWAY,
            ".pre-commit-config.yaml": _hook("b", entry),
        },
    )
    found = _find(root, tests=["pkg_a/tests", "pkg_b/tests"])
    assert [(f.path, f.rule) for f in found] == [("pkg_a/tests/test_ledger.py", RULE_NEVER_RUN)]


def test_a_directory_argument_names_every_file_below_it(tmp_path):
    root = _repo(
        tmp_path,
        {
            "pkg/tests/integration/test_ledger.py": DELETING_ON_THROWAWAY,
            ".pre-commit-config.yaml": _hook("d", "bash -c \"cd 'pkg' && python -m pytest tests/integration\""),
        },
    )
    found = _find(root, tests="pkg/tests")
    assert _rules(found) == [RULE_RUNNER]


def test_a_node_id_names_its_file(tmp_path):
    root = _repo(
        tmp_path, {"tests/test_ledger.py": DELETING_ON_THROWAWAY, ".pre-commit-config.yaml": _hook("n", "python -m pytest tests/test_ledger.py::test_cleanup")}
    )
    assert _rules(_find(root)) == [RULE_RUNNER]


def test_a_pathless_pytest_names_nothing_so_discovery_cannot_excuse_or_accuse(tmp_path):
    root = _repo(tmp_path, {"tests/test_ledger.py": DELETING_ON_THROWAWAY, ".pre-commit-config.yaml": _hook("p", "python -m pytest -q")})
    assert _rules(_find(root)) == [RULE_NEVER_RUN]


def test_a_marker_expression_that_deselects_the_file_means_the_runner_does_not_run_it(tmp_path):
    source = "import pytest\n\npytestmark = pytest.mark.integration\n\n\ndef test_x(pg_conn):\n    pg_conn.execute('DELETE FROM t')\n"
    unit = _hook("unit", "python -m pytest tests -m 'not integration'")
    root = _repo(tmp_path, {"tests/test_x.py": source, ".pre-commit-config.yaml": unit})
    assert _rules(_find(root, throwaway_accessors=("pg_conn",))) == [RULE_NEVER_RUN]
    selecting = _hook("int", "python -m pytest tests -m integration")
    (root / ".pre-commit-config.yaml").write_text(selecting, encoding="utf-8")
    assert _rules(_find(root, throwaway_accessors=("pg_conn",))) == [RULE_RUNNER]


def test_addopts_supply_the_default_expression(tmp_path):
    source = "import pytest\n\npytestmark = pytest.mark.integration\n\n\ndef test_x(pg_conn):\n    pg_conn.execute('DELETE FROM t')\n"
    root = _repo(tmp_path, {"tests/test_x.py": source, ".pre-commit-config.yaml": _hook("p", "python -m pytest tests")})
    assert _rules(_find(root, throwaway_accessors=("pg_conn",))) == [RULE_RUNNER]
    assert _rules(_find(root, throwaway_accessors=("pg_conn",), addopts="-m 'not integration'")) == [RULE_NEVER_RUN]


def test_a_workflow_step_uses_its_working_directory_and_is_judged_like_a_hook(tmp_path):
    workflow = """\
name: ci
on: push
jobs:
  db:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: pkg
    steps:
      - name: live tests
        run: pytest tests/test_ledger.py
"""
    root = _repo(
        tmp_path, {"pkg/tests/test_ledger.py": DELETING_ON_THROWAWAY, ".github/workflows/ci.yml": workflow, ".pre-commit-config.yaml": _hook("x", "true")}
    )
    found = _find(root, tests="pkg/tests", configs=[".pre-commit-config.yaml", ".github/workflows"])
    assert _rules(found) == [RULE_RUNNER]
    assert "ci.yml::db::live tests" in found[0].message


def test_a_runner_that_starts_its_own_disposable_server_can_be_allowed_with_a_reason(tmp_path):
    workflow = "name: ci\non: push\njobs:\n  db:\n    runs-on: x\n    steps:\n      - name: containers\n        run: pytest tests/test_ledger.py\n"
    root = _repo(tmp_path, {"tests/test_ledger.py": DELETING_ON_THROWAWAY, ".github/workflows/ci.yml": workflow})
    assert _rules(_find(root, configs=[".github/workflows"])) == [RULE_RUNNER]
    assert _find(root, configs=[".github/workflows"], allow_runners={"ci.yml::db": "testcontainers starts the server"}) == []
    with pytest.raises(ValueError, match="no reason"):
        _find(root, configs=[".github/workflows"], allow_runners={"ci.yml::db": " "})


def test_the_runner_pattern_is_configurable(tmp_path):
    root = _repo(
        tmp_path, {"tests/test_ledger.py": DELETING_ON_THROWAWAY, ".pre-commit-config.yaml": _hook("w", "with-disposable-db pytest tests/test_ledger.py")}
    )
    assert _rules(_find(root)) == [RULE_RUNNER]
    assert _find(root, runner_pattern=r"\bwith-disposable-db\b") == []


# ---------------------------------------------------------------------------------------------------------------------
# allowlist


REASON = "every statement targets a TEMP table that drops with the transaction"


def test_an_allowlisted_file_is_skipped_by_repo_relative_path_or_suffix(tmp_path):
    root = _repo(
        tmp_path, {"pkg/tests/test_ledger.py": DELETING_ON_PRODUCTION, ".pre-commit-config.yaml": _hook("l", "python -m pytest pkg/tests/test_ledger.py")}
    )
    assert _rules(_find(root, tests="pkg/tests")) != []
    assert _find(root, tests="pkg/tests", allow={"pkg/tests/test_ledger.py": REASON}) == []
    assert _find(root, tests="pkg/tests", allow={"tests/test_ledger.py": REASON}) == []


def test_an_allowlist_entry_needs_a_reason(tmp_path):
    root = _repo(tmp_path, {"tests/test_ledger.py": DELETING_ON_PRODUCTION, ".pre-commit-config.yaml": _hook("l", "true")})
    for reason in ("", "   "):
        with pytest.raises(ValueError, match="no reason"):
            _find(root, allow={"tests/test_ledger.py": reason})


def test_an_allowlist_entry_that_matches_no_destructive_file_is_reported_as_stale(tmp_path):
    root = _repo(tmp_path, {"tests/test_ledger.py": DELETING_ON_THROWAWAY, ".pre-commit-config.yaml": _hook("t", f"{EMBEDDED} tests/test_ledger.py")})
    found = _find(root, allow={"tests/test_gone.py": REASON})
    assert [(f.path, f.rule) for f in found] == [("tests/test_gone.py", RULE_STALE_ALLOW)]


def test_an_allowlisted_file_is_not_stale_for_the_one_it_names_only(tmp_path):
    root = _repo(tmp_path, {"tests/test_ledger.py": DELETING_ON_PRODUCTION, ".pre-commit-config.yaml": _hook("l", "true")})
    found = _find(root, allow={"tests/test_ledger.py": REASON, "tests/test_gone.py": REASON})
    assert _rules(found) == [RULE_STALE_ALLOW]


# ---------------------------------------------------------------------------------------------------------------------
# input errors: a gate that cannot read something must say so


def test_a_bom_prefixed_test_and_config_are_reported_like_the_plain_ones(tmp_path):
    plain = _repo(
        tmp_path / "plain", {"tests/test_ledger.py": DELETING_ON_PRODUCTION, ".pre-commit-config.yaml": _hook("l", "python -m pytest tests/test_ledger.py")}
    )
    bommed = _repo(
        tmp_path / "bom",
        {
            "tests/test_ledger.py": BOM + DELETING_ON_PRODUCTION.encode(),
            ".pre-commit-config.yaml": BOM + _hook("l", "python -m pytest tests/test_ledger.py").encode(),
        },
    )
    assert [f.render() for f in _find(bommed)] == [f.render() for f in _find(plain)] != []


def test_an_unparsable_test_file_fails_by_name_unless_allowed(tmp_path):
    root = _repo(tmp_path, {"tests/test_ok.py": "x = 1\n", "tests/test_broken.py": "def broken(:\n", ".pre-commit-config.yaml": _hook("l", "true")})
    with pytest.raises(UnparsedFilesError, match=r"test_broken[.]py"):
        _find(root)
    assert _find(root, allow_unparsed=True) == []


def test_an_empty_corpus_fails_the_floor(tmp_path):
    root = _repo(tmp_path, {"tests/readme.txt": "x", ".pre-commit-config.yaml": _hook("l", "true")})
    with pytest.raises(EmptyScanError):
        _find(root)


def test_a_scan_that_finds_no_destructive_file_can_be_made_to_fail(tmp_path):
    root = _repo(tmp_path, {"tests/test_ok.py": "x = 1\n", ".pre-commit-config.yaml": _hook("l", "true")})
    assert _find(root) == []
    with pytest.raises(AssertionError, match="only 0 destructive"):
        _find(root, min_destructive_files=1)


def test_a_missing_config_is_an_error_not_a_pass(tmp_path):
    root = _repo(tmp_path, {"tests/test_ledger.py": DELETING_ON_THROWAWAY})
    with pytest.raises(CorpusError, match="does not exist"):
        _find(root)


def test_no_configs_at_all_is_an_error(tmp_path):
    root = _repo(tmp_path, {"tests/test_ledger.py": DELETING_ON_THROWAWAY})
    with pytest.raises(ValueError, match="hook_config_paths is empty"):
        find_destructive_tests_throwaway_only([root / "tests"], hook_config_paths=[], repo_root=root, use_git=False)


def test_a_workflows_directory_without_yaml_is_an_error(tmp_path):
    root = _repo(tmp_path, {"tests/test_ledger.py": DELETING_ON_THROWAWAY, ".github/workflows/readme.txt": "x"})
    with pytest.raises(CorpusError, match=r"no [.]yml/[.]yaml"):
        _find(root, configs=[".github/workflows"])


def test_a_config_that_is_not_yaml_or_not_a_pipeline_config_is_an_error(tmp_path):
    root = _repo(tmp_path, {"tests/test_ledger.py": DELETING_ON_THROWAWAY, ".pre-commit-config.yaml": "a: [unclosed\n"})
    with pytest.raises(CorpusError, match="not valid YAML"):
        _find(root)
    (root / ".pre-commit-config.yaml").write_text("just: a mapping\n", encoding="utf-8")
    with pytest.raises(CorpusError, match="neither a pre-commit config"):
        _find(root)


def test_tests_outside_the_repository_root_are_an_error(tmp_path):
    root = _repo(tmp_path / "repo", {".pre-commit-config.yaml": _hook("l", "true")})
    outside = _repo(tmp_path / "elsewhere", {"tests/test_ledger.py": DELETING_ON_THROWAWAY})
    with pytest.raises(CorpusError, match="not under repo_root"):
        find_destructive_tests_throwaway_only([outside / "tests"], hook_config_paths=[root / ".pre-commit-config.yaml"], repo_root=root, use_git=False)


def test_the_repository_root_is_inferred_from_the_configs(tmp_path):
    root = _repo(
        tmp_path,
        {
            "tests/test_ledger.py": DELETING_ON_PRODUCTION,
            ".github/workflows/ci.yml": "name: x\non: push\njobs:\n  a:\n    runs-on: x\n    steps:\n      - run: echo\n",
        },
    )
    found = find_destructive_tests_throwaway_only(root / "tests", hook_config_paths=[root / ".github" / "workflows" / "ci.yml"], use_git=False)
    assert {f.path for f in found} == {"tests/test_ledger.py"}


def test_configs_implying_different_roots_need_an_explicit_repo_root(tmp_path):
    a = _repo(tmp_path / "a", {".pre-commit-config.yaml": _hook("l", "true"), "tests/test_ok.py": "x = 1\n"})
    b = _repo(tmp_path / "b", {".pre-commit-config.yaml": _hook("l", "true")})
    with pytest.raises(ValueError, match="pass repo_root"):
        find_destructive_tests_throwaway_only(a / "tests", hook_config_paths=[a / ".pre-commit-config.yaml", b / ".pre-commit-config.yaml"], use_git=False)


def test_the_baseline_form_accepts_a_recorded_finding_and_fails_a_new_one(tmp_path, monkeypatch):
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")
    root = _repo(tmp_path, {"tests/test_ledger.py": DELETING_ON_PRODUCTION, ".pre-commit-config.yaml": _hook("l", "python -m pytest tests/test_ledger.py")})
    baseline = tmp_path / "baseline.json"
    common = dict(hook_config_paths=[root / ".pre-commit-config.yaml"], repo_root=root, baseline_path=baseline, use_git=False)
    with pytest.raises(pytest.skip.Exception, match="baseline rewritten"):  # a refresh run reports itself as a skip by design
        assert_destructive_tests_are_throwaway_only(root / "tests", refresh=True, **common)
    assert_destructive_tests_are_throwaway_only(root / "tests", **common)
    (root / "tests" / "test_second.py").write_text(textwrap.dedent(DELETING_ON_PRODUCTION), encoding="utf-8")
    (root / ".pre-commit-config.yaml").write_text(_hook("l", "python -m pytest tests"), encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match=r"test_second[.]py"):
        assert_destructive_tests_are_throwaway_only(root / "tests", **common)
