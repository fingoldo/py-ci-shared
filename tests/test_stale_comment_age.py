"""Unit tests for the stale-comment age check. Real scratch git repos with backdated commits, same
no-mocking convention as this package's other tests.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from py_ci_shared import stale_comment_age as sca
from py_ci_shared.stale_comment_age import assert_no_stale_todos, find_stale_comments

_ENV_OLD = {
    "GIT_AUTHOR_DATE": "2020-01-01T00:00:00",
    "GIT_COMMITTER_DATE": "2020-01-01T00:00:00",
}


def _repo(tmp_path: Path, rel: str, body: str, *, old: bool = True, init_args: tuple = ()) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", *init_args], cwd=tmp_path, check=True)
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

    def test_a_missing_scan_directory_is_reported_not_skipped(self, tmp_path):
        repo = _repo(tmp_path, "lib/a.dart", "void a() {}\n")
        problems = find_stale_comments(repo, ["nope"], max_age_days=1)
        assert len(problems) == 1 and "nope: scan directory does not exist" in problems[0]
        assert find_stale_comments(repo, ["lib"], max_age_days=1) == []


class TestTrailingAndReferences:
    def test_a_trailing_todo_is_checked(self, tmp_path):
        repo = _repo(tmp_path, "lib/a.py", "x = 1  # TODO fix\ny = '# TODO not a comment'\n")
        problems = find_stale_comments(repo, ["lib"], max_age_days=30)
        assert len(problems) == 1 and problems[0].startswith("lib/a.py:1: TODO"), problems

    def test_a_trailing_todo_in_a_slash_language(self, tmp_path):
        repo = _repo(tmp_path, "lib/a.ts", 'const u = "http://x"; // TODO drop this\nconst v = "// TODO in a string";\n')
        problems = find_stale_comments(repo, ["lib"], max_age_days=30)
        assert len(problems) == 1 and problems[0].startswith("lib/a.ts:1: TODO"), problems

    def test_a_call_elsewhere_in_the_comment_is_not_an_issue_reference(self, tmp_path):
        repo = _repo(tmp_path, "lib/a.py", "# TODO: call foo(x) later\n# TODO: handle UTF-8 input\nx = 1\n")
        assert len(find_stale_comments(repo, ["lib"], max_age_days=30)) == 2

    def test_a_reference_right_after_the_marker_exempts(self, tmp_path):
        body = "# TODO: #12 wire this\n# FIXME ABC-12: later\n# TODO - https://example.com/i/1\n# TODO(topic): later\nx = 1\n"
        repo = _repo(tmp_path, "lib/a.py", body)
        assert find_stale_comments(repo, ["lib"], max_age_days=30) == []
        assert len(find_stale_comments(repo, ["lib"], max_age_days=30, require_issue_ref=False)) == 4

    def test_a_python_dead_call_needs_no_terminator(self, tmp_path):
        repo = _repo(tmp_path, "lib/a.py", "def f():\n    # foo(bar)\n    return 1\n")
        problems = find_stale_comments(repo, ["lib"], max_age_days=30)
        assert len(problems) == 1 and "commented-out code" in problems[0]

    def test_a_dart_call_without_terminator_is_still_not_code(self, tmp_path):
        repo = _repo(tmp_path, "lib/a.dart", "// foo(bar)\nvoid a() {}\n")
        assert find_stale_comments(repo, ["lib"], max_age_days=30) == []


class TestHistoryProblems:
    def test_a_shallow_clone_fails_instead_of_passing(self, tmp_path):
        src = _repo(tmp_path / "src", "lib/a.py", "# TODO: old\nx = 1\n")
        (src / "lib" / "b.py").write_text("y = 2\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=src, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "new"], cwd=src, check=True)
        clone = tmp_path / "clone"
        subprocess.run(["git", "clone", "-q", "--depth", "1", src.resolve().as_uri(), str(clone)], check=True)
        problems = find_stale_comments(clone, ["lib"], max_age_days=30)
        assert len(problems) == 1 and "shallow clone" in problems[0]
        assert len(find_stale_comments(src, ["lib"], max_age_days=30)) == 1

    def test_a_root_outside_git_fails(self, tmp_path):
        (tmp_path / "lib").mkdir()
        (tmp_path / "lib" / "a.py").write_text("# TODO: x\n", encoding="utf-8")
        problems = find_stale_comments(tmp_path, ["lib"], max_age_days=30)
        assert len(problems) == 1 and "not a git work tree" in problems[0]

    def test_a_blame_failure_is_reported(self, tmp_path):
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        (tmp_path / "lib").mkdir()
        (tmp_path / "lib" / "a.py").write_text("# TODO: x\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)  # staged, but no commit: blame has no HEAD
        problems = find_stale_comments(tmp_path, ["lib"], max_age_days=30)
        assert len(problems) == 1 and "git blame failed" in problems[0] and "could not be dated" in problems[0]

    def test_an_untracked_file_is_not_blamed(self, tmp_path):
        repo = _repo(tmp_path, "lib/a.py", "x = 1\n")
        (repo / "lib" / "new.py").write_text("# TODO: brand new\n", encoding="utf-8")
        assert find_stale_comments(repo, ["lib"], max_age_days=30) == []


class TestBlameMechanics:
    def test_many_candidates_are_batched_and_all_dated(self, tmp_path, monkeypatch):
        body = "".join(f"# TODO: item {i}\nx{i} = {i}\n" for i in range(30))
        repo = _repo(tmp_path, "lib/a.py", body)
        monkeypatch.setattr(sca, "_BLAME_BATCH", 4)
        calls = []
        real = sca._git

        def _spy(root, *args):
            calls.append(args)
            return real(root, *args)

        monkeypatch.setattr(sca, "_git", _spy)
        problems = find_stale_comments(repo, ["lib"], max_age_days=30)
        assert len(problems) == 30
        blames = [a for a in calls if a[0] == "blame"]
        assert len(blames) == 8 and all(a.count("-L") <= 4 for a in blames)

    def test_adjacent_lines_share_one_range(self):
        assert sca._ranges([5, 3, 4, 9, 10, 12]) == [(3, 5), (9, 10), (12, 12)]

    def test_non_ascii_author_and_text_are_decoded(self, tmp_path):
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.name", "Анатолий Ёжиков"], cwd=tmp_path, check=True)
        (tmp_path / "lib").mkdir()
        (tmp_path / "lib" / "a.py").write_text("# TODO: проверить ✓\nx = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=tmp_path, check=True, env={**subprocess.os.environ, **_ENV_OLD})
        problems = find_stale_comments(tmp_path, ["lib"], max_age_days=30)
        assert len(problems) == 1 and "проверить" in problems[0]

    def test_a_sha256_repository_is_dated(self, tmp_path):
        probe = subprocess.run(["git", "init", "-q", "--object-format=sha256", str(tmp_path / "probe")], capture_output=True)
        if probe.returncode != 0:
            pytest.skip("this git cannot create a SHA-256 repository")
        repo = _repo(tmp_path / "r", "lib/a.py", "# TODO: old\nx = 1\n", init_args=("--object-format=sha256",))
        problems = find_stale_comments(repo, ["lib"], max_age_days=30)
        assert len(problems) == 1 and "TODO" in problems[0]

    def test_the_blame_header_accepts_sha1_and_sha256(self):
        assert sca._BLAME_HEADER_RE.match("a" * 40 + " 1 3 1").group(1) == "3"
        assert sca._BLAME_HEADER_RE.match("b" * 64 + " 1 7").group(1) == "7"


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


_GLOSSED_PROSE = [
    "# Upsert (handles race conditions at DB level too)",
    "# Numbers (dates, versions)",
    "# Cost (USD)",
    "# Timing (seconds)",
    "# Vowels (Italian has 7 vowels, very regular)",
    "# mover (stem-changing o→ue)",
    "# თქმა (to say)",
    "# tok4 (ლამაზი): NOUN→ADJ, nsubj→conj of tok2",
    "# DEFINITIONS (learner-friendly, multi-language)",
    "# rejected_another_synset(3) / rejected_invented(4) / rejected_wrong_meaning(5) / rejected(7)",
]
_REAL_DEAD_CODE = [
    '# ensure_installed("numpy")',
    "# print(i, col)",
    "# wcols.append(weighted_std)",
    '#    update_values.append(field + "=" + x)',
    "# .group_by(...).agg(...)",
]


class TestProseVersusCode:
    """A parenthesised gloss after a heading word is English; a call is code. Judged per line, no git needed."""

    @staticmethod
    def _kinds(tmp_path: Path, comment: str, suffix: str = ".py") -> list[str]:
        p = tmp_path / f"m{suffix}"
        p.write_text("x = 1" + chr(10) + comment + chr(10) + "y = 2" + chr(10), encoding="utf-8")
        return [kind for kind, _ in sca._candidates(p, require_issue_ref=True).values()]

    @pytest.mark.parametrize("comment", _GLOSSED_PROSE)
    def test_a_glossed_heading_is_not_code(self, tmp_path, comment):
        assert self._kinds(tmp_path, comment) == []

    @pytest.mark.parametrize("comment", _REAL_DEAD_CODE)
    def test_real_dead_code_is_still_flagged(self, tmp_path, comment):
        assert self._kinds(tmp_path, comment) == ["commented-out code"]

    def test_a_glossed_heading_in_a_slash_language_is_not_code(self, tmp_path):
        assert self._kinds(tmp_path, "// Timing (seconds);", ".dart") == []

    def test_a_dart_dead_call_is_still_flagged(self, tmp_path):
        assert self._kinds(tmp_path, "// buildCta(context, onLaunchSurvey);", ".dart") == ["commented-out code"]


@pytest.mark.parametrize("body", ["identity (NaN-aware)", "F_q(s) per (q, s)", "Vowels (Spanish has 5 pure vowels)"])
def test_prose_with_parentheses_is_not_commented_out_code(body):
    line = f"# {body}"
    matched = sca._COMMENTED_HASH_CALL_RE.match(line) is not None
    assert not (matched and sca._body_is_code(body, python=True))


@pytest.mark.parametrize("body", ["print(i, col)", 'ensure_installed("numpy")', "fig.suptitle(title)"])
def test_commented_out_calls_are_still_code(body):
    assert sca._COMMENTED_HASH_CALL_RE.match(f"# {body}") and sca._body_is_code(body, python=True)


# A commit authored at exactly this UTC instant; `now` is then frozen relative to it.
_T0 = 1577836800  # 2020-01-01T00:00:00Z
_DAY = 86400


def _repo_at_t0(tmp_path: Path, rel: str, body: str) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    for args in (["init", "-q"], ["config", "user.email", "t@example.com"], ["config", "user.name", "T"]):
        subprocess.run(["git", *args], cwd=tmp_path, check=True)
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    stamp = f"{_T0} +0000"
    env = {**subprocess.os.environ, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=tmp_path, check=True, env=env)
    return tmp_path


class TestEarlyWarning:
    """``warn_days``: comments about to cross the limit are named, with their due date, and never fail."""

    BODY = "// TODO: call this after the first lesson\nvoid a() {}\n"

    def test_comment_inside_the_window_is_named_with_its_due_date(self, tmp_path):
        repo = _repo_at_t0(tmp_path, "lib/a.dart", self.BODY)
        out = sca.find_comments_going_stale(repo, ["lib"], max_age_days=30, warn_days=7, now=_T0 + 25 * _DAY)
        expected = (
            "lib/a.dart:1: TODO goes stale on 2020-01-31 (25 of 30 days) - `// TODO: call this after the first lesson`. "
            "Do it, delete it, or reference the issue that tracks it."
        )
        assert out == [expected]
        assert find_stale_comments(repo, ["lib"], max_age_days=30, now=_T0 + 25 * _DAY) == []

    @pytest.mark.parametrize("days, expected", [(22, 0), (23.5, 1), (30, 1), (30.5, 0)])
    def test_window_boundaries(self, tmp_path, days, expected):
        repo = _repo_at_t0(tmp_path, "lib/a.dart", self.BODY)
        out = sca.find_comments_going_stale(repo, ["lib"], max_age_days=30, warn_days=7, now=_T0 + days * _DAY)
        assert len(out) == expected
        # past the limit it is the gate's finding instead
        assert len(find_stale_comments(repo, ["lib"], max_age_days=30, now=_T0 + days * _DAY)) == (1 if days > 30 else 0)

    def test_assert_warns_but_does_not_fail(self, tmp_path):
        repo = _repo_at_t0(tmp_path, "lib/a.dart", self.BODY)
        with pytest.warns(UserWarning, match=r"lib/a\.dart:1: TODO goes stale on 2020-01-31"):
            out = assert_no_stale_todos(repo, ["lib"], max_age_days=30, warn_days=7, now=_T0 + 25 * _DAY)
        assert len(out) == 1

    def test_an_advisory_does_not_fail_under_warnings_as_errors(self, tmp_path):
        """N-21: under ``-W error`` the early warning raised, failing the test days before the comment was stale."""
        import warnings

        repo = _repo_at_t0(tmp_path, "lib/a.dart", self.BODY)
        with warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter("error")
            out = assert_no_stale_todos(repo, ["lib"], max_age_days=30, warn_days=7, now=_T0 + 25 * _DAY)
        assert len(out) == 1
        assert [w.category for w in seen] == [sca.StaleCommentAdvisory]

    def test_the_early_warning_is_on_by_default_and_warn_days_zero_is_silent(self, tmp_path, recwarn):
        """G-12 (audit 2026-10-03): seven days of warning by default, so a calendar day is no surprise."""
        repo = _repo_at_t0(tmp_path, "lib/a.dart", self.BODY)
        assert len(assert_no_stale_todos(repo, ["lib"], max_age_days=30, now=_T0 + 25 * _DAY)) == 1
        assert [w for w in recwarn if "early warning" in str(w.message)]
        recwarn.clear()
        assert assert_no_stale_todos(repo, ["lib"], max_age_days=30, warn_days=0, now=_T0 + 25 * _DAY) == []
        assert not [w for w in recwarn if "early warning" in str(w.message)]

    def test_stale_still_fails_with_warn_days(self, tmp_path):
        repo = _repo_at_t0(tmp_path, "lib/a.dart", self.BODY)
        with pytest.raises(pytest.fail.Exception, match="older than 30 days"):
            assert_no_stale_todos(repo, ["lib"], max_age_days=30, warn_days=7, now=_T0 + 31 * _DAY)


class TestShapeLabelsInAnalysers:
    """An AST analyser labels each branch with the call shape it matches; that label is not dead code (G-12 sample:
    every commented-out-code hit in pyutilz and py-ci-shared was one, pyutilz 635d0c5 reworded one to get past)."""

    @staticmethod
    def _kinds(tmp_path: Path, source: str) -> list[str]:
        p = tmp_path / "m.py"
        p.write_text(source, encoding="utf-8")
        return [kind for kind, _ in sca._candidates(p, require_issue_ref=True).values()]

    _ANALYSER = (
        "import ast\n\n_INC_HELPERS = {'_inc_stat'}\n\n\ndef keys(node):\n"
        '    # self.stats.setdefault("k", 0)\n'
        "    if node.func.attr == 'setdefault':\n        return 1\n"
        '    # self._inc_stat("k")\n'
        "    elif node.func.attr in _INC_HELPERS:\n        return 2\n"
        "    if node.func.attr == 'open':\n"
        "        # Path.open(mode, buffering, encoding, errors, newline)\n"
        "        return 3\n"
    )

    def test_shape_labels_in_an_ast_analyser_are_not_code(self, tmp_path):
        assert self._kinds(tmp_path, self._ANALYSER) == []

    def test_a_disabled_call_in_an_analyser_is_still_code(self, tmp_path):
        source = self._ANALYSER.replace("        return 3\n", "        return 3\n    # print(node.func.attr)\n")
        assert self._kinds(tmp_path, source) == ["commented-out code"]

    def test_the_same_label_outside_an_ast_analyser_is_still_code(self, tmp_path):
        source = "MODE = 'setdefault'\n\n\ndef f(stats):\n    # stats.setdefault(\"k\", 0)\n    return MODE\n"
        assert self._kinds(tmp_path, source) == ["commented-out code"]


class TestStaleWarningSummary:
    def test_counts_per_repo_and_a_total_line(self, tmp_path):
        a = _repo_at_t0(tmp_path / "alpha", "lib/a.dart", "// TODO: soon\nvoid a() {}\n")
        b = _repo_at_t0(tmp_path / "beta", "lib/b.dart", "void b() {}\n")
        lines, unchecked = sca.stale_warning_summary([a, b], scan_dirs=["lib"], max_age_days=30, warn_days=7, now=_T0 + 25 * _DAY)
        assert unchecked == 0
        assert lines == [
            "alpha: 1 going stale within 7 days, 0 already older than 30 days",
            "beta: 0 going stale within 7 days, 0 already older than 30 days",
            "stale-comment early warnings: 1",
        ]

    def test_the_repo_gate_table_sets_scope_and_limits(self, tmp_path):
        a = _repo_at_t0(tmp_path / "alpha", "lib/a.dart", "// TODO: soon\nvoid a() {}\n")
        (a / "pyproject.toml").write_text("[tool.py_ci_shared.gates.stale_comment_age]\nscan_dirs = ['lib']\nmax_age_days = 40\n", encoding="utf-8")
        lines, _ = sca.stale_warning_summary([a], now=_T0 + 25 * _DAY)
        assert lines[0] == "alpha: 0 going stale within 7 days, 0 already older than 40 days"
        lines, _ = sca.stale_warning_summary([a], now=_T0 + 35 * _DAY)
        assert lines[-1] == "stale-comment early warnings: 1"

    def test_a_repo_that_cannot_be_dated_is_not_checked_and_fails_the_cli(self, tmp_path, capsys):
        plain = tmp_path / "plain"
        (plain / "lib").mkdir(parents=True)
        lines, unchecked = sca.stale_warning_summary([plain], scan_dirs=["lib"])
        assert unchecked == 1 and lines[0].startswith("plain: not checked - ") and "not a git work tree" in lines[0]
        assert lines[-1] == "stale-comment early warnings: 0 (1 repo(s) not checked)"
        assert sca.main(["--stale-warning-summary", str(plain), "--scan-dir", "lib"]) == 1
        assert "stale-comment early warnings: 0 (1 repo(s) not checked)" in capsys.readouterr().out
