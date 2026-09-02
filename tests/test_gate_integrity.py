"""Unit tests for the gate-integrity checks. Real scratch files on disk, same
no-mocking convention as this package's other tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from py_ci_shared.gate_integrity import (
    find_coverage_gate_mismatches,
    find_gates_without_completion_assertion,
    find_narrowings,
    find_undeclared_narrowings,
)
from py_ci_shared.mypy_gate import check_mypy_output

pytest.importorskip("yaml")


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


_PRECOMMIT = """
repos:
  - repo: local
    hooks:
      - id: ruff-real-bugs
        entry: python -m ruff check --ignore C901
        stages: [pre-commit]
      - id: mypy-full-blocking
        entry: python -m mypy src/pkg
        stages: [pre-commit]
      - id: advisory-only
        entry: python -m ruff check --select ALL
        stages: [manual]
"""


class TestFindNarrowings:
    def test_blocking_hook_flags_are_captured_and_manual_hooks_are_not(self, tmp_path):
        precommit = _write(tmp_path / ".pre-commit-config.yaml", _PRECOMMIT)
        found = find_narrowings(precommit, tmp_path / "nowhere")
        assert "pre-commit::ruff-real-bugs::--ignore=C901" in found
        assert not any("advisory-only" in key for key in found), "a manual-stage hook is opt-in, not a gate"

    def test_a_flag_inside_a_run_block_scalar_is_captured(self, tmp_path):
        """The narrowing that matters most often sits BELOW the `run:` key, not on it."""
        workflows = tmp_path / ".github" / "workflows"
        _write(workflows / "ci.yml", "jobs:\n  test:\n    steps:\n      - run: |\n          pytest --cov-fail-under=62\n")
        found = find_narrowings(tmp_path / "absent.yaml", workflows)
        assert "ci.yml::run::--cov-fail-under=62" in found

    def test_a_commented_out_flag_is_not_a_narrowing(self, tmp_path):
        workflows = tmp_path / ".github" / "workflows"
        _write(workflows / "ci.yml", "jobs:\n  test:\n    steps:\n      # - run: pytest --cov-fail-under=62\n      - run: pytest\n")
        assert find_narrowings(tmp_path / "absent.yaml", workflows) == {}

    def test_pyproject_table_narrowings_are_captured(self, tmp_path):
        """`exclude = ["tests"]` under [tool.ruff] is invisible in both other venues."""
        pyproject = _write(tmp_path / "pyproject.toml", '[tool.ruff]\nexclude = ["tests"]\n')
        found = find_narrowings(tmp_path / "absent.yaml", tmp_path / "nowhere", pyproject, ("tool.ruff",))
        assert "pyproject::[tool.ruff]::exclude" in found


class TestFindUndeclaredNarrowings:
    def test_an_undeclared_narrowing_is_reported(self, tmp_path):
        precommit = _write(tmp_path / ".pre-commit-config.yaml", _PRECOMMIT)
        undeclared, stale = find_undeclared_narrowings(precommit, tmp_path / "nowhere", declared={})
        assert any("--ignore=C901" in item for item in undeclared)
        assert stale == []

    def test_a_declared_narrowing_passes(self, tmp_path):
        precommit = _write(tmp_path / ".pre-commit-config.yaml", _PRECOMMIT)
        undeclared, _stale = find_undeclared_narrowings(precommit, tmp_path / "nowhere", declared={"pre-commit::ruff-real-bugs::--ignore=C901": "complexity is advisory"})
        assert undeclared == []

    def test_a_declaration_for_a_removed_narrowing_is_reported_stale(self, tmp_path):
        """An allowlist nobody prunes is where reviewed decisions go to be forgotten."""
        precommit = _write(tmp_path / ".pre-commit-config.yaml", "repos: []\n")
        _, stale = find_undeclared_narrowings(precommit, tmp_path / "nowhere", declared={"pre-commit::gone::--ignore=X": "reason"})
        assert stale == ["pre-commit::gone::--ignore=X"]


class TestCompletionAssertion:
    def test_a_directly_invoked_ambiguous_tool_is_reported(self, tmp_path):
        precommit = _write(tmp_path / ".pre-commit-config.yaml", _PRECOMMIT)
        violations = find_gates_without_completion_assertion(precommit, {"python -m mypy": "py_ci_shared.mypy_gate"})
        assert len(violations) == 1 and "mypy-full-blocking" in violations[0]

    def test_routing_through_the_wrapper_passes(self, tmp_path):
        precommit = _write(
            tmp_path / ".pre-commit-config.yaml",
            "repos:\n  - repo: local\n    hooks:\n      - id: mypy-full-blocking\n        entry: python -m py_ci_shared.mypy_gate src/pkg\n        stages: [pre-commit]\n",
        )
        assert find_gates_without_completion_assertion(precommit, {"python -m mypy": "py_ci_shared.mypy_gate"}) == []


class TestCoverageGateParity:
    def test_a_ci_floor_below_the_project_floor_is_reported(self, tmp_path):
        pyproject = _write(tmp_path / "pyproject.toml", "[tool.coverage.report]\nfail_under = 82\n")
        workflows = tmp_path / "wf"
        _write(workflows / "ci.yml", "run: pytest --cov-fail-under=62\n")
        assert len(find_coverage_gate_mismatches(pyproject, workflows)) == 1

    def test_equal_floors_pass(self, tmp_path):
        pyproject = _write(tmp_path / "pyproject.toml", "[tool.coverage.report]\nfail_under = 82\n")
        workflows = tmp_path / "wf"
        _write(workflows / "ci.yml", "run: pytest --cov-fail-under=82\n")
        assert find_coverage_gate_mismatches(pyproject, workflows) == []


class TestMypyCompletionOutput:
    def test_internal_error_is_not_a_pass_even_at_exit_zero(self):
        """The motivating defect: mypy died inside a third-party stub, so which errors it
        reported depended on traversal order -- while the hook only read the exit code."""
        output = "site-packages/transformers/processing.py:77: error: INTERNAL ERROR --\nversion: 1.8.0\n"
        assert "INTERNAL ERROR" in (check_mypy_output(output, returncode=0) or "")

    def test_completion_line_is_required(self):
        assert check_mypy_output("", returncode=0) is not None

    def test_a_silently_narrowed_scope_fails_min_files(self):
        assert check_mypy_output("Success: no issues found in 3 source files\n", 0, min_files=200) is not None

    def test_a_complete_clean_run_passes(self):
        assert check_mypy_output("Success: no issues found in 216 source files\n", 0, min_files=200) is None
