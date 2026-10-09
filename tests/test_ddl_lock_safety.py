"""Tests for py_ci_shared.ddl_lock_safety: each rule has a seeded defect and its nearest correct form."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from py_ci_shared._core import EmptyScanError, UnparsedFilesError
from py_ci_shared.ddl_lock_safety import assert_ddl_files_lock_safe, find_ddl_lock_findings

BOM = b"\xef\xbb\xbf"


def _sql(tmp_path: Path, **files: str) -> Path:
    d = tmp_path / "sql"
    d.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (d / f"{name}.sql").write_text(textwrap.dedent(body).lstrip("\n"), encoding="utf-8")
    return tmp_path


def _find(root: Path, **kw):
    return find_ddl_lock_findings(root, sql_dirs=["sql"], use_git=False, **kw)


def _rules(root: Path, **kw) -> list[tuple[str, int]]:
    return [(f.rule, f.line) for f in _find(root, **kw)]


# --- rule 1: lock_timeout -------------------------------------------------------------------------------------------------------------------


def test_alter_table_without_lock_timeout_is_reported_with_its_line(tmp_path):
    root = _sql(tmp_path, a="-- header\nSELECT 1;\nALTER TABLE jobs ADD COLUMN note text;\n")
    (f,) = _find(root)
    assert (f.path, f.line, f.rule) == ("sql/a.sql", 3, "ddl-lock-timeout")
    assert "ALTER TABLE jobs" in f.message and "Add `SET lock_timeout" in f.message


@pytest.mark.parametrize(
    "setting",
    ["SET lock_timeout = '5s';", "set local lock_timeout TO 2000;", "SET SESSION lock_timeout='1min';", "BEGIN;\nSET LOCAL lock_timeout = '3s';"],
)
def test_a_lock_timeout_before_the_alter_passes(tmp_path, setting):
    assert _rules(_sql(tmp_path, a=f"{setting}\nALTER TABLE jobs ADD COLUMN note text;\n")) == []


@pytest.mark.parametrize("setting", ["SET lock_timeout = 0;", "SET lock_timeout = '0';", "SET lock_timeout TO '0ms';", "SET lock_timeout = DEFAULT;"])
def test_a_lock_timeout_that_does_not_bound_the_wait_does_not_count(tmp_path, setting):
    assert _rules(_sql(tmp_path, a=f"{setting}\nALTER TABLE jobs ADD COLUMN note text;\n")) == [("ddl-lock-timeout", 2)]


def test_a_lock_timeout_after_the_alter_does_not_count(tmp_path):
    assert _rules(_sql(tmp_path, a="ALTER TABLE jobs ADD COLUMN note text;\nSET lock_timeout = '5s';\n")) == [("ddl-lock-timeout", 1)]


def test_lock_timeout_named_only_in_a_plain_comment_or_a_string_does_not_count(tmp_path):
    root = _sql(tmp_path, a="-- SET lock_timeout = '5s';\nSELECT 'SET lock_timeout = 1';\nALTER TABLE jobs ADD COLUMN note text;\n")
    assert _rules(root) == [("ddl-lock-timeout", 3)]


def test_a_pasteable_lock_timeout_in_the_header_counts(tmp_path):
    root = _sql(tmp_path, a="--   SET lock_timeout = '5s';\nALTER TABLE jobs ADD COLUMN note text;\n")
    assert _rules(root) == []


def test_alter_table_in_a_comment_or_dollar_body_is_not_a_live_alter(tmp_path):
    body = "-- ALTER TABLE jobs ADD COLUMN x int;\n/* ALTER TABLE jobs\n DROP COLUMN y; */\nDO $$ BEGIN EXECUTE 'ALTER TABLE jobs ADD z int'; END $$;\n"
    assert _rules(_sql(tmp_path, a=body)) == []


def test_a_file_without_alter_table_needs_no_lock_timeout(tmp_path):
    assert _rules(_sql(tmp_path, a="CREATE TABLE t (id int);\nINSERT INTO t VALUES (1);\n")) == []


# --- rule 2: NOT VALID ----------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "clause",
    [
        "ADD CONSTRAINT c CHECK (x > 0)",
        "ADD CHECK (x > 0)",
        "ADD CONSTRAINT fk FOREIGN KEY (a) REFERENCES other (id)",
        "ADD FOREIGN KEY (a) REFERENCES other (id)",
    ],
)
def test_a_validating_constraint_is_reported_and_its_not_valid_form_is_not(tmp_path, clause):
    head = "SET lock_timeout = '5s';\n"
    bad = _sql(tmp_path / "bad", a=f"{head}ALTER TABLE big\n    {clause};\n")
    assert _rules(bad) == [("ddl-constraint-not-valid", 2)]
    good = _sql(tmp_path / "good", a=f"{head}ALTER TABLE big\n    {clause} NOT VALID;\nALTER TABLE big VALIDATE CONSTRAINT c;\n")
    assert _rules(good) == []


def test_one_not_valid_clause_does_not_excuse_a_sibling_clause(tmp_path):
    body = "SET lock_timeout = '5s';\nALTER TABLE big ADD CONSTRAINT a CHECK (x > 0) NOT VALID, ADD CONSTRAINT b CHECK (y > 0);\n"
    (f,) = _find(_sql(tmp_path, a=body))
    assert f.rule == "ddl-constraint-not-valid" and "CHECK b" in f.message


def test_a_constraint_with_a_comma_inside_its_expression_is_one_clause(tmp_path):
    body = "SET lock_timeout = '5s';\nALTER TABLE big ADD CONSTRAINT a CHECK (x IN (1, 2, 3)) NOT VALID;\n"
    assert _rules(_sql(tmp_path, a=body)) == []


def test_a_unique_or_primary_key_constraint_is_not_a_check_or_foreign_key(tmp_path):
    body = "SET lock_timeout = '5s';\nALTER TABLE big ADD CONSTRAINT u UNIQUE USING INDEX big_ix;\n"
    assert _rules(_sql(tmp_path, a=body)) == []


def test_a_pasteable_comment_command_counts_like_a_statement(tmp_path):
    body = "SET lock_timeout = '5s';\n-- Run this one:\n--   ALTER TABLE big\n--     ADD CONSTRAINT c CHECK (x > 0);\n"
    assert _rules(_sql(tmp_path, a=body)) == [("ddl-constraint-not-valid", 3)]
    prose = "SET lock_timeout = '5s';\n-- we never ADD CONSTRAINT c CHECK here\n"
    assert _rules(_sql(tmp_path / "p", a=prose)) == []


def test_tiny_tables_excuse_a_validating_constraint_by_bare_or_qualified_name(tmp_path):
    body = "SET lock_timeout = '5s';\nALTER TABLE upwork.kv_checkpoint ADD CONSTRAINT c CHECK (x > 0);\n"
    root = _sql(tmp_path, a=body)
    assert _rules(root) == [("ddl-constraint-not-valid", 2)]
    assert _rules(root, tiny_tables={"kv_checkpoint": "a few dozen watermark rows"}) == []
    assert _rules(root, tiny_tables={"upwork.kv_checkpoint": "a few dozen watermark rows"}) == []
    assert _rules(root, tiny_tables={"other": "tiny"}) == [("ddl-constraint-not-valid", 2)]


def test_a_tiny_table_entry_without_a_reason_is_refused(tmp_path):
    with pytest.raises(ValueError, match="reason"):
        _find(_sql(tmp_path, a="SELECT 1;\n"), tiny_tables={"t": "  "})


def test_a_constraint_on_a_table_the_file_creates_is_not_reported(tmp_path):
    body = "SET lock_timeout = '5s';\nCREATE TABLE fresh (x int);\nALTER TABLE fresh ADD CONSTRAINT c CHECK (x > 0);\n"
    assert _rules(_sql(tmp_path, a=body)) == []


# --- rule 3: CONCURRENTLY -------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stmt",
    ["CREATE INDEX ix ON big (a)", "CREATE UNIQUE INDEX IF NOT EXISTS ix ON ONLY public.big USING btree (a)", "create index on big (a)"],
)
def test_a_plain_index_build_is_reported_and_the_concurrent_form_is_not(tmp_path, stmt):
    assert _rules(_sql(tmp_path / "bad", a=f"{stmt};\n")) == [("ddl-index-concurrently", 1)]
    concurrent = stmt.replace("INDEX ", "INDEX CONCURRENTLY ", 1).replace("index ", "index concurrently ", 1)
    assert _rules(_sql(tmp_path / "good", a=f"{concurrent};\n")) == []


def test_a_plain_index_needs_no_lock_timeout_but_is_still_a_finding_only_once(tmp_path):
    assert [f.rule for f in _find(_sql(tmp_path, a="CREATE INDEX ix ON big (a);\n"))] == ["ddl-index-concurrently"]


def test_a_plain_index_on_a_table_the_file_creates_or_a_tiny_table_is_fine(tmp_path):
    created = "CREATE TABLE IF NOT EXISTS fresh (a int);\nCREATE INDEX ix ON fresh (a);\n"
    assert _rules(_sql(tmp_path / "c", a=created)) == []
    other = "CREATE INDEX ix ON tiny (a);\n"
    assert _rules(_sql(tmp_path / "t", a=other), tiny_tables={"tiny": "ten rows"}) == []
    assert _rules(_sql(tmp_path / "n", a=other)) == [("ddl-index-concurrently", 1)]


def test_concurrently_inside_an_explicit_begin_block_is_reported_even_on_a_tiny_table(tmp_path):
    body = "BEGIN;\nCREATE INDEX CONCURRENTLY ix ON tiny (a);\nCOMMIT;\n"
    (f,) = _find(_sql(tmp_path / "a", a=body), tiny_tables={"tiny": "ten rows"})
    assert (f.rule, f.line) == ("ddl-index-concurrently", 2) and "BEGIN block" in f.message
    assert _rules(_sql(tmp_path / "b", a="START TRANSACTION;\nDROP INDEX CONCURRENTLY ix;\nROLLBACK;\n")) == [("ddl-index-concurrently", 2)]
    assert _rules(_sql(tmp_path / "c", a="BEGIN;\nREINDEX INDEX CONCURRENTLY ix;\nEND;\n")) == [("ddl-index-concurrently", 2)]


def test_concurrently_outside_or_after_the_block_is_fine(tmp_path):
    body = "BEGIN;\nSELECT 1;\nCOMMIT;\nCREATE INDEX CONCURRENTLY ix ON big (a);\n"
    assert _rules(_sql(tmp_path, a=body)) == []


def test_begin_inside_a_dollar_quoted_body_does_not_open_a_block(tmp_path):
    body = "DO $$ BEGIN PERFORM 1; END $$;\nCREATE INDEX CONCURRENTLY ix ON big (a);\n"
    assert _rules(_sql(tmp_path, a=body)) == []


def test_psql_meta_commands_do_not_glue_onto_the_next_statement(tmp_path):
    body = "\\set ON_ERROR_STOP on\n\\c mydb\nCREATE INDEX CONCURRENTLY ix ON big (a);\n"
    assert _rules(_sql(tmp_path, a=body)) == []


# --- rule 4: table rewrites -----------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "clause",
    [
        "ADD COLUMN uid uuid DEFAULT gen_random_uuid()",
        "ADD COLUMN r float8 NOT NULL DEFAULT random()",
        "ADD COLUMN ts timestamptz DEFAULT clock_timestamp()",
        "ADD COLUMN n bigint DEFAULT nextval('seq')",
        "ADD COLUMN id bigserial",
        "ADD COLUMN d int GENERATED ALWAYS AS (a * 2) STORED",
        "ALTER COLUMN price TYPE numeric(12,4)",
        "ALTER COLUMN price SET DATA TYPE numeric(12,4)",
    ],
)
def test_a_rewriting_alter_is_reported_and_marked_or_tiny_files_are_not(tmp_path, clause):
    head = "SET lock_timeout = '5s';\n"
    assert _rules(_sql(tmp_path / "bad", a=f"{head}ALTER TABLE big {clause};\n")) == [("ddl-table-rewrite", 2)]
    assert _rules(_sql(tmp_path / "ok", a=f"-- rewrite-ok: table is empty in prod\n{head}ALTER TABLE big {clause};\n")) == []
    assert _rules(_sql(tmp_path / "tiny", a=f"{head}ALTER TABLE big {clause};\n"), tiny_tables={"big": "ten rows"}) == []
    assert _rules(_sql(tmp_path / "off", a=f"{head}ALTER TABLE big {clause};\n"), check_rewrites=False) == []


@pytest.mark.parametrize(
    "clause",
    [
        "ADD COLUMN note text",
        "ADD COLUMN n int NOT NULL DEFAULT 0",
        "ADD COLUMN ts timestamptz NOT NULL DEFAULT now()",
        "ADD COLUMN s text DEFAULT 'random()'",
        "ADD COLUMN b boolean DEFAULT false CHECK (b IS NOT NULL)",
        "ALTER COLUMN price SET DEFAULT 0",
        "ALTER COLUMN price SET NOT NULL",
    ],
)
def test_non_rewriting_alters_are_not_reported(tmp_path, clause):
    assert _rules(_sql(tmp_path, a=f"SET lock_timeout = '5s';\nALTER TABLE big {clause};\n")) == []


def test_a_rewrite_ok_marker_needs_a_reason(tmp_path):
    body = "-- rewrite-ok:\nSET lock_timeout = '5s';\nALTER TABLE big ALTER COLUMN price TYPE numeric;\n"
    assert _rules(_sql(tmp_path, a=body)) == [("ddl-table-rewrite", 3)]


def test_extra_volatile_functions_can_be_named(tmp_path):
    body = "SET lock_timeout = '5s';\nALTER TABLE big ADD COLUMN k text DEFAULT my_token();\n"
    root = _sql(tmp_path, a=body)
    assert _rules(root) == []
    assert _rules(root, volatile_functions=["my_token"]) == [("ddl-table-rewrite", 2)]


# --- reading, floor, baseline, entry -------------------------------------------------------------------------------------------------------


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    body = "ALTER TABLE jobs ADD COLUMN note text;\nCREATE INDEX ix ON jobs (note);\n"
    plain = _sql(tmp_path / "p", a=body)
    bommed = tmp_path / "b"
    (bommed / "sql").mkdir(parents=True)
    (bommed / "sql" / "a.sql").write_bytes(BOM + body.encode("utf-8"))
    assert [f.render() for f in _find(bommed)] == [f.render() for f in _find(plain)] and len(_find(plain)) == 2


def test_an_unreadable_file_fails_by_name_unless_allowed(tmp_path):
    root = _sql(tmp_path, ok="SET lock_timeout = '5s';\nALTER TABLE t ADD COLUMN c int;\n")
    (root / "sql" / "bad.sql").write_bytes(b"-- caf\xe9 \xff\nALTER TABLE t ADD COLUMN d int;\n")
    with pytest.raises(UnparsedFilesError, match=r"bad\.sql"):
        _find(root)
    assert _find(root, allow_unparsed=True) == []


def test_an_empty_or_missing_corpus_fails_the_floor(tmp_path):
    (tmp_path / "sql").mkdir()
    with pytest.raises(EmptyScanError, match="at least 1"):
        _find(tmp_path)
    root = _sql(tmp_path / "two", a="SELECT 1;\n")
    with pytest.raises(EmptyScanError, match="at least 3"):
        _find(root, min_files=3)
    from py_ci_shared._core import CorpusError

    with pytest.raises(CorpusError):
        find_ddl_lock_findings(tmp_path, sql_dirs=["nope"], use_git=False)


def test_several_sql_dirs_are_read_and_paths_are_relative_to_root(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "x.sql").write_text("CREATE INDEX i ON t (c);\n", encoding="utf-8")
    (tmp_path / "b" / "y.sql").write_text("CREATE INDEX j ON t (c);\n", encoding="utf-8")
    found = find_ddl_lock_findings(tmp_path, sql_dirs=["a", tmp_path / "b"], use_git=False)
    assert [f.path for f in found] == ["a/x.sql", "b/y.sql"]


def test_the_assert_fails_with_every_finding_and_a_fix_hint(tmp_path):
    root = _sql(tmp_path, a="ALTER TABLE t ADD COLUMN c int;\nCREATE INDEX i ON t (c);\n")
    with pytest.raises(AssertionError, match=r"2 ddl-lock-safety finding\(s\); Fix each statement") as err:
        assert_ddl_files_lock_safe(root, sql_dirs=["sql"], use_git=False)
    assert "sql/a.sql:1" in str(err.value) and "sql/a.sql:2" in str(err.value)
    assert_ddl_files_lock_safe(_sql(tmp_path / "ok", a="SELECT 1;\n"), sql_dirs=["sql"], use_git=False)


def test_a_baseline_admits_old_findings_and_fails_on_a_new_one(tmp_path, monkeypatch):
    monkeypatch.setenv("PY_CI_SHARED_REFRESH_ALLOW_GROW", "1")
    root = _sql(tmp_path, a="CREATE INDEX i ON t (c);\n")
    baseline = tmp_path / "baseline.json"
    with pytest.raises(pytest.skip.Exception):
        assert_ddl_files_lock_safe(root, sql_dirs=["sql"], baseline_path=baseline, refresh=True, use_git=False)
    assert json.loads(baseline.read_text(encoding="utf-8"))
    assert_ddl_files_lock_safe(root, sql_dirs=["sql"], baseline_path=baseline, use_git=False)
    (root / "sql" / "b.sql").write_text("CREATE INDEX k ON t (d);\n", encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match=r"b.sql"):
        assert_ddl_files_lock_safe(root, sql_dirs=["sql"], baseline_path=baseline, use_git=False)


def test_baseline_keys_carry_no_line_number(tmp_path):
    before = [f.key for f in _find(_sql(tmp_path / "a", a="CREATE INDEX i ON t (c);\n"))]
    after = [f.key for f in _find(_sql(tmp_path / "b", a="-- a\n-- b\n\nCREATE INDEX i ON t (c);\n"))]
    assert before == after and before


def test_an_unterminated_pasteable_header_does_not_hide_the_lock_timeout_below_it(tmp_path):
    body = "--   psql -v ON_ERROR_STOP=1 -f this_file.sql\nSET lock_timeout = '5s';\nALTER TABLE jobs ADD COLUMN note text;\n"
    assert _rules(_sql(tmp_path, a=body)) == []
    unbounded = "--   psql -f this_file.sql\nALTER TABLE jobs ADD COLUMN note text;\n"
    assert _rules(_sql(tmp_path / "u", a=unbounded)) == [("ddl-lock-timeout", 2)]


def test_set_and_alter_on_one_line_are_ordered_by_column(tmp_path):
    assert _rules(_sql(tmp_path / "a", a="SET lock_timeout = '5s'; ALTER TABLE jobs ADD COLUMN n text;\n")) == []
    assert _rules(_sql(tmp_path / "b", a="ALTER TABLE jobs ADD COLUMN n text; SET lock_timeout = '5s';\n")) == [("ddl-lock-timeout", 1)]
