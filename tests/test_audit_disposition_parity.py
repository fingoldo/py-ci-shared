"""Unit tests for the disposition/artefact parity check. Real scratch audit files and real trees,
same no-mocking convention as this package's other tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.audit_disposition_parity import (
    assert_dispositions_name_real_artefacts,
    find_unsupported_dispositions,
)


def _audit(tmp_path: Path, body: str, *existing: str) -> tuple[Path, Path]:
    audit_dir = tmp_path / "audits" / "2026-09-02"
    audit_dir.mkdir(parents=True)
    (audit_dir / "01_correctness.md").write_text(body, encoding="utf-8")
    for rel in existing:
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x", encoding="utf-8")
    return audit_dir, tmp_path


class TestPaths:
    def test_resolved_naming_a_missing_script_is_flagged(self, tmp_path):
        audit, root = _audit(
            tmp_path,
            "#### P04-3 - no live probe\n- Disposition: RESOLVED - `tool/check-live-rpc-exists.py` probes PostgREST\n",
        )
        problems = find_unsupported_dispositions(audit, root)
        assert len(problems) == 1
        assert "check-live-rpc-exists.py" in problems[0]

    def test_resolved_naming_a_script_that_exists_passes(self, tmp_path):
        audit, root = _audit(
            tmp_path,
            "#### P04-3\n- Disposition: RESOLVED - `tool/check-live-rpc-exists.py` probes PostgREST\n",
            "tool/check-live-rpc-exists.py",
        )
        assert find_unsupported_dispositions(audit, root) == []

    def test_a_bare_path_without_backticks_is_checked_too(self, tmp_path):
        audit, root = _audit(
            tmp_path,
            "#### P04-1\n- Disposition: RESOLVED - the step in .github/workflows/prod-e2e.yml runs nightly\n",
        )
        problems = find_unsupported_dispositions(audit, root)
        assert len(problems) == 1
        assert "prod-e2e.yml" in problems[0]

    def test_a_line_reference_is_stripped_before_resolving(self, tmp_path):
        audit, root = _audit(
            tmp_path,
            "#### P01-1\n- Disposition: RESOLVED - fixed in `lib/app.dart:219`\n",
            "lib/app.dart",
        )
        assert find_unsupported_dispositions(audit, root) == []

    def test_deferred_is_not_asserting_anything_so_is_not_checked(self, tmp_path):
        audit, root = _audit(
            tmp_path,
            "#### P07-12\n- Disposition: DEFERRED - splitting `lib/renderers/huge_renderer.dart` is a refactor\n",
        )
        assert find_unsupported_dispositions(audit, root) == []

    def test_wont_fix_is_not_checked_either(self, tmp_path):
        audit, root = _audit(
            tmp_path,
            "#### P09-15\n- Disposition: WON'T FIX - `lib/gone.dart` is deliberate\n",
        )
        assert find_unsupported_dispositions(audit, root) == []

    def test_ignore_list_covers_a_path_named_by_a_correction(self, tmp_path):
        audit, root = _audit(
            tmp_path,
            "#### P04-1\n- Disposition: RESOLVED - CORRECTION: `packages/flutter_app_core/x.dart` had left the repo\n",
        )
        assert (
            find_unsupported_dispositions(
                audit, root, ignore_paths=["packages/flutter_app_core/x.dart"]
            )
            == []
        )


class TestMigrations:
    def test_a_named_migration_that_does_not_exist_is_flagged(self, tmp_path):
        audit, root = _audit(
            tmp_path,
            "#### P03-14\n- Disposition: RESOLVED - migration 039 adds the unique index\n",
        )
        (root / "supabase" / "migrations").mkdir(parents=True)
        (root / "supabase" / "migrations" / "038_audit.sql").write_text("x", encoding="utf-8")
        problems = find_unsupported_dispositions(audit, root)
        assert len(problems) == 1
        assert "migration 039" in problems[0]

    def test_a_named_migration_that_exists_passes(self, tmp_path):
        audit, root = _audit(
            tmp_path,
            "#### P03-14\n- Disposition: RESOLVED - migration 039 adds the unique index\n",
        )
        (root / "supabase" / "migrations").mkdir(parents=True)
        (root / "supabase" / "migrations" / "039_replay.sql").write_text("x", encoding="utf-8")
        assert find_unsupported_dispositions(audit, root) == []

    def test_a_migration_range_checks_every_number_in_it(self, tmp_path):
        audit, root = _audit(
            tmp_path,
            "#### P03-1\n- Disposition: RESOLVED - migrations 034-036 revoke and register\n",
        )
        migrations = root / "supabase" / "migrations"
        migrations.mkdir(parents=True)
        for name in ("034_a.sql", "036_c.sql"):
            (migrations / name).write_text("x", encoding="utf-8")
        problems = find_unsupported_dispositions(audit, root)
        assert len(problems) == 1
        assert "migration 035" in problems[0]


class TestSelfChecks:
    def test_an_empty_audit_directory_reports_examining_nothing(self, tmp_path):
        audit = tmp_path / "audits" / "2026-09-02"
        audit.mkdir(parents=True)
        problems = find_unsupported_dispositions(audit, tmp_path)
        assert len(problems) == 1
        assert "examined nothing" in problems[0]

    def test_files_with_no_recognised_disposition_line_are_reported(self, tmp_path):
        audit, root = _audit(tmp_path, "#### P01-1 - a finding\nSome prose, no verdict line.\n")
        problems = find_unsupported_dispositions(audit, root)
        assert len(problems) == 1
        assert "examined nothing" in problems[0]


class TestAssert:
    def test_assert_passes(self, tmp_path):
        audit, root = _audit(
            tmp_path,
            "#### P01-1\n- Disposition: RESOLVED - fixed in `lib/app.dart`\n",
            "lib/app.dart",
        )
        assert_dispositions_name_real_artefacts(audit, root)

    def test_assert_fails_naming_the_artefact(self, tmp_path):
        audit, root = _audit(
            tmp_path,
            "#### P01-1\n- Disposition: RESOLVED - fixed in `lib/gone.dart`\n",
        )
        with pytest.raises(pytest.fail.Exception, match=r"gone\.dart"):
            assert_dispositions_name_real_artefacts(audit, root)
