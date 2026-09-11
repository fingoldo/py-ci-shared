"""Rule 6 of repo hygiene: no tracked `.py` / `.sql` inside an `audits/` folder.

Real scratch repos (git init), same no-mocking convention as test_repo_hygiene.py.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.repo_hygiene import assert_repo_hygiene, find_scripts_in_audit_folders


def _git_repo(tmp_path: Path, *tracked: str) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for rel in tracked:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A", "-f"], cwd=tmp_path, check=True)
    return tmp_path


class TestScriptsInAuditFolders:
    def test_a_runbook_beside_its_audit_is_flagged_at_any_depth(self, tmp_path):
        """The monorepo case that found it: projects keep audits/ two and three levels down."""
        repo = _git_repo(
            tmp_path,
            "audits/2026-09-01/probe.py",
            "upwork/new_scraper/prod/audits/2026-09-04/RUNBOOK_drop.sql",
            "upwork/new_scraper/prod/audits/implemented/2026-09-01/06_sql.md",
        )

        assert sorted(find_scripts_in_audit_folders(repo)) == [
            "audits/2026-09-01/probe.py",
            "upwork/new_scraper/prod/audits/2026-09-04/RUNBOOK_drop.sql",
        ]

    def test_the_same_files_in_their_proper_homes_pass(self, tmp_path):
        repo = _git_repo(tmp_path, "sql/RUNBOOK_drop.sql", "probes/probe.py", "scripts/build_tracker.py", "audits/2026-09-01/06_sql.md")

        assert find_scripts_in_audit_folders(repo) == []

    def test_a_file_merely_named_audits_is_not_a_folder(self, tmp_path):
        """`audits.py` / `test_audits.sql` are names, not the folder the rule is about."""
        repo = _git_repo(tmp_path, "src/pkg/audits.py", "tests/test_audits.py")

        assert find_scripts_in_audit_folders(repo) == []

    def test_an_untracked_scratch_probe_is_not_flagged(self, tmp_path):
        repo = _git_repo(tmp_path, "audits/2026-09-01/06_sql.md")
        (repo / "audits" / "2026-09-01" / "scratch.py").write_text("x\n", encoding="utf-8")

        assert find_scripts_in_audit_folders(repo) == []


class TestItIsPartOfTheSharedAssertion:
    def test_assert_repo_hygiene_fails_on_it_by_default(self, tmp_path):
        """Every consumer already calls assert_repo_hygiene; the rule reaches them only through it."""
        repo = _git_repo(tmp_path, "audits/2026-09-01/RUNBOOK.sql")

        with pytest.raises(pytest.fail.Exception, match="inside an audits/ folder"):
            assert_repo_hygiene(repo)

    def test_the_opt_out_is_explicit(self, tmp_path):
        repo = _git_repo(tmp_path, "audits/2026-09-01/RUNBOOK.sql")

        assert_repo_hygiene(repo, audit_dirs_hold_no_scripts=False)
