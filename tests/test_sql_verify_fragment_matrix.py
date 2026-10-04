"""sql_verify fragment matrix: the dashboard's narrow-listing "hire" ordering, reproduced from its real shapes."""

from __future__ import annotations

import pytest

from py_ci_shared import sql_verify as sv

pytest.importorskip("sqlglot")

# The shapes of realtime_applications/top_jobs_sql.py and top_jobs_documents.py, cut to what the defect needs.
FULL_SQL = """
    WITH core AS ({core_sql})
    SELECT core.*, cd.details->'location'->>'country' AS client_country,
           cd.details->'buyerExtra'->>'isPaymentMethodVerified' AS client_payment_verified,
           cs.details->'jobs' AS client_jobs
    FROM core
    LEFT JOIN LATERAL (
        SELECT co.cl_id FROM new_upwork.client_opened_jobs co
        WHERE co.job_cid = %(cid_prefix)s || core.job_uid AND co.job_cid LIKE '~02%%'
        ORDER BY co.cl_id LIMIT 1
    ) co ON true
    LEFT JOIN new_upwork.client_details cd ON cd.cl_id = co.cl_id
    LEFT JOIN LATERAL (
        SELECT details FROM new_upwork.client_stats s WHERE s.cl_id = co.cl_id ORDER BY s.last_updated DESC LIMIT 1
    ) cs ON true
    ORDER BY {outer_order_by}
"""
NARROW_SQL = """
    SELECT core.job_uid, core.final_score, core.client_country, core.client_payment_verified
    FROM ({full_sql}) core
    ORDER BY {outer_order_by}
"""
CORE_SQL = "SELECT sr.job_uid, sr.final_score FROM jobs_matching.screening_results sr WHERE sr.fl_cid = %(fl_cid)s"
_FILL = "CASE WHEN ({j}->>'postedCount') ~ '^[0-9]+$' THEN 0 ELSE 3 END"
OUTER = {
    "score": "core.final_score DESC, core.job_uid",
    "hire": "CASE WHEN cd.details->'buyerExtra'->>'isPaymentMethodVerified' = 'false' THEN 4 ELSE "
    + _FILL.format(j="cs.details->'jobs'")
    + " END ASC, core.final_score DESC, core.job_uid",
}
WRAPPED = {
    "hire": "CASE WHEN core.client_payment_verified = 'false' THEN 4 ELSE "
    + _FILL.format(j="core.client_jobs")
    + " END ASC, core.final_score DESC, core.job_uid",
}


def _listing(ordering: str, *, fixed: bool) -> str:
    full = FULL_SQL.format(core_sql=CORE_SQL, outer_order_by=OUTER[ordering])
    wrapped = WRAPPED.get(ordering, OUTER[ordering]) if fixed else OUTER[ordering]
    return NARROW_SQL.format(full_sql=full, outer_order_by=wrapped)


def _builder(fixed: bool) -> sv.FragmentMatrix:
    return sv.FragmentMatrix("narrow listing", build=lambda k: _listing(k, fixed=fixed), fragments=[OUTER, WRAPPED])


def test_the_hire_ordering_in_the_narrow_wrapper_is_reported():
    """The real defect: the wrapper re-sorted by the inner statement's `cd`/`cs`, which are out of scope there."""
    problems = sv.find_fragment_matrix_problems([_builder(fixed=False)])
    assert problems and all(p.startswith("narrow listing[hire]") for p in problems), problems
    assert any("names `cd`" in p for p in problems) and any("names `cs`" in p for p in problems)
    assert "['core']" in problems[0]
    with pytest.raises(pytest.fail.Exception, match="broken in some combination"):
        sv.assert_fragment_matrix([_builder(fixed=False)])


def test_the_fixed_listing_and_every_ordering_alone_are_clean():
    assert sv.find_fragment_matrix_problems([_builder(fixed=True)]) == []
    alone = sv.FragmentMatrix("full listing", template=FULL_SQL, slot="outer_order_by", fragments=[OUTER], fill={"core_sql": CORE_SQL})
    assert sv.find_fragment_matrix_problems([alone]) == []


def test_the_declarative_form_crosses_every_fragment_with_the_wrapper():
    """Each fragment valid alone; OUTER's hire formatted into the wrapper is the combination nobody built."""
    full = FULL_SQL.format(core_sql=CORE_SQL, outer_order_by="core.job_uid")
    matrix = sv.FragmentMatrix("narrow", template=NARROW_SQL, slot="outer_order_by", fragments=[OUTER, WRAPPED], fill={"full_sql": full})
    labels = [label for label, _ in sv.matrix_statements(matrix)]
    assert labels == ["narrow[fragments[0]:hire]", "narrow[fragments[0]:score]", "narrow[fragments[1]:hire]"]
    problems = sv.find_fragment_matrix_problems([matrix])
    assert {p.split(":")[0] for p in problems} == {"narrow[fragments[0]"}, problems
    assert all("fragments[0]:hire]" in p for p in problems)


def test_teeth_the_scope_check_is_what_reports_it(monkeypatch):
    monkeypatch.setattr(sv, "alias_scope_problems", lambda sql, **kw: [])
    assert sv.alias_scope_problems("SELECT x.a FROM t", extra_aliases=frozenset()) == []  # the substitution took
    assert sv.find_fragment_matrix_problems([_builder(fixed=False)]) == []


