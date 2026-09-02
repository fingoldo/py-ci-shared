"""Unit tests for the git-hook hygiene check. Real scratch hook files, same no-mocking convention
as this package's other tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.hook_hygiene import assert_hooks_are_honest, find_hook_hygiene_problems


def _hooks(tmp_path: Path, body: str, name: str = "pre-push") -> Path:
    d = tmp_path / ".githooks"
    d.mkdir(exist_ok=True)
    (d / name).write_text(body, encoding="utf-8")
    return d


class TestSilentSkips:
    def test_inline_and_guard_is_flagged(self, tmp_path):
        d = _hooks(tmp_path, "#!/bin/sh\n[ -f tool/check-x.sh ] && sh tool/check-x.sh\n")
        problems = find_hook_hygiene_problems(d)
        assert len(problems) == 1
        assert "no else branch" in problems[0]

    def test_if_guard_without_loud_else_is_flagged(self, tmp_path):
        d = _hooks(tmp_path, "#!/bin/sh\nif [ -f tool/check-x.sh ]; then\n  sh tool/check-x.sh\nfi\n")
        problems = find_hook_hygiene_problems(d)
        assert len(problems) == 1
        assert "else that fails" in problems[0]

    def test_if_guard_with_failing_else_passes(self, tmp_path):
        d = _hooks(
            tmp_path,
            "#!/bin/sh\nif [ -f tool/check-x.sh ]; then\n  sh tool/check-x.sh\nelse\n  echo 'ERROR: guard missing'; exit 1\nfi\n",
        )
        assert find_hook_hygiene_problems(d) == []

    def test_if_guard_with_warning_else_passes(self, tmp_path):
        d = _hooks(
            tmp_path,
            "#!/bin/sh\nif [ -f tool/check-x.sh ]; then\n  sh tool/check-x.sh\nelse\n  echo 'WARNING: python missing' >&2\nfi\n",
        )
        assert find_hook_hygiene_problems(d) == []

    def test_guard_that_exists_today_is_a_latent_risk_not_a_live_hole(self, tmp_path):
        # `[ -f x ] && x` on a committed file skips nothing right now; reporting it as a
        # hole buried the ones that ARE skipping under twenty lines of noise.
        d = _hooks(tmp_path, "#!/bin/sh\n[ -f tool/check-x.sh ] && sh tool/check-x.sh\n")
        (tmp_path / "tool").mkdir()
        (tmp_path / "tool" / "check-x.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        assert find_hook_hygiene_problems(d, repo_root=tmp_path) == []

    def test_guard_that_is_missing_is_still_flagged(self, tmp_path):
        d = _hooks(tmp_path, "#!/bin/sh\n[ -f tool/check-gone.sh ] && sh tool/check-gone.sh\n")
        assert len(find_hook_hygiene_problems(d, repo_root=tmp_path)) == 1

    def test_guard_unrelated_conditional_is_not_flagged(self, tmp_path):
        d = _hooks(tmp_path, "#!/bin/sh\n[ -f .env ] && . ./.env\n")
        assert find_hook_hygiene_problems(d) == []


class TestStagingAndVerdicts:
    def test_git_add_u_is_flagged(self, tmp_path):
        d = _hooks(tmp_path, "#!/bin/sh\ndart format .\ngit add -u\n", name="pre-commit")
        problems = find_hook_hygiene_problems(d)
        assert len(problems) == 1
        assert "stages every modified file" in problems[0]

    def test_grep_derived_verdict_is_flagged(self, tmp_path):
        d = _hooks(tmp_path, '#!/bin/sh\nflutter analyze | grep "error -" && exit 1\n')
        problems = find_hook_hygiene_problems(d)
        assert any("human-readable output" in p for p in problems)

    def test_grep_verdict_rule_can_be_disabled(self, tmp_path):
        d = _hooks(tmp_path, '#!/bin/sh\nflutter analyze | grep "error -" && exit 1\n')
        assert find_hook_hygiene_problems(d, check_grep_verdicts=False) == []

    def test_exit_code_verdict_passes(self, tmp_path):
        d = _hooks(tmp_path, "#!/bin/sh\nflutter analyze --no-fatal-infos\n")
        assert find_hook_hygiene_problems(d) == []


class TestHookVsCiParity:
    def _repo(self, tmp_path: Path, hook_body: str, ci_body: str) -> tuple[Path, Path]:
        d = _hooks(tmp_path, hook_body)
        wf = tmp_path / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "ci.yml").write_text(ci_body, encoding="utf-8")
        return d, wf

    def test_hook_only_guard_is_flagged(self, tmp_path):
        d, wf = self._repo(
            tmp_path,
            "#!/bin/sh\nsh tool/check-a.sh\nsh tool/check-b.sh\n",
            "jobs:\n  a:\n    steps:\n      - run: sh tool/check-a.sh\n",
        )
        problems = find_hook_hygiene_problems(d, workflows_dir=wf)
        assert len(problems) == 1
        assert "tool/check-b.sh" in problems[0]

    def test_manual_only_entry_suppresses_it(self, tmp_path):
        d, wf = self._repo(
            tmp_path,
            "#!/bin/sh\nsh tool/check-a.sh\nsh tool/check-b.sh\n",
            "jobs:\n  a:\n    steps:\n      - run: sh tool/check-a.sh\n",
        )
        manual = tmp_path / "manual-only-guards.txt"
        manual.write_text("check-b.sh  # needs a deployed URL\n", encoding="utf-8")
        assert find_hook_hygiene_problems(d, workflows_dir=wf, manual_only_file=manual) == []

    def test_parity_holds_when_ci_runs_everything(self, tmp_path):
        d, wf = self._repo(tmp_path, "#!/bin/sh\nsh tool/check-a.sh\n", "jobs:\n  a:\n    steps:\n      - run: sh tool/check-a.sh\n")
        assert find_hook_hygiene_problems(d, workflows_dir=wf) == []

    def test_a_verified_runner_satisfies_parity(self, tmp_path):
        # CI naming one runner instead of 60 guards is the better shape; the rule has to be able
        # to see it, or it pushes projects back towards a hand-maintained list.
        d, wf = self._repo(
            tmp_path,
            "#!/bin/sh\nsh tool/check-a.sh\nsh tool/check-b.sh\n",
            "jobs:\n  a:\n    steps:\n      - run: bash tool/run-guards.sh\n",
        )
        (tmp_path / "tool").mkdir(exist_ok=True)
        (tmp_path / "tool" / "run-guards.sh").write_text('#!/bin/sh\nfor g in tool/check-*.sh; do sh "$g"; done\n', encoding="utf-8")
        problems = find_hook_hygiene_problems(d, repo_root=tmp_path, workflows_dir=wf, runner_scripts=["tool/run-guards.sh"])
        assert problems == []

    def test_a_runner_that_lists_nothing_does_not_satisfy_parity(self, tmp_path):
        d, wf = self._repo(
            tmp_path,
            "#!/bin/sh\nsh tool/check-a.sh\nsh tool/check-b.sh\n",
            "jobs:\n  a:\n    steps:\n      - run: bash tool/run-guards.sh\n",
        )
        (tmp_path / "tool").mkdir(exist_ok=True)
        (tmp_path / "tool" / "run-guards.sh").write_text("#!/bin/sh\necho nothing to do\n", encoding="utf-8")
        problems = find_hook_hygiene_problems(d, repo_root=tmp_path, workflows_dir=wf, runner_scripts=["tool/run-guards.sh"])
        assert any("does not discover guards by glob" in x for x in problems)

    def test_a_runner_that_does_not_exist_is_reported(self, tmp_path):
        d, wf = self._repo(
            tmp_path,
            "#!/bin/sh\nsh tool/check-a.sh\n",
            "jobs:\n  a:\n    steps:\n      - run: bash tool/run-guards.sh\n",
        )
        problems = find_hook_hygiene_problems(d, repo_root=tmp_path, workflows_dir=wf, runner_scripts=["tool/run-guards.sh"])
        assert any("which does not exist" in x for x in problems)


class TestAssert:
    def test_empty_hooks_dir_reports_examining_nothing(self, tmp_path):
        d = tmp_path / ".githooks"
        d.mkdir()
        problems = find_hook_hygiene_problems(d)
        assert len(problems) == 1
        assert "examined nothing" in problems[0]

    def test_assert_passes(self, tmp_path):
        d = _hooks(tmp_path, "#!/bin/sh\nflutter test\n")
        assert_hooks_are_honest(d)

    def test_assert_fails(self, tmp_path):
        d = _hooks(tmp_path, "#!/bin/sh\n[ -f tool/check-x.sh ] && sh tool/check-x.sh\n")
        with pytest.raises(pytest.fail.Exception, match="else branch"):
            assert_hooks_are_honest(d)
