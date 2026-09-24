"""Unit tests for the SECURITY DEFINER privilege check. Real scratch .sql files, same no-mocking
convention as this package's other tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.sql_function_privileges import (
    assert_definer_functions_are_locked_down,
    find_unlocked_definer_functions,
)

_DEFINER_NO_REVOKE = """\
CREATE OR REPLACE FUNCTION public.sync_plan_grant(p_user uuid)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  INSERT INTO plan_grants(user_id, plan) VALUES (p_user, 'admin');
END;
$$;
"""

_DEFINER_WITH_REVOKE = _DEFINER_NO_REVOKE + """
REVOKE EXECUTE ON FUNCTION public.sync_plan_grant(uuid) FROM PUBLIC, anon, authenticated;
"""

_DEFINER_NO_SEARCH_PATH = """\
CREATE FUNCTION public.cleanup_old_rows()
RETURNS void
LANGUAGE sql
SECURITY DEFINER
AS $$ DELETE FROM logs WHERE created_at < now() - interval '1 year'; $$;

REVOKE EXECUTE ON FUNCTION public.cleanup_old_rows() FROM PUBLIC;
"""

_INVOKER = """\
CREATE FUNCTION public.harmless(p int)
RETURNS int
LANGUAGE sql
AS $$ SELECT p + 1; $$;
"""


def _migrations(tmp_path: Path, **files: str) -> Path:
    d = tmp_path / "migrations"
    d.mkdir(parents=True)
    for name, body in files.items():
        (d / f"{name}.sql").write_text(body, encoding="utf-8")
    return d


class TestFindUnlockedDefinerFunctions:
    def test_definer_without_revoke_is_flagged(self, tmp_path):
        d = _migrations(tmp_path, m001=_DEFINER_NO_REVOKE)
        problems = find_unlocked_definer_functions(d)
        assert len(problems) == 1
        assert "sync_plan_grant" in problems[0]
        assert "REVOKE EXECUTE" in problems[0]

    def test_definer_with_revoke_passes(self, tmp_path):
        d = _migrations(tmp_path, m001=_DEFINER_WITH_REVOKE)
        assert find_unlocked_definer_functions(d) == []

    def test_revoke_in_a_later_migration_counts(self, tmp_path):
        d = _migrations(
            tmp_path,
            m001=_DEFINER_NO_REVOKE,
            m002="REVOKE EXECUTE ON FUNCTION public.sync_plan_grant(uuid) FROM PUBLIC;",
        )
        assert find_unlocked_definer_functions(d) == []

    def test_allowlist_entry_suppresses_the_finding(self, tmp_path):
        d = _migrations(tmp_path, m001=_DEFINER_NO_REVOKE)
        assert find_unlocked_definer_functions(d, allowed={"sync_plan_grant": "scoped to auth.uid()"}) == []

    def test_missing_search_path_is_flagged_even_when_revoked(self, tmp_path):
        d = _migrations(tmp_path, m001=_DEFINER_NO_SEARCH_PATH)
        problems = find_unlocked_definer_functions(d)
        assert len(problems) == 1
        assert "search_path" in problems[0]

    def test_security_invoker_function_is_ignored(self, tmp_path):
        d = _migrations(tmp_path, m001=_INVOKER)
        assert find_unlocked_definer_functions(d) == []

    def test_replacing_a_definer_with_an_invoker_clears_it(self, tmp_path):
        d = _migrations(
            tmp_path,
            m001=_DEFINER_NO_REVOKE,
            m002="CREATE OR REPLACE FUNCTION public.sync_plan_grant(p_user uuid)\n" "RETURNS void LANGUAGE sql AS $$ SELECT 1; $$;\n",
        )
        assert find_unlocked_definer_functions(d) == []

    def test_grant_back_to_authenticated_needs_an_allowlist_reason(self, tmp_path):
        body = _DEFINER_WITH_REVOKE + "\nGRANT EXECUTE ON FUNCTION public.sync_plan_grant(uuid) TO authenticated;\n"
        d = _migrations(tmp_path, m001=body)
        problems = find_unlocked_definer_functions(d)
        assert len(problems) == 1
        assert "GRANTed back" in problems[0]

    def test_stale_allowlist_entry_is_reported(self, tmp_path):
        d = _migrations(tmp_path, m001=_DEFINER_WITH_REVOKE)
        problems = find_unlocked_definer_functions(d, allowed={"gone": "reason"})
        assert len(problems) == 1
        assert "stale allowlist" in problems[0]

    def test_empty_directory_reports_that_it_examined_nothing(self, tmp_path):
        d = tmp_path / "migrations"
        d.mkdir()
        problems = find_unlocked_definer_functions(d)
        assert len(problems) == 1
        assert "examined nothing" in problems[0]


_POST_BODY_DEFINER = """\
CREATE OR REPLACE FUNCTION public.late_definer() RETURNS void AS $$
BEGIN
  PERFORM 1;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;
