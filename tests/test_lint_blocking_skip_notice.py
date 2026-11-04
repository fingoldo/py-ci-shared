"""34-F10: lint-blocking names the tools its inputs skip (interrogate, deptry, zizmor) instead of counting the caller as running them."""

from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "lint-blocking.yml"


def test_lint_blocking_says_which_tools_its_inputs_skip():
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    step = {s.get("name"): s for s in wf["jobs"]["lint-blocking"]["steps"]}["Report tools skipped by their inputs"]
    run = step["run"]
    assert "::notice" in run and "GITHUB_STEP_SUMMARY" in run
    for needle in ("interrogate", "deptry", "zizmor"):
        assert needle in run
    assert "exit 1" not in run, "informational: never fails the caller"
    assert step["working-directory"] == "."