def test_correlated_subqueries_laterals_ctes_and_excluded_are_in_scope():
    ok = [
        "SELECT x.a FROM t x WHERE EXISTS (SELECT 1 FROM u WHERE u.id = x.id)",
        "WITH c AS (SELECT 1 AS a) SELECT c.a FROM c",
        "SELECT x.a, l.b FROM t x LEFT JOIN LATERAL (SELECT y.b FROM u y WHERE y.id = x.id) l ON true",
        "INSERT INTO t (a) VALUES (1) ON CONFLICT (a) DO UPDATE SET a = excluded.a",
        "SELECT screening_results.a FROM jobs_matching.screening_results",
    ]
    for sql in ok:
        assert sv.alias_scope_problems(sql) == [], sql
    assert sv.alias_scope_problems("SELECT x.a FROM t y")


def test_unbuildable_unparsable_and_empty_matrices_are_findings():
    missing_fill = sv.FragmentMatrix("m", template="SELECT {a} FROM {t}", slot="a", fragments=[{"k": "1"}])
    problem, floor = sv.find_fragment_matrix_problems([missing_fill])
    assert "formatting the template failed" in problem and "fill" in problem and "built 0" in floor

    def boom(key):
        raise RuntimeError("no such ordering")

    raised = sv.FragmentMatrix("b", build=boom, keys=["k"])
    assert "the builder raised RuntimeError" in sv.find_fragment_matrix_problems([raised])[0]
    broken = sv.FragmentMatrix("p", build=lambda k: "SELECT FROM WHERE (", keys=["k"])
    assert "does not parse" in sv.find_fragment_matrix_problems([broken])[0]
    assert "built 0 statement(s)" in sv.find_fragment_matrix_problems([])[0]
    raw_fill = sv.FragmentMatrix("raw", template=FULL_SQL, slot="outer_order_by", fragments=[OUTER], fill={"core_sql": "SELECT {cols} FROM t"})
    problems = sv.find_fragment_matrix_problems([raw_fill])
    assert len(problems) == 2 and all("unfilled placeholder(s) ['{cols}']" in p for p in problems), problems
    literal = sv.FragmentMatrix("lit", build=lambda k: "SELECT x.a FROM t x WHERE x.tags = '{solo}'", keys=["k"])
    assert sv.find_fragment_matrix_problems([literal]) == []
    not_a_dict = sv.FragmentMatrix("d", template="SELECT {a}", slot="a", fragments=[["x"]])
    assert "cannot build the matrix" in sv.find_fragment_matrix_problems([not_a_dict])[0]


def test_dotted_references_resolve_by_import_and_placeholders_follow_the_driver(tmp_path, monkeypatch):
    import uuid

    mod = f"frag_{uuid.uuid4().hex[:8]}"
    (tmp_path / f"{mod}.py").write_text(f"_SQL = 'SELECT x.a FROM t x ORDER BY {{o}}'\n_ORD = {{'ok': 'x.a', 'bad': 'y.a'}}\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    matrix = sv.FragmentMatrix("ref", template=f"{mod}._SQL", slot="o", fragments=[f"{mod}._ORD"])
    (problem,) = sv.find_fragment_matrix_problems([matrix])
    assert problem.startswith(f"ref[{mod}._ORD:bad]") and "names `y`" in problem
    not_a_dict = sv.FragmentMatrix("r", template="X {o}", slot="o", fragments=["os.sep"])
    assert "not a dict of fragments" in sv.find_fragment_matrix_problems([not_a_dict])[0]
    assert sv._prepare_ready("SELECT %(a)s, %(b)s, %(a)s, %s WHERE x LIKE 'a%%'", "pyformat") == "SELECT $1, $2, $1, $3 WHERE x LIKE 'a%'"
    assert sv._parse_ready("SELECT %(a)s WHERE x LIKE 'a%%'", "pyformat") == "SELECT NULL WHERE x LIKE 'a%'"
    assert sv._parse_ready("SELECT %(a)s", "format") == "SELECT %(a)s"


class _Cursor:
    def __init__(self, log, fail):
        self.log, self.fail = log, fail

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql):
        self.log.append(sql)
        if self.fail and sql.startswith("PREPARE"):
            raise RuntimeError('relation "t" does not exist')


class _Conn:
    def __init__(self, fail=False):
        self.log, self.fail, self.rollbacks = [], fail, 0

    def cursor(self):
        return _Cursor(self.log, self.fail)

    def rollback(self):
        self.rollbacks += 1


def test_with_a_connection_each_statement_is_prepared_and_rolled_back():
    matrix = sv.FragmentMatrix("c", build=lambda k: "SELECT x.a FROM t x WHERE x.b = %(v)s", keys=["k"])
    conn = _Conn()
    assert sv.find_fragment_matrix_problems([matrix], conn=conn) == []
    assert conn.log == ["PREPARE py_ci_shared_fragment_matrix AS SELECT x.a FROM t x WHERE x.b = $1", "DEALLOCATE py_ci_shared_fragment_matrix"]
    assert conn.rollbacks == 1
    failing = _Conn(fail=True)
    (problem,) = sv.find_fragment_matrix_problems([matrix], conn=failing)
    assert "PREPARE failed" in problem and failing.rollbacks == 1
