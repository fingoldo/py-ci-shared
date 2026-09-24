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
        found = find_narrowings(precommit, None)
        assert "pre-commit::ruff-real-bugs::--ignore=C901" in found
        assert not any("advisory-only" in key for key in found), "a manual-stage hook is opt-in, not a gate"

    def test_a_flag_inside_a_run_block_scalar_is_captured(self, tmp_path):
        """The narrowing that matters most often sits BELOW the `run:` key, not on it."""
        workflows = tmp_path / ".github" / "workflows"
        _write(workflows / "ci.yml", "jobs:\n  test:\n    steps:\n      - run: |\n          pytest --cov-fail-under=62\n")
        found = find_narrowings(None, workflows)
        assert "ci.yml::test::step1::run::--cov-fail-under=62" in found

    def test_a_commented_out_flag_is_not_a_narrowing(self, tmp_path):
        workflows = tmp_path / ".github" / "workflows"
        _write(workflows / "ci.yml", "jobs:\n  test:\n    steps:\n      # - run: pytest --cov-fail-under=62\n      - run: pytest\n")
        assert find_narrowings(None, workflows) == {}

    def test_pyproject_table_narrowings_are_captured(self, tmp_path):
        """`exclude = ["tests"]` under [tool.ruff] is invisible in both other venues."""
        pyproject = _write(tmp_path / "pyproject.toml", '[tool.ruff]\nexclude = ["tests"]\n')
        found = find_narrowings(None, None, pyproject, ("tool.ruff",))
        assert "pyproject::[tool.ruff]::exclude" in found


class TestFindUndeclaredNarrowings:
    def test_an_undeclared_narrowing_is_reported(self, tmp_path):
        precommit = _write(tmp_path / ".pre-commit-config.yaml", _PRECOMMIT)
        undeclared, stale = find_undeclared_narrowings(precommit, None, declared={})
        assert any("--ignore=C901" in item for item in undeclared)
        assert stale == []

    def test_a_declared_narrowing_passes(self, tmp_path):
        precommit = _write(tmp_path / ".pre-commit-config.yaml", _PRECOMMIT)
        undeclared, _stale = find_undeclared_narrowings(precommit, None, declared={"pre-commit::ruff-real-bugs::--ignore=C901": "complexity is advisory"})
        assert undeclared == []

    def test_a_declaration_for_a_removed_narrowing_is_reported_stale(self, tmp_path):
        """An allowlist nobody prunes is where reviewed decisions go to be forgotten."""
        precommit = _write(tmp_path / ".pre-commit-config.yaml", "repos: []\n")
        _, stale = find_undeclared_narrowings(precommit, None, declared={"pre-commit::gone::--ignore=X": "reason"})
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


def _hooks(tmp_path: Path, hooks: str, top: str = "") -> Path:
    return _write(tmp_path / ".pre-commit-config.yaml", f"{top}repos:\n  - repo: local\n    hooks:\n{hooks}")


