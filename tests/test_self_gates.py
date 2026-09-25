"""Dogfood: every gate this repo enables in its own [tool.py_ci_shared] passes on this repo, under pytest too."""

from __future__ import annotations

from pathlib import Path

import pytest

from py_ci_shared._core.config import load_config
from py_ci_shared._core.runner import PASSED, run_gate

REPO = Path(__file__).resolve().parents[1]
CONFIG = load_config(REPO)
assert CONFIG is not None, "pyproject.toml lost its [tool.py_ci_shared] table"


def test_the_dogfood_set_covers_the_gates_this_repo_can_break():
    enabled = {run.module for run in CONFIG.gates}
    expected = {
        "repo_hygiene",
        "version_consistency",
        "entry_points_resolvable",
        "ci_workflow_timeout_gate",
        "ci_workflow_paths",
        "private_imports",
        "naive_utcnow",
        "identity_comparisons",
        "phantom_markdown_links",
        "unresolved_imports",
        "value_bearing_asserts",
        "loc_budget",
        "function_length",
        "complexity_ratchet",
        "fail_open_handlers",
        "fail_message_quality",
        "audit_round_format",
        "pytest_markers",
    }
    assert expected <= enabled, f"dropped from [tool.py_ci_shared]: {sorted(expected - enabled)}"
    workflows = {p.name for p in (REPO / ".github" / "workflows").glob("*.yml")}
    timed = {Path(r.kwargs["workflow_path"]).name for r in CONFIG.gates if r.module == "ci_workflow_timeout_gate"}
    assert timed == workflows, f"workflows without a timeout gate entry: {sorted(workflows - timed)}"


@pytest.mark.parametrize("run", CONFIG.gates, ids=lambda r: r.name)
def test_gate_passes_on_this_repo(run):
    result = run_gate(CONFIG, run)
    assert result.status == PASSED, f"{run.name} ({run.module}) {result.status} on this repo:\n{result.message}"
