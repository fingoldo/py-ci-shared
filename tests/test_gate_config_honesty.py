"""Unit tests for gate_config_honesty: a gate runs its tool with the project's config, and can fail."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from py_ci_shared.gate_config_honesty import assert_gates_honest, find_gates_that_cannot_fail, find_tools_run_without_their_config, gate_commands

_PYPROJECT = "[tool.bandit]\nskips = ['B608']\n"


def _precommit(tmp_path: Path, hooks: str) -> Path:
    p = tmp_path / ".pre-commit-config.yaml"
    p.write_text("repos:\n  - repo: local\n    hooks:\n" + hooks, encoding="utf-8")
    return p


def _pyproject(tmp_path: Path, body: str = _PYPROJECT) -> Path:
    p = tmp_path / "pyproject.toml"
    p.write_text(body, encoding="utf-8")
    return p


class TestToolWithoutItsConfig:
    def test_bandit_without_c_is_reported_when_the_project_configures_it(self, tmp_path):
        pc = _precommit(tmp_path, "      - id: bandit\n        entry: python -m bandit -r . -ll\n        files: '^proj/'\n")
        (p,) = find_tools_run_without_their_config(gate_commands(pc, []), _pyproject(tmp_path))
        assert "runs bandit without -c/--configfile" in p

    def test_bandit_with_c_is_fine(self, tmp_path):
        pc = _precommit(tmp_path, "      - id: bandit\n        entry: python -m bandit -r . -ll -c pyproject.toml\n")
        assert find_tools_run_without_their_config(gate_commands(pc, []), _pyproject(tmp_path)) == []

    def test_no_table_no_rule(self, tmp_path):
        pc = _precommit(tmp_path, "      - id: bandit\n        entry: python -m bandit -r .\n")
        assert find_tools_run_without_their_config(gate_commands(pc, []), _pyproject(tmp_path, "[tool.ruff]\n")) == []

    def test_a_workflow_step_is_read_too(self, tmp_path):
        wf = tmp_path / "ci.yml"
        wf.write_text("jobs:\n  lint:\n    steps:\n      - name: Run bandit\n        run: uvx bandit -r . -ll\n", encoding="utf-8")
        (p,) = find_tools_run_without_their_config(gate_commands(None, [wf]), _pyproject(tmp_path))
        assert p.startswith("ci.yml::lint::Run bandit")


class TestGatesThatCannotFail:
    def test_a_warn_wrapper_behind_a_gate_name_is_reported(self, tmp_path):
        pc = _precommit(tmp_path, "      - id: bandit-gate\n        entry: python -m py_ci_shared.bandit_warn --src-path .\n")
        (p,) = find_gates_that_cannot_fail(gate_commands(pc, []))
        assert "always exits 0" in p

    def test_the_same_wrapper_named_as_advisory_is_fine(self, tmp_path):
        pc = _precommit(tmp_path, "      - id: bandit-warn\n        entry: python -m py_ci_shared.bandit_warn --src-path .\n")
        assert find_gates_that_cannot_fail(gate_commands(pc, [])) == []

    def test_or_true_in_a_step_is_reported_unless_the_step_is_advisory(self, tmp_path):
        wf = tmp_path / "ci.yml"
        wf.write_text(
            "jobs:\n  lint:\n    steps:\n      - name: Lint\n        run: ruff check . || true\n"
            "      - name: Report\n        run: ruff check . || true\n        continue-on-error: true\n",
            encoding="utf-8",
        )
        (p,) = find_gates_that_cannot_fail(gate_commands(None, [wf]))
        assert p.startswith("ci.yml::lint::Lint")

    def test_manual_only_hooks_are_not_gates(self, tmp_path):
        pc = _precommit(tmp_path, "      - id: fixer\n        entry: ruff check --fix . || true\n        stages: [manual]\n")
        assert gate_commands(pc, []) == {}


def test_installing_or_naming_the_tool_is_not_running_it(tmp_path):
    """realtime_applications' `pip install ... bandit` step was reported as running bandit without -c."""
    wf = tmp_path / "ci.yml"
    wf.write_text(
        "jobs:\n  lint:\n    steps:\n      - name: Install\n        run: pip install ruff bandit mypy\n"
        "      - name: Upload\n        run: echo bandit-report.json\n"
        "      - name: Scan\n        run: |\n          cd proj\n          uvx --from bandit==1.8 bandit -r . -ll\n",
        encoding="utf-8",
    )
    (p,) = find_tools_run_without_their_config(gate_commands(None, [wf]), _pyproject(tmp_path))
    assert p.startswith("ci.yml::lint::Scan")


def test_or_true_on_a_plumbing_line_is_not_a_defeated_gate(tmp_path):
    """A `git fetch ... || true` in a workflow is plumbing; only a line that runs a checking tool counts."""
    wf = tmp_path / "ci.yml"
    wf.write_text(
        "jobs:\n  nightly:\n    steps:\n      - name: Resolve the window\n        run: |\n          git fetch --depth 50 origin || true\n          echo done\n",
        encoding="utf-8",
    )
    assert find_gates_that_cannot_fail(gate_commands(None, [wf])) == []


def test_scope_keeps_other_projects_hooks_out(tmp_path):
    pc = _precommit(
        tmp_path,
        "      - id: a-bandit\n        entry: python -m bandit -r .\n        files: '^projA/'\n"
        "      - id: b-bandit\n        entry: python -m bandit -r .\n        files: '^projB/'\n",
    )
    assert set(gate_commands(pc, [], scope="projA/")) == {"pre-commit::a-bandit"}


def test_assert_uses_a_shrink_only_known_list(tmp_path):
    pc = _precommit(tmp_path, "      - id: bandit\n        entry: python -m bandit -r .\n")
    py = _pyproject(tmp_path)
    (problem,) = find_tools_run_without_their_config(gate_commands(pc, []), py)
    assert_gates_honest(pc, [], py, known=[problem])
    with pytest.raises(pytest.fail.Exception, match="do not do what they say"):
        assert_gates_honest(pc, [], py)
    with pytest.raises(pytest.fail.Exception, match="no longer reproduce"):
        assert_gates_honest(pc, [], py, known=[problem, "stale entry"])
