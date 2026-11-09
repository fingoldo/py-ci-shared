"""A tool version written out in CLAUDE.md or self-ci.yml is the one ``tool_versions.py`` pins.

CLAUDE.md once told contributors to lint with ``ruff@0.16.1`` while CI ran 0.16.10, so a local pass could fail in CI."""

from __future__ import annotations

import re
from pathlib import Path

from py_ci_shared.tool_versions import BLACK_VERSION, MYPY_VERSION, RUFF_VERSION

REPO = Path(__file__).resolve().parents[1]


def test_claude_md_names_the_pinned_ruff_and_black():
    text = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
    ruff = set(re.findall(r"ruff@(\d[\w.]*)", text))
    black = set(re.findall(r"black==(\d[\w.]*)", text))
    assert black == {BLACK_VERSION}, f"CLAUDE.md names black {sorted(black)}, tool_versions.BLACK_VERSION is {BLACK_VERSION}"
    assert ruff <= {RUFF_VERSION}, f"CLAUDE.md names ruff {sorted(ruff)}, tool_versions.RUFF_VERSION is {RUFF_VERSION}"


def test_self_ci_installs_the_pinned_mypy():
    text = (REPO / ".github" / "workflows" / "self-ci.yml").read_text(encoding="utf-8")
    assert set(re.findall(r"mypy==([\d.]+)", text)) == {MYPY_VERSION}


def test_the_reusable_mypy_workflows_default_to_the_pinned_mypy():
    """46-F19: the mypy pin is written in three places; the two reusable workflows' ``mypy-version`` defaults must match too."""
    import yaml

    for name in ("mypy-beachhead.yml", "mypy-full.yml"):
        workflow = yaml.safe_load((REPO / ".github" / "workflows" / name).read_text(encoding="utf-8"))
        triggers = workflow.get("on") or workflow[True]
        default = str(triggers["workflow_call"]["inputs"]["mypy-version"]["default"])
        assert default == MYPY_VERSION, f"{name} defaults mypy-version to {default}, tool_versions.MYPY_VERSION is {MYPY_VERSION}"