class TestAuditRegressions:
    def test_hook_args_are_inspected(self, tmp_path):
        precommit = _hooks(tmp_path, "      - id: flake8\n        entry: flake8\n        args: [--ignore=C901]\n")
        assert "pre-commit::flake8::--ignore=C901" in find_narrowings(precommit, None)
        precommit = _hooks(tmp_path, "      - id: flake8\n        entry: flake8\n        args: [--max-line-length=100]\n")
        assert find_narrowings(precommit, None) == {}

    @pytest.mark.parametrize("flag", ["--ignore-missing-imports", "--skip-without-db", "--selection=all", "--exclude-me"])
    def test_a_longer_flag_sharing_a_prefix_is_not_the_narrowing(self, tmp_path, flag):
        precommit = _hooks(tmp_path, f"      - id: t\n        entry: tool {flag}\n")
        assert find_narrowings(precommit, None) == {}

    def test_the_real_flag_is_still_found_after_the_boundary_fix(self, tmp_path):
        precommit = _hooks(tmp_path, "      - id: t\n        entry: pytest --skip=slow --ignore tests/x\n")
        assert set(find_narrowings(precommit, None)) == {"pre-commit::t::--skip=slow", "pre-commit::t::--ignore=tests/x"}

    def test_tool_short_flags_are_narrowings_for_their_tool_only(self, tmp_path):
        precommit = _hooks(
            tmp_path,
            "      - id: bandit\n        entry: bandit -ll -x tests -r src\n"
            '      - id: tests\n        entry: python -m pytest -m "not slow" -k fast\n'
            "      - id: other\n        entry: python -m tool -m module -x y\n",
        )
        assert set(find_narrowings(precommit, None)) == {
            "pre-commit::bandit::-ll=",
            "pre-commit::bandit::-x=tests",
            "pre-commit::tests::-m=not slow",
            "pre-commit::tests::-k=fast",
        }

    def test_workflow_keys_carry_job_and_step(self, tmp_path):
        workflows = tmp_path / "wf"
        _write(
            workflows / "ci.yml",
            "on:\n  push:\n    paths-ignore: ['docs/**']\njobs:\n"
            "  lint:\n    steps:\n      - name: Lint\n        run: ruff check --ignore=C901\n"
            "  lint-strict:\n    steps:\n      - name: Lint\n        run: ruff check --ignore=C901\n      - name: Lint\n        run: ruff check --ignore=E501\n"
            "  call:\n    uses: org/repo/.github/workflows/x.yml@v1\n    with:\n      ignore: E402\n",
        )
        assert set(find_narrowings(None, workflows)) == {
            "ci.yml::lint::Lint::run::--ignore=C901",
            "ci.yml::lint-strict::Lint::run::--ignore=C901",
            "ci.yml::lint-strict::Lint#2::run::--ignore=E501",
            "ci.yml::call::with::ignore=E402",
        }

    def test_more_config_keys_are_narrowings(self, tmp_path):
        pyproject = _write(
            tmp_path / "pyproject.toml",
            '[tool.bandit]\nskips = ["B101"]\nexclude_dirs = ["tests"]\n\n[tool.mypy]\nignore_errors = true\n\n'
            "[tool.pytest.ini_options]\naddopts = \"-m 'not slow' --deselect tests/test_x.py\"\n\n[tool.black]\nline-length = 100\n",
        )
        found = find_narrowings(None, None, pyproject, ("tool.bandit", "tool.mypy", "tool.pytest.ini_options", "tool.black"))
        assert set(found) == {
            "pyproject::[tool.bandit]::skips",
            "pyproject::[tool.bandit]::exclude_dirs",
            "pyproject::[tool.mypy]::ignore_errors",
            "pyproject::[tool.pytest.ini_options]::addopts::--deselect=tests/test_x.py",
            "pyproject::[tool.pytest.ini_options]::addopts::-m=not slow",
        }

    def test_a_missing_venue_path_raises_instead_of_passing(self, tmp_path):
        precommit = _hooks(tmp_path, "      - id: t\n        entry: tool\n")
        with pytest.raises(FileNotFoundError, match="workflow"):
            find_narrowings(precommit, tmp_path / ".github" / "workflow")
        with pytest.raises(FileNotFoundError, match="pre-commit"):
            find_narrowings(tmp_path / "missing.yaml", None)
        assert find_narrowings(precommit, None) == {}

    @pytest.mark.parametrize("entry", ["python3 -m mypy src", ".venv/bin/python -m mypy src", "mypy src", "uv run mypy src"])
    def test_completion_is_matched_by_program_not_substring(self, tmp_path, entry):
        precommit = _hooks(tmp_path, f"      - id: mypy\n        entry: {entry}\n")
        assert len(find_gates_without_completion_assertion(precommit, {"python -m mypy": "python -m py_ci_shared.mypy_gate"})) == 1

    def test_completion_wrapper_and_unrelated_tools_pass(self, tmp_path):
        precommit = _hooks(
            tmp_path,
            "      - id: gate\n        entry: python3 -m py_ci_shared.mypy_gate src\n      - id: other\n        entry: python -m mypyc_helper src\n",
        )
        assert find_gates_without_completion_assertion(precommit, {"python -m mypy": "python -m py_ci_shared.mypy_gate"}) == []

    def test_completion_reads_hook_args(self, tmp_path):
        precommit = _hooks(tmp_path, "      - id: mypy\n        entry: python\n        args: [-m, mypy, src]\n")
        assert len(find_gates_without_completion_assertion(precommit, {"python -m mypy": "python -m py_ci_shared.mypy_gate"})) == 1

    def test_coverage_parity_skips_comments_and_flags_templated_values(self, tmp_path):
        pyproject = _write(tmp_path / "pyproject.toml", "[tool.coverage.report]\nfail_under = 82\n")
        workflows = tmp_path / "wf"
        _write(workflows / "ci.yml", "# old: --cov-fail-under=50\nrun: pytest --cov-fail-under=82  # was --cov-fail-under=60\n")
        assert find_coverage_gate_mismatches(pyproject, workflows) == []
        _write(workflows / "ci.yml", "run: pytest --cov-fail-under=${{ env.MIN }}\n")
        (violation,) = find_coverage_gate_mismatches(pyproject, workflows)
        assert "not a literal number" in violation

    def test_trigger_paths_ignore_is_not_a_narrowing(self, tmp_path):
        workflows = tmp_path / "wf"
        _write(workflows / "ci.yml", "on:\n  push:\n    paths-ignore: ['docs/**']\njobs:\n  t:\n    steps:\n      - run: pytest\n")
        assert find_narrowings(None, workflows) == {}

    def test_top_level_precommit_scoping_is_a_narrowing(self, tmp_path):
        precommit = _hooks(tmp_path, "      - id: t\n        entry: tool\n", top="exclude: ^tests/\n")
        assert set(find_narrowings(precommit, None)) == {"pre-commit::<top-level>::exclude=^tests/"}
