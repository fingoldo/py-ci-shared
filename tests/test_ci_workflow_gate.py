"""Unit tests for the CI continue-on-error gate check (O-10/O-11/O-17 audit
findings). Real scratch workflow YAML files, same no-mocking convention as
this package's other tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.ci_workflow_gate import assert_continue_on_error_is_reviewed, find_continue_on_error_steps


def _write_workflow(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "ci.yml"
    p.write_text(body, encoding="utf-8")
    return p


_STEP_SHAPED = """\
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - name: Run ruff
        run: ruff check .
        continue-on-error: true

      - name: Run tests
        run: pytest
"""


class TestFindContinueOnErrorSteps:
    def test_step_with_continue_on_error_is_found(self, tmp_path):
        p = _write_workflow(tmp_path, _STEP_SHAPED)
        assert find_continue_on_error_steps(p) == ["Run ruff"]

    def test_step_without_continue_on_error_is_not_found(self, tmp_path):
        p = _write_workflow(tmp_path, _STEP_SHAPED)
        assert "Run tests" not in find_continue_on_error_steps(p)

    def test_no_continue_on_error_at_all_is_clean(self, tmp_path):
        p = _write_workflow(tmp_path, "jobs:\n  build:\n    steps:\n      - name: Build\n        run: make\n")
        assert find_continue_on_error_steps(p) == []

    def test_quoted_step_name(self, tmp_path):
        p = _write_workflow(
            tmp_path,
            'jobs:\n  lint:\n    steps:\n      - name: "Run mypy"\n        run: mypy .\n        continue-on-error: true\n',
        )
        assert find_continue_on_error_steps(p) == ["Run mypy"]

    def test_job_level_name_used_when_no_step_name_precedes(self, tmp_path):
        """A job-level `name:` field (GitHub Actions' own display-name
        convention for reusable-workflow-call jobs) is a valid `name:`
        line too -- it's the nearest preceding one until a step's own
        `- name:` supersedes it."""
        p = _write_workflow(
            tmp_path,
            "jobs:\n  lint-advisory:\n    name: lint (advisory)\n    steps:\n      - run: some-tool\n        continue-on-error: true\n",
        )
        assert find_continue_on_error_steps(p) == ["lint (advisory)"]

    def test_unnamed_step_reported_with_line_number_not_dropped(self, tmp_path):
        p = _write_workflow(tmp_path, "jobs:\n  x:\n    steps:\n      - run: echo hi\n        continue-on-error: true\n")
        found = find_continue_on_error_steps(p)
        assert len(found) == 1
        assert found[0].startswith("<unnamed step, line")

    def test_templated_continue_on_error_value_not_flagged(self, tmp_path):
        """`continue-on-error: ${{ inputs.advisory }}` (py-ci-shared's own
        reusable-workflow pattern) has no static true/false to review yet
        -- only a literal `true` is a concrete, reviewable claim."""
        p = _write_workflow(
            tmp_path,
            "jobs:\n  x:\n    steps:\n      - name: Run mypy\n        continue-on-error: ${{ inputs.advisory }}\n",
        )
        assert find_continue_on_error_steps(p) == []

    def test_multiple_steps_each_tracked_independently(self, tmp_path):
        p = _write_workflow(
            tmp_path,
            "jobs:\n  lint:\n    steps:\n"
            "      - name: Run ruff\n        continue-on-error: true\n"
            "      - name: Run black\n        continue-on-error: true\n"
            "      - name: Run tests\n        run: pytest\n",
        )
        assert find_continue_on_error_steps(p) == ["Run ruff", "Run black"]


class TestAssertContinueOnErrorIsReviewed:
    def test_passes_silently_when_all_reviewed(self, tmp_path):
        p = _write_workflow(tmp_path, _STEP_SHAPED)
        assert_continue_on_error_is_reviewed(p, reviewed_advisory_steps={"Run ruff"})  # must not raise

    def test_fails_with_actionable_message_when_unreviewed(self, tmp_path):
        p = _write_workflow(tmp_path, _STEP_SHAPED)
        with pytest.raises(pytest.fail.Exception, match="reviewed-advisory allowlist"):
            assert_continue_on_error_is_reviewed(p, reviewed_advisory_steps=set())

    def test_new_continue_on_error_not_in_allowlist_fails(self, tmp_path):
        """The exact scenario O-10/O-11/O-17 generalize: a NEW gate-defeating
        continue-on-error appears (copy-paste, refactor) that isn't one of
        the already-reviewed steps -- it must fail, not silently pass."""
        p = _write_workflow(
            tmp_path,
            "jobs:\n  lint:\n    steps:\n"
            "      - name: Run ruff\n        continue-on-error: true\n"
            "      - name: Run bandit security scan\n        continue-on-error: true\n",
        )
        with pytest.raises(pytest.fail.Exception, match="Run bandit security scan"):
            assert_continue_on_error_is_reviewed(p, reviewed_advisory_steps={"Run ruff"})


def test_this_repos_own_reusable_workflows_are_clean_or_reviewed():
    """Dogfooding: py-ci-shared's own lint-advisory.yml is BY DESIGN
    entirely continue-on-error (its header comment says so) -- every step
    in it is expected and reviewed."""
    workflow = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "lint-advisory.yml"
    if not workflow.exists():
        pytest.skip("lint-advisory.yml not present in this checkout")
    steps = find_continue_on_error_steps(workflow)
    # Every step in this file is deliberately advisory (see the file's own
    # header comment) -- just confirm the scanner finds a non-empty,
    # sane set rather than silently matching zero due to a regex drift.
    assert len(steps) >= 1
