"""Behavioral tests for py_ci_shared.install_safe_hook: rewrites .git/hooks/pre-commit to invoke
safe_precommit instead of raw pre_commit, idempotently.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

_TEMPLATE = """#!/bin/sh
INSTALL_PYTHON='/usr/bin/python3'
ARGS=(hook-impl --config=.pre-commit-config.yaml --hook-type=pre-commit)
HERE="$(cd "$(dirname "$0")" && pwd)"
ARGS+=(--hook-dir "$HERE" -- "$@")
if [ -x "$INSTALL_PYTHON" ]; then
    exec "$INSTALL_PYTHON" -mpre_commit "${ARGS[@]}"
fi
"""


def _init_repo_with_hook(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    (hooks_dir / "pre-commit").write_text(_TEMPLATE)
    return repo


def test_install_safe_hook_rewrites_the_module_invocation(tmp_path, monkeypatch):
    from py_ci_shared.install_safe_hook import main

    repo = _init_repo_with_hook(tmp_path)
    monkeypatch.chdir(repo)
    rc = main([])
    assert rc == 0
    text = (repo / ".git" / "hooks" / "pre-commit").read_text()
    assert "-m py_ci_shared.safe_precommit" in text
    assert "-mpre_commit" not in text


def test_install_safe_hook_is_idempotent(tmp_path, monkeypatch):
    from py_ci_shared.install_safe_hook import main

    repo = _init_repo_with_hook(tmp_path)
    monkeypatch.chdir(repo)
    assert main([]) == 0
    first = (repo / ".git" / "hooks" / "pre-commit").read_text()
    assert main([]) == 0
    second = (repo / ".git" / "hooks" / "pre-commit").read_text()
    assert first == second


def test_install_safe_hook_fails_cleanly_when_hook_missing(tmp_path, monkeypatch):
    from py_ci_shared.install_safe_hook import main

    repo = tmp_path / "no_hook_repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    monkeypatch.chdir(repo)
    assert main([]) == 1


def test_install_safe_hook_fails_cleanly_outside_a_repo(tmp_path, monkeypatch):
    from py_ci_shared.install_safe_hook import main

    monkeypatch.chdir(tmp_path)
    assert main([]) == 1
