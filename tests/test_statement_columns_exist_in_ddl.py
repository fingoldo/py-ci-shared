"""Tests for py_ci_shared.statement_columns_exist_in_ddl: SQL statements in code name only columns the DDL files define."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

pytest.importorskip("sqlglot")

from py_ci_shared._core import Baseline, CorpusError, EmptyScanError, UnparsedFilesError
from py_ci_shared.statement_columns_exist_in_ddl import (
    NO_DDL_TABLE,
    NO_RESOLVABLE_COLUMN,
    NOT_LITERAL,
    UNPARSABLE,
    assert_statement_columns_exist_in_ddl,
    ddl_catalogue,
    find_unknown_statement_columns,
    statement_column_report,
)

BOM = b"\xef\xbb\xbf"

CREATE = """
-- job_auth_details, as created
CREATE TABLE IF NOT EXISTS new_upwork.job_auth_details (
    job_uid       TEXT      NOT NULL,
    ts            TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    questions     JSONB     NOT NULL DEFAULT '[]'::jsonb,
    errors        JSONB,
    n_questions   INT       GENERATED ALWAYS AS (jsonb_array_length(questions)) STORED,
    PRIMARY KEY (job_uid, ts),
    CONSTRAINT job_uid_not_cipher CHECK (job_uid NOT LIKE '~%')
);
"""
ADD_CHECKED_AT = "ALTER TABLE new_upwork.job_auth_details ADD COLUMN IF NOT EXISTS checked_at TIMESTAMP;\n"


def _corpus(tmp_path: Path, files: dict[str, str | bytes]) -> Path:
    for rel, data in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data if isinstance(data, bytes) else textwrap.dedent(data).encode("utf-8"))
    return tmp_path


def _code(*statements: str) -> str:
    return "\n".join(f'{name} = """{sql}"""' for name, sql in zip((f"SQL_{i}" for i in range(len(statements))), statements)) + "\n"


def _report(tmp_path: Path, ddl: str, *statements: str, **kwargs):
    root = _corpus(tmp_path, {"sql/create.sql": ddl, "app.py": _code(*statements)})
    return statement_column_report(root, use_git=False, **kwargs)


def _columns_named(report) -> list[str]:
    return sorted(f.message.split("`")[1] for f in report.findings)


# -- the seeded defect: CORR-26 -------------------------------------------------------------------------------------------------------------------


STALE = "SELECT max(coalesce(d.checked_at, d.ts)) FROM new_upwork.job_auth_details d WHERE d.job_uid = %s"
INSERT = "INSERT INTO new_upwork.job_auth_details (job_uid, questions, checked_at) VALUES %s ON CONFLICT (job_uid, ts) DO NOTHING"
CONFIRM = "UPDATE new_upwork.job_auth_details SET checked_at = now() WHERE job_uid = %s"


def test_reports_the_seeded_violation(tmp_path):
    root = _corpus(tmp_path, {"sql/create.sql": CREATE, "queries.py": _code(STALE, INSERT, CONFIRM)})
    found = find_unknown_statement_columns(root, use_git=False)
    assert [(f.path, f.line, f.rule) for f in found] == [("queries.py", n, "statement-columns-exist-in-ddl") for n in (1, 2, 3)]
    assert [f.message.split(",")[0] for f in found] == [f"queries.SQL_{i} names column `checked_at` of new_upwork.job_auth_details" for i in range(3)]
    assert all("sql/create.sql" in f.message and "Add the ALTER TABLE" in f.message for f in found)
    with pytest.raises(AssertionError, match=r"3 statement-columns-exist-in-ddl finding\(s\); Add the additive") as exc:
        assert_statement_columns_exist_in_ddl(root, use_git=False)
    assert "3 checked against 1 DDL table(s)" in str(exc.value)


def test_negative_control_the_nearest_correct_code_is_not_reported(tmp_path):
    root = _corpus(tmp_path, {"sql/create.sql": CREATE, "sql/add_checked_at.sql": ADD_CHECKED_AT, "queries.py": _code(STALE, INSERT, CONFIRM)})
    assert find_unknown_statement_columns(root, use_git=False) == []
    assert_statement_columns_exist_in_ddl(root, use_git=False)


def test_a_bom_prefixed_file_is_reported_like_the_plain_one(tmp_path):
    plain = _corpus(tmp_path / "plain", {"sql/create.sql": CREATE, "q.py": _code(STALE)})
    bom = _corpus(tmp_path / "bom", {"sql/create.sql": BOM + CREATE.encode(), "q.py": BOM + _code(STALE).encode()})
    expected = find_unknown_statement_columns(plain, use_git=False)
    assert len(expected) == 1
    assert find_unknown_statement_columns(bom, use_git=False) == expected


def test_an_unparsable_file_fails_by_name_unless_allowed(tmp_path):
    root = _corpus(tmp_path, {"sql/create.sql": CREATE, "ok.py": _code(STALE), "broken.py": "def broken(:\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken.py"):
        find_unknown_statement_columns(root, use_git=False)
    assert len(find_unknown_statement_columns(root, allow_unparsed=True, use_git=False)) == 1


def test_an_unreadable_ddl_file_fails_by_name_unless_allowed(tmp_path):
    root = _corpus(tmp_path, {"sql/create.sql": CREATE, "sql/broken.sql": "CREATE TABLE t (a text DEFAULT 'oops);\n", "ok.py": _code(STALE)})
    with pytest.raises(UnparsedFilesError, match=r"broken\.sql:1: unparsable: unterminated string"):
        find_unknown_statement_columns(root, use_git=False)
    assert len(find_unknown_statement_columns(root, allow_unparsed=True, use_git=False)) == 1
    _corpus(tmp_path, {"sql/broken.sql": b"CREATE TABLE t (a text DEFAULT '\xff');\n"})
    with pytest.raises(UnparsedFilesError, match=r"broken\.sql.*not valid utf-8"):
        find_unknown_statement_columns(root, use_git=False)


def test_an_empty_corpus_fails_the_floor(tmp_path):
    (tmp_path / "sql").mkdir()
    with pytest.raises(EmptyScanError):
        assert_statement_columns_exist_in_ddl(tmp_path, use_git=False)


def test_a_missing_ddl_directory_or_one_without_tables_fails_instead_of_passing(tmp_path):
    root = _corpus(tmp_path, {"q.py": _code(STALE)})
    with pytest.raises(CorpusError, match="DDL directory does not exist"):
        find_unknown_statement_columns(root, use_git=False)
    _corpus(tmp_path, {"sql/notes.sql": "-- nothing here\nSELECT 1;\n"})
    with pytest.raises(AssertionError, match="only 0 table"):
        find_unknown_statement_columns(root, use_git=False)


def test_a_gate_that_compared_nothing_fails_min_checked(tmp_path):
    root = _corpus(tmp_path, {"sql/create.sql": CREATE, "q.py": _code("SELECT 1 FROM other_table")})
    with pytest.raises(AssertionError, match=r"only 0 SQL statement\(s\) could be compared"):
        assert_statement_columns_exist_in_ddl(root, use_git=False)
    assert_statement_columns_exist_in_ddl(root, min_checked=0, use_git=False)


# -- the catalogue --------------------------------------------------------------------------------------------------------------------------------


def _catalogue(tmp_path: Path, *ddl: str) -> dict[str, list[str]]:
    root = _corpus(tmp_path, {f"sql/{i:02d}.sql": text for i, text in enumerate(ddl)})
    return ddl_catalogue(root, use_git=False)


def test_create_table_columns_skip_constraints_generated_expressions_and_comments(tmp_path):
    cat = _catalogue(
        tmp_path,
        """
        CREATE TABLE s.t (
            a int,  -- b int,
            "Mixed Case" text DEFAULT 'x, y)',
            c numeric(10, 2) GENERATED ALWAYS AS (a * 2) STORED,
            /* d int, */ e text DEFAULT $f$ f int, ) $f$,
            PRIMARY KEY (a, c),
            UNIQUE (e),
            CONSTRAINT chk CHECK (a > 0),
            FOREIGN KEY (a) REFERENCES s.o (id),
            EXCLUDE USING gist (e WITH =)
        );
        CREATE INDEX ix ON s.t (a);
        DO $do$ BEGIN PERFORM 1; ALTER TABLE s.t ADD COLUMN ghost int; END $do$;
        """,
    )
    assert cat == {"s.t": ["a", "c", "e", "mixed case"]}


def test_alter_add_drop_rename_columns_in_file_order_and_across_files(tmp_path):
    cat = _catalogue(
        tmp_path,
        """
        CREATE TABLE s.t (a int, b int, c int);
        ALTER TABLE ONLY s.t ADD COLUMN IF NOT EXISTS d int, ADD e numeric(10,2), DROP COLUMN b, ADD CONSTRAINT k UNIQUE (a), DROP CONSTRAINT old;
        ALTER TABLE IF EXISTS s.t RENAME COLUMN c TO c2;
        """,
        "ALTER TABLE s.t DROP COLUMN IF EXISTS d; ALTER TABLE s.t ADD PRIMARY KEY (a); ALTER TABLE s.t ALTER COLUMN a SET NOT NULL;",
    )
    assert cat == {"s.t": ["a", "c2", "e"]}


def test_an_alter_that_sorts_before_its_create_waits_for_it(tmp_path):
    root = _corpus(
        tmp_path,
        {"sql/add_x.sql": "ALTER TABLE s.t ADD COLUMN x int;", "sql/create_t.sql": "CREATE TABLE s.t (a int);", "q.py": _code("SELECT t.x FROM s.t t")},
    )
    assert ddl_catalogue(root, use_git=False) == {"s.t": ["a", "x"]}
    assert find_unknown_statement_columns(root, use_git=False) == []


def test_drop_table_rename_table_and_tables_without_a_column_list(tmp_path):
    cat = _catalogue(
        tmp_path,
        """
        CREATE TABLE s.gone (a int);
        DROP TABLE IF EXISTS s.gone, s.never_there;
        CREATE TABLE s.old_name (a int);
        ALTER TABLE s.old_name RENAME TO new_name;
        CREATE TABLE s.copy (LIKE s.new_name INCLUDING ALL);
        CREATE TABLE s.child (b int) INHERITS (s.new_name);
        CREATE TABLE s.part PARTITION OF s.new_name FOR VALUES IN (1);
        CREATE TABLE s.snap AS SELECT 1 AS a;
        CREATE TEMP TABLE scratch (z int);
        CREATE MATERIALIZED VIEW s.mv AS SELECT 1 AS a;
        DO $$ BEGIN CREATE TABLE s.hidden (q int); END $$;
        """,
    )
    assert cat == {"s.new_name": ["a"]}


def test_a_column_the_ddl_dropped_is_named_as_dropped(tmp_path):
    report = _report(tmp_path, "CREATE TABLE s.t (a int, b int);\nALTER TABLE s.t DROP COLUMN b;", "SELECT t.b FROM s.t t")
    [finding] = report.findings
    assert "column `b` of s.t, which the DDL drops in sql/create.sql;" in finding.message


# -- what is compared -----------------------------------------------------------------------------------------------------------------------------


DDL = "CREATE TABLE s.t (id int, a int, b int);\nCREATE TABLE s.o (id int, t_id int, c int);\nCREATE TABLE u.t (id int, only_u int);\n"


@pytest.mark.parametrize(
    ("sql", "missing"),
    [
        ("INSERT INTO s.t (id, zz) VALUES (%s, %s)", ["zz"]),
        ("INSERT INTO s.t (id, a) VALUES %s ON CONFLICT (id, zz) DO NOTHING", ["zz"]),
        ("INSERT INTO s.t (id, a) VALUES %s ON CONFLICT (id) DO UPDATE SET a = EXCLUDED.a, zz = EXCLUDED.b", ["zz"]),
        ("INSERT INTO s.t (id, a) VALUES %s ON CONFLICT (id) DO UPDATE SET a = EXCLUDED.zz", ["zz"]),
        ("INSERT INTO s.t AS x (id, a) VALUES %s ON CONFLICT (id) DO UPDATE SET a = x.zz", ["zz"]),
        ("UPDATE s.t SET a = 1, zz = 2 WHERE id = %s", ["zz"]),
        ("UPDATE s.t u SET a = o.c FROM s.o o WHERE o.t_id = u.id AND u.zz > 0", ["zz"]),
        ("UPDATE s.t u SET a = o.zz FROM s.o o WHERE o.t_id = u.id", ["zz"]),
        ("SELECT t.zz FROM s.t t", ["zz"]),
        ("SELECT s.t.id FROM s.t", []),
        ("SELECT x.id, o.zz FROM s.t x JOIN s.o o ON o.t_id = x.id", ["zz"]),
        ("SELECT x.id FROM s.t x, s.o o WHERE o.t_id = x.id AND x.zz", ["zz"]),
        ("DELETE FROM s.t d USING s.o o WHERE d.id = o.t_id AND o.zz = 1", ["zz"]),
        ("SELECT 1 FROM s.t x WHERE EXISTS (SELECT 1 FROM s.o o WHERE o.t_id = x.id AND x.zz = 1)", ["zz"]),
        ("WITH n AS (SELECT t.zz FROM s.t t) SELECT n.zz FROM n", ["zz"]),
    ],
)
def test_each_statement_shape_checks_its_columns(tmp_path, sql, missing):
    assert _columns_named(_report(tmp_path, DDL, sql)) == missing


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO s.t (id, a) VALUES %s ON CONFLICT (id) DO UPDATE SET a = EXCLUDED.a, b = EXCLUDED.b",
        "INSERT INTO s.t (id) SELECT v.zz FROM (VALUES (1)) AS v(zz) ON CONFLICT DO NOTHING",
        "INSERT INTO s.t (id) VALUES (%s) ON CONFLICT ON CONSTRAINT t_pkey DO NOTHING",
        "SELECT x.zz FROM (SELECT 1 AS zz) x JOIN s.t t ON t.id = x.zz",
        "SELECT v.zz FROM s.t t CROSS JOIN LATERAL (SELECT t.id AS zz) v",
        "SELECT n.zz FROM s.t t JOIN LATERAL jsonb_array_elements(t.a) n ON true",
        "WITH t AS (SELECT 1 AS zz) SELECT t.zz FROM t",
        "WITH n AS (SELECT 1 AS zz) SELECT n.zz, o.c FROM n JOIN s.o o ON o.id = n.zz",
        "SELECT t.id FROM s.t t WHERE t.id IN (SELECT t.t_id FROM s.o t)",
        "SELECT u.only_u, t.a FROM u.t u JOIN s.t t ON t.id = u.id",
        "SELECT t.id, ghost.zz FROM s.t t",
        "SELECT g.zz FROM generate_series(1, 3) AS g(zz)",
        "SELECT t.a, t.b, t.id FROM s.t t ORDER BY t.id",
    ],
)
def test_nearest_correct_shapes_are_not_reported(tmp_path, sql):
    report = _report(tmp_path, DDL, sql)
    assert report.findings == []


def test_one_alias_in_two_scopes_binds_to_its_own_table(tmp_path):
    # `a` is s.t in the CTE and s.o in the main query; `c` exists only on s.o, `b` only on s.t.
    sql = "WITH n AS (SELECT a.b FROM s.t a) SELECT a.c FROM s.o a JOIN n ON n.b = a.id"
    assert _report(tmp_path, DDL, sql).findings == []
    assert _columns_named(_report(tmp_path / "x", DDL, sql.replace("a.b FROM s.t", "a.c FROM s.t"))) == ["c"]


def test_a_bare_table_name_resolves_when_one_schema_holds_it_and_not_when_two_do(tmp_path):
    sql = "SELECT t.zz FROM t"
    assert _columns_named(_report(tmp_path, "CREATE TABLE s.t (a int);", sql)) == ["zz"]
    ambiguous = _report(tmp_path / "x", DDL, sql)
    assert ambiguous.findings == [] and ambiguous.unknown_tables == {}
    assert _columns_named(_report(tmp_path / "y", "CREATE TABLE t (a int);", "SELECT t.zz FROM public.t")) == ["zz"]


def test_a_cte_named_like_a_table_shadows_it(tmp_path):
    assert _report(tmp_path, DDL, "WITH o AS (SELECT 1 AS zz) SELECT o.zz FROM o").findings == []


def test_placeholders_and_values_lists_do_not_stop_the_parse(tmp_path):
    report = _report(
        tmp_path,
        DDL,
        "INSERT INTO s.t (id, a) VALUES %s ON CONFLICT (id) DO UPDATE SET a = EXCLUDED.a",
        "SELECT t.id FROM s.t t WHERE t.a = %(a)s AND t.b > %s AND t.id = ANY(%s) AND t.a LIKE '100%%'",
        "SELECT t.id FROM s.t t WHERE t.a = :a AND t.b = $1",
    )
    assert report.checked == 3 and report.not_checked == {}


# -- what is not checked, and counted --------------------------------------------------------------------------------------------------------------


def test_statements_the_gate_cannot_read_are_counted_not_passed(tmp_path):
    root = _corpus(
        tmp_path,
        {
            "sql/create.sql": DDL,
            "q.py": """
                import os
                TABLE = "s.t"
                OK = "SELECT t.id FROM s.t t"
                FIELD = "SELECT t.zz FROM {table} t"
                FSTR = f"SELECT t.zz FROM {os.environ['T']} t"
                CALL = "SELECT t.zz FROM s.t t WHERE id = {}".format(1)
                GOOD_F = f"SELECT t.zz FROM {TABLE} t"
                BAD = "SELECT FROM WHERE ((("
                ELSEWHERE = "SELECT x.zz FROM somewhere.other x"
                BARE = "SELECT a FROM s.t"
                REFRESH = "REFRESH MATERIALIZED VIEW s.mv"
            """,
        },
    )
    report = statement_column_report(root, use_git=False)
    assert report.statements == 9 and report.checked == 2
    assert sorted(report.not_checked) == sorted([NOT_LITERAL, UNPARSABLE, NO_DDL_TABLE, NO_RESOLVABLE_COLUMN])
    assert report.not_checked[NOT_LITERAL] == ["q.FIELD", "q.FSTR", "q.CALL"]
    assert report.not_checked[UNPARSABLE] == ["q.BAD", "q.REFRESH"]
    assert report.not_checked[NO_DDL_TABLE] == ["q.ELSEWHERE"]
    assert report.not_checked[NO_RESOLVABLE_COLUMN] == ["q.BARE"]
    assert report.unchecked == 7 and report.unknown_tables == {"somewhere.other": 1}
    assert [f.message.split(" ")[0] for f in report.findings] == ["q.GOOD_F"]
    assert "7 NOT checked" in report.summary() and "somewhere.other (1)" in report.summary()


def test_a_plus_chain_and_bound_names_are_read_whole_and_an_unbound_name_is_not(tmp_path):
    root = _corpus(
        tmp_path,
        {
            "sql/create.sql": DDL,
            "q.py": """
                from elsewhere import FRAGMENT
                _HEAD = "SELECT t.zz "
                CHAIN = _HEAD + "FROM s.t t"
                UNBOUND = _HEAD + FRAGMENT + " FROM s.t t"
            """,
        },
    )
    report = statement_column_report(root, use_git=False)
    assert [f.message.split(" ")[0] for f in report.findings] == ["q.CHAIN"]
    assert report.not_checked[NOT_LITERAL] == ["q.UNBOUND"]  # `_HEAD` alone is a fragment that touches no table


def test_a_line_points_into_the_statement_at_the_column(tmp_path):
    root = _corpus(tmp_path, {"sql/create.sql": DDL, "q.py": 'X = 1\nSQL = """\nSELECT t.id,\n       t.zz\nFROM s.t t\n"""\n'})
    [finding] = find_unknown_statement_columns(root, use_git=False)
    assert finding.line == 4


def test_unqualified_columns_are_checked_only_on_request_and_only_where_unambiguous(tmp_path):
    cases = {
        "SELECT zz FROM s.t": ["zz"],
        "SELECT a, b FROM s.t WHERE id = %s ORDER BY a": [],
        "SELECT a AS total FROM s.t ORDER BY total": [],
        "SELECT row_to_json(t) FROM s.t t": [],
        "SELECT a FROM s.t WHERE id IN (SELECT t_id FROM s.o WHERE c > 0)": [],
        "SELECT a FROM s.t x WHERE EXISTS (SELECT 1 FROM s.o WHERE t_id = x.id AND a > 0)": [],
        "SELECT a, c FROM s.t JOIN s.o ON o.t_id = t.id": [],
        "WITH n AS (SELECT c FROM s.o) SELECT n.c FROM n": [],
        "UPDATE s.t SET a = 1 WHERE zz = 2": ["zz"],
        "SELECT x.n FROM (SELECT count(*) AS n FROM s.t) x": [],
    }
    for i, (sql, expected) in enumerate(cases.items()):
        off = _report(tmp_path / f"off{i}", DDL, sql)
        on = _report(tmp_path / f"on{i}", DDL, sql, check_unqualified=True)
        assert _columns_named(on) == expected, sql
        assert _columns_named(off) == [], sql


# -- baseline -------------------------------------------------------------------------------------------------------------------------------------


def test_baseline_accepts_known_findings_and_fails_new_and_stale_ones(tmp_path):
    root = _corpus(tmp_path, {"sql/create.sql": CREATE, "q.py": _code(STALE)})
    baseline = tmp_path / "baseline.json"
    Baseline(baseline, gate="statement_columns_exist_in_ddl").save(Baseline.count(find_unknown_statement_columns(root, use_git=False)))
    assert_statement_columns_exist_in_ddl(root, baseline, use_git=False)
    _corpus(tmp_path, {"q.py": _code(STALE, CONFIRM)})
    with pytest.raises(pytest.fail.Exception, match=r"(?s)1 new finding\(s\) .*q\.SQL_1 names column `checked_at`"):
        assert_statement_columns_exist_in_ddl(root, baseline, use_git=False)
    _corpus(tmp_path, {"q.py": _code(STALE), "sql/add.sql": ADD_CHECKED_AT})
    with pytest.raises(pytest.fail.Exception, match=r"no longer found"):
        assert_statement_columns_exist_in_ddl(root, baseline, use_git=False)
    with pytest.raises(pytest.skip.Exception, match="baseline rewritten, 0 entr"):
        assert_statement_columns_exist_in_ddl(root, baseline, refresh=True, use_git=False)
    assert Baseline(baseline, gate="statement_columns_exist_in_ddl").load()[0] == {}


def test_a_baseline_key_survives_a_line_shift(tmp_path):
    root = _corpus(tmp_path, {"sql/create.sql": CREATE, "q.py": _code(STALE)})
    baseline = tmp_path / "baseline.json"
    Baseline(baseline, gate="statement_columns_exist_in_ddl").save(Baseline.count(find_unknown_statement_columns(root, use_git=False)))
    _corpus(tmp_path, {"q.py": "# shifted\n\n" + _code(STALE)})
    assert_statement_columns_exist_in_ddl(root, baseline, use_git=False)


def test_exclude_top_dirs_leave_tests_and_scripts_out(tmp_path):
    root = _corpus(tmp_path, {"sql/create.sql": CREATE, "app.py": _code(STALE), "tests/test_q.py": _code(STALE), "scripts/run.py": _code(STALE)})
    assert len(find_unknown_statement_columns(root, use_git=False)) == 3
    kept = find_unknown_statement_columns(root, exclude_top_dirs=["tests", "scripts"], use_git=False)
    assert [f.path for f in kept] == ["app.py"]


def test_ddl_dirs_may_be_several_and_absolute(tmp_path):
    other = _corpus(tmp_path / "other", {"sql/create.sql": CREATE})
    root = _corpus(tmp_path / "proj", {"sql/add.sql": ADD_CHECKED_AT, "q.py": _code(STALE)})
    assert len(find_unknown_statement_columns(root, ddl_dirs=["sql", other / "sql"], min_tables=1, use_git=False)) == 0
    assert len(find_unknown_statement_columns(root, ddl_dirs=[other / "sql"], use_git=False)) == 1


def test_a_table_whose_columns_the_ddl_cannot_list_is_unknown_not_checked(tmp_path):
    ddl = "CREATE TABLE s.base (id int);\nCREATE TABLE s.copy (LIKE s.base INCLUDING ALL);\n"
    report = _report(tmp_path, ddl, "SELECT c.zz FROM s.copy c", "SELECT b.id FROM s.base b")
    assert report.findings == [] and report.unknown_tables == {"s.copy": 1}
    assert report.not_checked == {NO_DDL_TABLE: ["app.SQL_0"]} and report.checked == 1
