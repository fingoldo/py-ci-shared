"""Unit tests for the warn-only advisory pre-commit wrapper (ruff complexity + pip-audit)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from py_ci_shared import advisory_warn

SRC = str(Path(__file__).resolve().parents[1] / "src")


@pytest.fixture
def calls(monkeypatch):
    seen: list[list[str]] = []

    def run(cmd, **kwargs):
        seen.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(advisory_warn.subprocess, "run", run)
    monkeypatch.setattr(advisory_warn, "_tool_installed", lambda name: True)
    monkeypatch.delenv("PY_CI_SHARED_SRC_PATH", raising=False)
    return seen


def test_no_staged_source_file_runs_nothing_not_even_pip_audit(calls):
    assert advisory_warn.main(["--src-path", "src/pkg", "README.md"]) == 0
    assert calls == []


def test_a_staged_source_file_runs_ruff_twice_and_pip_audit_once(calls):
    assert advisory_warn.main(["--src-path=src/pkg", "src/pkg/a.py", "tests/t.py"]) == 0
    assert all(c[1] == "-I" for c in calls), "isolated mode: a repo-root ruff/ directory must not shadow the tool"
    tools = [c[3] for c in calls]
    assert tools == ["ruff", "ruff", "pip_audit"]
    assert calls[0][-1] == "src/pkg/a.py" and "tests/t.py" not in calls[0]


def test_a_sibling_prefix_is_not_the_src_path(calls):
    assert advisory_warn.main(["--src-path", "src/pkg", "src/pkg_other/a.py"]) == 0
    assert calls == []


def test_a_trailing_src_path_does_not_crash_the_hook():
    env = {**os.environ, "PYTHONPATH": SRC}
    env.pop("PY_CI_SHARED_SRC_PATH", None)
    proc = subprocess.run([sys.executable, "-m", "py_ci_shared.advisory_warn", "a.py", "--src-path"], capture_output=True, text=True, env=env, check=False)
    assert proc.returncode == 0, proc.stderr
    assert "--src-path was given without a value" in proc.stderr


@pytest.fixture
def exits(monkeypatch):
    """Like ``calls`` but each tool exits with the code ``codes[tool]`` (default 0)."""
    codes: dict = {}

    def run(cmd, **kwargs):
        tool = next((t for t in ("ruff", "pip_audit") if t in cmd), "")
        return subprocess.CompletedProcess(cmd, codes.get(tool, 0))

    monkeypatch.setattr(advisory_warn.subprocess, "run", run)
    monkeypatch.setattr(advisory_warn, "_tool_installed", lambda name: True)
    monkeypatch.delenv("PY_CI_SHARED_SRC_PATH", raising=False)
    return codes


def test_clean_tools_print_no_banner(exits, capsys):
    assert advisory_warn.main(["--src-path", "src/pkg", "src/pkg/a.py"]) == 0
    assert "WARNINGS ONLY" not in capsys.readouterr().err


@pytest.mark.parametrize("failing", ["ruff", "pip_audit"])
def test_a_non_zero_tool_exit_prints_the_advisory_banner_and_still_exits_zero(exits, capsys, failing):
    exits[failing] = 1
    assert advisory_warn.main(["--src-path", "src/pkg", "src/pkg/a.py"]) == 0
    assert "WARNINGS ONLY" in capsys.readouterr().err
