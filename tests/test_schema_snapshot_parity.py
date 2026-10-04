"""schema_snapshot_parity: pure comparison tests, the loud not-checked path, and canaries on a real throwaway server.

The server tests skip (with the reason naming the missing binaries) when there is no Postgres, like ``test_embedded_postgres.py``;
every behaviour they cover also has a pure test where it can, so a machine without binaries still pins the comparison.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import pytest

from py_ci_shared import schema_snapshot_parity as sp
from py_ci_shared.embedded_postgres import embedded_postgres, find_pg_bin

FIXTURES = Path(__file__).resolve().parent / "fixtures_schema_snapshot_parity"
SNAPSHOT = FIXTURES / "snapshot.json.canary"
needs_server = pytest.mark.skipif(
    find_pg_bin() is None,
    reason="no Postgres binaries here (PG_BIN / pgserver / PATH / `python -m py_ci_shared.embedded_postgres fetch`): server canaries NOT run",
)
REASON = "ahead of production by design"


def col(data_type="text", nullable="YES", **extra):
    return {"data_type": data_type, "is_nullable": nullable, "generated": False, "identity": None, **extra}


def cat(**tables):
    """cat(**{"a.t": {"c": col()}}) style: keys with ``__`` stand for dots."""
    return {k.replace("__", "."): {"columns": v, "indexes": None} for k, v in tables.items()}


def rules(findings):
    return Counter(f.rule for f in findings)


# ------------------------------------------------------------------------------------------------------------ split_statements


def test_split_keeps_dollar_quoted_blocks_and_quoted_semicolons_whole():
    sql = "CREATE TABLE a (x text DEFAULT 'a;b--c');\nDO $$ BEGIN PERFORM 1; PERFORM 2; END $$;\nSELECT $t$;$t$"
    assert sp.split_statements(sql) == [
        "CREATE TABLE a (x text DEFAULT 'a;b--c');",
        "DO $$ BEGIN PERFORM 1; PERFORM 2; END $$;",
        "SELECT $t$;$t$;",
    ]


def test_split_drops_line_and_nested_block_comments_and_empty_statements():
    sql = "-- lead; comment\nSELECT 1; /* a /* nested; */ still comment; */ SELECT 2;;\n-- tail"
    assert sp.split_statements(sql) == ["SELECT 1;", "SELECT 2;"]


def test_split_keeps_doubled_quotes_inside_one_literal():
    assert sp.split_statements("SELECT 'it''s; ok', \"a\"\"b;\";") == ["SELECT 'it''s; ok', \"a\"\"b;\";"]


# ------------------------------------------------------------------------------------------------------------ snapshot parsing


def test_the_committed_canary_snapshot_parses_and_a_legacy_snapshot_is_read_without_extra_attributes():
    parsed = sp.load_snapshot(SNAPSHOT)
    assert sorted(parsed) == ["app.events", "app.jobs"]
    assert parsed["app.jobs"]["columns"]["id"]["identity"] == "ALWAYS"
    legacy = sp.parse_snapshot({"tables": {"s.t": {"c": {"data_type": "text", "is_nullable": "NO"}}}})
    assert legacy["s.t"]["columns"]["c"] == {"data_type": "text", "is_nullable": "NO"}
    assert legacy["s.t"]["indexes"] is None


@pytest.mark.parametrize(
    "data, needle",
    [
        ([], "top level"),
        ({"tables": []}, "top level"),
        ({"schema_version": 2, "tables": {}}, "schema_version 2"),
        ({"tables": {"nodot": {}}}, "`schema.table`"),
        ({"schema_version": 1, "tables": {"s.t": {}}}, "no `columns`"),
        ({"schema_version": 1, "tables": {"s.t": {"columns": {"c": {"data_type": "text", "is_nullable": "MAYBE"}}}}}, "YES or NO"),
        ({"schema_version": 1, "tables": {"s.t": {"columns": {"c": {"data_type": "text", "is_nullable": "NO", "colour": 1}}}}}, "unknown keys"),
        ({"schema_version": 1, "tables": {"s.t": {"columns": {"c": {"data_type": "text", "is_nullable": "NO", "identity": "x"}}}}}, "identity"),
        ({"schema_version": 1, "tables": {"s.t": {"columns": {}, "indexes": "ix"}}}, "list of names"),
    ],
)
def test_a_malformed_snapshot_is_an_error_naming_the_defect(data, needle):
    with pytest.raises(sp.SchemaParityError, match=re.escape(needle)):
        sp.parse_snapshot(data, "snap.json")


def test_an_unreadable_snapshot_is_an_error_not_an_empty_comparison(tmp_path):
    with pytest.raises(sp.SchemaParityError, match="unreadable"):
        sp.load_snapshot(tmp_path / "absent.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(sp.SchemaParityError, match="unreadable"):
        sp.load_snapshot(bad)


# ------------------------------------------------------------------------------------------------------------ comparison


def test_each_difference_has_its_own_rule_and_names_old_to_new():
    snap = cat(
        s__t={"a": col("timestamp with time zone", "NO"), "b": col("integer"), "gone": col("text"), "g": col(generated=True), "i": col(identity="ALWAYS")},
        s__missing_table={"x": col()},
    )
    got = cat(s__t={"a": col("timestamp without time zone", "YES"), "b": col("integer"), "g": col(), "i": col(), "extra": col()}, s__stray={"y": col()})
    findings = sp.compare_catalogues(snap, got, path="schema.sql")
    assert rules(findings) == Counter(
        {
            "type-mismatch": 1,
            "nullability-mismatch": 1,
            "column-missing": 1,
            "generated-mismatch": 1,
            "identity-mismatch": 1,
            "extra-column": 1,
            "table-missing": 1,
            "extra-table": 1,
        }
    )
    by_rule = {f.rule: f.message for f in findings}
    assert by_rule["type-mismatch"].startswith("s.t.a: timestamp without time zone -> timestamp with time zone")
    assert by_rule["nullability-mismatch"].startswith("s.t.a: nullable YES -> NO")
    assert "s.t.gone" in by_rule["column-missing"] and "s.missing_table" in by_rule["table-missing"]
    assert "s.t.extra" in by_rule["extra-column"] and "s.stray" in by_rule["extra-table"]
    assert all(f.path == "schema.sql" for f in findings)


def test_identical_catalogues_have_no_findings_and_undeclared_attributes_are_not_compared():
    snap = {"s.t": {"columns": {"c": {"data_type": "text", "is_nullable": "YES"}}, "indexes": None}}
    got = cat(s__t={"c": col("text", "YES", generated=True, identity="ALWAYS", udt_name="text")})
    assert sp.compare_catalogues(snap, got) == []


def test_udt_name_is_compared_only_when_the_snapshot_declares_it():
    snap = cat(s__t={"v": col("USER-DEFINED", udt_name="vector")})
    assert rules(sp.compare_catalogues(snap, cat(s__t={"v": col("USER-DEFINED", udt_name="citext")}))) == Counter({"type-mismatch": 1})
    assert sp.compare_catalogues(snap, cat(s__t={"v": col("USER-DEFINED", udt_name="vector")})) == []


def test_allowed_extra_covers_a_column_or_a_whole_table_and_needs_a_reason():
    snap = cat(s__t={"a": col()})
    got = cat(s__t={"a": col(), "b": col()}, s__x={"c": col()})
    assert sp.compare_catalogues(snap, got, allowed_extra={"s.t.b": REASON, "s.x": REASON}) == []
    with pytest.raises(sp.SchemaParityError, match="needs a reason"):
        sp.compare_catalogues(snap, got, allowed_extra={"s.t.b": "", "s.x": REASON})
    with pytest.raises(sp.SchemaParityError, match="needs a reason"):
        sp.compare_catalogues(snap, got, allowed_missing={"s.t.zz": "short"})
    assert rules(sp.compare_catalogues(snap, got, allowed_extra={"s.t.b": REASON})) == Counter({"extra-table": 1})


def test_allowed_missing_covers_a_column_or_a_table():
    snap = cat(s__t={"a": col(), "b": col()}, s__m={"z": col()})
    got = cat(s__t={"a": col()})
    assert sp.compare_catalogues(snap, got, allowed_missing={"s.t.b": REASON, "s.m": REASON}) == []
    assert rules(sp.compare_catalogues(snap, got, allowed_missing={"s.t.b": REASON})) == Counter({"table-missing": 1})


def test_a_whole_table_allowance_also_covers_that_tables_columns():
    snap = cat(s__t={"a": col(), "lost": col()})
    got = cat(s__t={"a": col(), "extra": col()})
    assert sp.compare_catalogues(snap, got, allowed_extra={"s.t": REASON}, allowed_missing={"s.t": REASON}) == []
    assert rules(sp.compare_catalogues(snap, got, allowed_extra={"s.t": REASON})) == Counter({"column-missing": 1})
    assert rules(sp.compare_catalogues(snap, got, allowed_missing={"s.t": REASON})) == Counter({"extra-column": 1})


def test_a_stale_allowance_is_a_finding_for_both_kinds():
    snap = cat(s__t={"a": col()})
    findings = sp.compare_catalogues(snap, cat(s__t={"a": col()}), allowed_extra={"s.t.b": REASON}, allowed_missing={"s.t.c": REASON})
    assert [f.rule for f in findings] == ["stale-allowance", "stale-allowance"]
    assert "allowed_extra['s.t.b']" in findings[0].message and "allowed_missing['s.t.c']" in findings[1].message


def test_index_names_are_compared_only_when_asked():
    snap = {"s.t": {"columns": {"a": col()}, "indexes": ["keep", "lost"]}}
    got = {"s.t": {"columns": {"a": col()}, "indexes": ["keep", "added"]}}
    assert sp.compare_catalogues(snap, got) == []
    assert rules(sp.compare_catalogues(snap, got, check_indexes=True)) == Counter({"index-missing": 1, "index-extra": 1})
    clean = sp.compare_catalogues(snap, got, check_indexes=True, allowed_extra={"s.t#added": REASON}, allowed_missing={"s.t#lost": REASON})
    assert clean == []


def test_check_indexes_against_a_snapshot_without_indexes_is_a_finding_not_a_pass():
    snap = {"s.t": {"columns": {"a": col()}, "indexes": None}}
    findings = sp.compare_catalogues(snap, {"s.t": {"columns": {"a": col()}, "indexes": []}}, check_indexes=True)
    assert [f.rule for f in findings] == ["snapshot-incomplete"]


# ------------------------------------------------------------------------------------------------------------ no server


@pytest.fixture
def no_server(monkeypatch):
    monkeypatch.setattr(sp, "find_pg_bin", lambda explicit=None: None)


def test_without_binaries_the_result_says_not_checked_and_the_banner_is_loud(no_server, capsys):
    result = sp.find_schema_snapshot_problems(FIXTURES / "clean" / "schema.sql.canary", SNAPSHOT)
    assert result.checked is False and result.ok is False
    assert "Postgres binaries" in result.reason
    err = capsys.readouterr().err
    assert "NOT CHECKED" in err and "schema.sql.canary" in err and "was NOT provisioned" in err


def test_assert_returns_the_unchecked_result_but_raises_when_the_server_is_required(no_server):
    result = sp.assert_schema_matches_snapshot(FIXTURES / "clean" / "schema.sql.canary", SNAPSHOT)
    assert result.checked is False
    with pytest.raises(AssertionError, match="NOT CHECKED and require_server"):
        sp.assert_schema_matches_snapshot(FIXTURES / "clean" / "schema.sql.canary", SNAPSHOT, require_server=True)


def test_cli_exit_code_for_no_server_is_the_chosen_one(no_server, capsys):
    argv = ["check", str(FIXTURES / "clean" / "schema.sql.canary"), str(SNAPSHOT)]
    assert sp.main(argv) == 0
    assert sp.main([*argv, "--missing-exit", "3"]) == 3
    assert "NOT CHECKED" in capsys.readouterr().out


def test_inputs_are_validated_before_the_server_is_looked_for(no_server, tmp_path):
    with pytest.raises(sp.SchemaParityError, match="unreadable"):
        sp.find_schema_snapshot_problems(tmp_path / "absent.sql", SNAPSHOT)
    empty = tmp_path / "e.sql"
    empty.write_text("-- nothing\n", encoding="utf-8")
    with pytest.raises(sp.SchemaParityError, match="no SQL statement"):
        sp.find_schema_snapshot_problems(empty, SNAPSHOT)
    with pytest.raises(sp.SchemaParityError, match="min_tables"):
        sp.find_schema_snapshot_problems(FIXTURES / "clean" / "schema.sql.canary", SNAPSHOT, min_tables=3)


def test_cli_reports_a_usage_error_with_exit_2(capsys, tmp_path):
    assert sp.main(["check", str(tmp_path / "absent.sql"), str(SNAPSHOT)]) == 2
    assert sp.main(["check", "a.sql", "b.json", "--allow-extra", "s.t"]) == 2
    err = capsys.readouterr().err
    assert "schema_snapshot_parity:" in err and "unreadable" in err and "KEY=REASON" in err


# ------------------------------------------------------------------------------------------------------------ refresh (no server)


def test_refresh_needs_the_env_var_and_never_prints_a_dsn(tmp_path):
    with pytest.raises(sp.SchemaParityError, match="PROD_RO_DSN is not set"):
        sp.refresh_snapshot(tmp_path / "s.json", dsn_env="PROD_RO_DSN", schemas=["app"], source="x", environ={})
    assert not (tmp_path / "s.json").exists()


def test_a_failed_connect_names_the_class_and_sqlstate_but_not_the_dsn(tmp_path):
    pytest.importorskip("psycopg2")
    dsn = "host=127.0.0.1 port=1 user=leaky_user password=s3cr3t-pw dbname=leaky_db connect_timeout=2"
    with pytest.raises(sp.SchemaParityError) as info:
        sp.refresh_snapshot(tmp_path / "s.json", dsn_env="D", schemas=["app"], source="x", environ={"D": dsn})
    text = str(info.value)
    assert "OperationalError" in text and "SQLSTATE" in text
    assert not any(secret in text for secret in ("s3cr3t-pw", "leaky_user", "leaky_db", "127.0.0.1"))
    assert info.value.__cause__ is None and info.value.__suppress_context__


def test_refresh_demands_schemas_and_a_source_note(tmp_path):
    with pytest.raises(sp.SchemaParityError, match="at least one schema"):
        sp.refresh_snapshot(tmp_path / "s.json", dsn_env="D", schemas=[], source="x", environ={"D": "x"})
    with pytest.raises(sp.SchemaParityError, match="source"):
        sp.refresh_snapshot(tmp_path / "s.json", dsn_env="D", schemas=["a"], source=" ", environ={"D": "x"})


def test_the_gate_never_calls_refresh():
    import ast

    tree = ast.parse(Path(sp.__file__).read_text(encoding="utf-8"))
    callers = {
        fn.name
        for fn in ast.walk(tree)
        if isinstance(fn, ast.FunctionDef)
        for node in ast.walk(fn)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("refresh_snapshot", "connect_read_only")
    }
    assert callers == {"refresh_snapshot", "main"}, "only the refresh path (and the CLI's refresh command) may reach a live database"


# ------------------------------------------------------------------------------------------------------------ server canaries


@pytest.fixture(scope="module")
def server():
    """One throwaway server for the module: a start costs over a minute on a scanned Windows machine."""
    if find_pg_bin() is None:
        pytest.skip("no Postgres binaries here (PG_BIN / pgserver / PATH / `python -m py_ci_shared.embedded_postgres fetch`): server canaries NOT run")
    with embedded_postgres(find_pg_bin()) as dsn:  # type: ignore[arg-type]
        yield dsn


def test_a_server_dsn_that_is_not_local_is_refused_before_anything_is_created(capsys):
    for dsn in ("host=db.example.com port=5432 user=u dbname=d", "postgresql://u@prod.internal/db", "dbname=nohost"):
        with pytest.raises(sp.SchemaParityError, match="local throwaway server"):
            sp.find_schema_snapshot_problems(FIXTURES / "clean" / "schema.sql.canary", SNAPSHOT, server_dsn=dsn)
    argv = ["check", str(FIXTURES / "clean" / "schema.sql.canary"), str(SNAPSHOT), "--server-dsn-env", "NO_SUCH_VAR_SSP"]
    assert sp.main(argv) == 2
    assert "NO_SUCH_VAR_SSP is not set" in capsys.readouterr().err


@needs_server
def test_canary_violation_reports_the_incident_shapes_exactly(server):
    result = sp.find_schema_snapshot_problems(FIXTURES / "violation" / "schema.sql.canary", SNAPSHOT, check_indexes=True, server_dsn=server)
    assert result.checked is True
    assert rules(result.findings) == Counter({"type-mismatch": 1, "column-missing": 2, "table-missing": 1, "identity-mismatch": 1, "index-missing": 1})
    messages = " | ".join(f.message for f in result.findings)
    assert "app.jobs.created_at: timestamp without time zone -> timestamp with time zone" in messages
    assert "app.jobs.cost_usd" in messages and "app.jobs.total" in messages and "app.events" in messages
    with pytest.raises(AssertionError, match="does not provision what"):
        sp.assert_schema_matches_snapshot(FIXTURES / "violation" / "schema.sql.canary", SNAPSHOT, server_dsn=server)


@needs_server
def test_canary_clean_provisions_exactly_the_snapshot_including_indexes_and_twice():
    result = sp.assert_schema_matches_snapshot(FIXTURES / "clean" / "schema.sql.canary", SNAPSHOT, check_indexes=True)
    assert result.checked is True and result.findings == [] and result.ok is True


@needs_server
def test_a_file_that_is_not_idempotent_and_one_that_fails_to_apply_are_findings(server, tmp_path):
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps({"tables": {"app.t": {"a": {"data_type": "integer", "is_nullable": "YES"}}}}), encoding="utf-8")
    bad = tmp_path / "bad.sql"
    bad.write_text("CREATE SCHEMA IF NOT EXISTS app;\nCREATE TABLE app.t (a integer);\nCREATE TABLE app.nope (a nonexistent_type);\n", encoding="utf-8")
    result = sp.find_schema_snapshot_problems(bad, snap, server_dsn=server)
    assert rules(result.findings) == Counter({"apply-failed": 1, "not-idempotent": 1})
    failed = next(f for f in result.findings if f.rule == "apply-failed")
    assert "app.nope" in failed.message and "nonexistent_type" in failed.message
    once = sp.find_schema_snapshot_problems(bad, snap, run_twice=False, server_dsn=server)
    assert rules(once.findings) == Counter({"apply-failed": 1}), "the idempotence pass is the only thing run_twice adds"


@needs_server
def test_a_second_pass_that_changes_the_catalogue_is_not_idempotent(server, tmp_path):
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps({"tables": {"app.t": {"a": {"data_type": "integer", "is_nullable": "YES"}}}}), encoding="utf-8")
    sql = tmp_path / "grow.sql"
    # Each pass adds one more column through a DO block: every statement succeeds, yet the second state differs from the first.
    sql.write_text(
        "CREATE SCHEMA IF NOT EXISTS app;\nCREATE TABLE IF NOT EXISTS app.t (a integer);\n"
        "DO $$ BEGIN EXECUTE format('ALTER TABLE app.t ADD COLUMN c%s integer', (SELECT count(*) FROM information_schema.columns "
        "WHERE table_schema = 'app')); END $$;\n",
        encoding="utf-8",
    )
    result = sp.find_schema_snapshot_problems(sql, snap, server_dsn=server)
    assert any(f.rule == "not-idempotent" and "second time changed the catalogue" in f.message for f in result.findings)


@needs_server
def test_an_extension_the_server_lacks_is_an_error_and_a_present_one_provisions(server, tmp_path):
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps({"tables": {"public.t": {"a": {"data_type": "integer", "is_nullable": "YES"}}}}), encoding="utf-8")
    sql = tmp_path / "s.sql"
    sql.write_text("CREATE TABLE IF NOT EXISTS public.t (a integer);\n", encoding="utf-8")
    with pytest.raises(sp.SchemaParityError, match="extension 'no_such_extension_zz'"):
        sp.find_schema_snapshot_problems(sql, snap, extensions=["no_such_extension_zz"], server_dsn=server)
    assert sp.find_schema_snapshot_problems(sql, snap, extensions=["plpgsql"], server_dsn=server).ok


@needs_server
def test_refresh_writes_a_snapshot_the_gate_then_accepts_and_the_session_is_read_only(server, tmp_path):
    psycopg2 = pytest.importorskip("psycopg2")
    dsn = server
    admin = psycopg2.connect(dsn)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute("CREATE SCHEMA app")
        cur.execute("CREATE TABLE app.jobs (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, at timestamptz NOT NULL, tags text[], n numeric)")
        cur.execute("CREATE INDEX jobs_at ON app.jobs (at)")
    admin.close()
    ro = sp.connect_read_only(dsn)
    try:
        with ro.cursor() as cur:
            cur.execute("SHOW default_transaction_read_only")
            assert cur.fetchone() == ("on",)
            with pytest.raises(psycopg2.errors.ReadOnlySqlTransaction):
                cur.execute("CREATE TABLE app.should_not_exist (a int)")
    finally:
        ro.close()
    out = tmp_path / "snap.json"
    catalogue = sp.refresh_snapshot(out, dsn_env="RO", schemas=["app"], source="canary read", environ={"RO": dsn})
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["schema_version"] == 1 and written["_source"] == "canary read"
    assert list(written["tables"]) == ["app.jobs"] and sorted(catalogue) == ["app.jobs"]
    columns = written["tables"]["app.jobs"]["columns"]
    assert columns["id"] == {"data_type": "bigint", "generated": False, "identity": "ALWAYS", "is_nullable": "NO"}
    assert columns["at"]["data_type"] == "timestamp with time zone" and "udt_name" not in columns["at"]
    assert columns["tags"]["data_type"] == "ARRAY" and columns["tags"]["udt_name"] == "_text"
    assert written["tables"]["app.jobs"]["indexes"] == ["jobs_at", "jobs_pkey"]
    assert out.read_text(encoding="utf-8").endswith("\n") and not list(tmp_path.glob(".snap.json.*.tmp"))
    sql = tmp_path / "s.sql"
    sql.write_text(
        "CREATE SCHEMA IF NOT EXISTS app;\nCREATE TABLE IF NOT EXISTS app.jobs (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, "
        "at timestamptz NOT NULL, tags text[], n numeric);\nCREATE INDEX IF NOT EXISTS jobs_at ON app.jobs (at);\n",
        encoding="utf-8",
    )
    assert sp.assert_schema_matches_snapshot(sql, out, check_indexes=True, server_dsn=server).ok
