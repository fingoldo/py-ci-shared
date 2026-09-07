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


class TestProseThatLooksLikeCode:
    """A code-shaped sentence is told from dead code by the block it sits in, not by itself.

    Measured across five repositories, the line-only rule matched 37 comments and exactly one of
    them was commented-out code. The other 36 were sentences that happen to name functions, plus
    usage examples in module documentation.
    """

    # A paragraph naming three symbols the checked function must call. Structurally identical to
    # a commented-out statement; prose because of the sentence wrapped around it.
    _PARAGRAPH = (
        "# signOut() must clear every identifier the session established -\n"
        "# ErrorService.setUserId(null), AnalyticsService.setUserId(null),\n"
        "# and AnalyticsConsent.reset() - or a guest keeps the previous id.\n"
        "x = 1\n"
    )

    def test_a_named_call_inside_a_paragraph_is_prose(self, tmp_path):
        repo = _repo(tmp_path, "lib/a.py", self._PARAGRAPH)
        assert find_stale_comments(repo, ["lib"], max_age_days=30) == []

    def test_the_same_line_alone_is_still_flagged(self, tmp_path):
        """Teeth for the test above: the block is what exempts it, not the line's own shape.

        Without this, _PARAGRAPH passing would be consistent with the detector having stopped
        matching that line for some unrelated reason.
        """
        lone = "# ErrorService.setUserId(null), AnalyticsService.setUserId(null),\nx = 1\n"
        repo = _repo(tmp_path, "lib/a.py", lone)
        problems = find_stale_comments(repo, ["lib"], max_age_days=30)
        assert len(problems) == 1
        assert "commented-out code" in problems[0]

    def test_commented_out_code_among_commented_out_code_is_flagged(self, tmp_path):
        body = "def f():\n    # mobility, complexity = hjorth(arr)\n    # spectral_entropy(arr, sf=sf),\n    # num_zerocross(arr),\n    return 1\n"
        repo = _repo(tmp_path, "lib/a.py", body)
        problems = find_stale_comments(repo, ["lib"], max_age_days=30)
        assert len(problems) == 2, problems
        assert all("commented-out code" in p for p in problems)

    def test_a_usage_example_in_documentation_is_not_dead_code(self, tmp_path):
        body = (
            "// Wraps the send API with a hard timeout, so an app supplies only the payload.\n"
            "//\n"
            "// Usage:\n"
            "//   const res = await sendEmail({\n"
            "//     to: user.email,\n"
            "//   });\n"
            "//   if (!res.ok) return json({ error: 'failed' }, 502);\n"
            "export const x = 1;\n"
        )
        repo = _repo(tmp_path, "lib/a.ts", body)
        assert find_stale_comments(repo, ["lib"], max_age_days=30) == []

    def test_a_sentence_ending_in_a_semicolon_is_prose(self, tmp_path):
        body = "# tune_spec(skip_existing=...) is a no-op if already tuned (unless force);\n# the caller decides.\nx = 1\n"
        repo = _repo(tmp_path, "lib/a.py", body)
        assert find_stale_comments(repo, ["lib"], max_age_days=30) == []


class TestAssert:
    def test_assert_passes(self, tmp_path):
        repo = _repo(tmp_path, "lib/a.dart", "void a() {}\n")
        assert_no_stale_todos(repo, ["lib"], max_age_days=30)

    def test_assert_fails(self, tmp_path):
        repo = _repo(tmp_path, "lib/a.dart", "// FIXME: broken\nvoid a() {}\n")
        with pytest.raises(pytest.fail.Exception, match="FIXME"):
            assert_no_stale_todos(repo, ["lib"], max_age_days=30)
