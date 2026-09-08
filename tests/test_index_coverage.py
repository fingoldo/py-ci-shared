"""An index absent by NAME is not necessarily absent.

Two tools in two repositories reported the same false result, which is why the comparison lives in
this package rather than in either of them:

* `realtime_applications/scripts/check_indexes.py` (audit 2026-09-08 IDX-1) compared names and told
  the operator to apply a DDL file. Of 59 entries, 20 missed by name; nine were already present
  under a different name and five of those WERE the table's primary key, on tables of 35, 45, 46,
  115 GB and 1.7 GB. The file's own guard could not help: every statement is
  `CREATE INDEX CONCURRENTLY IF NOT EXISTS`, and IF NOT EXISTS matches by NAME -- precisely what
  differed.
* `production_scrapers/scripts/check_schema_drift.py` (SQL-10) had already fetched the live
  catalogue for its second list, so a renamed index appeared in both "missing" and "undeclared"
  while the report said nothing. Four of the ten it called missing had their access path.

These tests drive the comparison itself: every case is a pair of real `CREATE INDEX` statements.
"""

from __future__ import annotations

from py_ci_shared.index_coverage import find_cover, find_definition, parse, statements


def _cover(expected, *live):
    return find_cover(parse(expected), [parse(s) for s in live])


class TestRedundancyIsRecognised:
    def test_a_primary_key_covers_an_index_on_the_same_column(self):
        cover = _cover(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_job_embeddings_job_uid "
            "ON new_upwork.job_embeddings (job_uid)",
            "CREATE UNIQUE INDEX job_embeddings_pkey ON new_upwork.job_embeddings USING btree (job_uid)",
        )
        assert cover is not None and cover.name == "job_embeddings_pkey"

    def test_a_renamed_partial_index_covers_its_twin(self):
        cover = _cover(
            "CREATE INDEX idx_jobs_details_ts_desc_has_details ON new_upwork.jobs_details (ts DESC) "
            "WHERE details IS NOT NULL",
            "CREATE INDEX idx_jobs_details_ts_desc_not_null ON new_upwork.jobs_details "
            "USING btree (ts DESC) WHERE (details IS NOT NULL)",
        )
        assert cover is not None and cover.name == "idx_jobs_details_ts_desc_not_null"

    def test_a_wider_index_covers_a_prefix_of_itself(self):
        cover = _cover(
            "CREATE INDEX idx_jhs_job_uid_ts_desc ON new_upwork.jobs_hist_stats (job_uid, ts DESC)",
            "CREATE INDEX idx_jhs_job_ts_status ON new_upwork.jobs_hist_stats "
            "USING btree (job_uid, ts DESC) INCLUDE (status)",
        )
        assert cover is not None and cover.name == "idx_jhs_job_ts_status"

    def test_an_unconditional_index_covers_a_partial_expectation(self):
        """Every row the partial index wanted is in the unconditional one."""
        cover = _cover(
            "CREATE INDEX ix_jd_cl_id_nonzero ON new_upwork.jobs_details (cl_id) WHERE cl_id <> 0",
            "CREATE INDEX ix_jd_cl_id ON new_upwork.jobs_details USING btree (cl_id)",
        )
        assert cover is not None and cover.name == "ix_jd_cl_id"

    def test_include_columns_are_not_key_columns(self):
        """An INCLUDE payload cannot be seeked on, so it must not widen the key list."""
        assert parse("CREATE INDEX i ON t USING btree (job_uid, ts DESC) INCLUDE (status)").columns == (
            "job_uid",
            "ts",
        )


