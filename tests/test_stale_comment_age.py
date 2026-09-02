"""Unit tests for the stale-comment age check. Real scratch git repos with backdated commits, same
no-mocking convention as this package's other tests.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.stale_comment_age import assert_no_stale_todos, find_stale_comments

_ENV_OLD = {
    "GIT_AUTHOR_DATE": "2020-01-01T00:00:00",
    "GIT_COMMITTER_DATE": "2020-01-01T00:00:00",
}


def _repo(tmp_path: Path, rel: str, body: str, *, old: bool = True) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    env = dict(_ENV_OLD) if old else {}
    subprocess.run(
        ["git", "commit", "-q", "-m", "seed"],
        cwd=tmp_path,
        check=True,
        env={**subprocess.os.environ, **env},
    )
    return tmp_path


class TestStaleComments:
    def test_old_todo_is_flagged(self, tmp_path):
        repo = _repo(tmp_path, "lib/a.dart", "// TODO: call this after the first lesson\nvoid a() {}\n")
        problems = find_stale_comments(repo, ["lib"], max_age_days=30)
        assert len(problems) == 1
        assert "TODO" in problems[0]

    def test_fresh_todo_is_not_flagged(self, tmp_path):
        repo = _repo(tmp_path, "lib/a.dart", "// TODO: something\nvoid a() {}\n", old=False)
        assert find_stale_comments(repo, ["lib"], max_age_days=30) == []

    def test_todo_with_an_issue_reference_is_exempt(self, tmp_path):
        repo = _repo(tmp_path, "lib/a.dart", "// TODO(#42): tracked elsewhere\nvoid a() {}\n")
        assert find_stale_comments(repo, ["lib"], max_age_days=30) == []

    def test_issue_reference_exemption_can_be_switched_off(self, tmp_path):
        repo = _repo(tmp_path, "lib/a.dart", "// TODO(#42): tracked elsewhere\nvoid a() {}\n")
        assert len(find_stale_comments(repo, ["lib"], max_age_days=30, require_issue_ref=False)) == 1

    def test_old_commented_out_call_is_flagged(self, tmp_path):
        repo = _repo(tmp_path, "lib/a.dart", "void a() {\n  // buildCta(context, onLaunchSurvey),\n}\n")
        problems = find_stale_comments(repo, ["lib"], max_age_days=30)
        assert len(problems) == 1
        assert "commented-out code" in problems[0]

    def test_prose_comment_is_not_mistaken_for_code(self, tmp_path):
        repo = _repo(tmp_path, "lib/a.dart", "// The CTA was removed because nobody clicked it.\nvoid a() {}\n")
        assert find_stale_comments(repo, ["lib"], max_age_days=30) == []

    def test_a_generous_max_age_passes_everything(self, tmp_path):
        repo = _repo(tmp_path, "lib/a.dart", "// TODO: something\nvoid a() {}\n")
        assert find_stale_comments(repo, ["lib"], max_age_days=100000) == []

    def test_missing_directory_is_skipped(self, tmp_path):
        repo = _repo(tmp_path, "lib/a.dart", "void a() {}\n")
        assert find_stale_comments(repo, ["nope"], max_age_days=1) == []


class TestAssert:
    def test_assert_passes(self, tmp_path):
        repo = _repo(tmp_path, "lib/a.dart", "void a() {}\n")
        assert_no_stale_todos(repo, ["lib"], max_age_days=30)

    def test_assert_fails(self, tmp_path):
        repo = _repo(tmp_path, "lib/a.dart", "// FIXME: broken\nvoid a() {}\n")
        with pytest.raises(pytest.fail.Exception, match="FIXME"):
            assert_no_stale_todos(repo, ["lib"], max_age_days=30)
