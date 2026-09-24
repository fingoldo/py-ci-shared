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

    def test_continue_on_error_with_trailing_comment_is_found(self, tmp_path):
        """A trailing `# explanation` on the continue-on-error line itself
        (this repo's own workflows are full of dense inline comments) must
        not blind the end-anchored regex -- that would let a real gate-defeat
        silently vanish from the scan."""
        p = _write_workflow(
            tmp_path,
            "jobs:\n  lint:\n    steps:\n" "      - name: Run new-tool\n" "        continue-on-error: true  # TODO remove once fixed\n",
        )
        assert find_continue_on_error_steps(p) == ["Run new-tool"]

    def test_name_with_trailing_comment_is_not_polluted(self, tmp_path):
        """A trailing comment on the `name:` line itself must not leak into
        the captured step name, or a cosmetic comment edit on an
        already-reviewed step desyncs it from its allowlist entry."""
        p = _write_workflow(
            tmp_path,
            "jobs:\n  lint:\n    steps:\n" "      - name: Run new-tool  # noqa\n" "        continue-on-error: true\n",
        )
        assert find_continue_on_error_steps(p) == ["Run new-tool"]

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


class TestStructure:
    def test_a_job_without_a_name_does_not_inherit_the_previous_step_name(self, tmp_path):
        p = _write_workflow(
            tmp_path,
            "jobs:\n  a:\n    steps:\n      - name: Run ruff\n        run: ruff .\n        continue-on-error: true\n"
            "  b:\n    runs-on: x\n    continue-on-error: true\n    steps:\n      - run: pytest\n"
            "  c:\n    steps:\n      - name: Run ruff\n        run: ruff .\n      - run: deploy\n        continue-on-error: true\n",
        )
        found = find_continue_on_error_steps(p)
        assert found[0] == "Run ruff" and found[1] == "b" and found[2] == "<unnamed step, line 17>"
        with pytest.raises(pytest.fail.Exception, match="unnamed step"):
            assert_continue_on_error_is_reviewed(p, reviewed_advisory_steps={"Run ruff", "b"})

    def test_an_action_input_called_name_is_not_the_step_name(self, tmp_path):
        p = _write_workflow(
            tmp_path,
            "jobs:\n  t:\n    steps:\n      - name: Upload coverage\n        uses: actions/upload-artifact@v4\n"
            "        with:\n          name: cov\n          path: x\n        continue-on-error: true\n",
        )
        assert find_continue_on_error_steps(p) == ["Upload coverage"]

    def test_a_name_after_the_flag_still_names_the_step(self, tmp_path):
        p = _write_workflow(tmp_path, "jobs:\n  t:\n    steps:\n      - continue-on-error: true\n        name: Late name\n        run: x\n")
        assert find_continue_on_error_steps(p) == ["Late name"]

    @pytest.mark.parametrize("flag", ["- continue-on-error: true", "- continue-on-error: 'true'", '- continue-on-error: "TRUE"'])
    def test_the_flag_on_the_dash_line_and_quoted_is_found(self, tmp_path, flag):
        p = _write_workflow(tmp_path, f"jobs:\n  t:\n    name: tee\n    steps:\n      {flag}\n        name: S\n        run: x\n")
        assert find_continue_on_error_steps(p) == ["S"]

    def test_quoted_false_and_a_templated_value_are_not_found(self, tmp_path):
        p = _write_workflow(
            tmp_path, "jobs:\n  t:\n    steps:\n      - continue-on-error: 'false'\n        name: S\n      - name: T\n        continue-on-error: ${{ x }}\n"
        )
        assert find_continue_on_error_steps(p) == []

    def test_a_composite_action_without_jobs_is_read(self, tmp_path):
        p = _write_workflow(tmp_path, "runs:\n  using: composite\n  steps:\n    - name: A\n      run: x\n      continue-on-error: true\n")
        assert find_continue_on_error_steps(p) == ["A"]

    def test_a_bom_workflow_is_read(self, tmp_path):
        p = tmp_path / "ci.yml"
        p.write_bytes(b"\xef\xbb\xbf" + _STEP_SHAPED.encode())
        assert find_continue_on_error_steps(p) == ["Run ruff"]