class TestItRefusesToOverClaim:
    """Calling a missing index present is the one error that costs something."""

    def test_a_partial_index_does_not_cover_an_unconditional_need(self):
        assert (
            _cover(
                "CREATE INDEX want ON new_upwork.freelancers_jobs (client_team_uid)",
                "CREATE INDEX live ON new_upwork.freelancers_jobs USING btree (client_team_uid) "
                "WHERE (client_team_uid IS NOT NULL)",
            )
            is None
        )

    def test_a_different_predicate_does_not_cover(self):
        assert (
            _cover(
                "CREATE INDEX want ON t (a) WHERE b IS NOT NULL",
                "CREATE INDEX live ON t USING btree (a) WHERE (c IS NOT NULL)",
            )
            is None
        )

    def test_a_different_access_method_does_not_cover(self):
        assert _cover("CREATE INDEX want ON t USING gin (a)", "CREATE INDEX live ON t USING btree (a)") is None

    def test_a_longer_expectation_is_not_covered_by_a_shorter_index(self):
        assert _cover("CREATE INDEX want ON t (a, b)", "CREATE INDEX live ON t USING btree (a)") is None

    def test_a_mixed_direction_order_is_not_covered_by_an_ascending_index(self):
        """A btree scans both ways, so `(a, b)` yields `(a, b)` or `(a DESC, b DESC)` -- never
        `(a, b DESC)`. Discarding direction would call this covered and hide a real gap.

        Not hypothetical: `ix_fp_fl_cid_ts` is declared `(fl_cid, ts DESC)` and
        `pk_freelancers_profiles` is `(fl_cid, ts)`, and the index is genuinely absent.
        """
        assert _cover("CREATE INDEX want ON t (a, b DESC)", "CREATE INDEX live ON t USING btree (a, b)") is None

    def test_a_wholly_reversed_order_IS_covered(self):
        """The same reasoning the other way: a full reversal is just a backward scan."""
        cover = _cover("CREATE INDEX want ON t (a DESC, b DESC)", "CREATE INDEX live ON t USING btree (a, b)")
        assert cover is not None and cover.name == "live"


class TestParsing:
    def test_the_target_table_comes_from_the_statement(self):
        """An ad-hoc regex for "the table near this index name" in a .sql file matched a query
        ALIAS and reported `new_upwork.a`. The definition is the only reliable source."""
        index = parse("CREATE INDEX i ON new_upwork.freelancers_profiles (fl_cid)")
        assert (index.schema, index.table) == ("new_upwork", "freelancers_profiles")

    def test_an_unqualified_target_has_no_schema(self):
        index = parse("CREATE INDEX i ON freelancers_profiles (fl_cid)")
        assert index.schema is None and index.table == "freelancers_profiles"

    def test_a_commented_out_statement_is_not_a_definition(self):
        """A dead statement parses perfectly if comments are not stripped first."""
        assert find_definition("-- CREATE INDEX dead_idx ON t (a);\n", "dead_idx") is None

    def test_the_match_is_confirmed_by_the_parsed_name(self):
        """Searching for a name inside a statement blob credits a NEIGHBOUR's columns to it. The
        decoy here sits in a string literal, where comment-stripping cannot rescue it."""
        sql = (
            "CREATE INDEX idx_client_legacy_map_legacy_id ON new_upwork.client_legacy_map (legacy_id) "
            "WHERE note <> 'superseded by idx_coj_job_cid';\n"
            "CREATE INDEX idx_coj_job_cid ON new_upwork.client_opened_jobs (job_cid);\n"
        )
        found = find_definition(sql, "idx_coj_job_cid")
        assert found.name == "idx_coj_job_cid" and found.columns == ("job_cid",)

    def test_expression_columns_survive_the_split(self):
        """A plain `.split(",")` shreds `GREATEST(a, b)` into fragments that match nothing."""
        assert parse("CREATE INDEX i ON t USING btree (fl_cid, GREATEST(a, b), ts DESC)").columns == (
            "fl_cid",
            "greatest(a, b)",
            "ts",
        )

    def test_a_catalogue_echo_and_a_source_statement_normalise_alike(self):
        """Both sides go through one parser on purpose: if they normalised differently, coverage
        would depend on which spelling was compared."""
        source = parse("CREATE INDEX i ON new_upwork.t (ts DESC) WHERE details IS NOT NULL")
        echo = parse("CREATE INDEX i ON new_upwork.t USING btree (ts DESC) WHERE (details IS NOT NULL)")
        assert (source.columns, source.descending, source.predicate, source.method) == (
            echo.columns,
            echo.descending,
            echo.predicate,
            echo.method,
        )

    def test_statements_are_split_on_semicolons(self):
        assert len(list(statements("CREATE INDEX a ON t (x); -- note\nCREATE INDEX b ON t (y);"))) == 2

    def test_a_statement_that_is_not_an_index_parses_to_nothing(self):
        assert parse("CREATE TABLE t (a int)") is None
