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
    d.mkdir()
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


class TestAssert:
    def test_assert_passes_on_a_locked_down_schema(self, tmp_path):
        assert_definer_functions_are_locked_down(_migrations(tmp_path, m001=_DEFINER_WITH_REVOKE))

    def test_assert_fails_and_names_the_function(self, tmp_path):
        d = _migrations(tmp_path, m001=_DEFINER_NO_REVOKE)
        with pytest.raises(pytest.fail.Exception, match="sync_plan_grant"):
            assert_definer_functions_are_locked_down(d)