"""


class TestLexing:
    def test_security_definer_after_the_body_is_seen(self, tmp_path):
        d = _migrations(tmp_path, m001=_POST_BODY_DEFINER)
        problems = find_unlocked_definer_functions(d)
        assert len(problems) == 1 and "late_definer" in problems[0] and "REVOKE EXECUTE" in problems[0]
        locked = _POST_BODY_DEFINER + "REVOKE EXECUTE ON FUNCTION public.late_definer() FROM PUBLIC;\n"
        assert find_unlocked_definer_functions(_migrations(tmp_path / "b", m001=locked)) == []

    def test_security_definer_inside_the_body_does_not_count(self, tmp_path):
        body = "CREATE FUNCTION public.f() RETURNS text LANGUAGE sql AS $body$ SELECT 'SECURITY DEFINER' $body$;\n"
        assert find_unlocked_definer_functions(_migrations(tmp_path, m001=body)) == []

    def test_pg_dump_quoted_identifiers_and_multi_line_headers(self, tmp_path):
        body = (
            'CREATE OR REPLACE FUNCTION\n  "public"."Quoted"()\n  RETURNS void\n  LANGUAGE "plpgsql" SECURITY DEFINER\n'
            "  SET search_path = public\n  AS $$ BEGIN END; $$;\n"
        )
        problems = find_unlocked_definer_functions(_migrations(tmp_path, m001=body))
        assert len(problems) == 1 and "rpc/Quoted" in problems[0]
        fixed = body + 'REVOKE ALL ON FUNCTION "public"."Quoted"() FROM PUBLIC;\n'
        assert find_unlocked_definer_functions(_migrations(tmp_path / "b", m001=fixed)) == []
        wrong_case = body + "REVOKE ALL ON FUNCTION public.quoted() FROM PUBLIC;\n"
        assert len(find_unlocked_definer_functions(_migrations(tmp_path / "c", m001=wrong_case))) == 1

    def test_a_commented_out_revoke_does_not_count(self, tmp_path):
        line = "-- REVOKE EXECUTE ON FUNCTION public.sync_plan_grant(uuid) FROM PUBLIC;\n"
        block = "/* REVOKE EXECUTE ON FUNCTION public.sync_plan_grant(uuid) FROM PUBLIC; */\n"
        for i, text in enumerate((line, block)):
            problems = find_unlocked_definer_functions(_migrations(tmp_path / str(i), m001=_DEFINER_NO_REVOKE + text))
            assert len(problems) == 1 and "sync_plan_grant" in problems[0]

    def test_a_revoke_inside_a_string_does_not_count(self, tmp_path):
        text = "SELECT 'REVOKE EXECUTE ON FUNCTION public.sync_plan_grant(uuid) FROM PUBLIC;';\n"
        assert len(find_unlocked_definer_functions(_migrations(tmp_path, m001=_DEFINER_NO_REVOKE + text))) == 1


class TestReplay:
    def test_a_revoke_in_another_schema_does_not_cover_this_one(self, tmp_path):
        body = _DEFINER_NO_REVOKE.replace("public.sync_plan_grant", "private.sync_plan_grant")
        body += "REVOKE EXECUTE ON FUNCTION other.sync_plan_grant(uuid) FROM PUBLIC;\n"
        problems = find_unlocked_definer_functions(_migrations(tmp_path, m001=body))
        assert len(problems) == 1 and "private.sync_plan_grant" in problems[0]
        ok = body + "REVOKE EXECUTE ON FUNCTION private.sync_plan_grant(uuid) FROM PUBLIC;\n"
        assert find_unlocked_definer_functions(_migrations(tmp_path / "b", m001=ok)) == []

    def test_an_unqualified_name_resolves_through_set_search_path(self, tmp_path):
        body = "SET search_path TO private;\n" + _DEFINER_NO_REVOKE.replace("public.sync_plan_grant", "sync_plan_grant")
        wrong = body + "REVOKE EXECUTE ON FUNCTION public.sync_plan_grant(uuid) FROM PUBLIC;\n"
        assert len(find_unlocked_definer_functions(_migrations(tmp_path, m001=wrong))) == 1
        right = body + "REVOKE EXECUTE ON FUNCTION private.sync_plan_grant(uuid) FROM PUBLIC;\n"
        assert find_unlocked_definer_functions(_migrations(tmp_path / "b", m001=right)) == []

    def test_drop_and_create_resets_to_the_default_public_grant(self, tmp_path):
        recreate = "DROP FUNCTION IF EXISTS public.sync_plan_grant(uuid);\n" + _DEFINER_NO_REVOKE
        d = _migrations(tmp_path, m001=_DEFINER_WITH_REVOKE, m002=recreate)
        problems = find_unlocked_definer_functions(d)
        assert len(problems) == 1 and "m002.sql" in problems[0]
        replace_only = _migrations(tmp_path / "b", m001=_DEFINER_WITH_REVOKE, m002=_DEFINER_NO_REVOKE)
        assert find_unlocked_definer_functions(replace_only) == [], "CREATE OR REPLACE keeps the function's privileges"

    def test_a_revoke_undone_by_a_later_grant_to_public_is_flagged(self, tmp_path):
        d = _migrations(tmp_path, m001=_DEFINER_WITH_REVOKE, m002="GRANT EXECUTE ON FUNCTION public.sync_plan_grant(uuid) TO PUBLIC;\n")
        assert len(find_unlocked_definer_functions(d)) == 1

    def test_search_path_is_judged_on_the_last_definition_only(self, tmp_path):
        fixed = _DEFINER_NO_SEARCH_PATH.replace("SECURITY DEFINER\n", "SECURITY DEFINER\nSET search_path = ''\n").split("REVOKE")[0]
        d = _migrations(tmp_path, m001=_DEFINER_NO_SEARCH_PATH, m002=fixed.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION"))
        assert find_unlocked_definer_functions(d) == []
        regress = _migrations(tmp_path / "b", m001=_DEFINER_NO_SEARCH_PATH.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION"))
        problems = find_unlocked_definer_functions(regress)
        assert len(problems) == 1 and "search_path" in problems[0]

    def test_alter_function_security_definer_and_set_search_path(self, tmp_path):
        body = _INVOKER + "ALTER FUNCTION public.harmless(int) SECURITY DEFINER;\n"
        problems = find_unlocked_definer_functions(_migrations(tmp_path, m001=body))
        assert len(problems) == 2, problems
        body += "ALTER FUNCTION public.harmless(int) SET search_path = public;\nREVOKE ALL ON FUNCTION public.harmless FROM PUBLIC;\n"
        assert find_unlocked_definer_functions(_migrations(tmp_path / "b", m001=body)) == []


class TestRevokeForms:
    def test_revoke_without_parentheses(self, tmp_path):
        d = _migrations(tmp_path, m001=_DEFINER_NO_REVOKE + "REVOKE EXECUTE ON FUNCTION public.sync_plan_grant FROM PUBLIC;\n")
        assert find_unlocked_definer_functions(d) == []

    def test_one_revoke_listing_several_functions(self, tmp_path):
        two = _DEFINER_NO_REVOKE + _POST_BODY_DEFINER
        d = _migrations(tmp_path, m001=two + "REVOKE EXECUTE ON FUNCTION public.sync_plan_grant(uuid), public.late_definer() FROM PUBLIC;\n")
        assert find_unlocked_definer_functions(d) == []

    def test_revoke_on_all_functions_in_schema(self, tmp_path):
        d = _migrations(tmp_path, m001=_DEFINER_NO_REVOKE + "REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC;\n")
        assert find_unlocked_definer_functions(d) == []
        other = _migrations(tmp_path / "b", m001=_DEFINER_NO_REVOKE + "REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA private FROM PUBLIC;\n")
        assert len(find_unlocked_definer_functions(other)) == 1

    def test_revoke_on_all_functions_does_not_cover_functions_created_later(self, tmp_path):
        d = _migrations(tmp_path, m001="REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC;\n", m002=_DEFINER_NO_REVOKE)
        assert len(find_unlocked_definer_functions(d)) == 1

    def test_default_privileges_cover_functions_created_later(self, tmp_path):
        adp = "ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;\n"
        assert find_unlocked_definer_functions(_migrations(tmp_path, m001=adp, m002=_DEFINER_NO_REVOKE)) == []
        before = _migrations(tmp_path / "b", m001=_DEFINER_NO_REVOKE, m002=adp)
        assert len(find_unlocked_definer_functions(before)) == 1, "default privileges do not reach existing functions"

    def test_revoke_grant_option_leaves_execute(self, tmp_path):
        d = _migrations(tmp_path, m001=_DEFINER_NO_REVOKE + "REVOKE GRANT OPTION FOR EXECUTE ON FUNCTION public.sync_plan_grant(uuid) FROM PUBLIC;\n")
        assert len(find_unlocked_definer_functions(d)) == 1


def test_a_bom_migration_is_read(tmp_path):
    d = tmp_path / "migrations"
    d.mkdir()
    (d / "m001.sql").write_bytes(b"\xef\xbb\xbf" + _DEFINER_NO_REVOKE.encode("utf-8"))
    problems = find_unlocked_definer_functions(d)
    assert len(problems) == 1 and "sync_plan_grant" in problems[0]


def test_an_undecodable_migration_is_a_problem_not_a_pass(tmp_path):
    d = tmp_path / "migrations"
    d.mkdir()
    (d / "m001.sql").write_bytes(_DEFINER_WITH_REVOKE.encode("utf-8"))
    (d / "m002.sql").write_bytes(b"-- \xff\xfe broken\n")
    problems = find_unlocked_definer_functions(d)
    assert len(problems) == 1 and "m002.sql" in problems[0] and "not replayed" in problems[0]


def test_a_missing_directory_examined_nothing(tmp_path):
    problems = find_unlocked_definer_functions(tmp_path / "nope")
    assert len(problems) == 1 and "examined nothing" in problems[0]


def test_allowed_accepts_a_schema_qualified_name(tmp_path):
    d = _migrations(tmp_path, m001=_DEFINER_NO_REVOKE)
    assert find_unlocked_definer_functions(d, allowed={"public.sync_plan_grant": "scoped"}) == []


class TestAssert:
    def test_assert_passes_on_a_locked_down_schema(self, tmp_path):
        assert_definer_functions_are_locked_down(_migrations(tmp_path, m001=_DEFINER_WITH_REVOKE))

    def test_assert_fails_and_names_the_function(self, tmp_path):
        d = _migrations(tmp_path, m001=_DEFINER_NO_REVOKE)
        with pytest.raises(pytest.fail.Exception, match="sync_plan_grant"):
            assert_definer_functions_are_locked_down(d)
